from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from .chunk4 import GraphStore
from .duckdb_store import DuckDBStore
from .entity_resolution import EntityResolver
from .evidence import DocumentRecord, Evidence, stable_hex
from .semantic_extraction import SemanticExtractor
from .semantic_schemas import (
    Attribute,
    CanonicalEntity,
    EntityMention,
    ExtractionFailure,
    Relationship,
    SemanticExtractionResult,
)

logger = logging.getLogger(__name__)


@dataclass
class WorldModelReport:
    evidence_processed: int = 0
    mentions_created: int = 0
    entities_resolved: int = 0
    entities_ambiguous: int = 0
    entities_unresolved: int = 0
    relationships_created: int = 0
    attributes_created: int = 0
    extraction_failures: dict[str, int] = field(default_factory=dict)


class WorldModelBuilder:
    """Orchestrates Evidence -> SemanticExtractor -> EntityMention -> EntityResolver ->
    CanonicalEntity -> Relationship/Attribute -> GraphStore + DuckDBStore.

    Entity/relationship/attribute assertion content is entirely data-driven from what the
    extractor returns for each piece of evidence - this builder contains no hardcoded
    reasoning chains or predicate-specific control flow.
    """

    def __init__(self, extractor: SemanticExtractor, resolver: EntityResolver) -> None:
        self.extractor = extractor
        self.resolver = resolver
        self.canonical_entities: list[CanonicalEntity] = []
        self.mentions: list[EntityMention] = []
        self.relationships: list[Relationship] = []
        self.attributes: list[Attribute] = []
        self.report = WorldModelReport()
        self._extracted_document_ids: set[str] = set()
        self._lock = threading.RLock()

    def is_extracted(self, document_id: str) -> bool:
        with self._lock:
            return document_id in self._extracted_document_ids

    @property
    def extracted_document_count(self) -> int:
        with self._lock:
            return len(self._extracted_document_ids)

    def ensure_extracted(
        self,
        document_ids: list[str],
        evidence_by_document: dict[str, list[Evidence]],
        documents_by_id: dict[str, DocumentRecord] | None = None,
        batch_size: int = 30,
        max_workers: int = 8,
    ) -> list[str]:
        """Idempotent, incremental extraction: extracts only the documents in
        `document_ids` that haven't already been extracted by this builder instance.
        Safe to call repeatedly with overlapping sets - e.g. once per DISCOVER call
        across many hops/questions in the same run - already-covered documents are
        skipped for free, never re-sent to the LLM. Returns the document_ids that were
        actually newly extracted by this call (empty list if everything was already
        covered, which is the common case once a run has been going for a while).
        """
        with self._lock:
            new_ids = [doc_id for doc_id in dict.fromkeys(document_ids) if doc_id not in self._extracted_document_ids]
        if not new_ids:
            return []

        def _extract_one_doc(document_id: str) -> None:
            doc_evidence = evidence_by_document.get(document_id, [])
            if not doc_evidence:
                with self._lock:
                    self._extracted_document_ids.add(document_id)
                return
            document = (documents_by_id or {}).get(document_id)
            document_type = document.metadata.get("hackathon_doc_type") if document else None
            for start in range(0, len(doc_evidence), batch_size):
                chunk = doc_evidence[start : start + batch_size]
                self._process_batch(document_id, chunk, document_type)
            with self._lock:
                self._extracted_document_ids.add(document_id)

        if len(new_ids) <= 1:
            for doc_id in new_ids:
                _extract_one_doc(doc_id)
        else:
            workers = min(max_workers, len(new_ids))
            with ThreadPoolExecutor(max_workers=workers) as executor:
                list(executor.map(_extract_one_doc, new_ids))
        return new_ids

    def process_evidence(
        self,
        evidence_items: list[Evidence],
        documents_by_id: dict[str, DocumentRecord] | None = None,
    ) -> WorldModelReport:
        """One LLM call per evidence item. Simple and precise, but doesn't scale to a
        large corpus (hundreds of documents x tens of fragments each) - see
        process_documents_batched for the version that does. `documents_by_id`, when
        given, lets each item's extraction see the document's semantic type
        (hackathon_doc_type from document_index.csv, if ingested) as context.
        """
        for evidence in evidence_items:
            document = (documents_by_id or {}).get(evidence.document_id)
            document_type = document.metadata.get("hackathon_doc_type") if document else None
            self._process_one(evidence, document_type)
        return self.report

    def process_documents_batched(
        self,
        evidence_items: list[Evidence],
        documents_by_id: dict[str, DocumentRecord] | None = None,
        batch_size: int = 40,
    ) -> WorldModelReport:
        """Groups evidence by document_id and sends each group through the LLM in
        chunks of at most `batch_size` fragments per call, instead of one call per
        fragment - this is what makes a full multi-hundred-document corpus feasible to
        extract at all. Provenance is preserved per-assertion via source_ref, which the
        batched prompt requires the model to set on every entity/relationship/attribute.
        """
        evidence_by_document: dict[str, list[Evidence]] = {}
        for evidence in evidence_items:
            evidence_by_document.setdefault(evidence.document_id, []).append(evidence)

        for document_id, doc_evidence in evidence_by_document.items():
            document = (documents_by_id or {}).get(document_id)
            document_type = document.metadata.get("hackathon_doc_type") if document else None
            for start in range(0, len(doc_evidence), batch_size):
                chunk = doc_evidence[start : start + batch_size]
                self._process_batch(document_id, chunk, document_type)
            self._extracted_document_ids.add(document_id)
        return self.report

    def _process_one(self, evidence: Evidence, document_type: str | None = None) -> None:
        self.report.evidence_processed += 1
        result = self.extractor.extract(evidence, document_type=document_type)
        if isinstance(result, ExtractionFailure):
            self.report.extraction_failures[result.reason] = self.report.extraction_failures.get(result.reason, 0) + 1
            return
        self._ingest_result(evidence.document_id, result, resolve_evidence_id=lambda _ref, eid=evidence.evidence_id: eid)

    def _process_batch(self, document_id: str, evidence_chunk: list[Evidence], document_type: str | None = None) -> None:
        self.report.evidence_processed += len(evidence_chunk)
        outcome = self.extractor.extract_batch(document_id, evidence_chunk, document_type=document_type)
        if isinstance(outcome, ExtractionFailure):
            self.report.extraction_failures[outcome.reason] = self.report.extraction_failures.get(outcome.reason, 0) + len(evidence_chunk)
            return
        result, ref_map = outcome
        fallback_evidence_id = evidence_chunk[0].evidence_id if evidence_chunk else ""

        def resolve_evidence_id(source_ref: str) -> str:
            return ref_map.get(source_ref, fallback_evidence_id)

        self._ingest_result(document_id, result, resolve_evidence_id=resolve_evidence_id)

    def _ingest_result(self, document_id: str, result: SemanticExtractionResult, resolve_evidence_id: Callable[[str], str]) -> None:
        with self._lock:
            # mention_text (normalized within this call) -> entity_id, so relationships/attributes
            # extracted alongside can reference the entities this call just resolved (or created).
            mention_text_to_entity_id: dict[str, str] = {}

            for index, entity_assertion in enumerate(result.entities):
                evidence_id = resolve_evidence_id(entity_assertion.source_ref)
                mention = EntityMention(
                    mention_id=stable_hex("mention", evidence_id, entity_assertion.mention_text, entity_assertion.entity_type, index),
                    mention_text=entity_assertion.mention_text,
                    entity_type=entity_assertion.entity_type,
                    document_id=document_id,
                    evidence_id=evidence_id,
                    extraction_confidence=entity_assertion.confidence,
                    provenance={"evidence_id": evidence_id, "document_id": document_id},
                )
                self.mentions.append(mention)
                self.report.mentions_created += 1

                entity_id = self._resolve_or_create(mention)
                mention_text_to_entity_id[entity_assertion.mention_text] = entity_id

            for rel_assertion in result.relationships:
                subject_id = mention_text_to_entity_id.get(rel_assertion.subject_mention_text)
                object_id = mention_text_to_entity_id.get(rel_assertion.object_mention_text)
                if subject_id is None or object_id is None:
                    logger.debug(
                        "Skipping relationship '%s' for document %s: subject/object mention not extracted as an entity in the same call.",
                        rel_assertion.predicate,
                        document_id,
                    )
                    continue
                evidence_id = resolve_evidence_id(rel_assertion.source_ref)
                relationship = Relationship(
                    relationship_id=stable_hex("relationship", subject_id, rel_assertion.predicate, object_id, evidence_id),
                    subject_entity_id=subject_id,
                    predicate=rel_assertion.predicate,
                    object_entity_id=object_id,
                    confidence=rel_assertion.confidence,
                    evidence_id=evidence_id,
                    document_id=document_id,
                    provenance={"evidence_id": evidence_id, "document_id": document_id},
                )
                self.relationships.append(relationship)
                self.report.relationships_created += 1

            for attr_assertion in result.attributes:
                entity_id = mention_text_to_entity_id.get(attr_assertion.subject_mention_text)
                if entity_id is None:
                    logger.debug(
                        "Skipping attribute '%s' for document %s: subject mention not extracted as an entity in the same call.",
                        attr_assertion.predicate,
                        document_id,
                    )
                    continue
                evidence_id = resolve_evidence_id(attr_assertion.source_ref)
                attribute = Attribute(
                    attribute_id=stable_hex("attribute", entity_id, attr_assertion.predicate, str(attr_assertion.value), evidence_id),
                    entity_id=entity_id,
                    predicate=attr_assertion.predicate,
                    value=attr_assertion.value,
                    value_type=attr_assertion.value_type,
                    confidence=attr_assertion.confidence,
                    evidence_id=evidence_id,
                    document_id=document_id,
                    provenance={"evidence_id": evidence_id, "document_id": document_id},
                )
                self.attributes.append(attribute)
                self.report.attributes_created += 1

    def _resolve_or_create(self, mention: EntityMention) -> str:
        result = self.resolver.resolve_mention(mention, self.canonical_entities)

        if result.status == "resolved" and result.resolved_entity_id:
            entity = self._find_entity(result.resolved_entity_id)
            if entity is not None:
                if mention.mention_id not in entity.mention_ids:
                    entity.mention_ids.append(mention.mention_id)
                if mention.mention_text not in entity.aliases and mention.mention_text != entity.canonical_name:
                    entity.aliases.append(mention.mention_text)
                # An entity's very first mention is minted with resolution_status
                # "unresolved" because no prior candidates existed to match against
                # (see below). Once a second, independent mention confidently resolves
                # to it, that status is stale - the entity is now corroborated.
                if entity.resolution_status == "unresolved":
                    entity.resolution_status = "resolved"
                    entity.resolution_confidence = max(entity.resolution_confidence, result.candidates[0]["score"] if result.candidates else 0.0)
                self.report.entities_resolved += 1
                return entity.entity_id

        # Ambiguous or unresolved: mint a new canonical entity rather than forcing an
        # uncertain merge. Ambiguity is preserved in resolution_status/metadata, not hidden.
        # Content-derived (not mention_id-derived) so identical mention_text/entity_type always
        # produces the same canonical entity_id regardless of processing order - required for
        # idempotent re-runs of the pipeline over the same corpus.
        entity_id = stable_hex("canonical_entity", mention.entity_type, mention.mention_text)
        status = result.status if result.status in ("ambiguous", "unresolved") else "resolved"
        entity = CanonicalEntity(
            entity_id=entity_id,
            entity_type=mention.entity_type,
            canonical_name=mention.mention_text,
            aliases=[],
            mention_ids=[mention.mention_id],
            resolution_status=status,
            resolution_confidence=result.candidates[0]["score"] if result.candidates else 0.0,
            metadata={"ambiguous_candidates": [c["entity_id"] for c in result.candidates[:5]]} if status == "ambiguous" else {},
        )
        self.canonical_entities.append(entity)
        if status == "ambiguous":
            self.report.entities_ambiguous += 1
        else:
            self.report.entities_unresolved += 1
        return entity.entity_id

    def _find_entity(self, entity_id: str) -> CanonicalEntity | None:
        for entity in self.canonical_entities:
            if entity.entity_id == entity_id:
                return entity
        return None

    def persist(self, graph_store: GraphStore, duckdb_store: DuckDBStore) -> None:
        """Idempotent (INSERT OR REPLACE under the hood) - safe to call repeatedly as
        the world model grows, e.g. once per DISCOVER call, to keep DuckDB/the graph in
        sync with in-memory state without waiting for a single final build step.
        """
        with self._lock:
            graph_store.build_semantic_world(self.canonical_entities, self.relationships)
            duckdb_store.ingest_entities(self.canonical_entities)
            duckdb_store.ingest_relationships(self.relationships)
            duckdb_store.ingest_mentions(self.mentions)
            duckdb_store.ingest_attributes(self.attributes)

    def coverage(self) -> dict[str, Any]:
        return {
            "evidence_processed": self.report.evidence_processed,
            "mentions_created": self.report.mentions_created,
            "canonical_entities": len(self.canonical_entities),
            "entities_resolved": self.report.entities_resolved,
            "entities_ambiguous": self.report.entities_ambiguous,
            "entities_unresolved": self.report.entities_unresolved,
            "relationships_created": self.report.relationships_created,
            "attributes_created": self.report.attributes_created,
            "extraction_failures": dict(self.report.extraction_failures),
        }
