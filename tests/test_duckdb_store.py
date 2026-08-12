from __future__ import annotations

from jaw_ingest.duckdb_store import DuckDBStore
from jaw_ingest.semantic_schemas import CanonicalEntity, Relationship


def test_ingest_entities_persists_typed_fields() -> None:
    store = DuckDBStore(":memory:")
    entity = CanonicalEntity(
        entity_id="ent-1",
        entity_type="organization",
        canonical_name="National Infrastructure Corp. Ltd.",
        aliases=["NIC", "National Infra Corp"],
        mention_ids=["m1", "m2"],
        resolution_status="resolved",
        resolution_confidence=0.92,
        metadata={"source": "test"},
    )

    store.ingest_entities([entity])

    rows = store.query("SELECT * FROM entities WHERE entity_id = ?", ("ent-1",))
    assert len(rows) == 1
    row = rows[0]
    assert row["entity_type"] == "organization"
    assert row["canonical_name"] == "National Infrastructure Corp. Ltd."
    assert row["resolution_status"] == "resolved"
    assert row["resolution_confidence"] == 0.92
    assert "NIC" in row["aliases"]


def test_ingest_relationships_persists_typed_fields() -> None:
    store = DuckDBStore(":memory:")
    relationship = Relationship(
        relationship_id="rel-1",
        subject_entity_id="ent-1",
        predicate="awarded_to",
        object_entity_id="ent-2",
        confidence=0.9,
        evidence_id="e1",
        document_id="doc1",
        provenance={"evidence_id": "e1"},
    )

    store.ingest_relationships([relationship])

    rows = store.query("SELECT * FROM relationships WHERE relationship_id = ?", ("rel-1",))
    assert len(rows) == 1
    row = rows[0]
    assert row["subject_entity_id"] == "ent-1"
    assert row["object_entity_id"] == "ent-2"
    assert row["predicate"] == "awarded_to"
    assert row["evidence_id"] == "e1"
    assert row["document_id"] == "doc1"


def test_ingest_entities_upserts_by_entity_id() -> None:
    store = DuckDBStore(":memory:")
    entity = CanonicalEntity(
        entity_id="ent-1",
        entity_type="organization",
        canonical_name="Old Name",
        resolution_status="resolved",
        resolution_confidence=0.5,
    )
    store.ingest_entities([entity])

    updated = entity.model_copy(update={"canonical_name": "New Name", "resolution_confidence": 0.9})
    store.ingest_entities([updated])

    rows = store.query("SELECT * FROM entities WHERE entity_id = ?", ("ent-1",))
    assert len(rows) == 1
    assert rows[0]["canonical_name"] == "New Name"
    assert store.count("entities") == 1
