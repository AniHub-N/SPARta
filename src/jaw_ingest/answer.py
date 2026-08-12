from __future__ import annotations

import logging

from .llm_provider import LLMProvider, ProviderNotConfigured, ProviderRequestError
from .mcp_tools import ToolDispatcher
from .query_schemas import EvidenceCitation, FinalAnswer, ProofState, QueryPlan, VerificationResult

logger = logging.getLogger(__name__)

PHRASING_SYSTEM_PROMPT = """\
You write one concise, plain-language sentence answering a question, using ONLY the \
answer value and evidence snippets you are given. Do not add numbers, names, or \
claims that are not present in what you were given - you are phrasing an already-\
computed, already-verified result, not computing or verifying anything yourself.
"""


def _deterministic_summary(plan: QueryPlan, proof_state: ProofState, verification: VerificationResult) -> str:
    lines = [f"Question: {plan.question}"]
    if plan.understanding:
        lines.append(f"Understood as: {plan.understanding}")
    lines.append(proof_state.explain())
    if verification.issues:
        lines.append("Verification issues: " + "; ".join(verification.issues))
    else:
        lines.append("Verification: all checks passed.")
    return "\n".join(lines)


def _collect_evidence(proof_state: ProofState, dispatcher: ToolDispatcher, limit: int = 10) -> list[EvidenceCitation]:
    citations = []
    for evidence_id in proof_state.evidence_used[:limit]:
        detail = dispatcher.call("evidence_text", evidence_id=evidence_id)
        if detail.get("found"):
            citations.append(
                EvidenceCitation(
                    evidence_id=evidence_id,
                    document_id=detail.get("document_id", ""),
                    location=detail.get("location", {}),
                    text=detail.get("text"),
                )
            )
    return citations


def synthesize_answer(
    plan: QueryPlan,
    proof_state: ProofState,
    verification: VerificationResult,
    dispatcher: ToolDispatcher,
    provider: LLMProvider | None = None,
) -> FinalAnswer:
    """Builds the final answer strictly from the verified proof state. The answer value
    and evidence citations always come from proof_state - never from the LLM. An LLM
    provider, if configured, may only rephrase the deterministic summary into a nicer
    sentence; on any failure it silently falls back to the deterministic summary.
    """
    final_var = plan.final_var or (plan.operations[-1].output_var if plan.operations else "")
    raw_value = proof_state.bindings.get(final_var)
    evidence = _collect_evidence(proof_state, dispatcher)

    if verification.passed and raw_value not in (None, [], {}):
        status = "verified"
        confidence = 1.0 if evidence or not proof_state.evidence_used else 0.85
    elif raw_value in (None, [], {}):
        status = "insufficient_evidence"
        confidence = 0.0
    else:
        status = "unverified"
        confidence = 0.4

    proof_summary = _deterministic_summary(plan, proof_state, verification)

    if provider is not None and status == "verified":
        try:
            phrased = provider.complete(
                system=PHRASING_SYSTEM_PROMPT,
                user=f"Question: {plan.question}\nComputed answer: {raw_value}\nEvidence: {[e.text for e in evidence]}\n\nReturn {{\"sentence\": str}}.",
                response_schema={"type": "object", "properties": {"sentence": {"type": "string"}}, "required": ["sentence"]},
            )
            sentence = phrased.get("sentence")
            if sentence:
                proof_summary = f"{sentence}\n\n{proof_summary}"
        except (ProviderNotConfigured, ProviderRequestError) as exc:
            logger.debug("Answer phrasing skipped (%s); using deterministic summary.", exc)

    return FinalAnswer(answer=raw_value, status=status, confidence=confidence, proof_summary=proof_summary, evidence=evidence)
