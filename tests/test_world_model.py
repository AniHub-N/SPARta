from __future__ import annotations

from pathlib import Path

from jaw_ingest.chunk4 import EvidenceCorpus, GraphStore
from jaw_ingest.duckdb_store import DuckDBStore
from jaw_ingest.entity_resolution import EntityResolver
from jaw_ingest.evidence import DocumentRecord, Evidence, EvidenceContent, PDFEvidenceLocation
from jaw_ingest.semantic_extraction import SemanticExtractor
from jaw_ingest.world_model import WorldModelBuilder

EVIDENCE_ROOT = Path(__file__).resolve().parent.parent / "data" / "evidence"


class _CorpusFakeProvider:
    """Seeded with realistic canned extractions for the real JAW sample corpus
    (test/DOC-CC-120.pdf, test/DOC-GLB-2019.pdf, test/BOQ_and_Measurements_Contract_79.xlsx),
    keyed by substrings actually present in that corpus's evidence text.
    """

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        if "awarded" in user and "National" in user and "Infrastructure" in user:
            return {
                "entities": [
                    {"mention_text": "Highway Tunnel - West Bengal Pkg-120", "entity_type": "project", "confidence": 0.9},
                    {"mention_text": "National Infrastructure Corp. Ltd.", "entity_type": "organization", "confidence": 0.95},
                ],
                "relationships": [
                    {
                        "subject_mention_text": "Highway Tunnel - West Bengal Pkg-120",
                        "predicate": "awarded_to",
                        "object_mention_text": "National Infrastructure Corp. Ltd.",
                        "confidence": 0.9,
                    }
                ],
                "attributes": [],
            }
        if "supervised" in user and "Rahul Menon" in user:
            return {
                "entities": [
                    {"mention_text": "Rahul Menon", "entity_type": "person", "confidence": 0.9},
                    {"mention_text": "National Infrastructure Corp. Ltd.", "entity_type": "organization", "confidence": 0.8},
                ],
                "relationships": [
                    {
                        "subject_mention_text": "National Infrastructure Corp. Ltd.",
                        "predicate": "represented_on_site_by",
                        "object_mention_text": "Rahul Menon",
                        "confidence": 0.85,
                    }
                ],
                "attributes": [],
            }
        if "Irrigation & Waterways Dept" in user:
            return {
                "entities": [
                    {"mention_text": "Irrigation & Waterways Dept, Govt of West Bengal", "entity_type": "organization", "confidence": 0.85},
                ],
                "relationships": [],
                "attributes": [],
            }
        if "Authorised Signatory, National Infrastructure Corp" in user:
            return {
                "entities": [
                    {"mention_text": "National Infrastructure Corp. Ltd.", "entity_type": "organization", "confidence": 0.7},
                ],
                "relationships": [],
                "attributes": [],
            }
        return {"entities": [], "relationships": [], "attributes": []}


def _build_world() -> tuple[WorldModelBuilder, EvidenceCorpus]:
    corpus = EvidenceCorpus.from_evidence_root(EVIDENCE_ROOT)
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    extractor = SemanticExtractor(_CorpusFakeProvider())
    builder = WorldModelBuilder(extractor, resolver)
    builder.process_evidence(corpus.evidence)
    return builder, corpus


def test_evidence_root_has_real_corpus_data() -> None:
    corpus = EvidenceCorpus.from_evidence_root(EVIDENCE_ROOT)
    assert len(corpus.documents) == 3
    assert len(corpus.evidence) > 0


def test_world_model_builds_entities_and_relationships() -> None:
    builder, _ = _build_world()

    assert len(builder.canonical_entities) > 0
    assert len(builder.relationships) > 0
    assert builder.report.mentions_created > 0


def test_repeated_organization_mentions_resolve_to_one_canonical_entity() -> None:
    # "National Infrastructure Corp. Ltd." is mentioned across three separate evidence
    # fragments (DOC-CC-120 twice, DOC-GLB-2019 once) - they must resolve to one entity.
    builder, _ = _build_world()

    org_entities = [e for e in builder.canonical_entities if e.entity_type == "organization" and "National Infrastructure" in e.canonical_name]
    assert len(org_entities) == 1
    org = org_entities[0]
    assert len(org.mention_ids) >= 3


def test_provenance_chain_document_to_duckdb_row() -> None:
    builder, corpus = _build_world()
    graph_store = GraphStore()
    duckdb_store = DuckDBStore(":memory:")
    builder.persist(graph_store, duckdb_store)

    relationship = builder.relationships[0]

    # Evidence -> document
    evidence = corpus.evidence_by_id[relationship.evidence_id]
    assert evidence.document_id == relationship.document_id
    document = corpus.documents_by_id[evidence.document_id]
    assert document.document_id == relationship.document_id

    # Mention -> canonical entity, both ends of the relationship
    subject = next(e for e in builder.canonical_entities if e.entity_id == relationship.subject_entity_id)
    object_entity = next(e for e in builder.canonical_entities if e.entity_id == relationship.object_entity_id)
    subject_mention = next(m for m in builder.mentions if m.mention_id in subject.mention_ids)
    assert subject_mention.evidence_id in {relationship.evidence_id, *[m.evidence_id for m in builder.mentions if m.mention_id in subject.mention_ids]}

    # Graph edge carries the same evidence_id
    assert graph_store.graph.has_edge(relationship.subject_entity_id, relationship.object_entity_id)
    edge_data = graph_store.graph.get_edge_data(relationship.subject_entity_id, relationship.object_entity_id, key=relationship.relationship_id)
    assert edge_data["evidence_id"] == relationship.evidence_id

    # DuckDB row carries the same evidence_id
    rows = duckdb_store.query("SELECT * FROM relationships WHERE relationship_id = ?", (relationship.relationship_id,))
    assert len(rows) == 1
    assert rows[0]["evidence_id"] == relationship.evidence_id
    assert rows[0]["subject_entity_id"] == relationship.subject_entity_id
    assert rows[0]["object_entity_id"] == relationship.object_entity_id

    print(
        "\nProvenance chain: "
        f"document={document.document_id} -> evidence={evidence.evidence_id} -> "
        f"mention={subject_mention.mention_id} -> entity={subject.entity_id} ({subject.canonical_name}) -> "
        f"relationship={relationship.relationship_id} ({relationship.predicate}) -> "
        f"graph_edge(evidence_id={edge_data['evidence_id']}) -> duckdb_row(evidence_id={rows[0]['evidence_id']})"
    )


def test_generic_multi_hop_traversal_project_to_person() -> None:
    # Project --awarded_to--> Organization <--supervised_for-- Person
    # A generic, un-hardcoded traversal: find_path treats predicates as opaque data.
    builder, _ = _build_world()
    graph_store = GraphStore()
    duckdb_store = DuckDBStore(":memory:")
    builder.persist(graph_store, duckdb_store)

    project = next(e for e in builder.canonical_entities if e.entity_type == "project")
    person = next(e for e in builder.canonical_entities if e.entity_type == "person")

    path = graph_store.find_path(project.entity_id, person.entity_id)

    assert path is not None
    assert path[0] == project.entity_id
    assert path[-1] == person.entity_id
    assert len(path) >= 2


def test_idempotent_rerun_produces_identical_entity_and_relationship_counts() -> None:
    builder1, corpus = _build_world()
    coverage1 = builder1.coverage()

    resolver2 = EntityResolver(lexical=None, semantic=None, graph=None)
    extractor2 = SemanticExtractor(_CorpusFakeProvider())
    builder2 = WorldModelBuilder(extractor2, resolver2)
    builder2.process_evidence(corpus.evidence)
    coverage2 = builder2.coverage()

    assert coverage1["canonical_entities"] == coverage2["canonical_entities"]
    assert coverage1["relationships_created"] == coverage2["relationships_created"]
    entity_ids_1 = sorted(e.entity_id for e in builder1.canonical_entities)
    entity_ids_2 = sorted(e.entity_id for e in builder2.canonical_entities)
    assert entity_ids_1 == entity_ids_2


def _fragment(document_id: str, evidence_id: str, text: str) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        source_type="pdf",
        document_id=document_id,
        source_path=Path(f"{document_id}.pdf"),
        filename=f"{document_id}.pdf",
        extraction_method="native_text",
        content=EvidenceContent(raw_value=text, text=text),
        location=PDFEvidenceLocation(source_type="pdf", page_number=1, block_id=evidence_id, bbox=[0, 0, 1, 1]),
    )


class _BatchFakeProvider:
    """One canned, source_ref-tagged response per document_id, keyed on the concatenated
    batch prompt containing that document_id - simulates a real LLM correctly tagging
    each assertion with which fragment (by local ref) supports it.
    """

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system: str, user: str, response_schema: dict) -> dict:
        self.calls += 1
        if "document_id: doc-alpha" in user:
            return {
                "entities": [
                    {"mention_text": "Asha Nair", "entity_type": "person", "confidence": 0.9, "source_ref": "E1"},
                    {"mention_text": "Bridge Alpha", "entity_type": "project", "confidence": 0.9, "source_ref": "E1"},
                    {"mention_text": "Metro Authority", "entity_type": "client", "confidence": 0.9, "source_ref": "E2"},
                ],
                "relationships": [
                    {"subject_mention_text": "Asha Nair", "predicate": "led", "object_mention_text": "Bridge Alpha", "confidence": 0.9, "source_ref": "E1"},
                    {"subject_mention_text": "Bridge Alpha", "predicate": "commissioned_by", "object_mention_text": "Metro Authority", "confidence": 0.9, "source_ref": "E2"},
                ],
                "attributes": [
                    {"subject_mention_text": "Bridge Alpha", "predicate": "contract_value", "value": "100000000", "value_type": "currency", "confidence": 0.9, "source_ref": "E2"},
                ],
            }
        return {"entities": [], "relationships": [], "attributes": []}


def test_process_documents_batched_makes_one_call_per_document_not_per_fragment() -> None:
    fragments = [
        _fragment("doc-alpha", "doc-alpha-e1", "Asha Nair led the project Bridge Alpha."),
        _fragment("doc-alpha", "doc-alpha-e2", "Bridge Alpha was commissioned by Metro Authority for INR 10.00 Cr."),
        _fragment("doc-alpha", "doc-alpha-e3", "Page footer, nothing extractable here."),
    ]
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)

    builder.process_documents_batched(fragments, batch_size=40)

    assert provider.calls == 1  # 3 fragments, one document -> one call
    assert builder.report.evidence_processed == 3


def test_process_documents_batched_resolves_source_ref_to_correct_evidence_id() -> None:
    fragments = [
        _fragment("doc-alpha", "doc-alpha-e1", "Asha Nair led the project Bridge Alpha."),
        _fragment("doc-alpha", "doc-alpha-e2", "Bridge Alpha was commissioned by Metro Authority for INR 10.00 Cr."),
    ]
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)

    builder.process_documents_batched(fragments, batch_size=40)

    led_relationship = next(r for r in builder.relationships if r.predicate == "led")
    commissioned_relationship = next(r for r in builder.relationships if r.predicate == "commissioned_by")
    assert led_relationship.evidence_id == "doc-alpha-e1"  # from E1
    assert commissioned_relationship.evidence_id == "doc-alpha-e2"  # from E2

    attribute = builder.attributes[0]
    assert attribute.evidence_id == "doc-alpha-e2"  # from E2


def test_process_documents_batched_respects_batch_size_chunking() -> None:
    fragments = [_fragment("doc-alpha", f"doc-alpha-e{i}", "Page footer, nothing here.") for i in range(5)]
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)

    builder.process_documents_batched(fragments, batch_size=2)

    assert provider.calls == 3  # ceil(5/2) chunks for the one document
    assert builder.report.evidence_processed == 5


def test_process_documents_batched_honors_document_type_context() -> None:
    documents_by_id = {
        "doc-alpha": DocumentRecord(
            document_id="doc-alpha",
            filename="doc-alpha.pdf",
            source_path=Path("doc-alpha.pdf"),
            document_type="pdf",
            extension=".pdf",
            size_bytes=0,
            metadata={"hackathon_doc_type": "reference_letter"},
        )
    }
    seen_prompts = []

    class _CapturingProvider:
        def complete(self, system, user, response_schema):
            seen_prompts.append(user)
            return {"entities": [], "relationships": [], "attributes": []}

    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(_CapturingProvider()), resolver)

    builder.process_documents_batched(
        [_fragment("doc-alpha", "doc-alpha-e1", "Some text.")], documents_by_id=documents_by_id, batch_size=40
    )

    assert any("reference_letter" in prompt for prompt in seen_prompts)


def test_ensure_extracted_skips_already_extracted_documents() -> None:
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)
    evidence_by_document = {
        "doc-alpha": [
            _fragment("doc-alpha", "doc-alpha-e1", "Asha Nair led the project Bridge Alpha."),
            _fragment("doc-alpha", "doc-alpha-e2", "Bridge Alpha was commissioned by Metro Authority for INR 10.00 Cr."),
        ]
    }

    first = builder.ensure_extracted(["doc-alpha"], evidence_by_document, batch_size=40)
    assert first == ["doc-alpha"]
    assert provider.calls == 1
    assert builder.is_extracted("doc-alpha")
    assert builder.extracted_document_count == 1

    second = builder.ensure_extracted(["doc-alpha"], evidence_by_document, batch_size=40)
    assert second == []  # already extracted - no new work, no new LLM call
    assert provider.calls == 1


def test_ensure_extracted_only_processes_the_new_documents_in_a_mixed_batch() -> None:
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)
    evidence_by_document = {
        "doc-alpha": [_fragment("doc-alpha", "doc-alpha-e1", "Asha Nair led the project Bridge Alpha.")],
        "doc-beta": [_fragment("doc-beta", "doc-beta-e1", "Unrelated text with nothing extractable.")],
    }

    builder.ensure_extracted(["doc-alpha"], evidence_by_document, batch_size=40)
    calls_after_first = provider.calls

    newly = builder.ensure_extracted(["doc-alpha", "doc-beta"], evidence_by_document, batch_size=40)

    assert newly == ["doc-beta"]
    assert provider.calls == calls_after_first + 1  # only doc-beta cost a new call
    assert builder.extracted_document_count == 2


def test_ensure_extracted_unknown_document_id_does_not_retry_forever() -> None:
    provider = _BatchFakeProvider()
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    builder = WorldModelBuilder(SemanticExtractor(provider), resolver)

    first = builder.ensure_extracted(["does-not-exist"], evidence_by_document={}, batch_size=40)
    assert first == ["does-not-exist"]
    assert provider.calls == 0  # no evidence for it, so no LLM call was made

    second = builder.ensure_extracted(["does-not-exist"], evidence_by_document={}, batch_size=40)
    assert second == []  # marked covered, not retried
