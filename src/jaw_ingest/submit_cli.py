"""Answers every question in a questions.json file and writes a submission CSV in the
exact format the JAW hackathon evaluator expects:

    question_id,answer
    HV-IC-0001,2942400000
    HV-IC-0002,1516600000

The world model is built ONCE and reused across every question (rebuilding it per
question, like jaw-query does for a single ad-hoc question, would multiply LLM cost by
the question count for no benefit - the corpus doesn't change between questions).

By default this runs LAZY: the world model starts empty, and the planner's DISCOVER
operation grows it on demand as each question's hops need specific documents - only
the documents actually relevant to these questions are ever sent through the LLM,
instead of eagerly extracting the entire corpus. Pass --eager to revert to the old
upfront-extraction behavior (bounded by --evidence-limit/--all/--batch-size).
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .answer_coercion import coerce_numeric_answer, format_submission_value
from .cache import CacheManager
from .config import Settings, configure_logging
from .llm_provider import CachingLLMProvider, NullProvider, ProviderRequestError, build_provider_from_settings
from .mcp_tools import ToolDispatcher
from .planner import QueryPlanner
from .query_engine import QueryEngine
from .system import build_system

SMOKE_TEST_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="jaw-submit", description="Answer every question in questions.json and write a submission CSV.")
    parser.add_argument("--questions", type=Path, required=True, help="Path to questions.json ([{qid, question, answer_type}, ...]).")
    parser.add_argument("--output", type=Path, default=Path("submission.csv"))
    parser.add_argument("--evidence-root", type=Path, default=Path("data/evidence"))
    parser.add_argument(
        "--eager",
        action="store_true",
        help="Revert to eager upfront extraction (old behavior) instead of the default lazy, "
        "DISCOVER-driven, per-question extraction. Only use this for small corpora - on the full "
        "687-document corpus this reproduces the earlier full-extraction cost/timeout problem.",
    )
    parser.add_argument("--evidence-limit", type=int, default=30, help="[--eager only] Cap on evidence items sent through the LLM while building the world model. Pass --all for the full corpus.")
    parser.add_argument("--all", action="store_true", help="[--eager only] Process the entire evidence corpus, ignoring --evidence-limit.")
    parser.add_argument("--batch-size", type=int, default=30, help="Fragments per document per extraction call, both for --eager and for DISCOVER's targeted extraction (default: 30). Pass 1 to disable batching.")
    parser.add_argument("--discover-limit", type=int, default=15, help="[lazy mode] Default cap on documents a single DISCOVER call pulls in - bounds cost per hop. The planner may request a different limit per call.")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of questions to evaluate concurrently (default: 4). Pass 1 for serial execution.")
    parser.add_argument("--question-limit", type=int, default=None, help="Only answer the first N questions - for a cheap dry run before spending on all of them.")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/llm"))
    parser.add_argument("--no-cache", action="store_true")
    parser.add_argument(
        "--duckdb-path",
        default=".cache/retrieval_index/index.duckdb",
        help="Persistent on-disk DuckDB file (default: .cache/retrieval_index/index.duckdb). A later run "
        "against the same path and the same corpus reuses this instead of rebuilding. Pass ':memory:' "
        "for the old ephemeral, always-rebuilt behavior.",
    )
    parser.add_argument(
        "--qdrant-location",
        default=".cache/retrieval_index/qdrant",
        help="Persistent on-disk Qdrant storage directory (default: .cache/retrieval_index/qdrant). "
        "Pass ':memory:' for the old ephemeral, always-rebuilt behavior.",
    )
    parser.add_argument(
        "--force-reindex",
        action="store_true",
        help="Ignore any persisted retrieval index and rebuild from scratch (e.g. after changing --model-name).",
    )
    parser.add_argument("--model-name", default="all-MiniLM-L6-v2")
    parser.add_argument("--device", default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    return parser.parse_args(argv)


def _load_questions(path: Path, limit: int | None) -> list[dict]:
    """Accepts both the real questions.json/sample_questions.json shape - a top-level
    object with a "questions" (or "answers") key holding the list, matching what
    evaluate.py itself reads via `key.get("answers") or key.get("questions")` - and a
    bare top-level list, for simpler test fixtures.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        questions = raw.get("questions") or raw.get("answers")
        if questions is None:
            raise ValueError(f"{path} has no 'questions' or 'answers' key.")
    elif isinstance(raw, list):
        questions = raw
    else:
        raise ValueError(f"{path} must be a JSON object with a 'questions' list, or a bare list.")
    if limit is not None:
        questions = questions[:limit]
    return questions


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    if not args.questions.exists():
        print(f"ERROR: questions file not found: {args.questions}", file=sys.stderr)
        return 1
    questions = _load_questions(args.questions, args.question_limit)
    print(f"Loaded {len(questions)} question(s) from {args.questions}\n")

    provider = build_provider_from_settings(Settings)
    if isinstance(provider, NullProvider):
        print(
            f"ERROR: no usable LLM provider (JAW_LLM_PROVIDER={Settings.llm_provider!r} in your .env). "
            "Both world-model extraction and planning need a real provider.",
            file=sys.stderr,
        )
        return 1

    print(f"Provider: {Settings.llm_provider} | base_url={getattr(provider, 'base_url', '?')} | model={getattr(provider, 'model', '?')}")
    print("Running a one-call smoke test...")
    try:
        smoke_result = provider.complete(system="You are a JSON API test endpoint.", user='Respond with exactly {"ok": true}.', response_schema=SMOKE_TEST_SCHEMA)
        print(f"  smoke test OK: {smoke_result}\n")
    except ProviderRequestError as exc:
        print(f"ERROR: LLM provider smoke test failed:\n  {exc}", file=sys.stderr)
        return 1

    effective_provider = provider
    if not args.no_cache:
        effective_provider = CachingLLMProvider(provider, CacheManager(args.cache_dir, enabled=True))
        print(f"LLM response cache: {args.cache_dir}")

    lazy = not args.eager
    evidence_limit = None if args.all else args.evidence_limit
    batch_size = None if args.batch_size <= 1 else args.batch_size

    if lazy:
        print(
            "Building retrieval index over the full corpus (free, no LLM calls; reused from "
            f"{args.duckdb_path} / {args.qdrant_location} if a matching persisted index already "
            "exists there). World model starts EMPTY and grows on demand via DISCOVER as each "
            f"question's hops need specific documents (discover_limit={args.discover_limit}, "
            f"batch_size={batch_size}, concurrency={args.concurrency}).\n"
        )
    else:
        print(f"[--eager] Building world model up front (evidence_limit={evidence_limit}, batch_size={batch_size}) - this is built ONCE and reused for all {len(questions)} questions.\n")

    system = build_system(
        evidence_root=args.evidence_root,
        provider=effective_provider,
        duckdb_path=args.duckdb_path,
        qdrant_location=args.qdrant_location,
        model_name=args.model_name,
        device=args.device,
        evidence_limit=evidence_limit,
        batch_size=batch_size,
        lazy=lazy,
        force_reindex=args.force_reindex,
    )
    print(f"World model: {len(system.world_model.canonical_entities)} entities, {len(system.world_model.relationships)} relationships, {len(system.world_model.attributes)} attributes.\n")

    dispatcher = ToolDispatcher(system, max_discover_limit=args.discover_limit if lazy else None)
    planner = QueryPlanner(effective_provider)
    engine = QueryEngine(dispatcher, planner, max_iterations=args.max_iterations, answer_provider=effective_provider)

    rows: list[tuple[str, str]] = [("", "0")] * len(questions)
    unresolved_count = 0
    print_lock = threading.Lock()

    def _process_question(item: tuple[int, dict]) -> tuple[int, str, str, bool]:
        index, question = item
        qid = question.get("qid") or question.get("question_id") or f"Q{index}"
        answer_type = question.get("answer_type", "")
        question_text = question.get("question", "")

        with print_lock:
            print(f"[{index}/{len(questions)}] {qid} ({answer_type}): {question_text[:80]}")

        result = engine.run(question_text)
        numeric = coerce_numeric_answer(result.final_answer.answer, answer_type)

        if numeric is None:
            formatted = "0"
            is_unresolved = True
            with print_lock:
                print(f"  [{qid}] -> UNRESOLVED (status={result.final_answer.status}); writing 0 as a placeholder.")
        else:
            formatted = format_submission_value(numeric, answer_type)
            is_unresolved = False
            with print_lock:
                print(f"  [{qid}] -> {formatted}  (status={result.final_answer.status}, confidence={result.final_answer.confidence:.2f})")

        with print_lock:
            print(
                f"    [world model status] {len(system.world_model.canonical_entities)} entities, "
                f"{len(system.world_model.relationships)} relationships, "
                f"{system.world_model.extracted_document_count} documents extracted so far"
            )

        return index, qid, formatted, is_unresolved

    indexed_questions = list(enumerate(questions, start=1))

    if args.concurrency <= 1 or len(questions) <= 1:
        for item in indexed_questions:
            idx, qid, formatted, is_unresolved = _process_question(item)
            rows[idx - 1] = (qid, formatted)
            if is_unresolved:
                unresolved_count += 1
    else:
        with ThreadPoolExecutor(max_workers=args.concurrency) as executor:
            results = executor.map(_process_question, indexed_questions)
            for idx, qid, formatted, is_unresolved in results:
                rows[idx - 1] = (qid, formatted)
                if is_unresolved:
                    unresolved_count += 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["question_id", "answer"])
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} rows to {args.output} ({unresolved_count} unresolved -> placeholder 0).")
    return 0



if __name__ == "__main__":
    sys.exit(main())
