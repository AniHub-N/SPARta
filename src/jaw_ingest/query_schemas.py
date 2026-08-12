from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

QUERY_SCHEMA_VERSION = "1.0"

# Generic operation vocabulary. These are execution primitives, not question-specific
# handlers - the planner combines them however a given question requires, and the
# executor dispatches purely on op_type + params, with no per-question branching.
OpType = Literal[
    "DISCOVER",
    "RESOLVE_ENTITY",
    "SEARCH_EVIDENCE",
    "TRAVERSE",
    "ENUMERATE",
    "GET_ATTRIBUTE",
    "FILTER",
    "DIFFERENCE",
    "AGGREGATE",
    "COMPARE",
    "COMPUTE",
    "CHECK_COMPLETENESS",
    "RETURN",
]


class Operation(BaseModel):
    """One step in a query plan. `params` is intentionally an open dict - its expected
    keys depend on op_type (see planner.py's PLANNER_SYSTEM_PROMPT for the per-op-type
    contract) but the schema itself does not hardcode per-question fields.

    Values in `params` may reference an earlier operation's output by writing
    "$<output_var>" (e.g. {"input_var": "$projects"}) - the executor substitutes the
    live binding at execution time.
    """

    output_var: str = Field(description="Name this operation's result is bound to in the proof state.")
    op_type: OpType
    params: dict[str, Any] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list, description="output_var names this operation reads from.")
    description: str = Field(default="", description="Short human-readable reason for this step.")

    @staticmethod
    def json_schema() -> dict[str, Any]:
        return Operation.model_json_schema()


class QueryPlan(BaseModel):
    """A structured execution plan for one natural-language question."""

    question: str
    understanding: str = Field(default="", description="Brief restatement of what the question is asking for.")
    operations: list[Operation] = Field(default_factory=list)
    final_var: str = Field(default="", description="output_var holding the final answer value.")

    @staticmethod
    def json_schema() -> dict[str, Any]:
        return QueryPlan.model_json_schema()


class ProofStep(BaseModel):
    """One executed operation's outcome, kept for the 'why do we believe this' trail."""

    output_var: str
    op_type: OpType
    params: dict[str, Any] = Field(default_factory=dict)
    status: Literal["ok", "empty", "ambiguous", "failed"]
    result_summary: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    entity_ids: list[str] = Field(default_factory=list)
    note: str = ""


class ProofState(BaseModel):
    """Everything the executor has established so far, with provenance at every step."""

    bindings: dict[str, Any] = Field(default_factory=dict)
    binding_kinds: dict[str, str] = Field(default_factory=dict)  # var -> "entities" | "scalar" | "evidence" | "rows"
    evidence_used: list[str] = Field(default_factory=list)
    entities_touched: list[str] = Field(default_factory=list)
    trace: list[ProofStep] = Field(default_factory=list)
    unresolved_ambiguity: list[dict[str, Any]] = Field(default_factory=list)
    completed_ops: list[str] = Field(default_factory=list)
    remaining_ops: list[str] = Field(default_factory=list)
    confidence: float = 1.0

    def explain(self) -> str:
        lines = []
        for step in self.trace:
            marker = {"ok": "OK", "empty": "EMPTY", "ambiguous": "AMBIGUOUS", "failed": "FAILED"}[step.status]
            lines.append(f"[{marker}] {step.output_var} = {step.op_type}({step.params}) -> {step.result_summary}")
        return "\n".join(lines)


class PlanningFailure(BaseModel):
    """Explicit failure record for a planning/replanning attempt. Never silently dropped."""

    reason: str
    detail: str | None = None
    raw_output: str | None = None


class VerificationResult(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    checked: list[str] = Field(default_factory=list)


class EvidenceCitation(BaseModel):
    evidence_id: str
    document_id: str
    location: dict[str, Any] = Field(default_factory=dict)
    text: str | None = None


class FinalAnswer(BaseModel):
    answer: Any
    status: Literal["verified", "unverified", "insufficient_evidence"] = "unverified"
    confidence: float = 0.0
    proof_summary: str = ""
    evidence: list[EvidenceCitation] = Field(default_factory=list)


class QueryResult(BaseModel):
    """The full, inspectable output of running one question through the engine."""

    question: str
    final_answer: FinalAnswer
    plan: QueryPlan
    proof_state: ProofState
    verification: VerificationResult
    iterations_used: int
