from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import networkx as nx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams
from rapidfuzz import fuzz, process
from sentence_transformers import SentenceTransformer
import torch

from .duckdb_store import DuckDBStore
from .entity_resolution import EntityResolutionResult, EntityResolver
from .evidence import DocumentRecord, Evidence, Fact, stable_hex
from .retrieval_index import compute_corpus_fingerprint
from .semantic_schemas import CanonicalEntity, Relationship

logger = logging.getLogger(__name__)


def _normalize_text(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    cleaned = re.sub(r"[\W_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _serialize_value(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        return str(value)


class EvidenceCorpus:
    def __init__(self, documents: list[DocumentRecord], evidence: list[Evidence], facts: list[Fact]) -> None:
        self.documents = documents
        self.evidence = evidence
        self.facts = facts
        self.documents_by_id = {doc.document_id: doc for doc in documents}
        self.evidence_by_id = {item.evidence_id: item for item in evidence}
        self.facts_by_id = {fact.fact_id: fact for fact in facts}

    @classmethod
    def from_evidence_root(cls, root: Path) -> "EvidenceCorpus":
        documents = cls._load_jsonl(root / "documents.jsonl", DocumentRecord)
        evidence = cls._load_jsonl(root / "evidence.jsonl", Evidence)
        facts = cls._load_jsonl(root / "facts.jsonl", Fact)
        return cls(documents=documents, evidence=evidence, facts=facts)

    @staticmethod
    def _load_jsonl(path: Path, model_type: type) -> list[Any]:
        items: list[Any] = []
        if not path.exists():
            return items
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                data = json.loads(line)
                items.append(model_type.model_validate(data))
        return items

    def ingest_into_duckdb(self, store: DuckDBStore) -> None:
        store.ingest_documents(self.documents)
        store.ingest_evidence(self.evidence)
        store.ingest_facts(self.facts)

    def evidence_texts(self) -> list[tuple[str, Evidence]]:
        items: list[tuple[str, Evidence]] = []
        for evidence in self.evidence:
            text = evidence.content.text or str(evidence.content.raw_value or "")
            if text:
                items.append((text, evidence))
        return items

    def fact_texts(self) -> list[tuple[str, Fact]]:
        items: list[tuple[str, Fact]] = []
        for fact in self.facts:
            raw = str(fact.raw_value or "")
            candidate = f"{fact.predicate}: {raw}"
            items.append((candidate, fact))
        return items


@dataclass(frozen=True)
class GraphEntity:
    entity_id: str
    name: str
    entity_type: str
    metadata: dict[str, Any]


class GraphStore:
    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()

    def _entity_id(self, entity_type: str, name: str) -> str:
        return stable_hex("entity", entity_type, name)

    def add_document(self, document: DocumentRecord) -> str:
        entity_id = self._entity_id("document", document.document_id)
        self.graph.add_node(
            entity_id,
            name=document.document_id,
            type="document",
            metadata={
                "filename": document.filename,
                "source_path": str(document.source_path),
                "document_type": document.document_type,
            },
        )
        return entity_id

    def add_entity(self, name: str, entity_type: str, metadata: dict[str, Any] | None = None) -> str:
        entity_id = self._entity_id(entity_type, name)
        self.graph.add_node(
            entity_id,
            name=name,
            type=entity_type,
            metadata=metadata or {},
        )
        return entity_id

    def add_relationship(
        self,
        source_entity_id: str,
        target_entity_id: str,
        relation_type: str,
        evidence_id: str | None = None,
        confidence: float | None = None,
        provenance: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        relationship_id = stable_hex(source_entity_id, target_entity_id, relation_type, evidence_id or "")
        self.graph.add_edge(
            source_entity_id,
            target_entity_id,
            key=relationship_id,
            relation_type=relation_type,
            evidence_id=evidence_id,
            confidence=confidence,
            provenance=provenance or {},
            metadata=metadata or {},
        )
        return relationship_id

    def build_semantic_world(self, entities: list[CanonicalEntity], relationships: list[Relationship]) -> None:
        """Populate the graph with the resolved semantic world model: canonical entities as
        nodes (keyed by their own stable entity_id) and relationships as edges. Distinct from
        build_from_corpus, which builds the coarser document/predicate/value view.
        """
        for entity in entities:
            self.graph.add_node(
                entity.entity_id,
                name=entity.canonical_name,
                type=entity.entity_type,
                metadata={
                    "aliases": entity.aliases,
                    "mention_ids": entity.mention_ids,
                    "resolution_status": entity.resolution_status,
                    "resolution_confidence": entity.resolution_confidence,
                    **entity.metadata,
                },
            )
        for relationship in relationships:
            self.graph.add_edge(
                relationship.subject_entity_id,
                relationship.object_entity_id,
                key=relationship.relationship_id,
                relation_type=relationship.predicate,
                evidence_id=relationship.evidence_id,
                confidence=relationship.confidence,
                provenance=relationship.provenance,
                metadata={},
            )

    def build_from_corpus(self, corpus: EvidenceCorpus) -> None:
        for document in corpus.documents:
            self.add_document(document)
        for fact in corpus.facts:
            doc_id = self._entity_id("document", fact.document_id)
            predicate_id = self.add_entity(fact.predicate, "predicate", {"source": "fact"})
            value_name = str(fact.normalized_value if fact.normalized_value is not None else fact.raw_value)
            value_id = self.add_entity(value_name, "value", {"predicate": fact.predicate})
            self.add_relationship(doc_id, predicate_id, "contains_predicate", fact.evidence_id, fact.normalization_confidence, fact.provenance.model_dump(mode="json"), {})
            self.add_relationship(predicate_id, value_id, fact.predicate, fact.evidence_id, fact.normalization_confidence, fact.provenance.model_dump(mode="json"), {})
            if fact.subject_mention:
                subject_id = self.add_entity(fact.subject_mention, "subject", {"source": "fact"})
                self.add_relationship(subject_id, value_id, "mentions", fact.evidence_id, fact.normalization_confidence, fact.provenance.model_dump(mode="json"), {})

    def search_nodes(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        normalized = _normalize_text(query)
        candidates: list[dict[str, Any]] = []
        for node_id, data in self.graph.nodes(data=True):
            score = fuzz.token_sort_ratio(normalized, _normalize_text(data.get("name", "")))
            if score > 0:
                candidates.append({"entity_id": node_id, "name": data.get("name"), "type": data.get("type"), "score": score, "metadata": data.get("metadata", {})})
        candidates.sort(key=lambda item: item["score"], reverse=True)
        return candidates[:limit]

    def traverse(self, start_entity_id: str, depth: int = 2) -> list[str]:
        visited = {start_entity_id}
        frontier = [start_entity_id]
        for _ in range(depth):
            next_frontier: list[str] = []
            for node_id in frontier:
                for neighbor in self.graph.successors(node_id):
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.append(neighbor)
            frontier = next_frontier
        return list(visited)

    def find_path(self, source_entity_id: str, target_entity_id: str) -> list[str] | None:
        try:
            return nx.shortest_path(self.graph, source_entity_id, target_entity_id)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def entity_count(self) -> int:
        return self.graph.number_of_nodes()

    def relationship_count(self) -> int:
        return self.graph.number_of_edges()


class LexicalRetriever:
    def __init__(self) -> None:
        self.candidates: list[dict[str, Any]] = []
        self.choice_texts: list[str] = []
        self.choice_map: dict[str, list[dict[str, Any]]] = {}

    @staticmethod
    def _normalize(value: str) -> str:
        return _normalize_text(value)

    def add_candidate(self, text: str, source: str, metadata: dict[str, Any]) -> None:
        normalized = self._normalize(text)
        if not normalized:
            return
        self.candidates.append({"text": text, "normalized": normalized, "source": source, "metadata": metadata})
        self.choice_texts.append(normalized)
        self.choice_map.setdefault(normalized, []).append({"text": text, "source": source, "metadata": metadata})

    def index_corpus(self, corpus: EvidenceCorpus) -> None:
        for evidence_text, evidence in corpus.evidence_texts():
            self.add_candidate(evidence_text, "evidence", {"evidence_id": evidence.evidence_id, "document_id": evidence.document_id})
        for fact_text, fact in corpus.fact_texts():
            self.add_candidate(fact_text, "fact", {"fact_id": fact.fact_id, "predicate": fact.predicate, "document_id": fact.document_id})

    def search_exact(self, query: str) -> list[dict[str, Any]]:
        normalized = self._normalize(query)
        return self.choice_map.get(normalized, [])

    def search_fuzzy(self, query: str, limit: int = 10, score_cutoff: int = 30) -> list[dict[str, Any]]:
        normalized = self._normalize(query)
        if not normalized:
            return []
        matches = process.extract(normalized, self.choice_texts, scorer=fuzz.token_sort_ratio, limit=limit, score_cutoff=score_cutoff)
        results: list[dict[str, Any]] = []
        for matched_text, score, _ in matches:
            for candidate in self.choice_map.get(matched_text, []):
                result = {
                    "text": candidate["text"],
                    "source": candidate["source"],
                    "metadata": candidate["metadata"],
                    "score": score,
                }
                results.append(result)
        return results[:limit]


class EmbeddingService:
    DEFAULT_DIMENSION = 384

    def __init__(self, model_name: str = "all-MiniLM-L6-v2", device: str | None = None, strict: bool = False) -> None:
        self.model_name = model_name
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        self.model = None
        self._dimension = self.DEFAULT_DIMENSION
        self.strict = strict
        try:
            model = SentenceTransformer(model_name, device=self.device)
            self.model = model
            if hasattr(model, "get_embedding_dimension"):
                self._dimension = model.get_embedding_dimension()
            else:
                self._dimension = model.get_sentence_embedding_dimension()
        except Exception as exc:
            logger = logging.getLogger(__name__)
            msg = f"Embedding model load failed for '{model_name}': {exc}"
            if strict:
                logger.error(msg)
                raise
            logger.warning("%s; using fallback deterministic embeddings.", msg)
            self.model = None
            self._dimension = self.DEFAULT_DIMENSION

    @property
    def dimension(self) -> int:
        return self._dimension

    def _deterministic_vector(self, text: str) -> list[float]:
        full_bytes = b""
        counter = 0
        while len(full_bytes) < self._dimension * 4:
            message = f"{text}:{counter}".encode("utf-8")
            full_bytes += hashlib.sha256(message).digest()
            counter += 1
        values: list[float] = []
        for idx in range(self._dimension):
            chunk = full_bytes[idx * 4 : idx * 4 + 4]
            integer = int.from_bytes(chunk, "big", signed=False)
            values.append((integer / float(2**32 - 1)) * 2.0 - 1.0)
        norm = math.sqrt(sum(v * v for v in values)) or 1.0
        return [float(v / norm) for v in values]

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        clean_texts = [str(text or "") for text in texts]
        if self.model is not None:
            try:
                embeddings = self.model.encode(clean_texts, convert_to_numpy=False, show_progress_bar=False, device=self.device)
                return [vector.tolist() if hasattr(vector, "tolist") else list(vector) for vector in embeddings]
            except Exception as exc:
                logging.getLogger(__name__).warning("Embedding model encode failed (%s); using fallback embeddings.", exc)
        return [self._deterministic_vector(text) for text in clean_texts]


class SemanticRetriever:
    def __init__(self, embedding_service: EmbeddingService) -> None:
        self.embedding_service = embedding_service
        self.items: list[dict[str, Any]] = []
        self.embeddings: list[list[float]] = []

    def index_corpus(self, corpus: EvidenceCorpus) -> None:
        texts = []
        metadatas = []
        for evidence_text, evidence in corpus.evidence_texts():
            texts.append(evidence_text)
            metadatas.append({"source": "evidence", "evidence_id": evidence.evidence_id, "document_id": evidence.document_id})
        for fact_text, fact in corpus.fact_texts():
            texts.append(fact_text)
            metadatas.append({"source": "fact", "fact_id": fact.fact_id, "document_id": fact.document_id})
        if not texts:
            return
        self.embeddings = [vector.tolist() if hasattr(vector, 'tolist') else list(vector) for vector in self.embedding_service.embed_texts(texts)]
        self.items = [{"text": text, "metadata": metadata, "index": idx} for idx, (text, metadata) in enumerate(zip(texts, metadatas))]

    def search_semantic(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        if not self.items or not query:
            return []
        query_vector = self.embedding_service.embed_texts([query])[0]
        scores: list[tuple[int, float]] = []
        for idx, embedding in enumerate(self.embeddings):
            dot = sum(float(a) * float(b) for a, b in zip(query_vector, embedding))
            norm_a = math.sqrt(sum(float(a) ** 2 for a in query_vector))
            norm_b = math.sqrt(sum(float(b) ** 2 for b in embedding))
            score = dot / (norm_a * norm_b + 1e-9)
            scores.append((idx, score))
        scores.sort(key=lambda item: item[1], reverse=True)
        results = []
        for idx, score in scores[:limit]:
            item = self.items[idx]
            results.append({"text": item["text"], "metadata": item["metadata"], "score": float(score)})
        return results


class QdrantStore:
    def __init__(self, collection_name: str = "evidence_embeddings", location: str | None = None, vector_size: int = 384) -> None:
        self.collection_name = collection_name
        self.location = location or ":memory:"
        self.vector_size = vector_size
        if self.location == ":memory:" or self.location.startswith(("http://", "https://", "grpc://")):
            self.client = QdrantClient(location=self.location)
        else:
            # A plain filesystem path, not ":memory:" or a server URL: qdrant-client's
            # local persistent (on-disk) mode needs the `path=` kwarg specifically -
            # passing a filesystem path via `location=` would be misinterpreted as a
            # URL/host and fail. This is what actually persists vectors across processes.
            Path(self.location).mkdir(parents=True, exist_ok=True)
            self.client = QdrantClient(path=self.location)
        self._ensure_collection()

    def _ensure_collection(self, reset: bool = False) -> None:
        exists = self.client.collection_exists(self.collection_name)
        if exists and reset:
            self.client.recreate_collection(self.collection_name, vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE))
        elif not exists:
            self.client.create_collection(self.collection_name, vectors_config=VectorParams(size=self.vector_size, distance=Distance.COSINE))
        # exists and not reset: leave the existing collection (and its points) untouched.

    def _normalize_point_id(self, item_id: str | int | Any) -> str | int:
        if isinstance(item_id, int):
            return item_id
        item_str = str(item_id or "")
        try:
            uuid_obj = uuid.UUID(item_str)
            return str(uuid_obj)
        except (ValueError, TypeError):
            return str(uuid.uuid5(uuid.NAMESPACE_URL, item_str))

    def upsert_embeddings(self, embeddings: list[tuple[str, list[float], dict[str, Any]]]) -> None:
        points = []
        for item_id, vector, payload in embeddings:
            normalized_id = self._normalize_point_id(item_id)
            wrapped_payload = {**(payload or {}), "original_id": item_id}
            points.append(PointStruct(id=normalized_id, vector=vector, payload=wrapped_payload))
        if points:
            self.client.upsert(self.collection_name, points)

    def search(self, query_vector: list[float], limit: int = 10) -> list[dict[str, Any]]:
        response = self.client.query_points(self.collection_name, query_vector, limit=limit, with_payload=True)
        rows: list[dict[str, Any]] = []
        # response may be a QueryResponse-like object, a list, or dict; normalize generically
        results = None
        if response is None:
            return rows
        if hasattr(response, "result"):
            results = response.result
        elif hasattr(response, "to_dict"):
            try:
                results = response.to_dict().get("result") or response.to_dict().get("hits")
            except Exception:
                results = None
        elif isinstance(response, dict):
            results = response.get("result") or response.get("hits") or response.get("points") or []
        else:
            results = response

        if not results:
            return rows

        for hit in results:
            try:
                # dict-like
                if isinstance(hit, dict):
                    hid = hit.get("id") or hit.get("point_id") or hit.get("payload", {}).get("original_id")
                    payload = hit.get("payload") or hit.get("payload", {})
                    score = hit.get("score") or hit.get("payload", {}).get("score") or hit.get("distance")
                else:
                    # object-like with attributes
                    hid = getattr(hit, "id", None) or getattr(hit, "point_id", None)
                    payload = getattr(hit, "payload", None)
                    score = getattr(hit, "score", None) or getattr(hit, "distance", None)
                # attempt to extract original_id if wrapped
                if isinstance(payload, dict) and "original_id" in payload:
                    original = payload.get("original_id")
                else:
                    original = None
                rows.append({"id": original or hid, "payload": payload, "score": float(score) if score is not None else None})
            except Exception:
                logger.warning("Failed to parse qdrant hit: %s", hit)
        return rows

    def count(self) -> int:
        result = self.client.count(self.collection_name)
        if hasattr(result, "count"):
            return int(result.count)
        try:
            return int(result)
        except Exception:
            return 0

    def scroll_all(self) -> list[tuple[str, list[float], dict[str, Any]]]:
        """Fetches every point back (id, vector, payload) - used to repopulate an
        in-memory SemanticRetriever from an already-persisted Qdrant collection
        WITHOUT re-running the embedding model, when reusing a persisted retrieval
        index instead of rebuilding it from scratch.
        """
        results: list[tuple[str, list[float], dict[str, Any]]] = []
        offset = None
        while True:
            points, next_offset = self.client.scroll(
                self.collection_name, limit=256, offset=offset, with_vectors=True, with_payload=True
            )
            for point in points:
                payload = point.payload or {}
                original_id = payload.get("original_id", point.id)
                vector = point.vector
                if vector is not None and not isinstance(vector, list):
                    vector = list(vector)
                results.append((original_id, vector or [], payload))
            if not next_offset:
                break
            offset = next_offset
        return results

    def close(self) -> None:
        """Releases the client (and, for local on-disk mode, its file lock) - needed
        before another process/connection can open the same on-disk path.
        """
        self.client.close()


class HybridRetriever:
    def __init__(self, lexical: LexicalRetriever, semantic: SemanticRetriever, graph: GraphStore) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.graph = graph

    def hybrid_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        lexical_results = self.lexical.search_fuzzy(query, limit)
        semantic_results = self.semantic.search_semantic(query, limit)
        graph_results = self.graph.search_nodes(query, limit)

        combined: dict[str, dict[str, Any]] = {}
        for result in lexical_results:
            key = f"lexical:{result['text']}:{result['source']}"
            combined[key] = {"source": "lexical", "text": result["text"], "score": float(result["score"]), "metadata": result["metadata"]}
        for result in semantic_results:
            key = f"semantic:{result['text']}"
            existing = combined.get(key)
            if existing:
                existing["score"] = max(existing["score"], float(result["score"]))
            else:
                combined[key] = {"source": "semantic", "text": result["text"], "score": float(result["score"]), "metadata": result["metadata"]}
        for result in graph_results:
            key = f"graph:{result['entity_id']}"
            combined[key] = {"source": "graph", "text": result["name"], "score": float(result["score"]), "metadata": result["metadata"], "entity_id": result["entity_id"]}

        results = sorted(combined.values(), key=lambda item: item["score"], reverse=True)
        return results[:limit]


class DoclingAdapter:
    """Optional coverage cross-check against docling. NOT the primary extractor - PyMuPDF
    (extraction.py) is, and it stays authoritative.

    Caveat when reading compare_text_coverage() numbers: ~half of this corpus's completion
    certificates embed the value font with no usable character map. Any extractor built on
    pdfminer - docling's text backend included - silently drops that text, so docling will
    show low coverage on exactly those documents. That is a docling/pdfminer limitation, not
    missing data: PyMuPDF recovers the text via its own font fallback. Do not "fix" low
    coverage by switching the primary path away from PyMuPDF.
    """

    def __init__(self) -> None:
        # Import lazily and be tolerant in test environments where docling
        # isn't installed. Tests should still be able to run core pipeline
        # functionality without docling being present.
        try:
            from docling.document_extractor import DocumentExtractor  # type: ignore

            self.extractor = None
            try:
                self.extractor = DocumentExtractor()
            except Exception as exc:
                logger.warning("Docling extractor init failed: %s", exc)
        except Exception:
            self.extractor = None
            logger.info("Docling not installed; DoclingAdapter running in no-op mode.")

    def extract_pdf(self, source_path: Path) -> dict[str, Any]:
        if self.extractor is None:
            raise RuntimeError("Docling adapter is unavailable in this environment")
        result = self.extractor.extract(source_path, template="json_docling")
        return result.model_dump(mode="json")

    def compare_text_coverage(self, baseline_texts: list[str], docling_json: dict[str, Any]) -> dict[str, Any]:
        baseline = " ".join(str(text or "") for text in baseline_texts)
        docling_text = self._extract_text_from_docling(docling_json)
        baseline_tokens = set(_normalize_text(baseline).split())
        docling_tokens = set(_normalize_text(docling_text).split())
        overlap = baseline_tokens.intersection(docling_tokens)
        coverage = float(len(overlap)) / float(max(len(baseline_tokens), 1))
        return {
            "baseline_tokens": len(baseline_tokens),
            "docling_tokens": len(docling_tokens),
            "overlap_tokens": len(overlap),
            "coverage_ratio": coverage,
        }

    def _extract_text_from_docling(self, docling_json: dict[str, Any]) -> str:
        pages = docling_json.get("pages", [])
        texts: list[str] = []
        for page in pages:
            page_text = page.get("text", "")
            if page_text:
                texts.append(page_text)
            for block in page.get("blocks", []):
                if isinstance(block, dict) and block.get("text"):
                    texts.append(block.get("text"))
        return " ".join(texts)


class MCPToolRegistry:
    def __init__(self, pipeline: "Chunk4Pipeline") -> None:
        # mcp is an optional runtime component; in test/dev environments
        # it may not be installed. Fall back to a lightweight shim so the
        # pipeline can still expose a tool registry without requiring mcp.
        try:
            from mcp import Tool  # type: ignore

            self.Tool = Tool
        except Exception:
            # lightweight shim
            class _Tool:
                def __init__(self, name: str, title: str, description: str, inputSchema: dict, outputSchema: dict) -> None:
                    self.name = name
                    self.title = title
                    self.description = description

            self.Tool = _Tool

        self.pipeline = pipeline

    def tools(self) -> list[Any]:
        return [
            self.Tool(
                name="search_evidence",
                title="Search Evidence",
                description="Search evidence by lexical and semantic filters.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_evidence",
                title="Get Evidence",
                description="Retrieve a single evidence item by evidence ID.",
                inputSchema={"type": "object", "properties": {"evidence_id": {"type": "string"}}, "required": ["evidence_id"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="search_entities",
                title="Search Entities",
                description="Search entity nodes in the graph.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="resolve_entity",
                title="Resolve Entity",
                description="Resolve an entity mention using lexical, semantic, and graph context.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_entity",
                title="Get Entity",
                description="Retrieve entity node metadata by ID.",
                inputSchema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_relationships",
                title="Get Relationships",
                description="Retrieve graph relationships associated with an entity.",
                inputSchema={"type": "object", "properties": {"entity_id": {"type": "string"}}, "required": ["entity_id"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="traverse_graph",
                title="Traverse Graph",
                description="Traverse the graph around an entity.",
                inputSchema={"type": "object", "properties": {"entity_id": {"type": "string"}, "depth": {"type": "integer"}}, "required": ["entity_id"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="find_path",
                title="Find Graph Path",
                description="Find a path between two entities in the graph.",
                inputSchema={"type": "object", "properties": {"source_entity_id": {"type": "string"}, "target_entity_id": {"type": "string"}}, "required": ["source_entity_id", "target_entity_id"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="semantic_search",
                title="Semantic Search",
                description="Search by semantic similarity using embeddings.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="lexical_search",
                title="Lexical Search",
                description="Search by exact and fuzzy lexical matching.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="hybrid_search",
                title="Hybrid Search",
                description="Combine lexical, semantic, and graph search results.",
                inputSchema={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_facts",
                title="Get Facts",
                description="Retrieve facts from the evidence corpus.",
                inputSchema={"type": "object", "properties": {"predicate": {"type": "string"}}},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="query_duckdb",
                title="Query DuckDB",
                description="Run a SQL query against the DuckDB store.",
                inputSchema={"type": "object", "properties": {"sql": {"type": "string"}}, "required": ["sql"]},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_provenance",
                title="Get Provenance",
                description="Retrieve provenance metadata for evidence or facts.",
                inputSchema={"type": "object", "properties": {"evidence_id": {"type": "string"}, "fact_id": {"type": "string"}}},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="check_evidence_support",
                title="Check Evidence Support",
                description="Check whether evidence supports a candidate fact or entity.",
                inputSchema={"type": "object", "properties": {"evidence_id": {"type": "string"}, "query": {"type": "string"}}},
                outputSchema={"type": "object"},
            ),
            self.Tool(
                name="get_coverage",
                title="Get Coverage",
                description="Return coverage statistics for evidence and entity indexing.",
                inputSchema={"type": "object", "properties": {}},
                outputSchema={"type": "object"},
            ),
        ]


class Chunk4Pipeline:
    def __init__(
        self,
        evidence_root: Path,
        duckdb_path: Path | str = ":memory:",
        qdrant_location: str | None = None,
        model_name: str = "all-MiniLM-L6-v2",
        device: str | None = None,
    ) -> None:
        self.evidence_root = evidence_root
        self.corpus = EvidenceCorpus.from_evidence_root(evidence_root)
        self.duckdb_store = DuckDBStore(duckdb_path)
        self.graph_store = GraphStore()
        self.lexical_retriever = LexicalRetriever()
        # If a BGE-style model is requested, enforce strict loading (no silent fallback)
        strict = True if (model_name and "bge" in model_name.lower()) else False
        self.embedding_service = EmbeddingService(model_name=model_name, device=device, strict=strict)
        self.semantic_retriever = SemanticRetriever(self.embedding_service)
        self.qdrant_store = QdrantStore(collection_name="evidence_embeddings", location=qdrant_location, vector_size=self.embedding_service.dimension)
        self.hybrid_retriever = HybridRetriever(self.lexical_retriever, self.semantic_retriever, self.graph_store)
        self.entity_resolver = EntityResolver(self.lexical_retriever, self.semantic_retriever, self.graph_store)
        self.docling_adapter = DoclingAdapter()
        self.mcp_registry = MCPToolRegistry(self)

    def close(self) -> None:
        """Releases DuckDB/Qdrant resources (and file locks, for on-disk persistence) -
        a short-lived CLI process releases these naturally on exit, but a long-lived
        process (or a test opening the same persisted path more than once) should call
        this explicitly before reopening the same paths elsewhere.
        """
        self.duckdb_store.close()
        self.qdrant_store.close()

    def index(self, force_reindex: bool = False) -> None:
        """Builds (or reuses) the retrieval index for this corpus.

        The lexical index and the coarse document/predicate/value graph are pure
        in-memory Python structures with no ML/IO cost worth avoiding - they're always
        rebuilt fresh in this process, every call, regardless of persistence.

        The expensive part is embedding every evidence/fact fragment. That work is
        skipped entirely when a persisted DuckDB (`duckdb_path`) + Qdrant
        (`qdrant_location`) index already exists AND its recorded corpus fingerprint +
        embedding model match this corpus/model exactly (see retrieval_index.py,
        DuckDBStore.get_index_meta) - in that case Qdrant's already-persisted vectors
        are read back (QdrantStore.scroll_all) to repopulate the in-memory
        SemanticRetriever, and DuckDB's tables are simply reused as-is (not re-inserted).

        `force_reindex=True` always rebuilds from scratch, ignoring any existing index.
        """
        fingerprint = compute_corpus_fingerprint(self.evidence_root)
        existing_meta = self.duckdb_store.get_index_meta()
        reuse = (
            not force_reindex
            and existing_meta is not None
            and existing_meta.get("corpus_fingerprint") == fingerprint
            and existing_meta.get("embedding_model") == self.embedding_service.model_name
        )

        self.graph_store.build_from_corpus(self.corpus)
        self.lexical_retriever.index_corpus(self.corpus)

        if reuse:
            logger.info(
                "Reusing persisted retrieval index (corpus fingerprint and embedding "
                "model match) - skipping DuckDB re-ingest and re-embedding."
            )
            self._reload_semantic_retriever_from_qdrant()
            return

        logger.info(
            "Building retrieval index from scratch (%s).",
            "forced rebuild" if force_reindex else "no valid persisted index found for this corpus",
        )
        self.corpus.ingest_into_duckdb(self.duckdb_store)
        # The ONE embedding computation this method performs - covers evidence AND
        # fact texts together. Both SemanticRetriever's in-memory index and Qdrant's
        # persisted index are populated from these SAME vectors, so nothing is ever
        # embedded twice.
        self.semantic_retriever.index_corpus(self.corpus)

        if self.semantic_retriever.items:
            self.qdrant_store._ensure_collection(reset=True)
            embeddings: list[tuple[str, list[float], dict[str, Any]]] = [
                (
                    item["metadata"].get("evidence_id") or item["metadata"].get("fact_id"),
                    list(vector),
                    {**item["metadata"], "text": item["text"]},
                )
                for item, vector in zip(self.semantic_retriever.items, self.semantic_retriever.embeddings)
            ]
            self.qdrant_store.upsert_embeddings(embeddings)

        self.duckdb_store.set_index_meta(fingerprint, self.embedding_service.model_name)

    def _reload_semantic_retriever_from_qdrant(self) -> None:
        points = self.qdrant_store.scroll_all()
        items = []
        embeddings = []
        for idx, (_point_id, vector, payload) in enumerate(points):
            text = payload.get("text", "")
            metadata = {k: v for k, v in payload.items() if k not in ("text", "original_id")}
            items.append({"text": text, "metadata": metadata, "index": idx})
            embeddings.append(vector)
        self.semantic_retriever.items = items
        self.semantic_retriever.embeddings = embeddings

    def get_toolset(self) -> list[Any]:
        return self.mcp_registry.tools()

    def get_evidence(self, evidence_id: str) -> Evidence | None:
        return self.corpus.evidence_by_id.get(evidence_id)

    def get_fact(self, fact_id: str) -> Fact | None:
        return self.corpus.facts_by_id.get(fact_id)

    def search_evidence(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        lexical = self.lexical_retriever.search_fuzzy(query, limit)
        semantic = self.semantic_retriever.search_semantic(query, limit)
        qdrant_results = self.qdrant_store.search(self.embedding_service.embed_texts([query])[0], limit)
        combined = {f"lex:{i}": {"source": "lexical", "score": item["score"], "metadata": item["metadata"], "text": item["text"]} for i, item in enumerate(lexical)}
        for i, item in enumerate(semantic):
            combined[f"sem:{i}"] = {"source": "semantic", "score": item["score"], "metadata": item["metadata"], "text": item["text"]}
        for i, item in enumerate(qdrant_results):
            combined[f"qdr:{i}"] = {"source": "qdrant", "score": item["score"], "metadata": item.get("payload", {}), "id": item["id"]}
        results = sorted(combined.values(), key=lambda item: item["score"] if item["score"] is not None else -1.0, reverse=True)
        return results[:limit]

    def query_duckdb(self, sql: str, parameters: tuple[Any, ...] | None = None) -> list[dict[str, Any]]:
        return self.duckdb_store.query(sql, parameters)

    def get_provenance(self, evidence_id: str | None = None, fact_id: str | None = None) -> dict[str, Any] | None:
        if evidence_id:
            evidence = self.get_evidence(evidence_id)
            return {"evidence": evidence.model_dump(mode="json")} if evidence else None
        if fact_id:
            fact = self.get_fact(fact_id)
            return {"fact": fact.model_dump(mode="json")} if fact else None
        return None

    def check_evidence_support(self, evidence_id: str, query: str) -> dict[str, Any]:
        evidence = self.get_evidence(evidence_id)
        if evidence is None:
            return {"found": False, "reason": "evidence_not_found"}
        text = evidence.content.text or str(evidence.content.raw_value or "")
        lexical = self.lexical_retriever.search_fuzzy(query, limit=5)
        coverage = any(item["metadata"].get("evidence_id") == evidence_id for item in lexical)
        return {"found": coverage, "evidence_id": evidence_id, "query": query}

    def get_coverage(self) -> dict[str, Any]:
        return {
            "documents": len(self.corpus.documents),
            "evidence": len(self.corpus.evidence),
            "facts": len(self.corpus.facts),
            "duckdb_documents": self.duckdb_store.count("documents"),
            "duckdb_evidence": self.duckdb_store.count("evidence"),
            "duckdb_facts": self.duckdb_store.count("facts"),
            "qdrant_points": self.qdrant_store.count(),
            "graph_nodes": self.graph_store.entity_count(),
            "graph_edges": self.graph_store.relationship_count(),
        }
