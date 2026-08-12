from __future__ import annotations

import logging
from typing import Any

from .mcp_tools import ToolDispatcher
from .normalization import NormalizationError, infer_normalization
from .query_schemas import Operation, ProofState, ProofStep, QueryPlan

logger = logging.getLogger(__name__)


def _resolve_refs(value: Any, bindings: dict[str, Any]) -> Any:
    """Recursively substitutes "$var" strings with their live binding."""
    if isinstance(value, str) and value.startswith("$"):
        return bindings.get(value[1:])
    if isinstance(value, list):
        return [_resolve_refs(v, bindings) for v in value]
    if isinstance(value, dict):
        return {k: _resolve_refs(v, bindings) for k, v in value.items()}
    return value


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _to_float(value: Any) -> float | None:
    """Coerces a value to a float for deterministic computation. Tries a plain numeric
    parse first; if that fails, falls back to the same deterministic money/percentage/
    number normalization used everywhere else in the pipeline (infer_normalization) -
    so a raw, LLM-transcribed string like "INR 33.38 Cr" or "3,338.00 Lakh" is still
    read correctly instead of silently vanishing from an aggregate as None.
    """
    if value is None:
        return None
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        pass
    try:
        result = infer_normalization(value)
    except NormalizationError:
        return None
    if result.normalized_type in ("currency_inr", "percentage", "number") and result.normalized_value is not None:
        try:
            return float(result.normalized_value)
        except (TypeError, ValueError):
            return None
    return None


def _apply_condition(value: Any, op: str, target: Any) -> bool:
    if op == "eq":
        return value == target
    if op == "neq":
        return value != target
    if op == "contains":
        return target is not None and str(target).lower() in str(value or "").lower()
    if op == "in":
        return value in (target or [])
    if op in ("gt", "gte", "lt", "lte"):
        left, right = _to_float(value), _to_float(target)
        if left is None or right is None:
            return False
        return {"gt": left > right, "gte": left >= right, "lt": left < right, "lte": left <= right}[op]
    return False


class MultiHopExecutor:
    """Executes a QueryPlan's operations in order, maintaining a ProofState with
    provenance. Dispatch is purely by op_type -> handler method; there is no
    per-question or per-predicate branching here - all domain content comes from the
    plan's params and the ToolDispatcher's data.
    """

    def __init__(self, dispatcher: ToolDispatcher) -> None:
        self.dispatcher = dispatcher
        self._handlers = {
            "DISCOVER": self._op_discover,
            "RESOLVE_ENTITY": self._op_resolve_entity,
            "SEARCH_EVIDENCE": self._op_search_evidence,
            "TRAVERSE": self._op_traverse,
            "ENUMERATE": self._op_enumerate,
            "GET_ATTRIBUTE": self._op_get_attribute,
            "FILTER": self._op_filter,
            "DIFFERENCE": self._op_difference,
            "AGGREGATE": self._op_aggregate,
            "COMPARE": self._op_compare,
            "COMPUTE": self._op_compute,
            "CHECK_COMPLETENESS": self._op_check_completeness,
            "RETURN": self._op_return,
        }

    def execute(self, plan: QueryPlan) -> ProofState:
        state = ProofState(remaining_ops=[op.output_var for op in plan.operations])
        for op in plan.operations:
            resolved_params = _resolve_refs(op.params, state.bindings)
            handler = self._handlers.get(op.op_type)
            if handler is None:
                self._record(state, op, "failed", note=f"unknown op_type: {op.op_type}")
            else:
                try:
                    handler(op, resolved_params, state)
                except Exception as exc:  # noqa: BLE001 - execution must not crash the loop
                    logger.warning("Operation %s (%s) failed: %s", op.output_var, op.op_type, exc)
                    state.bindings[op.output_var] = None
                    state.binding_kinds[op.output_var] = "scalar"
                    self._record(state, op, "failed", note=str(exc))
            if op.output_var in state.remaining_ops:
                state.remaining_ops.remove(op.output_var)
            state.completed_ops.append(op.output_var)
        return state

    def _record(
        self,
        state: ProofState,
        op: Operation,
        status: str,
        result_summary: str = "",
        evidence_ids: list[str] | None = None,
        entity_ids: list[str] | None = None,
        note: str = "",
    ) -> None:
        state.trace.append(
            ProofStep(
                output_var=op.output_var,
                op_type=op.op_type,
                params=op.params,
                status=status,
                result_summary=result_summary,
                evidence_ids=evidence_ids or [],
                entity_ids=entity_ids or [],
                note=note,
            )
        )
        if evidence_ids:
            for evidence_id in evidence_ids:
                if evidence_id and evidence_id not in state.evidence_used:
                    state.evidence_used.append(evidence_id)
        if entity_ids:
            for entity_id in entity_ids:
                if entity_id and entity_id not in state.entities_touched:
                    state.entities_touched.append(entity_id)

    # --- operation handlers --------------------------------------------------------------

    def _op_discover(self, op: Operation, params: dict, state: ProofState) -> None:
        """Populates the semantic graph on demand: runs the free, full-corpus lexical/
        semantic index to find which documents are relevant to `query`, then runs
        targeted (idempotent, cache-aware) extraction on only those documents. This is
        the ONLY op that spends LLM calls on extraction - everything downstream
        (RESOLVE_ENTITY, TRAVERSE, ENUMERATE, GET_ATTRIBUTE, ...) reads the resulting
        structured rows exactly as if they'd always been there. DISCOVER never answers
        anything itself; its output_var just reports which documents it touched, for
        the proof trace.
        """
        query = params.get("query", "")
        limit = params.get("limit", 15)
        found = self.dispatcher.call("discover_evidence", query=query, limit=limit)
        document_ids = found.get("document_ids", [])
        extracted = self.dispatcher.call("extract_documents", document_ids=document_ids, batch_size=params.get("batch_size", 30))
        newly_extracted = extracted.get("newly_extracted_document_ids", [])

        state.bindings[op.output_var] = document_ids
        state.binding_kinds[op.output_var] = "documents"
        status = "ok" if document_ids else "empty"
        self._record(
            state,
            op,
            status,
            f"{len(document_ids)} candidate documents, {len(newly_extracted)} newly extracted "
            f"(world model now {extracted.get('entities_total', 0)} entities)",
        )

    def _op_resolve_entity(self, op: Operation, params: dict, state: ProofState) -> None:
        result = self.dispatcher.call("resolve_entity", query=params.get("query", ""), entity_type=params.get("entity_type"))
        status = result.get("status", "unresolved")
        if status == "resolved" and result.get("resolved_entity_id"):
            entity_ids = [result["resolved_entity_id"]]
            state.bindings[op.output_var] = entity_ids
            state.binding_kinds[op.output_var] = "entities"
            self._record(state, op, "ok", f"resolved -> {entity_ids}", entity_ids=entity_ids)
        elif status == "ambiguous":
            entity_ids = [c["entity_id"] for c in result.get("candidates", [])]
            state.bindings[op.output_var] = entity_ids
            state.binding_kinds[op.output_var] = "entities"
            state.unresolved_ambiguity.append({"output_var": op.output_var, "query": params.get("query"), "candidates": result.get("candidates")})
            self._record(state, op, "ambiguous", f"ambiguous among {entity_ids}", entity_ids=entity_ids)
        else:
            state.bindings[op.output_var] = []
            state.binding_kinds[op.output_var] = "entities"
            self._record(state, op, "empty", "no matching entity")

    def _op_search_evidence(self, op: Operation, params: dict, state: ProofState) -> None:
        result = self.dispatcher.call("search_evidence", query=params.get("query", ""), limit=params.get("limit", 10))
        results = result.get("results", [])
        evidence_ids = [
            item.get("metadata", {}).get("evidence_id") or item.get("id")
            for item in results
            if item.get("metadata", {}).get("evidence_id") or item.get("id")
        ]
        state.bindings[op.output_var] = results
        state.binding_kinds[op.output_var] = "evidence"
        status = "ok" if results else "empty"
        self._record(state, op, status, f"{len(results)} evidence snippets", evidence_ids=evidence_ids)

    def _op_traverse(self, op: Operation, params: dict, state: ProofState) -> None:
        entity_ids = _as_list(params.get("input_var"))
        result = self.dispatcher.call(
            "traverse_graph", entity_ids=entity_ids, predicate=params.get("predicate"), direction=params.get("direction", "out")
        )
        neighbor_ids = result.get("neighbor_entity_ids", [])
        evidence_ids = [edge.get("evidence_id") for edge in result.get("edges", []) if edge.get("evidence_id")]
        state.bindings[op.output_var] = neighbor_ids
        state.binding_kinds[op.output_var] = "entities"
        status = "ok" if neighbor_ids else "empty"
        self._record(state, op, status, f"{len(neighbor_ids)} neighbors", evidence_ids=evidence_ids, entity_ids=neighbor_ids)

    def _op_enumerate(self, op: Operation, params: dict, state: ProofState) -> None:
        anchor_ids = _as_list(params.get("anchor_var"))
        entity_type = params.get("entity_type")
        predicate = params.get("predicate")
        direction = params.get("direction", "in")

        if not anchor_ids:
            result = self.dispatcher.call("enumerate_population", entity_type=entity_type, predicate=predicate)
            entity_ids = result.get("entity_ids", [])
        else:
            entity_ids_set: set[str] = set()
            for anchor_id in anchor_ids:
                result = self.dispatcher.call(
                    "enumerate_population", entity_type=entity_type, predicate=predicate, anchor_entity_id=anchor_id, direction=direction
                )
                entity_ids_set.update(result.get("entity_ids", []))
            entity_ids = sorted(entity_ids_set)

        state.bindings[op.output_var] = entity_ids
        state.binding_kinds[op.output_var] = "entities"
        status = "ok" if entity_ids else "empty"
        self._record(state, op, status, f"{len(entity_ids)} entities enumerated", entity_ids=entity_ids)

    def _op_get_attribute(self, op: Operation, params: dict, state: ProofState) -> None:
        entity_ids = _as_list(params.get("input_var"))
        result = self.dispatcher.call("get_attribute", entity_ids=entity_ids, predicate=params.get("predicate", ""))
        attributes = result.get("attributes", [])
        evidence_ids = [a.get("evidence_id") for a in attributes if a.get("evidence_id")]
        state.bindings[op.output_var] = attributes
        state.binding_kinds[op.output_var] = "rows"
        status = "ok" if attributes else "empty"
        self._record(state, op, status, f"{len(attributes)} attribute values", evidence_ids=evidence_ids)

    def _op_filter(self, op: Operation, params: dict, state: ProofState) -> None:
        input_var_name = op.params.get("input_var", "").lstrip("$")
        items = _as_list(params.get("input_var"))
        kind = state.binding_kinds.get(input_var_name, "rows")
        field = params.get("field")
        condition_op = params.get("op", "eq")
        target = params.get("value")

        kept: list[Any] = []
        for item in items:
            if kind == "entities":
                entity = self.dispatcher.call("get_entity", entity_id=item)
                value = entity.get(field)
            elif isinstance(item, dict):
                value = item.get(field)
            else:
                value = item
            if _apply_condition(value, condition_op, target):
                kept.append(item)

        state.bindings[op.output_var] = kept
        state.binding_kinds[op.output_var] = kind
        status = "ok" if kept else "empty"
        entity_ids = kept if kind == "entities" else []
        self._record(state, op, status, f"{len(kept)}/{len(items)} kept", entity_ids=entity_ids)

    def _op_difference(self, op: Operation, params: dict, state: ProofState) -> None:
        """Set difference: input_var minus exclude_var. This is the generic tool for
        "absence" questions - e.g. "which completed projects have no reference letter"
        is ENUMERATE(all projects) DIFFERENCE ENUMERATE(projects with a reference_letter
        relationship), then AGGREGATE(count). Identity is by entity_id for entity/row
        items, falling back to raw equality otherwise - no predicate-specific logic.
        """
        included = _as_list(params.get("input_var"))
        excluded = _as_list(params.get("exclude_var"))
        excluded_keys = {self._identity(item) for item in excluded}
        kept = [item for item in included if self._identity(item) not in excluded_keys]

        input_var_name = op.params.get("input_var", "").lstrip("$")
        kind = state.binding_kinds.get(input_var_name, "entities")
        state.bindings[op.output_var] = kept
        state.binding_kinds[op.output_var] = kind
        status = "ok" if kept else "empty"
        entity_ids = kept if kind == "entities" else []
        self._record(state, op, status, f"{len(kept)} remain after excluding {len(excluded)}", entity_ids=entity_ids)

    @staticmethod
    def _identity(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get("entity_id", item.get("id", repr(item)))
        return item

    def _op_aggregate(self, op: Operation, params: dict, state: ProofState) -> None:
        items = _as_list(params.get("input_var"))
        numeric = [_to_float(item.get("value") if isinstance(item, dict) else item) for item in items]
        numeric = [v for v in numeric if v is not None]
        evidence_ids = [item.get("evidence_id") for item in items if isinstance(item, dict) and item.get("evidence_id")]

        result = self.dispatcher.call("calculate", operation=params.get("function", "sum"), values=numeric)
        state.bindings[op.output_var] = result.get("result")
        state.binding_kinds[op.output_var] = "scalar"
        status = "ok" if "error" not in result else "failed"
        self._record(state, op, status, f"{params.get('function')} -> {result.get('result')}", evidence_ids=evidence_ids, note=result.get("error", ""))

    def _op_compare(self, op: Operation, params: dict, state: ProofState) -> None:
        left = _to_float(params.get("left_var"))
        right = _to_float(params.get("right_var"))
        condition_op = params.get("op", "eq")
        outcome = _apply_condition(left, condition_op, right) if left is not None and right is not None else None
        state.bindings[op.output_var] = outcome
        state.binding_kinds[op.output_var] = "scalar"
        self._record(state, op, "ok" if outcome is not None else "failed", f"{left} {condition_op} {right} -> {outcome}")

    def _op_compute(self, op: Operation, params: dict, state: ProofState) -> None:
        input_vars = params.get("input_vars", [])
        values: list[float] = []
        for group in input_vars:
            for item in _as_list(group):
                numeric = _to_float(item.get("value") if isinstance(item, dict) else item)
                if numeric is not None:
                    values.append(numeric)
        result = self.dispatcher.call("calculate", operation=params.get("operation", "sum"), values=values)
        state.bindings[op.output_var] = result.get("result")
        state.binding_kinds[op.output_var] = "scalar"
        status = "ok" if "error" not in result else "failed"
        self._record(state, op, status, f"{params.get('operation')} -> {result.get('result')}", note=result.get("error", ""))

    def _op_check_completeness(self, op: Operation, params: dict, state: ProofState) -> None:
        result = self.dispatcher.call(
            "check_completeness",
            entity_type=params.get("entity_type", ""),
            expected_min=params.get("expected_min"),
            anchor_query=params.get("anchor_query"),
        )
        state.bindings[op.output_var] = result
        state.binding_kinds[op.output_var] = "scalar"
        status = "ok" if result.get("complete") else "failed"
        self._record(state, op, status, f"complete={result.get('complete')}", note=str(result))

    def _op_return(self, op: Operation, params: dict, state: ProofState) -> None:
        value = params.get("input_var")
        input_var_name = op.params.get("input_var", "").lstrip("$")
        state.bindings[op.output_var] = value
        state.binding_kinds[op.output_var] = state.binding_kinds.get(input_var_name, "scalar")
        self._record(state, op, "ok" if value not in (None, [], {}) else "empty", f"final -> {value}")
