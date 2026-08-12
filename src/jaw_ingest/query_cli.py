from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    parser = argparse.ArgumentParser(prog="jaw-query", description="Ask a natural-language question over the JAW corpus.")
    parser.add_argument("question", help="The natural-language question to answer.")
    parser.add_argument("--evidence-root", type=Path, default=Path("data/evidence"), help="Directory containing documents.jsonl/evidence.jsonl/facts.jsonl.")
    parser.add_argument(
        "--evidence-limit",
        type=int,
        default=30,
        help="Max evidence items to run through semantic extraction while building the world model "
        "(default: 30). The full corpus can be hundreds of items - each one is an LLM call - so this "
        "is capped by default. Pass --all to process everything.",
    )
    parser.add_argument("--all", action="store_true", help="Process the entire evidence corpus, ignoring --evidence-limit.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=30,
        help="Fragments per document sent in a single LLM call during world-model construction "
        "(default: 30, shared setting with jaw-world-model). Pass 1 for one call per fragment.",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/llm"), help="LLM response cache dir (shared with jaw-world-model, keyed by exact prompt+schema).")
    parser.add_argument("--no-cache", action="store_true", help="Disable the response cache (always call the LLM).")
    parser.add_argument(
        "--duckdb-path",
        default=".cache/retrieval_index/index.duckdb",
        help="Persistent on-disk DuckDB file, reused across runs against the same corpus. Pass ':memory:' to disable.",
    )
    parser.add_argument(
        "--qdrant-location",
        default=".cache/retrieval_index/qdrant",
        help="Persistent on-disk Qdrant storage directory, reused across runs against the same corpus. Pass ':memory:' to disable.",
    )
    parser.add_argument("--force-reindex", action="store_true", help="Ignore any persisted retrieval index and rebuild from scratch.")
    parser.add_argument("--model-name", default="all-MiniLM-L6-v2")
    parser.add_argument("--device", default=None)
    parser.add_argument("--max-iterations", type=int, default=3)
    parser.add_argument("--json", action="store_true", help="Print the full QueryResult as JSON instead of a formatted summary.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    provider = build_provider_from_settings(Settings)
    if isinstance(provider, NullProvider):
        provider_name = (Settings.llm_provider or "none").strip().lower()
        print(
            f"ERROR: no usable LLM provider (JAW_LLM_PROVIDER={provider_name!r} in your .env). "
            "The planner needs a real provider to turn your question into a plan. Set in .env:\n"
            "  JAW_LLM_PROVIDER=openai_compatible\n"
            "  JAW_LLM_BASE_URL=...\n"
            "  JAW_LLM_API_KEY=...\n"
            "  JAW_LLM_MODEL=...",
            file=sys.stderr,
        )
        return 1

    print(f"Provider: {Settings.llm_provider} | base_url={getattr(provider, 'base_url', '?')} | model={getattr(provider, 'model', '?')}")
    print("Running a one-call smoke test before building the world model...")
    try:
        smoke_result = provider.complete(system="You are a JSON API test endpoint.", user='Respond with exactly {"ok": true}.', response_schema=SMOKE_TEST_SCHEMA)
        print(f"  smoke test OK: {smoke_result}\n")
    except ProviderRequestError as exc:
        print(f"ERROR: LLM provider smoke test failed - configuration is invalid or the endpoint is unreachable:\n  {exc}", file=sys.stderr)
        return 1

    effective_provider = provider
    if not args.no_cache:
        effective_provider = CachingLLMProvider(provider, CacheManager(args.cache_dir, enabled=True))
        print(f"LLM response cache: {args.cache_dir} (shared with jaw-world-model - matching prior runs cost nothing)")

    evidence_limit = None if args.all else args.evidence_limit
    if evidence_limit is not None:
        print(f"Building world model from at most {evidence_limit} evidence items (pass --all for the full corpus).\n")
    else:
        print("Building world model from the ENTIRE evidence corpus - this may be many LLM calls.\n")

    batch_size = None if args.batch_size <= 1 else args.batch_size

    system = build_system(
        evidence_root=args.evidence_root,
        provider=effective_provider,
        duckdb_path=args.duckdb_path,
        qdrant_location=args.qdrant_location,
        model_name=args.model_name,
        device=args.device,
        evidence_limit=evidence_limit,
        batch_size=batch_size,
        force_reindex=args.force_reindex,
    )
    print(f"World model: {len(system.world_model.canonical_entities)} entities, {len(system.world_model.relationships)} relationships, {len(system.world_model.attributes)} attributes.\n")

    dispatcher = ToolDispatcher(system)
    planner = QueryPlanner(effective_provider)
    engine = QueryEngine(dispatcher, planner, max_iterations=args.max_iterations, answer_provider=effective_provider)

    result = engine.run(args.question)

    if args.json:
        print(result.model_dump_json(indent=2))
        return 0

    print(f"Question: {result.question}\n")
    print(f"Answer: {result.final_answer.answer}")
    print(f"Status: {result.final_answer.status}  (confidence: {result.final_answer.confidence:.2f})")
    print(f"Iterations used: {result.iterations_used}\n")
    print("Proof / reasoning summary:")
    print(result.final_answer.proof_summary)
    print()
    if result.final_answer.evidence:
        print("Evidence:")
        for citation in result.final_answer.evidence:
            snippet = (citation.text or "")[:120]
            print(f"  - [{citation.evidence_id}] {citation.document_id} ({json.dumps(citation.location)}): {snippet!r}")
    else:
        print("Evidence: (none)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
