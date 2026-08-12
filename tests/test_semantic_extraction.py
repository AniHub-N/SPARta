from __future__ import annotations

from pathlib import Path

from jaw_ingest.evidence import Evidence, EvidenceContent, PDFEvidenceLocation
from jaw_ingest.llm_provider import NullProvider, ProviderRequestError
from jaw_ingest.semantic_extraction import SemanticExtractor
from jaw_ingest.semantic_schemas import ExtractionFailure, SemanticExtractionResult


def _sample_evidence(text: str = "Contract Value: INR 33.38 Cr") -> Evidence:
    return Evidence(
        evidence_id="e1",
        source_type="pdf",
        document_id="doc1",
        source_path=Path("doc1.pdf"),
        filename="doc1.pdf",
        extraction_method="native_text",
        content=EvidenceContent(raw_value=text, text=text),
        location=PDFEvidenceLocation(source_type="pdf", page_number=1, block_id="b1", bbox=[0, 0, 1, 1]),
    )


class _FakeProvider:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        self.calls += 1
        return self.payload


class _RaisingProvider:
    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        raise ProviderRequestError("boom")


def test_extract_returns_no_provider_configured_failure_without_calling_null_provider() -> None:
    extractor = SemanticExtractor(NullProvider())
    result = extractor.extract(_sample_evidence())
    assert isinstance(result, ExtractionFailure)
    assert result.reason == "no_provider_configured"


def test_extract_valid_payload_returns_semantic_extraction_result() -> None:
    payload = {
        "entities": [{"mention_text": "Sunita Joshi", "entity_type": "person", "confidence": 0.9}],
        "relationships": [
            {
                "subject_mention_text": "Sunita Joshi",
                "predicate": "led",
                "object_mention_text": "Ring Road Pkg-107",
                "confidence": 0.8,
            }
        ],
        "attributes": [],
    }
    provider = _FakeProvider(payload)
    extractor = SemanticExtractor(provider)

    result = extractor.extract(_sample_evidence())

    assert isinstance(result, SemanticExtractionResult)
    assert provider.calls == 1
    assert len(result.entities) == 1
    assert result.entities[0].mention_text == "Sunita Joshi"
    assert result.relationships[0].predicate == "led"


def test_extract_malformed_payload_returns_explicit_failure_not_a_raise() -> None:
    provider = _FakeProvider({"entities": "not-a-list"})
    extractor = SemanticExtractor(provider)

    result = extractor.extract(_sample_evidence())

    assert isinstance(result, ExtractionFailure)
    assert result.reason == "invalid_extraction_output"
    assert result.raw_output is not None


def test_extract_provider_request_error_returns_explicit_failure() -> None:
    extractor = SemanticExtractor(_RaisingProvider())
    result = extractor.extract(_sample_evidence())
    assert isinstance(result, ExtractionFailure)
    assert result.reason == "provider_request_failed"


def test_extract_empty_lists_are_valid_not_forced_fabrication() -> None:
    provider = _FakeProvider({"entities": [], "relationships": [], "attributes": []})
    extractor = SemanticExtractor(provider)

    result = extractor.extract(_sample_evidence(text="Page footer text with no entities."))

    assert isinstance(result, SemanticExtractionResult)
    assert result.entities == []
    assert result.relationships == []
    assert result.attributes == []


def _fragment(evidence_id: str, text: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type="pdf",
        document_id="doc1",
        source_path=Path("doc1.pdf"),
        filename="doc1.pdf",
        extraction_method="native_text",
        content=EvidenceContent(raw_value=text, text=text),
        location=PDFEvidenceLocation(source_type="pdf", page_number=1, block_id=evidence_id, bbox=[0, 0, 1, 1]),
    )


def test_extract_batch_one_call_for_many_fragments_and_resolves_source_ref() -> None:
    fragments = [
        _fragment("ev-a", "Asha Nair led Bridge Alpha."),
        _fragment("ev-b", "Bridge Alpha was commissioned by Metro Authority."),
        _fragment("ev-c", "Page footer, nothing here."),
    ]
    payload = {
        "entities": [
            {"mention_text": "Asha Nair", "entity_type": "person", "confidence": 0.9, "source_ref": "E1"},
            {"mention_text": "Bridge Alpha", "entity_type": "project", "confidence": 0.9, "source_ref": "E1"},
            {"mention_text": "Metro Authority", "entity_type": "client", "confidence": 0.9, "source_ref": "E2"},
        ],
        "relationships": [
            {"subject_mention_text": "Asha Nair", "predicate": "led", "object_mention_text": "Bridge Alpha", "confidence": 0.9, "source_ref": "E1"}
        ],
        "attributes": [],
    }
    provider = _FakeProvider(payload)
    extractor = SemanticExtractor(provider)

    outcome = extractor.extract_batch("doc1", fragments)

    assert provider.calls == 1  # one call covered all 3 fragments
    assert not isinstance(outcome, ExtractionFailure)
    result, ref_map = outcome
    assert ref_map == {"E1": "ev-a", "E2": "ev-b", "E3": "ev-c"}
    assert len(result.entities) == 3
    assert result.entities[0].source_ref == "E1"
    assert result.entities[2].source_ref == "E2"


def test_extract_batch_no_provider_returns_explicit_failure() -> None:
    outcome = SemanticExtractor(NullProvider()).extract_batch("doc1", [_fragment("ev-a", "text")])
    assert isinstance(outcome, ExtractionFailure)
    assert outcome.reason == "no_provider_configured"


def test_extract_batch_malformed_payload_returns_explicit_failure() -> None:
    provider = _FakeProvider({"entities": "not-a-list"})
    outcome = SemanticExtractor(provider).extract_batch("doc1", [_fragment("ev-a", "text")])
    assert isinstance(outcome, ExtractionFailure)
    assert outcome.reason == "invalid_extraction_output"


def test_document_type_appears_in_single_and_batch_prompts() -> None:
    captured_users = []

    class _CapturingProvider:
        def complete(self, system, user, response_schema):
            captured_users.append(user)
            return {"entities": [], "relationships": [], "attributes": []}

    extractor = SemanticExtractor(_CapturingProvider())
    extractor.extract(_sample_evidence(), document_type="reference_letter")
    extractor.extract_batch("doc1", [_fragment("ev-a", "text")], document_type="reference_letter")

    assert all("reference_letter" in user for user in captured_users)
