from __future__ import annotations

from typing import Any

from .query_schemas import ProofState, ProofStep, QueryPlan, VerificationResult

EVIDENCE_BEARING_OPS = {"TRAVERSE", "SEARCH_EVIDENCE", "GET_ATTRIBUTE"}


def _extract_refs(value: Any) -> list[str]:
    refs: list[str] = []
    if isinstance(value, str) and value.startswith("$"):
        refs.append(value[1:])
    elif isinstance(value, list):
        for item in value:
            refs.extend(_extract_refs(item))
    elif isinstance(value, dict):
        for item in value.values():
            refs.extend(_extract_refs(item))
    return refs


def _ancestor_vars(plan: QueryPlan, final_var: str) -> set[str]:
    """Walks the plan's $var references backward from final_var to find every operation
    that actually contributes to the answer - the verifier only holds THOSE operations
    to account, rather than every operation the planner happened to emit.
    """
    var_to_op = {op.output_var: op for op in plan.operations}
    visited: set[str] = set()
    stack = [final_var]
    while stack:
        var = stack.pop()
        if var in visited:
            continue
        visited.add(var)
        op = var_to_op.get(var)
        if op is None:
            continue
        for ref in _extract_refs(op.params):
            if ref not in visited:
                stack.append(ref)
    return visited


class Verifier:
    """Deterministic, generic verification of a proof state against its plan. Contains
    no question-specific logic - it only inspects operation types, statuses, and
    evidence, walking the plan's own dependency graph to decide what's relevant.
    """

    def verify(self, plan: QueryPlan, proof_state: ProofState) -> VerificationResult:
        final_var = plan.final_var or (plan.operations[-1].output_var if plan.operations else "")
        if not final_var:
            return VerificationResult(passed=False, issues=["plan has no operations / no final_var"], checked=[])

        relevant_vars = _ancestor_vars(plan, final_var)
        trace_by_var: dict[str, ProofStep] = {step.output_var: step for step in proof_state.trace}

        issues: list[str] = []
        checked: list[str] = []

        for var in sorted(relevant_vars):
            step = trace_by_var.get(var)
            if step is None:
                issues.append(f"{var}: operation never executed")
                continue
            checked.append(var)

            if step.status == "failed":
                issues.append(f"{var} ({step.op_type}) failed: {step.note or 'no detail'}")
            elif step.status == "ambiguous":
                issues.append(f"{var} ({step.op_type}) resolved ambiguously among multiple candidates - not a confident match")
            elif step.status == "empty" and var != final_var:
                issues.append(f"{var} ({step.op_type}) produced no result, breaking the chain toward the answer")

            if step.op_type in EVIDENCE_BEARING_OPS and step.status == "ok" and not step.evidence_ids:
                issues.append(f"{var} ({step.op_type}) has a result with no supporting evidence_id")

            if step.op_type == "CHECK_COMPLETENESS" and step.status != "ok":
                issues.append(f"{var}: population completeness check did not pass - {step.note}")

        final_step = trace_by_var.get(final_var)
        if final_step is None or final_step.status == "empty":
            issues.append("final answer is empty or unbound")

        if proof_state.unresolved_ambiguity:
            for entry in proof_state.unresolved_ambiguity:
                if entry.get("output_var") in relevant_vars:
                    issues.append(f"unresolved ambiguity for '{entry.get('query')}' feeds into the final answer")

        return VerificationResult(passed=len(issues) == 0, issues=issues, checked=sorted(checked))
