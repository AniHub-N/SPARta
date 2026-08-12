from __future__ import annotations

from pathlib import Path

from jaw_ingest.query_cli import _parse_args


def test_default_evidence_limit_is_small_not_the_whole_corpus() -> None:
    args = _parse_args(["some question"])
    assert args.evidence_limit == 30
    assert args.all is False


def test_all_flag_overrides_the_limit() -> None:
    args = _parse_args(["some question", "--all"])
    assert args.all is True


def test_evidence_limit_is_overridable() -> None:
    args = _parse_args(["some question", "--evidence-limit", "5"])
    assert args.evidence_limit == 5


def test_cache_dir_defaults_to_shared_llm_cache() -> None:
    args = _parse_args(["some question"])
    assert args.cache_dir == Path(".cache/llm")
    assert args.no_cache is False


def test_no_cache_flag() -> None:
    args = _parse_args(["some question", "--no-cache"])
    assert args.no_cache is True


def test_batch_size_defaults_to_batched_mode() -> None:
    args = _parse_args(["some question"])
    assert args.batch_size == 30


def test_batch_size_of_one_is_settable() -> None:
    args = _parse_args(["some question", "--batch-size", "1"])
    assert args.batch_size == 1
