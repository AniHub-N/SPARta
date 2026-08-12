from __future__ import annotations

import logging
import re
import statistics
from typing import Any

from rapidfuzz import fuzz

from .entity_resolution import EntityResolver
from .semantic_schemas import AssertionProvenance, CanonicalEntity, EntityMention
from .system import JAWSystem

logger = logging.getLogger(__name__)


def _normalize(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    cleaned = re.sub(r"[\W_]+", " ", cleaned)
    return re.sub(r"\s+", " ", cleaned).strip()


def _predicate_matches(candidate: str, target: str | None, threshold: float = 60.0) -> bool:
    if not target:
        return True
    return fuzz.token_sort_ratio(_normalize(candidate), _normalize(target)) >= threshold


class ToolDispatcher:
    """The MCP-style tool boundary between the planner/executor and the underlying
    systems (DuckDB, NetworkX, Qdrant, RapidFuzz, embeddings, entity resolution).

    Every method here is a real, working tool backed by the actual data - nothing is a
    stub. The executor calls tools exclusively through `call()`; it never reaches into
    `system.pipeline` / `system.world_model` directly.
    """

    def __init__(self, system: JAWSystem, max_discover_limit: int | None = None) -> None:
        self.system = system
        self.pipeline = system.pipeline
        self.world_model = system.world_model
        self._max_discover_limit = max_discover_limit
        self._resolver = EntityResolver(
            system.pipeline.lexical_retriever, system.pipeline.semantic_retriever, system.pipeline.graph_store
        )
        # Built once (a single O(n) pass over the full evidence corpus) since it never
        # changes - which documents exist and what evidence they contain is fixed by
        # ingestion, independent of how much has been semantically extracted so far.
        self._evidence_by_document: dict[str, list] = {}
        for evidence in self.pipeline.corpus.evidence:
            self._evidence_by_document.setdefault(evidence.document_id, []).append(evidence)

    @property
    def _entities_by_id(self) -> dict[str, CanonicalEntity]:
        # NOT cached at construction time: under lazy/DISCOVER-driven extraction,
        # world_model.canonical_entities grows during the run, so a snapshot taken at
        # __init__ would silently go stale the moment the first new entity is added.
        return {e.entity_id: e for e in self.world_model.canonical_entities}

    def call(self, tool_name: str, **kwargs: Any) -> dict[str, Any]:
        handler = getattr(self, tool_name, None)
        if handler is None or tool_name.startswith("_") or tool_name == "call":
            return {"error": f"unknown_tool: {tool_name}"}
        try:
            return handler(**kwargs)
        except TypeError as exc:
            return {"error": f"bad_arguments: {exc}"}

    # --- entity resolution -------------------------------------------------------------

    def resolve_entity(self, query: str, entity_type: str | None = None, limit: int = 5) -> dict[str, Any]:
        mention = EntityMention(
            mention_id="query::" + query,
            mention_text=query,
            entity_type=entity_type or "",
            document_id="",
            evidence_id="",
            extraction_confidence=1.0,
            provenance=AssertionProvenance(evidence_id="", document_id=""),
        )
        result = self._resolver.resolve_mention(mention, self.world_model.canonical_entities)
        candidates = [
            {"entity_id": c["entity_id"], "name": c["name"], "score": c["score"]} for c in result.candidates[:limit]
        ]
        return {
            "status": result.status,
            "resolved_entity_id": result.resolved_entity_id,
            "candidates": candidates,
        }

    def get_entity(self, entity_id: str) -> dict[str, Any]:
        entity = self._entities_by_id.get(entity_id)
        if entity is None:
            return {"found": False}
        relationships_out = [r for r in self.world_model.relationships if r.subject_entity_id == entity_id]
        relationships_in = [r for r in self.world_model.relationships if r.object_entity_id == entity_id]
        return {
            "found": True,
            "entity_id": entity.entity_id,
            "entity_type": entity.entity_type,
            "canonical_name": entity.canonical_name,
            "aliases": entity.aliases,
            "resolution_status": entity.resolution_status,
            "resolution_confidence": entity.resolution_confidence,
            "mention_ids": entity.mention_ids,
            "relationships_out": [r.relationship_id for r in relationships_out],
            "relationships_in": [r.relationship_id for r in relationships_in],
        }

    # --- graph traversal -----------------------------------------------------------------

    def traverse_graph(
        self,
        entity_ids: list[str],
        predicate: str | None = None,
        direction: str = "out",
    ) -> dict[str, Any]:
        """Generic one-hop traversal from a set of entities, optionally filtered by
        predicate (fuzzy-matched against whatever predicate strings extraction produced -
        never an exact/hardcoded string comparison).
        """
        hops = []
        for relationship in self.world_model.relationships:
            if direction in ("out", "both") and relationship.subject_entity_id in entity_ids:
                if _predicate_matches(relationship.predicate, predicate):
                    hops.append(relationship)
            if direction in ("in", "both") and relationship.object_entity_id in entity_ids:
                if _predicate_matches(relationship.predicate, predicate):
                    hops.append(relationship)

        neighbor_ids: set[str] = set()
        edges = []
        for relationship in hops:
            neighbor_id = (
                relationship.object_entity_id
                if relationship.subject_entity_id in entity_ids
                else relationship.subject_entity_id
            )
            neighbor_ids.add(neighbor_id)
            edges.append(
                {
                    "relationship_id": relationship.relationship_id,
                    "subject_entity_id": relationship.subject_entity_id,
                    "predicate": relationship.predicate,
                    "object_entity_id": relationship.object_entity_id,
                    "confidence": relationship.confidence,
                    "evidence_id": relationship.evidence_id,
                }
            )
        return {"neighbor_entity_ids": sorted(neighbor_ids), "edges": edges}

    def enumerate_population(
        self,
        entity_type: str | None = None,
        predicate: str | None = None,
        anchor_entity_id: str | None = None,
        direction: str = "in",
    ) -> dict[str, Any]:
        """Finds ALL entities matching entity_type that are connected to anchor_entity_id
        via a predicate (fuzzy-matched), or all entities of entity_type if no anchor is
        given. This is the tool ENUMERATE plan steps use for population-style questions
        ("all completed projects for client X").
        """
        candidates = self.world_model.canonical_entities
        if entity_type:
            candidates = [c for c in candidates if _normalize(c.entity_type) == _normalize(entity_type)]

        if anchor_entity_id is None:
            entity_ids = [c.entity_id for c in candidates]
            return {"entity_ids": entity_ids, "count": len(entity_ids)}

        candidate_ids = {c.entity_id for c in candidates}
        matched: set[str] = set()
        for relationship in self.world_model.relationships:
            if not _predicate_matches(relationship.predicate, predicate):
                continue
            if direction in ("in", "both") and relationship.object_entity_id == anchor_entity_id:
                if relationship.subject_entity_id in candidate_ids:
                    matched.add(relationship.subject_entity_id)
            if direction in ("out", "both") and relationship.subject_entity_id == anchor_entity_id:
                if relationship.object_entity_id in candidate_ids:
                    matched.add(relationship.object_entity_id)
        return {"entity_ids": sorted(matched), "count": len(matched)}

    def get_attribute(self, entity_ids: list[str], predicate: str) -> dict[str, Any]:
        """Numeric/date attribute lookup. Deterministic Facts (Chunk 3's normalization -
        already correctly handles INR Cr/Lakh/Indian digit grouping/dates) take priority
        over LLM-transcribed Attribute values PER ENTITY when both exist, since the
        former is not subject to the LLM mis-copying or mis-normalizing a number. An
        entity with no matching Fact still falls back to its LLM-extracted Attribute
        (predicates that only exist in prose never appear as a structured Fact) -
        priority is decided per entity, not as an all-or-nothing switch for the batch.
        """
        fact_results = self._facts_for_entities(entity_ids, predicate)
        covered_entity_ids = {row["entity_id"] for row in fact_results}

        llm_results = []
        for attribute in self.world_model.attributes:
            if attribute.entity_id in entity_ids and attribute.entity_id not in covered_entity_ids and _predicate_matches(attribute.predicate, predicate):
                llm_results.append(
                    {
                        "entity_id": attribute.entity_id,
                        "predicate": attribute.predicate,
                        "value": attribute.value,
                        "value_type": attribute.value_type,
                        "confidence": attribute.confidence,
                        "evidence_id": attribute.evidence_id,
                        "source": "llm_extraction",
                    }
                )

        combined = fact_results + llm_results
        return {
            "attributes": combined,
            "count": len(combined),
            "source": "mixed" if fact_results and llm_results else ("facts" if fact_results else "llm_extraction"),
        }

    def _facts_for_entities(self, entity_ids: list[str], predicate: str) -> list[dict[str, Any]]:
        # entity_id -> its mentions' document_ids (deduped - an entity mentioned three
        # times in the same document must not triple-count that document's facts).
        documents_by_entity: dict[str, set[str]] = {}
        for entity_id in entity_ids:
            entity = self._entities_by_id.get(entity_id)
            if entity is None:
                continue
            documents_by_entity[entity_id] = {
                mention.document_id for mention in self.world_model.mentions if mention.mention_id in entity.mention_ids
            }

        document_ids = sorted({doc_id for docs in documents_by_entity.values() for doc_id in docs})
        if not document_ids:
            return []

        placeholders = ",".join("?" for _ in document_ids)
        rows = self.pipeline.query_duckdb(
            f"SELECT document_id, evidence_id, predicate, normalized_value, normalized_value_numeric, "
            f"normalized_value_date, normalized_type, normalization_confidence, validation_status "
            f"FROM facts WHERE document_id IN ({placeholders})",
            tuple(document_ids),
        )
        matching_rows = [
            row
            for row in rows
            if row.get("validation_status") == "valid" and _predicate_matches(row.get("predicate") or "", predicate)
        ]

        results = []
        for entity_id, doc_ids in documents_by_entity.items():
            for row in matching_rows:
                if row["document_id"] not in doc_ids:
                    continue
                value = row.get("normalized_value_numeric")
                if value is None:
                    value = row.get("normalized_value_date")
                if value is None:
                    value = row.get("normalized_value")
                results.append(
                    {
                        "entity_id": entity_id,
                        "predicate": row.get("predicate"),
                        "value": value,
                        "value_type": row.get("normalized_type"),
                        "confidence": float(row.get("normalization_confidence") or 1.0),
                        "evidence_id": row.get("evidence_id"),
                        "source": "deterministic_fact",
                    }
                )
        return results

    # --- retrieval -------------------------------------------------------------------------

    def search_evidence(self, query: str, limit: int = 10) -> dict[str, Any]:
        return {"results": self.pipeline.search_evidence(query, limit)}

    def semantic_search(self, query: str, limit: int = 10) -> dict[str, Any]:
        results = self.pipeline.semantic_retriever.search_semantic(query, limit)
        return {"results": results}

    # --- deterministic computation ----------------------------------------------------------

    def query_duckdb(self, sql: str, parameters: list[Any] | None = None) -> dict[str, Any]:
        stripped = sql.strip().lower()
        if not stripped.startswith("select"):
            return {"error": "only SELECT statements are permitted through this tool"}
        rows = self.pipeline.query_duckdb(sql, tuple(parameters) if parameters else None)
        return {"rows": rows, "count": len(rows)}

    def calculate(self, operation: str, values: list[float]) -> dict[str, Any]:
        numeric = [float(v) for v in values if v is not None]
        op = operation.strip().lower()
        try:
            if op in ("sum",):
                result = sum(numeric)
            elif op in ("avg", "average", "mean"):
                result = statistics.mean(numeric) if numeric else 0.0
            elif op == "min":
                result = min(numeric) if numeric else None
            elif op == "max":
                result = max(numeric) if numeric else None
            elif op == "count":
                result = len(numeric)
            elif op == "median":
                result = statistics.median(numeric) if numeric else None
            elif op in ("diff", "difference"):
                result = numeric[0] - numeric[1] if len(numeric) >= 2 else None
            elif op == "ratio":
                result = numeric[0] / numeric[1] if len(numeric) >= 2 and numeric[1] != 0 else None
            elif op in ("pct_diff", "percentage_difference"):
                result = ((numeric[0] - numeric[1]) / numeric[1] * 100) if len(numeric) >= 2 and numeric[1] != 0 else None
            elif op == "rank_desc":
                result = [i for i, _ in sorted(enumerate(numeric), key=lambda x: -x[1])]
            else:
                return {"error": f"unsupported operation: {operation}"}
        except (ZeroDivisionError, statistics.StatisticsError) as exc:
            return {"error": str(exc)}
        return {"operation": op, "inputs": numeric, "result": result}

    # --- provenance & completeness --------------------------------------------------------

    def get_provenance(
        self,
        entity_id: str | None = None,
        relationship_id: str | None = None,
        evidence_id: str | None = None,
        fact_id: str | None = None,
    ) -> dict[str, Any]:
        if entity_id:
            entity = self._entities_by_id.get(entity_id)
            if entity is None:
                return {"found": False}
            mentions = [m for m in self.world_model.mentions if m.mention_id in entity.mention_ids]
            return {
                "found": True,
                "entity_id": entity_id,
                "mentions": [
                    {"mention_id": m.mention_id, "evidence_id": m.evidence_id, "document_id": m.document_id}
                    for m in mentions
                ],
            }
        if relationship_id:
            relationship = next((r for r in self.world_model.relationships if r.relationship_id == relationship_id), None)
            if relationship is None:
                return {"found": False}
            return {
                "found": True,
                "relationship_id": relationship_id,
                "evidence_id": relationship.evidence_id,
                "document_id": relationship.document_id,
            }
        return self.pipeline.get_provenance(evidence_id=evidence_id, fact_id=fact_id) or {"found": False}

    def check_completeness(
        self,
        entity_type: str,
        expected_min: int | None = None,
        anchor_query: str | None = None,
    ) -> dict[str, Any]:
        """Without `anchor_query`: internal consistency only (any unresolved/ambiguous
        entities of this type, any extraction failures) - this cannot detect a document
        that was never even looked at.

        With `anchor_query` (e.g. a resolved client's canonical name): a DETERMINISTIC
        cross-check against the always-complete, LLM-free lexical/semantic index - finds
        every document that index thinks is relevant to the anchor, and compares that
        against which documents the current entities of this type actually came from
        (via their mentions). A mismatch means lazy/DISCOVER-driven extraction under-
        covered this population - not an LLM's opinion, a count comparison against the
        full corpus. This is what a REPLAN should widen DISCOVER's query/limit in
        response to.
        """
        matching = [e for e in self.world_model.canonical_entities if _normalize(e.entity_type) == _normalize(entity_type)]
        unresolved = [e for e in matching if e.resolution_status == "unresolved"]
        ambiguous = [e for e in matching if e.resolution_status == "ambiguous"]
        extraction_failures = self.world_model.report.extraction_failures
        complete = len(unresolved) == 0 and len(ambiguous) == 0 and not extraction_failures
        if expected_min is not None:
            complete = complete and len(matching) >= expected_min

        result: dict[str, Any] = {
            "entity_type": entity_type,
            "count": len(matching),
            "unresolved_count": len(unresolved),
            "ambiguous_count": len(ambiguous),
            "extraction_failures": dict(extraction_failures),
        }

        if anchor_query:
            covered_document_ids = {
                mention.document_id
                for entity in matching
                for mention in self.world_model.mentions
                if mention.mention_id in entity.mention_ids
            }
            discovery = self.discover_evidence(anchor_query, limit=50)
            candidate_document_ids = set(discovery["document_ids"])
            missing_document_ids = sorted(candidate_document_ids - covered_document_ids)
            complete = complete and not missing_document_ids
            result["candidate_document_ids"] = sorted(candidate_document_ids)
            result["covered_document_ids"] = sorted(covered_document_ids)
            result["missing_document_ids"] = missing_document_ids

        result["complete"] = complete
        return result

    # --- lazy/DISCOVER-driven extraction --------------------------------------------------

    def discover_evidence(self, query: str, limit: int = 15) -> dict[str, Any]:
        """Full-corpus, LLM-free retrieval: finds which documents are relevant to
        `query` using the same lexical+semantic index that covers every one of the
        corpus's evidence fragments, regardless of whether they've been semantically
        extracted yet. Returns candidate document_ids ranked by match strength - this
        is the "which documents does this hop actually need" signal, computed
        deterministically from RapidFuzz/embeddings, not guessed by an LLM. A hard cap
        (`max_discover_limit`, set at ToolDispatcher construction) wins over whatever
        limit the planner requested, as a cost safety valve independent of planner behavior.
        """
        if self._max_discover_limit is not None:
            limit = min(limit, self._max_discover_limit)
        results = self.pipeline.search_evidence(query, limit=max(limit * 4, 40))
        document_ids: list[str] = []
        seen: set[str] = set()
        for item in results:
            doc_id = (item.get("metadata") or {}).get("document_id")
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                document_ids.append(doc_id)
            if len(document_ids) >= limit:
                break
        return {"document_ids": document_ids, "count": len(document_ids)}

    def extract_documents(self, document_ids: list[str], batch_size: int = 30) -> dict[str, Any]:
        """The only tool that spends LLM calls. Idempotent: documents already extracted
        earlier in this run (by this question or an earlier one - the world model is
        shared across a whole benchmark run) are skipped for free, and the LLM response
        cache means even documents extracted in a PRIOR run cost nothing to redo. Newly
        extracted entities/relationships/mentions/attributes are persisted immediately
        so subsequent tool calls in the same hop see them.
        """
        newly_extracted = self.world_model.ensure_extracted(
            document_ids,
            self._evidence_by_document,
            documents_by_id=self.pipeline.corpus.documents_by_id,
            batch_size=batch_size,
        )
        if newly_extracted:
            self.world_model.persist(self.pipeline.graph_store, self.pipeline.duckdb_store)
        return {
            "requested_document_ids": document_ids,
            "newly_extracted_document_ids": newly_extracted,
            "already_covered_count": len(document_ids) - len(newly_extracted),
            "entities_total": len(self.world_model.canonical_entities),
            "relationships_total": len(self.world_model.relationships),
        }

    def evidence_text(self, evidence_id: str) -> dict[str, Any]:
        evidence = self.pipeline.get_evidence(evidence_id)
        if evidence is None:
            return {"found": False}
        return {
            "found": True,
            "evidence_id": evidence_id,
            "document_id": evidence.document_id,
            "text": evidence.content.text or str(evidence.content.raw_value or ""),
            "location": evidence.location.model_dump(mode="json"),
        }
