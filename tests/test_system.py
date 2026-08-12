from __future__ import annotations

from pathlib import Path

from _fixtures import build_scenario_system


def test_evidence_limit_caps_llm_calls_not_retrieval_indexing(tmp_path: Path) -> None:
    system = build_scenario_system(tmp_path)  # default: no limit, all 7 evidence items
    assert system.world_model.report.evidence_processed == 7

    # Retrieval indexing (DuckDB/lexical/semantic) always covers the whole corpus.
    assert system.pipeline.get_coverage()["duckdb_evidence"] == 7


def test_evidence_limit_restricts_semantic_extraction(tmp_path: Path) -> None:
    from _fixtures import ScenarioExtractionProvider, write_scenario_evidence_root
    from jaw_ingest.system import build_system

    evidence_root = write_scenario_evidence_root(tmp_path)
    system = build_system(
        evidence_root=evidence_root,
        provider=ScenarioExtractionProvider(),
        duckdb_path=":memory:",
        qdrant_location=":memory:",
        device="cpu",
        evidence_limit=1,
    )

    assert system.world_model.report.evidence_processed == 1
    # Full corpus is still indexed for retrieval, regardless of the LLM-call limit.
    assert system.pipeline.get_coverage()["duckdb_evidence"] == 7


def test_lazy_mode_starts_with_empty_world_model_but_full_retrieval_index(tmp_path: Path) -> None:
    from _fixtures import ScenarioBatchExtractionProvider, write_scenario_evidence_root
    from jaw_ingest.system import build_system

    evidence_root = write_scenario_evidence_root(tmp_path)
    system = build_system(
        evidence_root=evidence_root,
        provider=ScenarioBatchExtractionProvider(),
        duckdb_path=":memory:",
        qdrant_location=":memory:",
        device="cpu",
        lazy=True,
    )

    assert system.world_model.report.evidence_processed == 0
    assert len(system.world_model.canonical_entities) == 0
    # Retrieval indexing still covers the whole corpus even though nothing was extracted.
    assert system.pipeline.get_coverage()["duckdb_evidence"] == 7


def test_lazy_mode_world_model_grows_via_ensure_extracted(tmp_path: Path) -> None:
    from _fixtures import ScenarioBatchExtractionProvider, write_scenario_evidence_root
    from jaw_ingest.system import build_system

    evidence_root = write_scenario_evidence_root(tmp_path)
    system = build_system(
        evidence_root=evidence_root,
        provider=ScenarioBatchExtractionProvider(),
        duckdb_path=":memory:",
        qdrant_location=":memory:",
        device="cpu",
        lazy=True,
    )
    evidence_by_document: dict[str, list] = {}
    for evidence in system.pipeline.corpus.evidence:
        evidence_by_document.setdefault(evidence.document_id, []).append(evidence)

    system.world_model.ensure_extracted(["PKG-ALPHA"], evidence_by_document, batch_size=40)

    assert system.world_model.extracted_document_count == 1
    assert len(system.world_model.canonical_entities) > 0
