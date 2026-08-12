from __future__ import annotations

import logging

from pydantic import ValidationError

from .llm_provider import LLMProvider, ProviderNotConfigured, ProviderRequestError
from .query_schemas import PlanningFailure, ProofState, QueryPlan, VerificationResult

logger = logging.getLogger(__name__)

PLANNER_SYSTEM_PROMPT = """\
You turn a natural-language question about a document corpus into a structured query \
plan: an ordered list of generic operations. You do not know the corpus's schema in \
advance - it is given to you per-question as the actual entity types and predicates \
that exist in this particular world model RIGHT NOW. That world model is built \
on demand, not upfront: it may start empty or only partly populated, and grows only \
where DISCOVER tells it to look. A short/empty schema context does not mean the \
corpus is small or that the question is unanswerable - it means nothing relevant has \
been pulled into the structured graph yet.

Available operation types (op_type) and their expected params:
- DISCOVER: {"query": str, "limit": int} -> finds documents relevant to `query` using the full-corpus deterministic retrieval index (fast, free, complete - covers every document regardless of what's been extracted), then runs structured extraction on ONLY those documents, adding their entities/relationships/attributes to the world model. ALWAYS call DISCOVER for a question's key named things (a person, a project, a certificate number, a client) BEFORE the first RESOLVE_ENTITY/ENUMERATE that needs them - an entity that doesn't appear in "known entity types/predicates" above almost certainly hasn't been discovered yet. Use a small limit (e.g. 5-10) for a single named entity, a larger limit (e.g. 30-50) before a population-style ENUMERATE, since the whole client's portfolio needs to be found, not just one document.
- RESOLVE_ENTITY: {"query": str, "entity_type": str|null} -> binds a resolved entity id (or ambiguous/unresolved candidates). Only useful after DISCOVER has made the entity extractable.
- SEARCH_EVIDENCE: {"query": str} -> binds a list of evidence snippets (for grounding language, not for entities/numbers). This does NOT populate the world model - use DISCOVER for that.
- TRAVERSE: {"input_var": "$var", "predicate": str|null, "direction": "out"|"in"|"both"} -> one-hop neighbor entities from bound entity id(s).
- ENUMERATE: {"entity_type": str|null, "predicate": str|null, "anchor_var": "$var"|null, "direction": "in"|"out"|"both"} -> ALL entities of a type, optionally connected to an anchor entity - use this for "all X" / population questions, not TRAVERSE. Precede with a broad DISCOVER (using the anchor's resolved name as the query) so the population is actually in the world model before you enumerate it.
- GET_ATTRIBUTE: {"input_var": "$var", "predicate": str} -> literal attribute values (e.g. a numeric value) for a set of entities.
- FILTER: {"input_var": "$var", "field": str, "op": "eq"|"neq"|"gt"|"gte"|"lt"|"lte"|"contains"|"in", "value": any} -> subset of a prior list matching a condition.
- DIFFERENCE: {"input_var": "$var", "exclude_var": "$var"} -> items in input_var that are NOT in exclude_var, by entity identity. This is how you answer "absence" questions ("which/how many X have no Y on file", "X missing Z"): ENUMERATE the full population as input_var, ENUMERATE (or TRAVERSE) the subset that DOES have the thing as exclude_var, then DIFFERENCE them - never assume something is missing just because you didn't find it; prove it by computing the difference against the full population.
- AGGREGATE: {"input_var": "$var", "function": "sum"|"avg"|"count"|"min"|"max"|"median"} -> a single number over a prior list of attribute values.
- COMPARE: {"left_var": "$var", "right_var": "$var", "op": "eq"|"neq"|"gt"|"gte"|"lt"|"lte"} -> boolean.
- COMPUTE: {"operation": "sum"|"avg"|"min"|"max"|"count"|"median"|"diff"|"ratio"|"pct_diff", "input_vars": ["$var", ...]} -> a deterministic numeric result over one or more prior bindings.
- CHECK_COMPLETENESS: {"entity_type": str, "expected_min": int|null, "anchor_query": str|null} -> whether the population of that type looks complete. Pass `anchor_query` (e.g. the resolved client's canonical name) for population/"all X" questions - this cross-checks your ENUMERATE result against the full deterministic corpus index and will tell you exactly which documents you're still missing, so you can DISCOVER them and retry rather than guessing.
- RETURN: {"input_var": "$var"} -> marks the final answer variable. Always end the plan with exactly one RETURN.

Rules:
- Every params value that should read a prior operation's result must be the string "$<output_var>" of that earlier operation.
- Every operation's output_var must be unique.
- Use ENUMERATE (not TRAVERSE) whenever the question implies "all"/"every"/a total across a population.
- Never invent arithmetic yourself - always route numbers through GET_ATTRIBUTE + AGGREGATE/COMPUTE.
- Keep the plan as short as it can be while still being fully supported by tool calls - do not add speculative steps, and do not DISCOVER more broadly than the question needs.
- Output must validate against the QueryPlan JSON schema you are given.
"""


def _schema_context_text(schema_context: dict) -> str:
    entity_types = ", ".join(schema_context.get("entity_types", [])) or "(none discovered yet - the world model starts empty and grows via DISCOVER)"
    predicates = ", ".join(schema_context.get("predicates", [])) or "(none discovered yet)"
    return f"Known entity types in this world model: {entity_types}\nKnown predicates in this world model: {predicates}"


class QueryPlanner:
    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def plan(self, question: str, schema_context: dict) -> QueryPlan | PlanningFailure:
        user_prompt = f"{_schema_context_text(schema_context)}\n\nQuestion: {question}\n\nProduce a QueryPlan."
        return self._request(PLANNER_SYSTEM_PROMPT, user_prompt)

    def replan(
        self,
        question: str,
        prior_plan: QueryPlan,
        proof_state: ProofState,
        verification: VerificationResult,
        schema_context: dict,
    ) -> QueryPlan | PlanningFailure:
        user_prompt = (
            f"{_schema_context_text(schema_context)}\n\n"
            f"Question: {question}\n\n"
            f"A previous plan was executed and failed verification.\n"
            f"Previous plan operations: {[op.model_dump() for op in prior_plan.operations]}\n"
            f"Proof trail so far:\n{proof_state.explain()}\n"
            f"Verification issues: {verification.issues}\n\n"
            f"Produce a REVISED QueryPlan that addresses these specific issues - do not "
            f"simply repeat the same operations. Consider: resolving a different entity "
            f"candidate, using ENUMERATE where TRAVERSE was used, fetching missing "
            f"attributes, or searching evidence to disambiguate."
        )
        return self._request(PLANNER_SYSTEM_PROMPT, user_prompt)

    def _request(self, system: str, user: str) -> QueryPlan | PlanningFailure:
        try:
            raw = self.provider.complete(system=system, user=user, response_schema=QueryPlan.json_schema())
        except ProviderNotConfigured as exc:
            return PlanningFailure(reason="no_provider_configured", detail=str(exc))
        except ProviderRequestError as exc:
            logger.warning("Planning request failed: %s", exc)
            return PlanningFailure(reason="provider_request_failed", detail=str(exc))

        try:
            return QueryPlan.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Planner output failed validation: %s", exc)
            return PlanningFailure(reason="invalid_plan_output", detail=str(exc), raw_output=str(raw))
