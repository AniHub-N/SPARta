from __future__ import annotations

import json
from pathlib import Path

from jaw_ingest.chunk4 import Chunk4Pipeline
from jaw_ingest.evidence import DocumentRecord, Evidence, Fact
from jaw_ingest.retrieval_index import compute_corpus_fingerprint


def _document(document_id: str, tmp_path: Path) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        filename=f"{document_id}.pdf",
        source_path=tmp_path / f"{document_id}.pdf",
        document_type="pdf",
        extension=".pdf",
        size_bytes=0,
        checksum="abc",
        page_count=1,
        sheet_count=None,
        extraction_status="success",
        extraction_version="1.0",
        evidence_schema_version="1.0",
        metadata={},
    )


def _evidence(document_id: str, evidence_id: str, text: str, tmp_path: Path) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type="pdf",
        document_id=document_id,
        source_path=tmp_path / f"{document_id}.pdf",
        filename=f"{document_id}.pdf",
        extraction_method="native_text",
        content={"raw_value": text, "text": text, "metadata": {}},
        location={"source_type": "pdf", "page_number": 1, "block_id": "b1", "bbox": [0.0, 0.0, 1.0, 1.0]},
        metadata={},
    )


def _fact(document_id: str, evidence_id: str, fact_id: str, tmp_path: Path) -> Fact:
    return Fact(
        fact_id=fact_id,
        evidence_id=evidence_id,
        document_id=document_id,
        source_path=tmp_path / f"{document_id}.pdf",
        subject_mention=None,
        predicate="contract_value",
        raw_value="INR 33.38 Cr",
        normalized_value="333800000",
        normalized_type="currency_inr",
        normalized_unit="INR",
        original_unit="INR",
        extraction_method="native_text",
        normalization_method="infer_currency_inr",
        extraction_confidence=None,
        normalization_confidence=1.0,
        validation_status="valid",
        provenance={
            "evidence_id": evidence_id,
            "document_id": document_id,
            "source_path": str(tmp_path / f"{document_id}.pdf"),
            "location": {"source_type": "pdf", "page_number": 1, "block_id": "b1", "bbox": [0.0, 0.0, 1.0, 1.0]},
        },
        metadata={},
    )


def _write_corpus(evidence_root: Path, tmp_path: Path, document_ids: list[str]) -> None:
    documents = [_document(doc_id, tmp_path) for doc_id in document_ids]
    evidence = [_evidence(doc_id, f"e_{doc_id}", f"Contract Value: INR 33.38 Cr for {doc_id}", tmp_path) for doc_id in document_ids]
    facts = [_fact(doc_id, f"e_{doc_id}", f"f_{doc_id}", tmp_path) for doc_id in document_ids]
    for name, items in [("documents.jsonl", documents), ("evidence.jsonl", evidence), ("facts.jsonl", facts)]:
        with (evidence_root / name).open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")


def _count_embed_calls(pipeline: Chunk4Pipeline, monkeypatch) -> list[int]:
    counter = [0]
    original = pipeline.embedding_service.embed_texts

    def _counting_embed_texts(texts):
        counter[0] += 1
        return original(texts)

    monkeypatch.setattr(pipeline.embedding_service, "embed_texts", _counting_embed_texts)
    return counter


def test_compute_corpus_fingerprint_deterministic_and_sensitive_to_content(tmp_path: Path) -> None:
    root_a = tmp_path / "a"
    root_a.mkdir()
    _write_corpus(root_a, tmp_path, ["doc1"])

    fingerprint_1 = compute_corpus_fingerprint(root_a)
    fingerprint_2 = compute_corpus_fingerprint(root_a)
    assert fingerprint_1 == fingerprint_2

    _write_corpus(root_a, tmp_path, ["doc1", "doc2"])
    fingerprint_3 = compute_corpus_fingerprint(root_a)
    assert fingerprint_3 != fingerprint_1


def test_second_pipeline_reuses_persisted_index_without_reembedding(tmp_path: Path, monkeypatch) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    _write_corpus(evidence_root, tmp_path, ["doc1"])

    duckdb_path = tmp_path / "index.duckdb"
    qdrant_location = tmp_path / "qdrant"

    pipeline1 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    calls_1 = _count_embed_calls(pipeline1, monkeypatch)
    pipeline1.index()
    assert calls_1[0] == 1  # fresh build embeds exactly once
    coverage_1 = pipeline1.get_coverage()
    pipeline1.close()

    pipeline2 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    calls_2 = _count_embed_calls(pipeline2, monkeypatch)
    pipeline2.index()
    assert calls_2[0] == 0  # reused - no re-embedding at all
    coverage_2 = pipeline2.get_coverage()
    pipeline2.close()

    assert coverage_2["qdrant_points"] == coverage_1["qdrant_points"]
    assert len(pipeline2.semantic_retriever.items) == len(pipeline1.semantic_retriever.items)


def test_corpus_change_invalidates_persisted_index(tmp_path: Path, monkeypatch) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    _write_corpus(evidence_root, tmp_path, ["doc1"])

    duckdb_path = tmp_path / "index.duckdb"
    qdrant_location = tmp_path / "qdrant"

    pipeline1 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    pipeline1.index()
    pipeline1.close()

    _write_corpus(evidence_root, tmp_path, ["doc1", "doc2"])  # corpus now has an extra document

    pipeline2 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    calls_2 = _count_embed_calls(pipeline2, monkeypatch)
    pipeline2.index()
    assert calls_2[0] == 1  # fingerprint mismatch -> rebuilt, not silently stale
    coverage_2 = pipeline2.get_coverage()
    assert coverage_2["documents"] == 2
    assert coverage_2["duckdb_documents"] == 2
    pipeline2.close()


def test_force_reindex_always_rebuilds(tmp_path: Path, monkeypatch) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    _write_corpus(evidence_root, tmp_path, ["doc1"])

    duckdb_path = tmp_path / "index.duckdb"
    qdrant_location = tmp_path / "qdrant"

    pipeline1 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    pipeline1.index()
    pipeline1.close()

    pipeline2 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=duckdb_path, qdrant_location=str(qdrant_location), device="cpu")
    calls_2 = _count_embed_calls(pipeline2, monkeypatch)
    pipeline2.index(force_reindex=True)
    assert calls_2[0] == 1  # forced - rebuilt even though corpus/model match
    pipeline2.close()


def test_in_memory_mode_never_reuses_across_pipelines(tmp_path: Path, monkeypatch) -> None:
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    _write_corpus(evidence_root, tmp_path, ["doc1"])

    pipeline1 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=":memory:", qdrant_location=":memory:", device="cpu")
    calls_1 = _count_embed_calls(pipeline1, monkeypatch)
    pipeline1.index()
    assert calls_1[0] == 1

    pipeline2 = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=":memory:", qdrant_location=":memory:", device="cpu")
    calls_2 = _count_embed_calls(pipeline2, monkeypatch)
    pipeline2.index()
    assert calls_2[0] == 1  # independent in-memory stores - never reused, matches old ephemeral behavior
