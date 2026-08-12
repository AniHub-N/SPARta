from __future__ import annotations

from jaw_ingest.executor import MultiHopExecutor
from jaw_ingest.query_schemas import Operation, QueryPlan


class FakeDispatcher:
    """Records calls and returns pre-programmed responses, keyed by tool name."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict]] = []

    def call(self, tool_name: str, **kwargs) -> dict:
        self.calls.append((tool_name, kwargs))
        return self.responses.get(tool_name, {})


def _plan(*ops: Operation, final_var: str | None = None) -> QueryPlan:
    return QueryPlan(question="q", operations=list(ops), final_var=final_var or (ops[-1].output_var if ops else ""))


def test_resolve_entity_resolved_binds_single_id_and_records_provenance() -> None:
    dispatcher = FakeDispatcher({"resolve_entity": {"status": "resolved", "resolved_entity_id": "e1", "candidates": [{"entity_id": "e1", "name": "X", "score": 0.9}]}})
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "X"}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["p"] == ["e1"]
    assert state.binding_kinds["p"] == "entities"
    assert "e1" in state.entities_touched
    assert state.trace[0].status == "ok"


def test_resolve_entity_ambiguous_records_unresolved_ambiguity() -> None:
    dispatcher = FakeDispatcher(
        {"resolve_entity": {"status": "ambiguous", "resolved_entity_id": None, "candidates": [{"entity_id": "e1", "name": "X", "score": 0.7}, {"entity_id": "e2", "name": "X2", "score": 0.65}]}}
    )
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "X"}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["p"] == ["e1", "e2"]
    assert state.trace[0].status == "ambiguous"
    assert len(state.unresolved_ambiguity) == 1


def test_resolve_entity_unresolved_binds_empty_list() -> None:
    dispatcher = FakeDispatcher({"resolve_entity": {"status": "unresolved", "resolved_entity_id": None, "candidates": []}})
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "nope"}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["p"] == []
    assert state.trace[0].status == "empty"


def test_var_substitution_passes_prior_binding_into_next_op() -> None:
    dispatcher = FakeDispatcher(
        {
            "resolve_entity": {"status": "resolved", "resolved_entity_id": "client1", "candidates": []},
            "enumerate_population": {"entity_ids": ["proj1", "proj2"], "count": 2},
        }
    )
    plan = _plan(
        Operation(output_var="client", op_type="RESOLVE_ENTITY", params={"query": "Metro Authority", "entity_type": "client"}),
        Operation(output_var="projects", op_type="ENUMERATE", params={"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}),
    )

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["projects"] == ["proj1", "proj2"]
    # the executor must have substituted the live binding, not the literal string "$client"
    enumerate_call = next(c for name, c in dispatcher.calls if name == "enumerate_population")
    assert enumerate_call["anchor_entity_id"] == "client1"


def test_get_attribute_then_aggregate_sum_and_provenance_carries_through() -> None:
    dispatcher = FakeDispatcher(
        {
            "get_attribute": {
                "attributes": [
                    {"entity_id": "p1", "predicate": "contract_value", "value": "100000000", "value_type": "currency", "confidence": 0.9, "evidence_id": "ev1"},
                    {"entity_id": "p2", "predicate": "contract_value", "value": "200000000", "value_type": "currency", "confidence": 0.9, "evidence_id": "ev2"},
                ],
                "count": 2,
            },
            "calculate": {"operation": "sum", "inputs": [100000000.0, 200000000.0], "result": 300000000.0},
        }
    )
    plan = _plan(
        Operation(output_var="projects", op_type="RESOLVE_ENTITY", params={"query": "x"}),  # placeholder, unused ref
        Operation(output_var="values", op_type="GET_ATTRIBUTE", params={"input_var": ["p1", "p2"], "predicate": "contract_value"}),
        Operation(output_var="total", op_type="AGGREGATE", params={"input_var": "$values", "function": "sum"}),
        final_var="total",
    )

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["total"] == 300000000.0
    assert set(state.evidence_used) >= {"ev1", "ev2"}


def test_filter_on_rows_by_numeric_threshold() -> None:
    dispatcher = FakeDispatcher(
        {"get_attribute": {"attributes": [{"entity_id": "p1", "value": "5", "evidence_id": "e1"}, {"entity_id": "p2", "value": "50", "evidence_id": "e2"}], "count": 2}}
    )
    plan = _plan(
        Operation(output_var="rows", op_type="GET_ATTRIBUTE", params={"input_var": [], "predicate": "x"}),
        Operation(output_var="big", op_type="FILTER", params={"input_var": "$rows", "field": "value", "op": "gt", "value": 10}),
        final_var="big",
    )

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["big"] == [{"entity_id": "p2", "value": "50", "evidence_id": "e2"}]


def test_compute_across_multiple_input_vars() -> None:
    dispatcher = FakeDispatcher({"calculate": {"operation": "diff", "inputs": [300.0, 100.0], "result": 200.0}})
    plan = _plan(
        Operation(output_var="a", op_type="COMPUTE", params={"operation": "diff", "input_vars": [[300], [100]]}),
        final_var="a",
    )

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["a"] == 200.0


def test_check_completeness_binds_result_dict() -> None:
    dispatcher = FakeDispatcher({"check_completeness": {"entity_type": "project", "count": 3, "unresolved_count": 0, "ambiguous_count": 0, "extraction_failures": {}, "complete": True}})
    plan = _plan(Operation(output_var="c", op_type="CHECK_COMPLETENESS", params={"entity_type": "project"}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["c"]["complete"] is True
    assert state.trace[0].status == "ok"


def test_failed_operation_does_not_crash_the_loop() -> None:
    class ExplodingDispatcher:
        def call(self, tool_name, **kwargs):
            raise RuntimeError("boom")

    plan = _plan(
        Operation(output_var="a", op_type="RESOLVE_ENTITY", params={"query": "x"}),
        Operation(output_var="b", op_type="RETURN", params={"input_var": "$a"}),
        final_var="b",
    )

    state = MultiHopExecutor(ExplodingDispatcher()).execute(plan)

    assert state.trace[0].status == "failed"
    assert "boom" in state.trace[0].note
    # execution continues to the next operation rather than raising
    assert state.completed_ops == ["a", "b"]


def test_return_binds_final_value_with_matching_kind() -> None:
    dispatcher = FakeDispatcher({"resolve_entity": {"status": "resolved", "resolved_entity_id": "e1", "candidates": []}})
    plan = _plan(
        Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}),
        Operation(output_var="answer", op_type="RETURN", params={"input_var": "$p"}),
        final_var="answer",
    )

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["answer"] == ["e1"]
    assert state.binding_kinds["answer"] == "entities"


def test_discover_calls_discover_evidence_then_extract_documents() -> None:
    dispatcher = FakeDispatcher(
        {
            "discover_evidence": {"document_ids": ["doc-1", "doc-2"], "count": 2},
            "extract_documents": {
                "requested_document_ids": ["doc-1", "doc-2"],
                "newly_extracted_document_ids": ["doc-1", "doc-2"],
                "already_covered_count": 0,
                "entities_total": 4,
                "relationships_total": 2,
            },
        }
    )
    plan = _plan(Operation(output_var="discovered", op_type="DISCOVER", params={"query": "Asha Nair", "limit": 10}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["discovered"] == ["doc-1", "doc-2"]
    assert state.binding_kinds["discovered"] == "documents"
    assert state.trace[0].status == "ok"
    discover_call = next(c for name, c in dispatcher.calls if name == "discover_evidence")
    assert discover_call == {"query": "Asha Nair", "limit": 10}
    extract_call = next(c for name, c in dispatcher.calls if name == "extract_documents")
    assert extract_call["document_ids"] == ["doc-1", "doc-2"]


def test_discover_empty_when_nothing_found() -> None:
    dispatcher = FakeDispatcher(
        {
            "discover_evidence": {"document_ids": [], "count": 0},
            "extract_documents": {"requested_document_ids": [], "newly_extracted_document_ids": [], "already_covered_count": 0, "entities_total": 0, "relationships_total": 0},
        }
    )
    plan = _plan(Operation(output_var="discovered", op_type="DISCOVER", params={"query": "nonexistent thing", "limit": 10}))

    state = MultiHopExecutor(dispatcher).execute(plan)

    assert state.bindings["discovered"] == []
    assert state.trace[0].status == "empty"
