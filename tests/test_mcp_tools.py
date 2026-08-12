from __future__ import annotations

from pathlib import Path

import pytest

from jaw_ingest.mcp_tools import ToolDispatcher

from _fixtures import ScenarioBatchExtractionProvider, build_scenario_system


@pytest.fixture(scope="module")
def dispatcher(tmp_path_factory) -> ToolDispatcher:
    tmp_path = tmp_path_factory.mktemp("mcp_tools_scenario")
    system = build_scenario_system(tmp_path)
    return ToolDispatcher(system)


def _entity_id(dispatcher: ToolDispatcher, name: str, entity_type: str) -> str:
    result = dispatcher.call("resolve_entity", query=name, entity_type=entity_type)
    assert result["status"] == "resolved", result
    return result["resolved_entity_id"]


def test_world_model_built_real_entities_and_relationships(dispatcher: ToolDispatcher) -> None:
    assert len(dispatcher.world_model.canonical_entities) >= 4  # Asha Nair, 3 projects, 2 clients
    assert len(dispatcher.world_model.relationships) >= 4
    assert len(dispatcher.world_model.attributes) == 3


def test_resolve_entity_exact_and_unknown(dispatcher: ToolDispatcher) -> None:
    resolved = dispatcher.call("resolve_entity", query="Metro Authority", entity_type="client")
    assert resolved["status"] == "resolved"

    unresolved = dispatcher.call("resolve_entity", query="Totally Fictional Entity Zprq", entity_type="client")
    assert unresolved["status"] == "unresolved"


def test_get_entity_returns_relationships(dispatcher: ToolDispatcher) -> None:
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    entity = dispatcher.call("get_entity", entity_id=alpha_id)
    assert entity["found"] is True
    assert entity["entity_type"] == "project"
    assert len(entity["relationships_out"]) >= 1  # commissioned_by (led is inbound to Alpha, not outbound)


def test_get_entity_unknown_id(dispatcher: ToolDispatcher) -> None:
    assert dispatcher.call("get_entity", entity_id="does-not-exist") == {"found": False}


def test_traverse_graph_out_direction_with_predicate(dispatcher: ToolDispatcher) -> None:
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    result = dispatcher.call("traverse_graph", entity_ids=[alpha_id], predicate="commissioned_by", direction="out")
    metro_id = _entity_id(dispatcher, "Metro Authority", "client")
    assert metro_id in result["neighbor_entity_ids"]
    assert all(edge["evidence_id"] for edge in result["edges"])


def test_traverse_graph_predicate_filter_excludes_unrelated_edges(dispatcher: ToolDispatcher) -> None:
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    result = dispatcher.call("traverse_graph", entity_ids=[alpha_id], predicate="totally_unrelated_predicate_xyz", direction="both")
    assert result["neighbor_entity_ids"] == []


def test_enumerate_population_filters_by_anchor_and_type(dispatcher: ToolDispatcher) -> None:
    metro_id = _entity_id(dispatcher, "Metro Authority", "client")
    result = dispatcher.call(
        "enumerate_population", entity_type="project", predicate="commissioned_by", anchor_entity_id=metro_id, direction="in"
    )
    names = {dispatcher.call("get_entity", entity_id=eid)["canonical_name"] for eid in result["entity_ids"]}
    assert names == {"Bridge Alpha", "Bridge Beta"}  # Gamma belongs to a different client


def test_enumerate_population_without_anchor_returns_all_of_type(dispatcher: ToolDispatcher) -> None:
    result = dispatcher.call("enumerate_population", entity_type="project")
    assert result["count"] == 3


def test_get_attribute_prefers_deterministic_fact_over_llm_attribute(dispatcher: ToolDispatcher) -> None:
    # Bridge Alpha has BOTH a deterministic Fact (150000000, from Chunk 3's normalization)
    # and an LLM-extracted Attribute (100000000, transcribed by the fake provider) for
    # "contract_value" - the Fact must win, since it isn't subject to LLM transcription error.
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    result = dispatcher.call("get_attribute", entity_ids=[alpha_id], predicate="contract value")  # note: space, not underscore
    assert result["source"] == "facts"
    assert result["count"] == 1
    assert result["attributes"][0]["value"] == 150000000.0
    assert result["attributes"][0]["evidence_id"]


def test_get_attribute_falls_back_to_llm_extraction_when_no_fact_exists(dispatcher: ToolDispatcher) -> None:
    # Bridge Beta has no deterministic Fact in this fixture - only the LLM-extracted
    # Attribute - so get_attribute must fall back to it rather than returning nothing.
    beta_id = _entity_id(dispatcher, "Bridge Beta", "project")
    result = dispatcher.call("get_attribute", entity_ids=[beta_id], predicate="contract_value")
    assert result["source"] == "llm_extraction"
    assert result["count"] == 1
    assert result["attributes"][0]["value"] == "200000000"


def test_search_evidence_and_semantic_search_return_lists(dispatcher: ToolDispatcher) -> None:
    search_result = dispatcher.call("search_evidence", query="Bridge Alpha contract value", limit=5)
    assert isinstance(search_result["results"], list)

    semantic_result = dispatcher.call("semantic_search", query="Bridge Alpha contract value", limit=5)
    assert isinstance(semantic_result["results"], list)


def test_query_duckdb_rejects_non_select(dispatcher: ToolDispatcher) -> None:
    result = dispatcher.call("query_duckdb", sql="DELETE FROM entities")
    assert "error" in result


def test_query_duckdb_allows_select(dispatcher: ToolDispatcher) -> None:
    result = dispatcher.call("query_duckdb", sql="SELECT COUNT(*) AS n FROM entities")
    assert result["rows"][0]["n"] >= 4


@pytest.mark.parametrize(
    "operation,values,expected",
    [
        ("sum", [1, 2, 3], 6.0),
        ("avg", [2, 4], 3.0),
        ("min", [3, 1, 2], 1.0),
        ("max", [3, 1, 2], 3.0),
        ("count", [1, 2, 3], 3),
        ("diff", [10, 4], 6.0),
        ("ratio", [10, 4], 2.5),
    ],
)
def test_calculate_deterministic_operations(dispatcher: ToolDispatcher, operation, values, expected) -> None:
    result = dispatcher.call("calculate", operation=operation, values=values)
    assert result["result"] == expected


def test_calculate_unsupported_operation_returns_error(dispatcher: ToolDispatcher) -> None:
    result = dispatcher.call("calculate", operation="not_a_real_op", values=[1, 2])
    assert "error" in result


def test_get_provenance_by_entity_id(dispatcher: ToolDispatcher) -> None:
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    result = dispatcher.call("get_provenance", entity_id=alpha_id)
    assert result["found"] is True
    assert len(result["mentions"]) >= 1
    assert all(m["evidence_id"] for m in result["mentions"])


def test_check_completeness_flags_lexically_ambiguous_projects(dispatcher: ToolDispatcher) -> None:
    # "Bridge Beta"/"Bridge Gamma" are minted after "Bridge Alpha" already exists as a
    # same-typed candidate, and share enough lexical/semantic similarity with it to be
    # flagged ambiguous rather than blindly merged or blindly trusted - this is the
    # resolver behaving correctly (see test_entity_resolution.py), and check_completeness
    # must surface it rather than reporting a false "complete".
    result = dispatcher.call("check_completeness", entity_type="project")
    assert result["count"] == 3
    assert result["unresolved_count"] == 0
    assert result["ambiguous_count"] > 0
    assert result["complete"] is False
    assert result["extraction_failures"] == {}


def test_evidence_text_returns_grounded_text(dispatcher: ToolDispatcher) -> None:
    alpha_id = _entity_id(dispatcher, "Bridge Alpha", "project")
    provenance = dispatcher.call("get_provenance", entity_id=alpha_id)
    evidence_id = provenance["mentions"][0]["evidence_id"]

    result = dispatcher.call("evidence_text", evidence_id=evidence_id)
    assert result["found"] is True
    assert "Bridge Alpha" in result["text"]


def test_unknown_tool_returns_error(dispatcher: ToolDispatcher) -> None:
    result = dispatcher.call("not_a_real_tool")
    assert "error" in result


# --- lazy / DISCOVER-driven extraction ---------------------------------------------------


@pytest.fixture()
def lazy_dispatcher(tmp_path) -> ToolDispatcher:
    system = build_scenario_system(tmp_path, provider=ScenarioBatchExtractionProvider(), lazy=True)
    return ToolDispatcher(system)


def test_lazy_dispatcher_starts_with_empty_world_model(lazy_dispatcher: ToolDispatcher) -> None:
    assert lazy_dispatcher.world_model.canonical_entities == []
    assert lazy_dispatcher.world_model.extracted_document_count == 0


def test_discover_evidence_finds_documents_without_any_llm_call(lazy_dispatcher: ToolDispatcher) -> None:
    result = lazy_dispatcher.call("discover_evidence", query="Bridge Alpha", limit=5)
    assert result["count"] > 0
    assert "PKG-ALPHA" in result["document_ids"]
    # No extraction happened just from discovering - world model still empty.
    assert lazy_dispatcher.world_model.extracted_document_count == 0


def test_extract_documents_populates_world_model_and_is_idempotent(lazy_dispatcher: ToolDispatcher) -> None:
    result = lazy_dispatcher.call("extract_documents", document_ids=["PKG-ALPHA"])
    assert result["newly_extracted_document_ids"] == ["PKG-ALPHA"]
    assert result["entities_total"] > 0
    assert lazy_dispatcher.world_model.extracted_document_count == 1

    # Second call with the same document_id costs nothing new.
    result2 = lazy_dispatcher.call("extract_documents", document_ids=["PKG-ALPHA"])
    assert result2["newly_extracted_document_ids"] == []
    assert result2["already_covered_count"] == 1


def test_extract_documents_persists_to_duckdb() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        system = build_scenario_system(Path(tmp), provider=ScenarioBatchExtractionProvider(), lazy=True)
        dispatcher = ToolDispatcher(system)
        dispatcher.call("extract_documents", document_ids=["PKG-ALPHA"])

        rows = system.pipeline.query_duckdb("SELECT COUNT(*) AS n FROM entities")
        assert rows[0]["n"] > 0


def test_discover_then_resolve_entity_end_to_end(lazy_dispatcher: ToolDispatcher) -> None:
    # RESOLVE_ENTITY must fail before discovery (world model is empty)...
    before = lazy_dispatcher.call("resolve_entity", query="Bridge Alpha", entity_type="project")
    assert before["status"] == "unresolved"

    # ...and succeed after DISCOVER populates the graph from exactly the documents
    # the free retrieval index says are relevant.
    found = lazy_dispatcher.call("discover_evidence", query="Bridge Alpha", limit=5)
    lazy_dispatcher.call("extract_documents", document_ids=found["document_ids"])

    after = lazy_dispatcher.call("resolve_entity", query="Bridge Alpha", entity_type="project")
    assert after["status"] == "resolved"


def test_discover_limit_is_capped_by_max_discover_limit(tmp_path) -> None:
    system = build_scenario_system(tmp_path, provider=ScenarioBatchExtractionProvider(), lazy=True)
    dispatcher = ToolDispatcher(system, max_discover_limit=1)

    result = dispatcher.call("discover_evidence", query="Bridge", limit=10)

    assert result["count"] <= 1


def test_check_completeness_anchor_query_detects_undiscovered_documents(tmp_path) -> None:
    system = build_scenario_system(tmp_path, provider=ScenarioBatchExtractionProvider(), lazy=True)
    dispatcher = ToolDispatcher(system)

    # Only extract ONE of Metro Authority's two projects (Bridge Alpha), deliberately
    # under-covering the population, then ask completeness to check against the full
    # corpus index rather than just internal consistency.
    dispatcher.call("extract_documents", document_ids=["PKG-ALPHA"])

    result = dispatcher.call("check_completeness", entity_type="project", anchor_query="Bridge")

    assert result["complete"] is False
    assert "PKG-BETA" in result["missing_document_ids"] or "PKG-GAMMA" in result["missing_document_ids"]


def test_check_completeness_anchor_query_passes_once_fully_discovered(tmp_path) -> None:
    system = build_scenario_system(tmp_path, provider=ScenarioBatchExtractionProvider(), lazy=True)
    dispatcher = ToolDispatcher(system)

    for doc_id in ["PKG-ALPHA", "PKG-BETA", "PKG-GAMMA"]:
        dispatcher.call("extract_documents", document_ids=[doc_id])

    result = dispatcher.call("check_completeness", entity_type="project", anchor_query="Bridge")

    assert result["missing_document_ids"] == []
