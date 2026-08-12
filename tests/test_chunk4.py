from __future__ import annotations

import json
from pathlib import Path

from jaw_ingest.chunk4 import (
    Chunk4Pipeline,
    DuckDBStore,
    GraphStore,
    LexicalRetriever,
    EmbeddingService,
    SemanticRetriever,
    EntityResolver,
    QdrantStore,
)
from jaw_ingest.evidence import Evidence, Fact, DocumentRecord


def _load_sample_corpus(tmp_path: Path) -> tuple[list[DocumentRecord], list[Evidence], list[Fact]]:
    document = DocumentRecord(
        document_id="sample",
        filename="sample.pdf",
        source_path=tmp_path / "sample.pdf",
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
    evidence = Evidence(
        evidence_id="e1",
        source_type="pdf",
        document_id="sample",
        source_path=tmp_path / "sample.pdf",
        filename="sample.pdf",
        extraction_method="native_text",
        content={
            "raw_value": "Contract Value: INR 33.38 Cr",
            "text": "Contract Value: INR 33.38 Cr",
            "metadata": {},
        },
        location={
            "source_type": "pdf",
            "page_number": 1,
            "block_id": "b1",
            "bbox": [0.0, 0.0, 1.0, 1.0],
        },
        metadata={},
    )
    fact = Fact(
        fact_id="f1",
        evidence_id="e1",
        document_id="sample",
        source_path=tmp_path / "sample.pdf",
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
            "evidence_id": "e1",
            "document_id": "sample",
            "source_path": str(tmp_path / "sample.pdf"),
            "location": {"source_type": "pdf", "page_number": 1, "block_id": "b1", "bbox": [0.0, 0.0, 1.0, 1.0]},
        },
        metadata={},
    )
    return [document], [evidence], [fact]


def test_duckdb_store_roundtrip(tmp_path: Path) -> None:
    documents, evidence, facts = _load_sample_corpus(tmp_path)
    store = DuckDBStore(tmp_path / "test.db")
    store.ingest_documents(documents)
    store.ingest_evidence(evidence)
    store.ingest_facts(facts)

    assert store.count("documents") == 1
    assert store.count("evidence") == 1
    assert store.count("facts") == 1
    result = store.query("SELECT * FROM documents WHERE document_id = ?", ("sample",))
    assert result[0]["filename"] == "sample.pdf"
    # ensure numeric normalized value was stored in typed column
    f = store.query("SELECT normalized_value_numeric FROM facts WHERE fact_id = ?", ("f1",))
    assert f
    assert float(f[0]["normalized_value_numeric"]) == 333800000.0
    # aggregation should succeed
    agg = store.query("SELECT SUM(normalized_value_numeric) AS total FROM facts")
    assert float(agg[0]["total"]) == 333800000.0


def test_graph_store_build_and_search(tmp_path: Path) -> None:
    documents, evidence, facts = _load_sample_corpus(tmp_path)
    from jaw_ingest.chunk4 import EvidenceCorpus

    corpus = EvidenceCorpus(documents, evidence, facts)
    graph = GraphStore()
    graph.build_from_corpus(corpus)

    assert graph.entity_count() > 0
    assert graph.relationship_count() > 0
    nodes = graph.search_nodes("contract value")
    assert nodes


def test_lexical_retriever_fuzzy_search(tmp_path: Path) -> None:
    from jaw_ingest.chunk4 import EvidenceCorpus

    documents, evidence, facts = _load_sample_corpus(tmp_path)
    corpus = EvidenceCorpus(documents, evidence, facts)
    retriever = LexicalRetriever()
    retriever.index_corpus(corpus)

    results = retriever.search_fuzzy("contract value inr", limit=5)
    assert results
    assert results[0]["score"] >= 50


def test_embedding_service_and_semantic_search(tmp_path: Path) -> None:
    from jaw_ingest.chunk4 import EvidenceCorpus

    documents, evidence, facts = _load_sample_corpus(tmp_path)
    corpus = EvidenceCorpus(documents, evidence, facts)
    embedding_service = EmbeddingService(device="cpu")
    semantic = SemanticRetriever(embedding_service)
    semantic.index_corpus(corpus)
    results = semantic.search_semantic("contract value amount", limit=3)

    assert isinstance(results, list)


def test_entity_resolver_status(tmp_path: Path) -> None:
    from jaw_ingest.chunk4 import EvidenceCorpus

    documents, evidence, facts = _load_sample_corpus(tmp_path)
    corpus = EvidenceCorpus(documents, evidence, facts)
    retriever = LexicalRetriever()
    retriever.index_corpus(corpus)
    embedding_service = EmbeddingService(device="cpu")
    semantic = SemanticRetriever(embedding_service)
    semantic.index_corpus(corpus)
    graph = GraphStore()
    graph.build_from_corpus(corpus)
    resolver = EntityResolver(retriever, semantic, graph)
    result = resolver.resolve("contract value")

    assert result.status in {"resolved", "ambiguous", "unresolved"}


def test_entity_resolver_unresolved_for_unknown_query(tmp_path: Path) -> None:
    from jaw_ingest.chunk4 import EvidenceCorpus

    documents, evidence, facts = _load_sample_corpus(tmp_path)
    corpus = EvidenceCorpus(documents, evidence, facts)
    retriever = LexicalRetriever()
    retriever.index_corpus(corpus)
    embedding_service = EmbeddingService(device="cpu")
    semantic = SemanticRetriever(embedding_service)
    semantic.index_corpus(corpus)
    graph = GraphStore()
    graph.build_from_corpus(corpus)
    resolver = EntityResolver(retriever, semantic, graph)

    result = resolver.resolve("nonexistent entity 12345")

    assert result.status == "unresolved"


def test_qdrant_store_upsert_and_count(tmp_path: Path) -> None:
    store = QdrantStore(collection_name="test_points", location=":memory:", vector_size=384)
    store.upsert_embeddings([("1", [0.0] * 384, {"foo": "bar"})])
    assert store.count() == 1


def test_chunk4_pipeline_integration(tmp_path: Path) -> None:
    from jaw_ingest.chunk4 import EvidenceCorpus

    documents, evidence, facts = _load_sample_corpus(tmp_path)
    evidence_root = tmp_path / "evidence"
    evidence_root.mkdir()
    for name, items in [("documents.jsonl", documents), ("evidence.jsonl", evidence), ("facts.jsonl", facts)]:
        with (evidence_root / name).open("w", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n")

    pipeline = Chunk4Pipeline(evidence_root=evidence_root, duckdb_path=tmp_path / "chunk4.db", qdrant_location=":memory:", device="cpu")
    pipeline.index()
    coverage = pipeline.get_coverage()
    assert coverage["documents"] == 1
    assert coverage["evidence"] == 1
    assert coverage["facts"] == 1
    assert coverage["duckdb_documents"] == 1
    assert coverage["duckdb_evidence"] == 1
    assert coverage["duckdb_facts"] == 1
    # Qdrant now covers both evidence AND fact texts (matching SemanticRetriever's full
    # scope), not evidence alone - 1 evidence + 1 fact = 2 points for this corpus.
    assert coverage["qdrant_points"] == 2
    assert coverage["graph_nodes"] > 0
    assert coverage["graph_edges"] > 0
