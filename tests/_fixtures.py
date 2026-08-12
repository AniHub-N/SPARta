"""Shared, non-collected test helpers (filename deliberately doesn't match test_*.py)."""
from __future__ import annotations

import json
import re
from pathlib import Path

from jaw_ingest.evidence import DocumentRecord, Evidence, EvidenceContent, Fact, FactProvenance, PDFEvidenceLocation
from jaw_ingest.system import build_system

# Deliberately different from the LLM-extracted "contract_value" attribute for the same
# evidence (100000000) - lets tests prove the deterministic Fact wins over the
# LLM-transcribed Attribute when both exist for the same entity/predicate.
ALPHA_DETERMINISTIC_CONTRACT_VALUE = 150000000.0

# A small synthetic scenario: a person led a project, two projects were commissioned by
# the same client (with a matching contract value each), and a third project was
# commissioned by a different client - enough to exercise multi-hop traversal,
# population enumeration, filtering by client, and aggregation while making sure a
# wrong-client project would be wrongly included if the join were done incorrectly.
_SCENARIO_TEXTS = [
    ("PKG-ALPHA", "Asha Nair led the project Bridge Alpha."),
    ("PKG-ALPHA", "Bridge Alpha was commissioned by Metro Authority."),
    ("PKG-ALPHA", "Bridge Alpha contract value is INR 100000000."),
    ("PKG-BETA", "Bridge Beta was commissioned by Metro Authority."),
    ("PKG-BETA", "Bridge Beta contract value is INR 200000000."),
    ("PKG-GAMMA", "Bridge Gamma was commissioned by Other Authority."),
    ("PKG-GAMMA", "Bridge Gamma contract value is INR 999999999."),
]


def _match_fragment_text(text: str) -> dict | None:
    """The canned extraction result for one fragment of _SCENARIO_TEXTS, matched by
    substring, WITHOUT source_ref (callers add that - single-item callers leave it
    default, batch callers tag it with the fragment's own [E<n>] reference).
    Shared by both ScenarioExtractionProvider (single-item prompts) and
    ScenarioBatchExtractionProvider (batched [E1]/[E2]-tagged prompts).
    """
    if "Asha Nair led the project Bridge Alpha" in text:
        return {
            "entities": [
                {"mention_text": "Asha Nair", "entity_type": "person", "confidence": 0.9},
                {"mention_text": "Bridge Alpha", "entity_type": "project", "confidence": 0.9},
            ],
            "relationships": [{"subject_mention_text": "Asha Nair", "predicate": "led", "object_mention_text": "Bridge Alpha", "confidence": 0.9}],
            "attributes": [],
        }
    if "Bridge Alpha was commissioned by Metro Authority" in text:
        return {
            "entities": [
                {"mention_text": "Bridge Alpha", "entity_type": "project", "confidence": 0.9},
                {"mention_text": "Metro Authority", "entity_type": "client", "confidence": 0.9},
            ],
            "relationships": [{"subject_mention_text": "Bridge Alpha", "predicate": "commissioned_by", "object_mention_text": "Metro Authority", "confidence": 0.9}],
            "attributes": [],
        }
    if "Bridge Alpha contract value" in text:
        return {
            "entities": [{"mention_text": "Bridge Alpha", "entity_type": "project", "confidence": 0.85}],
            "relationships": [],
            "attributes": [{"subject_mention_text": "Bridge Alpha", "predicate": "contract_value", "value": "100000000", "value_type": "currency", "confidence": 0.9}],
        }
    if "Bridge Beta was commissioned by Metro Authority" in text:
        return {
            "entities": [
                {"mention_text": "Bridge Beta", "entity_type": "project", "confidence": 0.9},
                {"mention_text": "Metro Authority", "entity_type": "client", "confidence": 0.9},
            ],
            "relationships": [{"subject_mention_text": "Bridge Beta", "predicate": "commissioned_by", "object_mention_text": "Metro Authority", "confidence": 0.9}],
            "attributes": [],
        }
    if "Bridge Beta contract value" in text:
        return {
            "entities": [{"mention_text": "Bridge Beta", "entity_type": "project", "confidence": 0.85}],
            "relationships": [],
            "attributes": [{"subject_mention_text": "Bridge Beta", "predicate": "contract_value", "value": "200000000", "value_type": "currency", "confidence": 0.9}],
        }
    if "Bridge Gamma was commissioned by Other Authority" in text:
        return {
            "entities": [
                {"mention_text": "Bridge Gamma", "entity_type": "project", "confidence": 0.9},
                {"mention_text": "Other Authority", "entity_type": "client", "confidence": 0.9},
            ],
            "relationships": [{"subject_mention_text": "Bridge Gamma", "predicate": "commissioned_by", "object_mention_text": "Other Authority", "confidence": 0.9}],
            "attributes": [],
        }
    if "Bridge Gamma contract value" in text:
        return {
            "entities": [{"mention_text": "Bridge Gamma", "entity_type": "project", "confidence": 0.85}],
            "relationships": [],
            "attributes": [{"subject_mention_text": "Bridge Gamma", "predicate": "contract_value", "value": "999999999", "value_type": "currency", "confidence": 0.9}],
        }
    return None


class ScenarioExtractionProvider:
    """Canned semantic-extraction responses for the OLD single-item prompt format
    (semantic_extraction.py's SYSTEM_PROMPT/_build_user_prompt - one evidence fragment
    per call). Returns only the first matching fragment's result, which is correct for
    single-item calls but WRONG if handed a batched multi-fragment prompt - use
    ScenarioBatchExtractionProvider for anything going through ensure_extracted/DISCOVER,
    which always batches (even a single-fragment "batch" uses the tagged format).
    """

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        return _match_fragment_text(user) or {"entities": [], "relationships": [], "attributes": []}


class ScenarioBatchExtractionProvider:
    """Batch-aware counterpart: parses every "[E<n>] <fragment text>" line out of a
    batched prompt (semantic_extraction.py's BATCH_SYSTEM_PROMPT/_build_batch_user_prompt)
    and returns a combined result covering EVERY matching fragment in the batch, each
    correctly tagged with its own source_ref - not just the first match. This is what
    ensure_extracted/DISCOVER actually send, regardless of how many fragments a
    document has, so this is the provider lazy-mode tests should use.
    """

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        entities: list[dict] = []
        relationships: list[dict] = []
        attributes: list[dict] = []
        for line in user.splitlines():
            match = re.match(r"\[(E\d+)\]\s(.*)", line)
            if not match:
                continue
            ref, fragment_text = match.group(1), match.group(2)
            result = _match_fragment_text(fragment_text)
            if result is None:
                continue
            for entity in result["entities"]:
                entities.append({**entity, "source_ref": ref})
            for relationship in result["relationships"]:
                relationships.append({**relationship, "source_ref": ref})
            for attribute in result["attributes"]:
                attributes.append({**attribute, "source_ref": ref})
        return {"entities": entities, "relationships": relationships, "attributes": attributes}


def write_scenario_evidence_root(tmp_path: Path) -> Path:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()

    documents: dict[str, DocumentRecord] = {}
    evidence_items: list[Evidence] = []
    for index, (doc_id, text) in enumerate(_SCENARIO_TEXTS):
        if doc_id not in documents:
            documents[doc_id] = DocumentRecord(
                document_id=doc_id,
                filename=f"{doc_id}.pdf",
                source_path=tmp_path / f"{doc_id}.pdf",
                document_type="pdf",
                extension=".pdf",
                size_bytes=0,
                checksum=f"chk-{doc_id}",
                page_count=1,
                sheet_count=None,
                extraction_status="success",
                extraction_version="1.0",
                evidence_schema_version="1.0",
                metadata={},
            )
        evidence_items.append(
            Evidence(
                evidence_id=f"{doc_id}-e{index}",
                source_type="pdf",
                document_id=doc_id,
                source_path=tmp_path / f"{doc_id}.pdf",
                filename=f"{doc_id}.pdf",
                extraction_method="native_text",
                content=EvidenceContent(raw_value=text, text=text),
                location=PDFEvidenceLocation(source_type="pdf", page_number=1, block_id=f"b{index}", bbox=[0.0, 0.0, 1.0, 1.0]),
            )
        )

    with (evidence_root / "documents.jsonl").open("w", encoding="utf-8") as handle:
        for document in documents.values():
            handle.write(json.dumps(document.model_dump(mode="json"), ensure_ascii=False) + "\n")
    with (evidence_root / "evidence.jsonl").open("w", encoding="utf-8") as handle:
        for evidence in evidence_items:
            handle.write(json.dumps(evidence.model_dump(mode="json"), ensure_ascii=False) + "\n")
    alpha_contract_evidence_id = "PKG-ALPHA-e2"  # "Bridge Alpha contract value is INR 100000000." (index 2)
    fact = Fact(
        fact_id="fact-alpha-contract-value",
        evidence_id=alpha_contract_evidence_id,
        document_id="PKG-ALPHA",
        source_path=tmp_path / "PKG-ALPHA.pdf",
        subject_mention=None,
        predicate="contract_value",
        raw_value="INR 15.00 Cr",
        normalized_value=str(ALPHA_DETERMINISTIC_CONTRACT_VALUE),
        normalized_type="currency_inr",
        normalized_unit="INR",
        original_unit="INR",
        extraction_method="native_text",
        normalization_method="infer_currency_inr",
        extraction_confidence=None,
        normalization_confidence=1.0,
        validation_status="valid",
        provenance=FactProvenance(
            evidence_id=alpha_contract_evidence_id,
            document_id="PKG-ALPHA",
            source_path=tmp_path / "PKG-ALPHA.pdf",
            location={"source_type": "pdf", "page_number": 1, "block_id": "b2", "bbox": [0.0, 0.0, 1.0, 1.0]},
        ),
        metadata={},
    )
    with (evidence_root / "facts.jsonl").open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(fact.model_dump(mode="json"), ensure_ascii=False) + "\n")

    return evidence_root


def build_scenario_system(tmp_path: Path, provider=None, lazy: bool = False):
    evidence_root = write_scenario_evidence_root(tmp_path)
    return build_system(
        evidence_root=evidence_root,
        provider=provider or ScenarioExtractionProvider(),
        duckdb_path=":memory:",
        qdrant_location=":memory:",
        device="cpu",
        lazy=lazy,
    )
