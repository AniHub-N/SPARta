from __future__ import annotations

import pytest

from jaw_ingest.entity_resolution import EntityResolver, ResolverConfig
from jaw_ingest.semantic_schemas import AssertionProvenance, CanonicalEntity, EntityMention


def _mention(text: str, entity_type: str = "organization") -> EntityMention:
    return EntityMention(
        mention_id=f"m-{text}",
        mention_text=text,
        entity_type=entity_type,
        document_id="doc1",
        evidence_id="e1",
        extraction_confidence=0.9,
        provenance=AssertionProvenance(evidence_id="e1", document_id="doc1"),
    )


def _entity(entity_id: str, name: str, entity_type: str = "organization", aliases: list[str] | None = None) -> CanonicalEntity:
    return CanonicalEntity(
        entity_id=entity_id,
        entity_type=entity_type,
        canonical_name=name,
        aliases=aliases or [],
        mention_ids=[],
        resolution_status="resolved",
        resolution_confidence=1.0,
    )


def test_resolver_config_requires_weights_summing_to_one() -> None:
    with pytest.raises(ValueError):
        ResolverConfig(lexical_weight=0.5, semantic_weight=0.5, graph_weight=0.5, type_weight=0.5)


def test_weak_lexical_only_match_does_not_resolve() -> None:
    # Regression test for the old bug: RapidFuzz's 0-100 scale blended directly with a
    # 0-1 threshold meant almost any lexical hit falsely cleared "resolved". Normalized,
    # a weak match with no semantic/graph support must land as unresolved.
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    mention = _mention("Zephyr Holdings Consortium")
    candidates = [_entity("c1", "Completely Different Name Inc")]

    result = resolver.resolve_mention(mention, candidates)

    assert result.status == "unresolved"
    assert result.resolved_entity_id is None


def test_exact_alias_match_resolves() -> None:
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    mention = _mention("NEDA")
    candidates = [
        _entity("c1", "National Expressway Development Authority", aliases=["NEDA", "National Expressway Dev. Authority"])
    ]

    result = resolver.resolve_mention(mention, candidates)

    assert result.status == "resolved"
    assert result.resolved_entity_id == "c1"


def test_different_entity_type_never_resolves_even_with_identical_text() -> None:
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    mention = _mention("Ring Road Pkg-107", entity_type="project")
    candidates = [_entity("c1", "Ring Road Pkg-107", entity_type="person")]

    result = resolver.resolve_mention(mention, candidates)

    # Blocking excludes type-incompatible candidates entirely.
    assert result.candidates == []
    assert result.status == "unresolved"


def test_finalize_ambiguous_when_two_close_scoring_candidates_above_threshold() -> None:
    # Direct test of the threshold/margin logic itself, independent of signal computation.
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    scored = [
        {"entity_id": "c1", "score": 0.70},
        {"entity_id": "c2", "score": 0.68},
    ]

    result = resolver._finalize("query", scored)

    assert result.status == "ambiguous"
    assert result.resolved_entity_id is None


def test_finalize_resolved_when_clear_margin_above_threshold() -> None:
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    scored = [
        {"entity_id": "c1", "score": 0.95},
        {"entity_id": "c2", "score": 0.40},
    ]

    result = resolver._finalize("query", scored)

    assert result.status == "resolved"
    assert result.resolved_entity_id == "c1"


def test_finalize_ambiguous_when_top_candidate_lacks_margin_over_runner_up() -> None:
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    scored = [
        {"entity_id": "c1", "score": 0.90},
        {"entity_id": "c2", "score": 0.85},  # margin 0.05 < resolved_margin 0.10
    ]

    result = resolver._finalize("query", scored)

    assert result.status == "ambiguous"
    assert result.resolved_entity_id is None


def test_unresolved_when_no_candidates() -> None:
    resolver = EntityResolver(lexical=None, semantic=None, graph=None)
    mention = _mention("Some Entity")

    result = resolver.resolve_mention(mention, [])

    assert result.status == "unresolved"
    assert result.candidates == []


def test_free_text_resolve_unresolved_for_unknown_query() -> None:
    class _EmptyLexical:
        def search_fuzzy(self, query, limit):
            return []

    class _EmptySemantic:
        embedding_service = None

        def search_semantic(self, query, limit):
            return []

    class _EmptyGraph:
        def search_nodes(self, query, limit):
            return []

    resolver = EntityResolver(lexical=_EmptyLexical(), semantic=_EmptySemantic(), graph=_EmptyGraph())
    result = resolver.resolve("nonexistent entity 12345")

    assert result.status == "unresolved"
