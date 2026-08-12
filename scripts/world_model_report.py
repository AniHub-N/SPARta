"""Runs WorldModelBuilder end-to-end over the real JAW sample corpus already ingested
into data/evidence/, using a canned FakeProvider (no LLM key is configured in this
environment - see .env.example / JAW_LLM_* settings for how to point this at a real
OpenAI-compatible endpoint instead). Prints coverage stats, a provenance chain, and a
generic multi-hop traversal.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from jaw_ingest.chunk4 import EvidenceCorpus, GraphStore
from jaw_ingest.duckdb_store import DuckDBStore
from jaw_ingest.entity_resolution import EntityResolver
from jaw_ingest.llm_provider import NullProvider
from jaw_ingest.semantic_extraction import SemanticExtractor
from jaw_ingest.world_model import WorldModelBuilder


class CorpusFakeProvider:
    """Stands in for a real LLM in this environment (no JAW_LLM_* key configured).
    Seeded with realistic extractions keyed on substrings actually present in the
    ingested sample corpus (test/DOC-CC-120.pdf, test/DOC-GLB-2019.pdf,
    test/BOQ_and_Measurements_Contract_79.xlsx).
    """

    def complete(self, system, user, response_schema):
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


def main() -> None:
    evidence_root = ROOT / "data" / "evidence"
    corpus = EvidenceCorpus.from_evidence_root(evidence_root)

    print(f"NullProvider check: JAW_LLM_PROVIDER default -> {type(NullProvider()).__name__}")
    print(f"Real LLM extraction NOT exercised in this run - no JAW_LLM_* provider configured in this environment.")
    print(f"Corpus: {len(corpus.documents)} documents, {len(corpus.evidence)} evidence items\n")

    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    extractor = SemanticExtractor(CorpusFakeProvider())
    builder = WorldModelBuilder(extractor, resolver)
    builder.process_evidence(corpus.evidence)

    graph_store = GraphStore()
    duckdb_store = DuckDBStore(":memory:")
    builder.persist(graph_store, duckdb_store)

    print("=== Coverage ===")
    for key, value in builder.coverage().items():
        print(f"  {key}: {value}")

    print("\n=== Graph ===")
    print(f"  graph_nodes: {graph_store.entity_count()}")
    print(f"  graph_edges: {graph_store.relationship_count()}")

    print("\n=== DuckDB ===")
    print(f"  entities rows: {duckdb_store.count('entities')}")
    print(f"  relationships rows: {duckdb_store.count('relationships')}")

    if builder.relationships:
        relationship = builder.relationships[0]
        evidence = corpus.evidence_by_id[relationship.evidence_id]
        document = corpus.documents_by_id[evidence.document_id]
        subject = next(e for e in builder.canonical_entities if e.entity_id == relationship.subject_entity_id)
        object_entity = next(e for e in builder.canonical_entities if e.entity_id == relationship.object_entity_id)
        subject_mention = next(m for m in builder.mentions if m.mention_id in subject.mention_ids)
        row = duckdb_store.query("SELECT * FROM relationships WHERE relationship_id = ?", (relationship.relationship_id,))[0]

        print("\n=== Provenance chain ===")
        print(f"  document           : {document.document_id} ({document.filename})")
        print(f"  evidence           : {evidence.evidence_id} (page {getattr(evidence.location, 'page_number', None)})")
        print(f"  entity_mention      : {subject_mention.mention_id} -> '{subject_mention.mention_text}'")
        print(f"  canonical_entity    : {subject.entity_id} -> '{subject.canonical_name}' ({subject.entity_type})")
        print(f"  relationship        : {relationship.relationship_id} -> {subject.canonical_name} --{relationship.predicate}--> {object_entity.canonical_name}")
        edge = graph_store.graph.get_edge_data(relationship.subject_entity_id, relationship.object_entity_id, key=relationship.relationship_id)
        print(f"  graph_edge          : evidence_id={edge['evidence_id']}")
        print(f"  duckdb_row          : relationships.relationship_id={row['relationship_id']}, evidence_id={row['evidence_id']}")

    projects = [e for e in builder.canonical_entities if e.entity_type == "project"]
    people = [e for e in builder.canonical_entities if e.entity_type == "person"]
    if projects and people:
        path = graph_store.find_path(projects[0].entity_id, people[0].entity_id)
        print("\n=== Generic multi-hop traversal ===")
        print(f"  {projects[0].canonical_name} -> ... -> {people[0].canonical_name}")
        if path:
            names = []
            for node_id in path:
                entity = next((e for e in builder.canonical_entities if e.entity_id == node_id), None)
                names.append(entity.canonical_name if entity else node_id)
            print(f"  path: {' -> '.join(names)}")
        else:
            print("  path: None")


if __name__ == "__main__":
    main()
