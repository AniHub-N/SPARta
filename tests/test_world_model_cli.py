from __future__ import annotations

from pathlib import Path

from jaw_ingest.world_model_cli import _parse_args


def test_default_limit_is_small() -> None:
    args = _parse_args([])
    assert args.limit == 20
    assert args.all is False


def test_batch_size_defaults_to_batched_mode() -> None:
    args = _parse_args([])
    assert args.batch_size == 30


def test_batch_size_of_one_disables_batching_downstream() -> None:
    args = _parse_args(["--batch-size", "1"])
    assert args.batch_size == 1


def test_cache_dir_default() -> None:
    args = _parse_args([])
    assert args.cache_dir == Path(".cache/llm")
