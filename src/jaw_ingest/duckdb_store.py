from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb

from .evidence import DocumentRecord, Evidence, Fact
from .semantic_schemas import Attribute, CanonicalEntity, EntityMention, Relationship


def _serialize_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


class DuckDBStore:
    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = db_path
        if str(db_path) != ":memory:":
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.connection = duckdb.connect(database=str(db_path), read_only=False)
        self.connection.execute("PRAGMA threads=1")
        self.create_tables()

    def create_tables(self) -> None:
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS documents (
                document_id TEXT PRIMARY KEY,
                filename TEXT,
                source_path TEXT,
                document_type TEXT,
                extension TEXT,
                size_bytes BIGINT,
                checksum TEXT,
                page_count INTEGER,
                sheet_count INTEGER,
                extraction_status TEXT,
                extraction_version TEXT,
                evidence_schema_version TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS evidence (
                evidence_id TEXT PRIMARY KEY,
                source_type TEXT,
                document_id TEXT,
                source_path TEXT,
                filename TEXT,
                extraction_method TEXT,
                extraction_version TEXT,
                extraction_confidence DOUBLE,
                raw_value TEXT,
                text TEXT,
                formula TEXT,
                note TEXT,
                metadata TEXT,
                location_type TEXT,
                page_number INTEGER,
                block_id TEXT,
                bbox TEXT,
                workbook_id TEXT,
                sheet_name TEXT,
                cell_address TEXT,
                row INTEGER,
                cell_column INTEGER,
                location_json TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                fact_id TEXT PRIMARY KEY,
                evidence_id TEXT,
                document_id TEXT,
                source_path TEXT,
                subject_mention TEXT,
                predicate TEXT,
                raw_value TEXT,
                normalized_value TEXT,
                -- typed normalized columns for numeric/date/bool queries
                normalized_value_numeric DOUBLE,
                normalized_value_date DATE,
                normalized_value_bool BOOLEAN,
                normalized_type TEXT,
                normalized_unit TEXT,
                original_unit TEXT,
                extraction_method TEXT,
                normalization_method TEXT,
                extraction_confidence DOUBLE,
                normalization_confidence DOUBLE,
                validation_status TEXT,
                provenance TEXT,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS entities (
                entity_id TEXT PRIMARY KEY,
                entity_type TEXT,
                canonical_name TEXT,
                aliases TEXT,
                mention_ids TEXT,
                resolution_status TEXT,
                resolution_confidence DOUBLE,
                metadata TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS relationships (
                relationship_id TEXT PRIMARY KEY,
                subject_entity_id TEXT,
                predicate TEXT,
                object_entity_id TEXT,
                confidence DOUBLE,
                evidence_id TEXT,
                document_id TEXT,
                provenance TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS mentions (
                mention_id TEXT PRIMARY KEY,
                mention_text TEXT,
                entity_type TEXT,
                document_id TEXT,
                evidence_id TEXT,
                extraction_confidence DOUBLE,
                provenance TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS attributes (
                attribute_id TEXT PRIMARY KEY,
                entity_id TEXT,
                predicate TEXT,
                value TEXT,
                value_type TEXT,
                confidence DOUBLE,
                evidence_id TEXT,
                document_id TEXT,
                provenance TEXT
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS index_meta (
                id INTEGER PRIMARY KEY,
                corpus_fingerprint TEXT,
                embedding_model TEXT,
                built_at TEXT
            )
            """
        )

    def get_index_meta(self) -> dict[str, Any] | None:
        """The retrieval index's provenance: which corpus fingerprint and embedding
        model it was built from. None if this DuckDB store has never had an index
        built into it (fresh :memory: store, or a persisted file from before this
        table existed). Callers compare this against the CURRENT corpus fingerprint to
        decide whether a persisted index is still valid or must be rebuilt.
        """
        rows = self.query("SELECT corpus_fingerprint, embedding_model, built_at FROM index_meta WHERE id = 0")
        return rows[0] if rows else None

    def set_index_meta(self, corpus_fingerprint: str, embedding_model: str) -> None:
        import datetime

        self.connection.execute(
            "INSERT OR REPLACE INTO index_meta (id, corpus_fingerprint, embedding_model, built_at) VALUES (0, ?, ?, ?)",
            (corpus_fingerprint, embedding_model, datetime.datetime.now(datetime.timezone.utc).isoformat()),
        )

    def ingest_documents(self, documents: list[DocumentRecord]) -> None:
        records = [
            (
                doc.document_id,
                doc.filename,
                str(doc.source_path),
                doc.document_type,
                doc.extension,
                doc.size_bytes,
                doc.checksum,
                doc.page_count,
                doc.sheet_count,
                doc.extraction_status,
                doc.extraction_version,
                doc.evidence_schema_version,
                _serialize_value(doc.metadata),
            )
            for doc in documents
        ]
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO documents (
                document_id, filename, source_path, document_type, extension,
                size_bytes, checksum, page_count, sheet_count, extraction_status,
                extraction_version, evidence_schema_version, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_evidence(self, evidence_items: list[Evidence]) -> None:
        records = []
        for evidence in evidence_items:
            location = evidence.location.model_dump(mode="json")
            records.append(
                (
                    evidence.evidence_id,
                    evidence.source_type,
                    evidence.document_id,
                    str(evidence.source_path),
                    evidence.filename,
                    evidence.extraction_method,
                    evidence.extraction_version,
                    evidence.extraction_confidence,
                    _serialize_value(evidence.content.raw_value),
                    evidence.content.text,
                    evidence.content.formula,
                    evidence.content.note,
                    _serialize_value(evidence.content.metadata),
                    location.get("source_type"),
                    getattr(evidence.location, "page_number", None),
                    getattr(evidence.location, "block_id", None),
                    _serialize_value(getattr(evidence.location, "bbox", None)),
                    getattr(evidence.location, "workbook_id", None),
                    getattr(evidence.location, "sheet_name", None),
                    getattr(evidence.location, "cell_address", None),
                    getattr(evidence.location, "row", None),
                    getattr(evidence.location, "column", None),
                    _serialize_value(location),
                )
            )
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO evidence (
                evidence_id, source_type, document_id, source_path, filename,
                extraction_method, extraction_version, extraction_confidence,
                raw_value, text, formula, note, metadata, location_type,
                page_number, block_id, bbox, workbook_id, sheet_name,
                cell_address, row, cell_column, location_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_facts(self, facts: list[Fact]) -> None:
        records = []
        for fact in facts:
            provenance = fact.provenance.model_dump(mode="json")
            # Prepare typed normalized fields. We preserve the original normalized_value
            # in `normalized_value` (text), and populate typed columns when possible.
            norm_text = None
            norm_numeric = None
            norm_date = None
            norm_bool = None

            if fact.normalized_value is not None:
                # If already primitive, map directly
                if isinstance(fact.normalized_value, bool):
                    norm_bool = bool(fact.normalized_value)
                    norm_text = _serialize_value(fact.normalized_value)
                elif isinstance(fact.normalized_value, (int, float)):
                    norm_numeric = float(fact.normalized_value)
                    norm_text = _serialize_value(fact.normalized_value)
                else:
                    # Use normalized_type to coerce common types
                    sval = str(fact.normalized_value)
                    nt = (fact.normalized_type or "").lower()
                    try:
                        if nt.startswith("currency") or nt.startswith("number") or nt in ("int", "integer"):
                            norm_numeric = float(sval.replace(",", "").strip('"'))
                            norm_text = sval
                        elif nt in ("float", "decimal"):
                            norm_numeric = float(sval.replace(",", "").strip('"'))
                            norm_text = sval
                        elif "date" in nt:
                            # try ISO parse
                            from datetime import datetime

                            try:
                                dt = datetime.fromisoformat(sval.strip('"'))
                                norm_date = dt.date()
                                norm_text = sval
                            except Exception:
                                norm_text = sval
                        elif nt in ("bool", "boolean"):
                            if sval.strip('"').lower() in ("true", "1", "yes"):
                                norm_bool = True
                            elif sval.strip('"').lower() in ("false", "0", "no"):
                                norm_bool = False
                            norm_text = sval
                        else:
                            # fallback: keep text
                            norm_text = sval
                    except Exception:
                        norm_text = sval
            else:
                norm_text = None

            records.append(
                (
                    fact.fact_id,
                    fact.evidence_id,
                    fact.document_id,
                    str(fact.source_path),
                    fact.subject_mention,
                    fact.predicate,
                    _serialize_value(fact.raw_value),
                    _serialize_value(fact.normalized_value),
                    norm_numeric,
                    norm_date,
                    norm_bool,
                    fact.normalized_type,
                    fact.normalized_unit,
                    fact.original_unit,
                    fact.extraction_method,
                    fact.normalization_method,
                    fact.extraction_confidence,
                    float(fact.normalization_confidence),
                    fact.validation_status,
                    _serialize_value(provenance),
                    _serialize_value(fact.metadata),
                )
            )
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO facts (
                fact_id, evidence_id, document_id, source_path, subject_mention,
                predicate, raw_value, normalized_value, normalized_value_numeric, normalized_value_date, normalized_value_bool,
                normalized_type, normalized_unit, original_unit, extraction_method,
                normalization_method, extraction_confidence,
                normalization_confidence, validation_status, provenance, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_entities(self, entities: list[CanonicalEntity]) -> None:
        records = [
            (
                entity.entity_id,
                entity.entity_type,
                entity.canonical_name,
                _serialize_value(entity.aliases),
                _serialize_value(entity.mention_ids),
                entity.resolution_status,
                entity.resolution_confidence,
                _serialize_value(entity.metadata),
            )
            for entity in entities
        ]
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO entities (
                entity_id, entity_type, canonical_name, aliases, mention_ids,
                resolution_status, resolution_confidence, metadata
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_relationships(self, relationships: list[Relationship]) -> None:
        records = [
            (
                rel.relationship_id,
                rel.subject_entity_id,
                rel.predicate,
                rel.object_entity_id,
                rel.confidence,
                rel.evidence_id,
                rel.document_id,
                _serialize_value(rel.provenance),
            )
            for rel in relationships
        ]
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO relationships (
                relationship_id, subject_entity_id, predicate, object_entity_id,
                confidence, evidence_id, document_id, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_mentions(self, mentions: list[EntityMention]) -> None:
        records = [
            (
                mention.mention_id,
                mention.mention_text,
                mention.entity_type,
                mention.document_id,
                mention.evidence_id,
                mention.extraction_confidence,
                _serialize_value(mention.provenance.model_dump(mode="json")),
            )
            for mention in mentions
        ]
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO mentions (
                mention_id, mention_text, entity_type, document_id, evidence_id,
                extraction_confidence, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def ingest_attributes(self, attributes: list[Attribute]) -> None:
        records = [
            (
                attribute.attribute_id,
                attribute.entity_id,
                attribute.predicate,
                _serialize_value(attribute.value),
                attribute.value_type,
                attribute.confidence,
                attribute.evidence_id,
                attribute.document_id,
                _serialize_value(attribute.provenance),
            )
            for attribute in attributes
        ]
        if not records:
            return
        self.connection.executemany(
            """
            INSERT OR REPLACE INTO attributes (
                attribute_id, entity_id, predicate, value, value_type,
                confidence, evidence_id, document_id, provenance
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            records,
        )

    def query(self, sql: str, parameters: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        cursor = self.connection.cursor()
        if parameters is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, parameters)
        columns = [desc[0] for desc in cursor.description] if cursor.description else []
        rows = cursor.fetchall()
        return [dict(zip(columns, row)) for row in rows]

    def count(self, table_name: str) -> int:
        result = self.query(f"SELECT COUNT(*) AS count FROM {table_name}")
        return int(result[0]["count"])

    def close(self) -> None:
        """Releases the connection (and, for a file-backed store, its file lock) -
        needed before another process/connection can open the same on-disk path.
        Short-lived CLI processes release this naturally on exit; explicit close()
        matters for long-lived processes and for tests that open the same persisted
        path more than once.
        """
        self.connection.close()
