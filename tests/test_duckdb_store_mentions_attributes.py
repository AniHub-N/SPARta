from __future__ import annotations

from jaw_ingest.duckdb_store import DuckDBStore
from jaw_ingest.semantic_schemas import AssertionProvenance, Attribute, EntityMention


def test_ingest_mentions_persists_and_upserts() -> None:
    store = DuckDBStore(":memory:")
    mention = EntityMention(
        mention_id="m1",
        mention_text="Asha Nair",
        entity_type="person",
        document_id="doc1",
        evidence_id="e1",
        extraction_confidence=0.9,
        provenance=AssertionProvenance(evidence_id="e1", document_id="doc1"),
    )

    store.ingest_mentions([mention])

    rows = store.query("SELECT * FROM mentions WHERE mention_id = ?", ("m1",))
    assert len(rows) == 1
    assert rows[0]["mention_text"] == "Asha Nair"
    assert rows[0]["document_id"] == "doc1"
    assert store.count("mentions") == 1

    store.ingest_mentions([mention])  # upsert, not duplicate
    assert store.count("mentions") == 1


def test_ingest_attributes_persists_and_upserts() -> None:
    store = DuckDBStore(":memory:")
    attribute = Attribute(
        attribute_id="a1",
        entity_id="ent1",
        predicate="contract_value",
        value="100000000",
        value_type="currency",
        confidence=0.9,
        evidence_id="e1",
        document_id="doc1",
        provenance={"evidence_id": "e1"},
    )

    store.ingest_attributes([attribute])

    rows = store.query("SELECT * FROM attributes WHERE attribute_id = ?", ("a1",))
    assert len(rows) == 1
    assert rows[0]["entity_id"] == "ent1"
    assert rows[0]["predicate"] == "contract_value"
    assert store.count("attributes") == 1

    store.ingest_attributes([attribute])
    assert store.count("attributes") == 1


def test_ingest_mentions_and_attributes_handle_empty_lists() -> None:
    store = DuckDBStore(":memory:")
    store.ingest_mentions([])
    store.ingest_attributes([])
    assert store.count("mentions") == 0
    assert store.count("attributes") == 0
