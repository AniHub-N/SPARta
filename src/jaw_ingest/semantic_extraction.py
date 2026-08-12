from __future__ import annotations

import logging

from pydantic import ValidationError

from .evidence import Evidence
from .llm_provider import LLMProvider, ProviderNotConfigured, ProviderRequestError
from .semantic_schemas import ExtractionFailure, SemanticExtractionResult

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You extract structured facts from a single fragment of evidence taken from a real-world "
    "document (a contract, certificate, report, or spreadsheet cell). You do not know the "
    "domain or schema in advance - entity types and relationship predicates are open-ended "
    "strings you choose based on what the text actually says. Only extract what this fragment "
    "of text directly supports; do not infer facts from outside knowledge. Return entities "
    "(named things mentioned - people, organizations, projects, documents, locations, etc.), "
    "relationships (entity-to-entity assertions, e.g. 'X commissioned_by Y'), and attributes "
    "(entity-to-literal-value assertions, e.g. 'Project X' -> contract_value -> '333800000'). "
    "If the fragment contains no extractable entities or relationships, return empty lists - "
    "do not fabricate content to fill the schema. When a document_type is given, treat it as "
    "context, not a fixed category: for example, a document whose type indicates it IS a "
    "particular kind of record (e.g. a reference letter, a completion certificate) is itself "
    "evidence that a corresponding relationship exists between the document's subject and that "
    "record type - extract that relationship using whatever entity_type/predicate names fit, "
    "so that later questions about which subjects do or don't have a record of a given type can "
    "be answered by checking for the presence or absence of that relationship."
)


def _build_user_prompt(evidence: Evidence, document_type: str | None = None) -> str:
    text = evidence.content.text or str(evidence.content.raw_value or "")
    lines = [
        f"document_id: {evidence.document_id}",
        f"evidence_id: {evidence.evidence_id}",
        f"source_type: {evidence.source_type}",
    ]
    if document_type:
        # The corpus's own classification of what kind of document this is (e.g.
        # "reference_letter", "completion_certificate") - not a fixed enum the
        # extractor hardcodes against, just context that helps interpret the fragment
        # (e.g. a "reference_letter" document existing at all is itself a fact worth
        # a relationship, for "which projects have no reference letter" questions).
        lines.append(f"document_type: {document_type}")
    lines.append(f"text:\n{text}")
    return "\n".join(lines)


BATCH_SYSTEM_PROMPT = (
    SYSTEM_PROMPT
    + " You are being given MULTIPLE tagged fragments from the same document in one call, "
    "each preceded by a short reference tag like [E3]. Every entity, relationship, and "
    "attribute you return MUST set source_ref to the exact tag (e.g. \"E3\", not \"[E3]\") "
    "of the single fragment that supports it - never leave source_ref empty, and never "
    "combine facts from two different fragments into one assertion. Fragments are "
    "independent; do not infer a relationship between two fragments just because they "
    "are in the same document unless the text of one fragment actually states it."
)


def _build_batch_user_prompt(document_id: str, evidence_items: list[Evidence], document_type: str | None = None) -> tuple[str, dict[str, str]]:
    ref_map: dict[str, str] = {}
    lines = [f"document_id: {document_id}"]
    if document_type:
        lines.append(f"document_type: {document_type}")
    lines.append("fragments:")
    for index, evidence in enumerate(evidence_items, start=1):
        ref = f"E{index}"
        ref_map[ref] = evidence.evidence_id
        text = evidence.content.text or str(evidence.content.raw_value or "")
        lines.append(f"[{ref}] {text}")
    return "\n".join(lines), ref_map


class SemanticExtractor:
    """Transforms raw Evidence into candidate entity/relationship/attribute assertions via an LLMProvider.

    The extractor never mutates the database directly - it only returns validated
    Pydantic objects (or an explicit ExtractionFailure) for a downstream builder to consume.
    """

    def __init__(self, provider: LLMProvider) -> None:
        self.provider = provider

    def extract(self, evidence: Evidence, document_type: str | None = None) -> SemanticExtractionResult | ExtractionFailure:
        try:
            raw = self.provider.complete(
                system=SYSTEM_PROMPT,
                user=_build_user_prompt(evidence, document_type),
                response_schema=SemanticExtractionResult.json_schema(),
            )
        except ProviderNotConfigured as exc:
            return ExtractionFailure(reason="no_provider_configured", detail=str(exc))
        except ProviderRequestError as exc:
            logger.warning("Semantic extraction request failed for evidence %s: %s", evidence.evidence_id, exc)
            return ExtractionFailure(reason="provider_request_failed", detail=str(exc))

        try:
            return SemanticExtractionResult.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Semantic extraction output failed validation for evidence %s: %s", evidence.evidence_id, exc)
            return ExtractionFailure(reason="invalid_extraction_output", detail=str(exc), raw_output=str(raw))

    def extract_batch(
        self,
        document_id: str,
        evidence_items: list[Evidence],
        document_type: str | None = None,
    ) -> tuple[SemanticExtractionResult, dict[str, str]] | ExtractionFailure:
        """Extracts entities/relationships/attributes for MANY evidence fragments from
        the same document in a single LLM call, instead of one call per fragment. This
        is the difference between a full 687-document corpus costing on the order of
        tens of thousands of calls (one per fragment) versus roughly one call per
        document (or a handful, for very large documents chunked by the caller).

        Returns (result, ref_map) on success, where ref_map maps each fragment's local
        tag ("E1", "E2", ...) back to its real evidence_id, so callers can resolve each
        assertion's source_ref into real provenance.
        """
        user_prompt, ref_map = _build_batch_user_prompt(document_id, evidence_items, document_type)
        try:
            raw = self.provider.complete(
                system=BATCH_SYSTEM_PROMPT,
                user=user_prompt,
                response_schema=SemanticExtractionResult.json_schema(),
            )
        except ProviderNotConfigured as exc:
            return ExtractionFailure(reason="no_provider_configured", detail=str(exc))
        except ProviderRequestError as exc:
            logger.warning("Batched semantic extraction request failed for document %s: %s", document_id, exc)
            return ExtractionFailure(reason="provider_request_failed", detail=str(exc))

        try:
            result = SemanticExtractionResult.model_validate(raw)
        except ValidationError as exc:
            logger.warning("Batched semantic extraction output failed validation for document %s: %s", document_id, exc)
            return ExtractionFailure(reason="invalid_extraction_output", detail=str(exc), raw_output=str(raw))

        return result, ref_map
