from __future__ import annotations

from jaw_ingest.query_schemas import Operation, ProofState, ProofStep, QueryPlan
from jaw_ingest.verifier import Verifier


def _plan(*ops: Operation, final_var: str) -> QueryPlan:
    return QueryPlan(question="q", operations=list(ops), final_var=final_var)


def _step(**kwargs) -> ProofStep:
    kwargs.setdefault("params", {})
    return ProofStep(**kwargs)


def test_passes_when_evidence_bearing_chain_is_clean() -> None:
    plan = _plan(
        Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}),
        Operation(output_var="vals", op_type="GET_ATTRIBUTE", params={"input_var": "$p", "predicate": "contract_value"}),
        Operation(output_var="total", op_type="AGGREGATE", params={"input_var": "$vals", "function": "sum"}),
        final_var="total",
    )
    state = ProofState(
        trace=[
            _step(output_var="p", op_type="RESOLVE_ENTITY", status="ok"),
            _step(output_var="vals", op_type="GET_ATTRIBUTE", status="ok", evidence_ids=["e1"]),
            _step(output_var="total", op_type="AGGREGATE", status="ok", result_summary="sum -> 5"),
        ]
    )

    result = Verifier().verify(plan, state)

    assert result.passed
    assert result.issues == []
    assert set(result.checked) == {"p", "vals", "total"}


def test_fails_when_evidence_bearing_op_has_no_evidence() -> None:
    plan = _plan(
        Operation(output_var="vals", op_type="GET_ATTRIBUTE", params={"input_var": [], "predicate": "x"}),
        final_var="vals",
    )
    state = ProofState(trace=[_step(output_var="vals", op_type="GET_ATTRIBUTE", status="ok", evidence_ids=[])])

    result = Verifier().verify(plan, state)

    assert not result.passed
    assert any("no supporting evidence_id" in issue for issue in result.issues)


def test_fails_on_ambiguous_resolution_in_the_answer_chain() -> None:
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}), final_var="p")
    state = ProofState(trace=[_step(output_var="p", op_type="RESOLVE_ENTITY", status="ambiguous")])

    result = Verifier().verify(plan, state)

    assert not result.passed
    assert any("ambiguous" in issue for issue in result.issues)


def test_fails_on_failed_operation() -> None:
    plan = _plan(Operation(output_var="p", op_type="AGGREGATE", params={"input_var": [], "function": "sum"}), final_var="p")
    state = ProofState(trace=[_step(output_var="p", op_type="AGGREGATE", status="failed", note="division by zero")])

    result = Verifier().verify(plan, state)

    assert not result.passed
    assert any("division by zero" in issue for issue in result.issues)


def test_ignores_operations_not_in_the_final_answer_dependency_chain() -> None:
    # 'dead_end' failed but is never referenced by anything leading to 'total' -
    # the verifier should not fail the plan because of it.
    plan = _plan(
        Operation(output_var="dead_end", op_type="RESOLVE_ENTITY", params={"query": "unused"}),
        Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}),
        Operation(output_var="total", op_type="RETURN", params={"input_var": "$p"}),
        final_var="total",
    )
    state = ProofState(
        trace=[
            _step(output_var="dead_end", op_type="RESOLVE_ENTITY", status="failed", note="boom"),
            _step(output_var="p", op_type="RESOLVE_ENTITY", status="ok"),
            _step(output_var="total", op_type="RETURN", status="ok"),
        ]
    )

    result = Verifier().verify(plan, state)

    assert result.passed
    assert "dead_end" not in result.checked


def test_fails_when_final_answer_never_executed() -> None:
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}), final_var="missing")
    state = ProofState(trace=[_step(output_var="p", op_type="RESOLVE_ENTITY", status="ok")])

    result = Verifier().verify(plan, state)

    assert not result.passed
    assert any("never executed" in issue or "unbound" in issue for issue in result.issues)


def test_fails_when_unresolved_ambiguity_feeds_final_var() -> None:
    plan = _plan(Operation(output_var="p", op_type="RESOLVE_ENTITY", params={"query": "x"}), final_var="p")
    state = ProofState(
        trace=[_step(output_var="p", op_type="RESOLVE_ENTITY", status="ambiguous")],
        unresolved_ambiguity=[{"output_var": "p", "query": "x", "candidates": []}],
    )

    result = Verifier().verify(plan, state)

    assert not result.passed


def test_empty_plan_fails_cleanly() -> None:
    plan = QueryPlan(question="q", operations=[], final_var="")
    result = Verifier().verify(plan, ProofState())
    assert not result.passed
    assert result.issues
