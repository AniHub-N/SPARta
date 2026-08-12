from __future__ import annotations

import logging

from .answer import synthesize_answer
from .executor import MultiHopExecutor
from .llm_provider import LLMProvider
from .mcp_tools import ToolDispatcher
from .planner import QueryPlanner
from .query_schemas import FinalAnswer, PlanningFailure, ProofState, QueryPlan, QueryResult, VerificationResult
from .verifier import Verifier

logger = logging.getLogger(__name__)


class QueryEngine:
    """The PLAN -> EXECUTE -> OBSERVE -> VERIFY -> REPLAN loop, bounded by
    max_iterations. All domain reasoning is delegated to the planner (LLM-backed) and
    executor (deterministic tool dispatch) - this class only owns control flow.
    """

    def __init__(
        self,
        dispatcher: ToolDispatcher,
        planner: QueryPlanner,
        verifier: Verifier | None = None,
        max_iterations: int = 3,
        answer_provider: LLMProvider | None = None,
    ) -> None:
        self.dispatcher = dispatcher
        self.planner = planner
        self.verifier = verifier or Verifier()
        self.max_iterations = max(1, max_iterations)
        self.answer_provider = answer_provider
        self.executor = MultiHopExecutor(dispatcher)

    def schema_context(self) -> dict:
        world_model = self.dispatcher.world_model
        entity_types = sorted({e.entity_type for e in world_model.canonical_entities if e.entity_type})
        predicates = sorted(
            {r.predicate for r in world_model.relationships if r.predicate}
            | {a.predicate for a in world_model.attributes if a.predicate}
        )
        return {"entity_types": entity_types, "predicates": predicates}

    def run(self, question: str) -> QueryResult:
        schema_context = self.schema_context()

        plan_or_failure = self.planner.plan(question, schema_context)
        if isinstance(plan_or_failure, PlanningFailure):
            return self._failure_result(question, plan_or_failure)
        plan = plan_or_failure

        proof_state = ProofState()
        verification = VerificationResult(passed=False, issues=["not yet executed"])
        iterations_used = 0

        for iteration in range(1, self.max_iterations + 1):
            iterations_used = iteration
            proof_state = self.executor.execute(plan)
            verification = self.verifier.verify(plan, proof_state)
            if verification.passed:
                break
            if iteration == self.max_iterations:
                logger.info("Verification still failing after %s iterations: %s", iteration, verification.issues)
                break
            replanned = self.planner.replan(question, plan, proof_state, verification, schema_context)
            if isinstance(replanned, PlanningFailure):
                logger.info("Replanning unavailable (%s); stopping with best-effort proof state.", replanned.reason)
                break
            plan = replanned

        final_answer = synthesize_answer(plan, proof_state, verification, self.dispatcher, self.answer_provider)
        return QueryResult(
            question=question,
            final_answer=final_answer,
            plan=plan,
            proof_state=proof_state,
            verification=verification,
            iterations_used=iterations_used,
        )

    def _failure_result(self, question: str, failure: PlanningFailure) -> QueryResult:
        empty_plan = QueryPlan(question=question)
        proof_state = ProofState()
        issue = f"planning failed ({failure.reason}): {failure.detail or ''}"
        verification = VerificationResult(passed=False, issues=[issue])
        final_answer = FinalAnswer(answer=None, status="insufficient_evidence", confidence=0.0, proof_summary=issue, evidence=[])
        return QueryResult(
            question=question,
            final_answer=final_answer,
            plan=empty_plan,
            proof_state=proof_state,
            verification=verification,
            iterations_used=0,
        )
