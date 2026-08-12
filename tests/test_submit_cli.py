from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from jaw_ingest import submit_cli
from jaw_ingest.submit_cli import _load_questions, _parse_args, main

from _fixtures import ScenarioBatchExtractionProvider, ScenarioExtractionProvider, write_scenario_evidence_root


def test_parse_args_defaults(tmp_path: Path) -> None:
    args = _parse_args(["--questions", str(tmp_path / "q.json")])
    assert args.output == Path("submission.csv")
    assert args.evidence_limit == 30
    assert args.batch_size == 30
    assert args.question_limit is None
    assert args.eager is False  # lazy, DISCOVER-driven extraction is the default
    assert args.discover_limit == 15


def test_parse_args_eager_flag(tmp_path: Path) -> None:
    args = _parse_args(["--questions", str(tmp_path / "q.json"), "--eager"])
    assert args.eager is True


def test_parse_args_discover_limit_overridable(tmp_path: Path) -> None:
    args = _parse_args(["--questions", str(tmp_path / "q.json"), "--discover-limit", "5"])
    assert args.discover_limit == 5


def test_load_questions_real_shape_with_questions_key(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(
        json.dumps({"set_id": "hidden_set_v1.4", "n_questions": 2, "questions": [{"qid": "A", "question": "q1", "answer_type": "money"}, {"qid": "B", "question": "q2", "answer_type": "count"}]}),
        encoding="utf-8",
    )
    questions = _load_questions(path, limit=None)
    assert [q["qid"] for q in questions] == ["A", "B"]


def test_load_questions_answers_key_shape(tmp_path: Path) -> None:
    path = tmp_path / "answers.json"
    path.write_text(json.dumps({"answers": [{"qid": "A", "question": "q1", "answer_type": "money"}]}), encoding="utf-8")
    questions = _load_questions(path, limit=None)
    assert [q["qid"] for q in questions] == ["A"]


def test_load_questions_bare_list_shape(tmp_path: Path) -> None:
    path = tmp_path / "bare.json"
    path.write_text(json.dumps([{"qid": "A", "question": "q1", "answer_type": "money"}]), encoding="utf-8")
    questions = _load_questions(path, limit=None)
    assert [q["qid"] for q in questions] == ["A"]


def test_load_questions_respects_limit(tmp_path: Path) -> None:
    path = tmp_path / "questions.json"
    path.write_text(json.dumps({"questions": [{"qid": f"Q{i}"} for i in range(10)]}), encoding="utf-8")
    questions = _load_questions(path, limit=3)
    assert len(questions) == 3


def test_load_questions_missing_key_raises(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"unrelated": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        _load_questions(path, limit=None)


def test_main_missing_questions_file_fails_clearly(tmp_path: Path) -> None:
    exit_code = main(["--questions", str(tmp_path / "does-not-exist.json")])
    assert exit_code == 1


class _PlanningOnlyProvider:
    """Routes extraction to the scenario provider; returns a trivial fixed plan that
    just resolves and returns the client entity, for a lightweight full-pipeline check.
    """

    def __init__(self) -> None:
        self.extraction = ScenarioExtractionProvider()

    def complete(self, system, user, response_schema):
        if system.startswith("You extract structured facts"):
            return self.extraction.complete(system, user, response_schema)
        if system.startswith("You turn a natural-language question"):
            return {
                "question": "irrelevant",
                "understanding": "",
                "operations": [
                    {"output_var": "client", "op_type": "RESOLVE_ENTITY", "params": {"query": "Metro Authority", "entity_type": "client"}, "depends_on": [], "description": ""},
                    {"output_var": "projects", "op_type": "ENUMERATE", "params": {"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}, "depends_on": ["client"], "description": ""},
                    {"output_var": "answer", "op_type": "RETURN", "params": {"input_var": "$projects"}, "depends_on": ["projects"], "description": ""},
                ],
                "final_var": "answer",
            }
        if system.startswith("You write one concise"):
            return {"sentence": "done"}
        return {}


def test_main_writes_submission_csv_end_to_end(tmp_path: Path, monkeypatch) -> None:
    evidence_root = write_scenario_evidence_root(tmp_path)
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps(
            {
                "questions": [
                    {"qid": "Q1", "question": "How many projects does Metro Authority have?", "answer_type": "count"},
                ]
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "submission.csv"

    provider = _PlanningOnlyProvider()
    monkeypatch.setattr(submit_cli, "build_provider_from_settings", lambda settings: provider)

    exit_code = main(
        [
            "--questions", str(questions_path),
            "--output", str(output_path),
            "--evidence-root", str(evidence_root),
            "--device", "cpu",
            "--no-cache",
            "--duckdb-path", ":memory:",
            "--qdrant-location", ":memory:",
            # --eager: this test verifies CSV writing/coercion, not DISCOVER mechanics
            # (covered separately in test_mcp_tools.py/test_world_model.py). The fixed
            # plan below has no DISCOVER step, so it needs the world model pre-built.
            "--eager",
            # ScenarioExtractionProvider is a single-fragment fixture (one canned
            # response per call) - force one-call-per-fragment mode so it matches what
            # the fixture actually supports.
            "--batch-size", "1",
        ]
    )

    assert exit_code == 0
    assert output_path.exists()
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[0] == ["question_id", "answer"]
    assert rows[1][0] == "Q1"
    assert rows[1][1] == "2"  # Bridge Alpha + Bridge Beta, both commissioned_by Metro Authority


class _LazyPlanningProvider:
    """Batch-format-aware fixed provider for exercising the DEFAULT lazy path through
    the CLI: extraction responses use the batched [E1]/[E2]-tagged prompt format (what
    ensure_extracted/DISCOVER always uses), and the fixed plan includes a DISCOVER step.
    """

    def __init__(self) -> None:
        self.extraction = ScenarioBatchExtractionProvider()

    def complete(self, system, user, response_schema):
        if system.startswith("You extract structured facts"):
            return self.extraction.complete(system, user, response_schema)
        if system.startswith("You turn a natural-language question"):
            return {
                "question": "irrelevant",
                "understanding": "",
                "operations": [
                    {"output_var": "found", "op_type": "DISCOVER", "params": {"query": "Metro Authority", "limit": 10}, "depends_on": [], "description": ""},
                    {"output_var": "client", "op_type": "RESOLVE_ENTITY", "params": {"query": "Metro Authority", "entity_type": "client"}, "depends_on": ["found"], "description": ""},
                    {"output_var": "projects", "op_type": "ENUMERATE", "params": {"entity_type": "project", "predicate": "commissioned_by", "anchor_var": "$client", "direction": "in"}, "depends_on": ["client"], "description": ""},
                    {"output_var": "answer", "op_type": "RETURN", "params": {"input_var": "$projects"}, "depends_on": ["projects"], "description": ""},
                ],
                "final_var": "answer",
            }
        if system.startswith("You write one concise"):
            return {"sentence": "done"}
        return {}


def test_main_lazy_default_discovers_before_answering(tmp_path: Path, monkeypatch) -> None:
    evidence_root = write_scenario_evidence_root(tmp_path)
    questions_path = tmp_path / "questions.json"
    questions_path.write_text(
        json.dumps({"questions": [{"qid": "Q1", "question": "How many projects does Metro Authority have?", "answer_type": "count"}]}),
        encoding="utf-8",
    )
    output_path = tmp_path / "submission.csv"

    provider = _LazyPlanningProvider()
    monkeypatch.setattr(submit_cli, "build_provider_from_settings", lambda settings: provider)

    exit_code = main(
        [
            "--questions", str(questions_path),
            "--output", str(output_path),
            "--evidence-root", str(evidence_root),
            "--device", "cpu",
            "--no-cache",
            "--duckdb-path", ":memory:",
            "--qdrant-location", ":memory:",
            # no --eager: exercising the new default lazy path
        ]
    )

    assert exit_code == 0
    with output_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert rows[1] == ["Q1", "2"]
