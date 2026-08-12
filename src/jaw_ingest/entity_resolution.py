from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from rapidfuzz import fuzz

from .semantic_schemas import CanonicalEntity, EntityMention


def _normalize_text(text: str) -> str:
    cleaned = str(text or "").strip().lower()
    cleaned = re.sub(r"[\W_]+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(float(x) * float(y) for x, y in zip(a, b))
    norm_a = sum(float(x) ** 2 for x in a) ** 0.5
    norm_b = sum(float(y) ** 2 for y in b) ** 0.5
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class ResolverConfig:
    """Weights and thresholds for entity resolution. All signals are normalized to [0, 1]
    before combination - never mix raw RapidFuzz (0-100) or raw cosine (-1..1) scores directly.
    """

    lexical_weight: float = 0.40
    semantic_weight: float = 0.40
    graph_weight: float = 0.10
    type_weight: float = 0.10
    resolved_threshold: float = 0.80
    resolved_margin: float = 0.10
    ambiguous_threshold: float = 0.55

    def __post_init__(self) -> None:
        total = self.lexical_weight + self.semantic_weight + self.graph_weight + self.type_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(f"ResolverConfig weights must sum to 1.0, got {total}")


class EntityResolutionResult:
    def __init__(self, query: str, status: str, resolved_entity_id: str | None, candidates: list[dict[str, Any]]) -> None:
        self.query = query
        self.status = status
        self.resolved_entity_id = resolved_entity_id
        self.candidates = candidates


class EntityResolver:
    """Resolves entity mentions against known candidates using normalized, weighted signals.

    Two entry points:
      - resolve(query): free-text search over the indexed corpus (lexical/semantic/graph retrievers),
        preserved for the existing hybrid-retrieval/search use case.
      - resolve_mention(mention, candidates): resolves a structured EntityMention against a
        candidate list of CanonicalEntity objects - the entry point the world-model builder uses.
    """

    def __init__(self, lexical: Any, semantic: Any, graph: Any, config: ResolverConfig | None = None) -> None:
        self.lexical = lexical
        self.semantic = semantic
        self.graph = graph
        self.config = config or ResolverConfig()

    # --- free-text search (kept for retrieval/hybrid-search compatibility) -----------

    def resolve(self, query: str, limit: int = 10) -> EntityResolutionResult:
        lexical_results = self.lexical.search_fuzzy(query, limit) if self.lexical else []
        semantic_results = self.semantic.search_semantic(query, limit) if self.semantic else []
        graph_results = self.graph.search_nodes(query, limit) if self.graph else []

        combined: dict[str, dict[str, Any]] = {}
        for result in lexical_results:
            key = f"lexical:{result['text']}:{result['source']}"
            combined[key] = {
                "name": result["text"],
                "source": "lexical",
                "lexical_score": float(result["score"]) / 100.0,
                "semantic_score": 0.0,
                "graph_score": 0.0,
                "metadata": result["metadata"],
            }
        for result in semantic_results:
            key = f"semantic:{result['text']}"
            entry = combined.get(key)
            normalized_semantic = (float(result["score"]) + 1.0) / 2.0
            if entry:
                entry["semantic_score"] = normalized_semantic
            else:
                combined[key] = {
                    "name": result["text"],
                    "source": "semantic",
                    "lexical_score": 0.0,
                    "semantic_score": normalized_semantic,
                    "graph_score": 0.0,
                    "metadata": result["metadata"],
                }
        for result in graph_results:
            key = f"graph:{result['entity_id']}"
            combined[key] = {
                "name": result["name"],
                "source": "graph",
                "lexical_score": 0.0,
                "semantic_score": 0.0,
                "graph_score": float(result["score"]) / 100.0,
                "metadata": result["metadata"],
                "entity_id": result["entity_id"],
            }

        cfg = self.config
        candidates = []
        for entry in combined.values():
            # Free-text search has no direct type signal; treat type compatibility as neutral (0.5).
            score = (
                cfg.lexical_weight * entry.get("lexical_score", 0.0)
                + cfg.semantic_weight * entry.get("semantic_score", 0.0)
                + cfg.graph_weight * entry.get("graph_score", 0.0)
                + cfg.type_weight * 0.5
            )
            entry["score"] = float(score)
            candidates.append(entry)

        candidates.sort(key=lambda item: item["score"], reverse=True)
        return self._finalize(query, candidates)

    # --- structured mention resolution -----------------------------------------------

    def resolve_mention(self, mention: EntityMention, candidates: list[CanonicalEntity]) -> EntityResolutionResult:
        blocked = [c for c in candidates if self._type_compatible(mention.entity_type, c.entity_type)]

        mention_vector: list[float] | None = None
        if self.semantic is not None and getattr(self.semantic, "embedding_service", None) is not None:
            mention_vector = self.semantic.embedding_service.embed_texts([mention.mention_text])[0]

        scored: list[dict[str, Any]] = []
        for candidate in blocked:
            lexical = self._best_lexical_score(mention.mention_text, candidate)
            semantic = self._best_semantic_score(mention_vector, candidate)
            graph = self._graph_score(mention.mention_text, candidate)
            type_score = 1.0 if mention.entity_type == candidate.entity_type else 0.5

            cfg = self.config
            score = (
                cfg.lexical_weight * lexical
                + cfg.semantic_weight * semantic
                + cfg.graph_weight * graph
                + cfg.type_weight * type_score
            )
            if self._is_alias_exact_match(mention.mention_text, candidate):
                score = 1.0

            scored.append(
                {
                    "entity_id": candidate.entity_id,
                    "name": candidate.canonical_name,
                    "lexical_score": lexical,
                    "semantic_score": semantic,
                    "graph_score": graph,
                    "type_score": type_score,
                    "score": float(score),
                }
            )

        scored.sort(key=lambda item: item["score"], reverse=True)
        return self._finalize(mention.mention_text, scored)

    # --- shared helpers ----------------------------------------------------------------

    def _finalize(self, query: str, candidates: list[dict[str, Any]]) -> EntityResolutionResult:
        if not candidates:
            return EntityResolutionResult(query, "unresolved", None, [])

        cfg = self.config
        top = candidates[0]
        second = candidates[1] if len(candidates) > 1 else None
        top_score = top["score"]
        second_score = second["score"] if second else None

        if top_score >= cfg.resolved_threshold and (second_score is None or top_score - second_score >= cfg.resolved_margin):
            status = "resolved"
        elif top_score >= cfg.ambiguous_threshold:
            status = "ambiguous"
        else:
            status = "unresolved"

        resolved_entity_id = top.get("entity_id") if status == "resolved" else None
        return EntityResolutionResult(query, status, resolved_entity_id, candidates)

    @staticmethod
    def _type_compatible(mention_type: str | None, candidate_type: str | None) -> bool:
        if not mention_type or not candidate_type:
            return True
        return _normalize_text(mention_type) == _normalize_text(candidate_type)

    @staticmethod
    def _best_lexical_score(mention_text: str, candidate: CanonicalEntity) -> float:
        normalized_mention = _normalize_text(mention_text)
        names = [candidate.canonical_name, *candidate.aliases]
        best = 0.0
        for name in names:
            score = fuzz.token_sort_ratio(normalized_mention, _normalize_text(name)) / 100.0
            best = max(best, score)
        return best

    def _best_semantic_score(self, mention_vector: list[float] | None, candidate: CanonicalEntity) -> float:
        if mention_vector is None or self.semantic is None:
            return 0.0
        embedding_service = getattr(self.semantic, "embedding_service", None)
        if embedding_service is None:
            return 0.0
        names = [candidate.canonical_name, *candidate.aliases]
        best = 0.0
        for name in names:
            candidate_vector = embedding_service.embed_texts([name])[0]
            cosine = _cosine(mention_vector, candidate_vector)
            normalized = (cosine + 1.0) / 2.0
            best = max(best, normalized)
        return best

    def _graph_score(self, mention_text: str, candidate: CanonicalEntity) -> float:
        if self.graph is None:
            return 0.0
        results = self.graph.search_nodes(mention_text, limit=5)
        for result in results:
            if result.get("entity_id") == candidate.entity_id:
                return float(result["score"]) / 100.0
        return 0.0

    @staticmethod
    def _is_alias_exact_match(mention_text: str, candidate: CanonicalEntity) -> bool:
        normalized_mention = _normalize_text(mention_text)
        names = [candidate.canonical_name, *candidate.aliases]
        return any(_normalize_text(name) == normalized_mention for name in names)
