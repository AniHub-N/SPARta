"""Runs REAL semantic extraction (via the JAW_LLM_* provider configured in .env) over
data/evidence, resolves entities, and builds/persists the world model. This is the
live-LLM counterpart to scripts/world_model_report.py, which stays a deterministic
fake-provider demo and is not touched by this module.

Usage (see README section this prints, or --help):
    jaw-world-model --limit 20        # safe default: 20 evidence items
    jaw-world-model --all             # process the entire corpus (be aware of cost)
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .cache import CacheManager
from .config import Settings, configure_logging
from .llm_provider import (
    CachingLLMProvider,
    NullProvider,
    ProviderRequestError,
    build_provider_from_settings,
)
from .system import build_system

SMOKE_TEST_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="jaw-world-model",
        description="Build the entity/relationship world model from data/evidence using the JAW_LLM_* provider configured in .env.",
    )
    parser.add_argument("--evidence-root", type=Path, default=Path("data/evidence"))
    parser.add_argument("--limit", type=int, default=20, help="Max evidence items to send through the LLM (default: 20, a safe/cheap smoke run).")
    parser.add_argument("--all", action="store_true", help="Process the entire corpus, ignoring --limit. Can be hundreds of LLM calls - use deliberately.")
    parser.add_argument(
        "--batch-size",
        type=int,
        default=30,
        help="Fragments per document sent in a single LLM call (default: 30). This is what makes a "
        "large corpus affordable - one call per ~30 fragments instead of one call per fragment. "
        "Pass 1 to force the old one-call-per-fragment behavior.",
    )
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache/llm"), help="Where LLM responses are cached, keyed by exact prompt+schema.")
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
    parser.add_argument("--device", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    args = _parse_args(argv)

    provider_name = (Settings.llm_provider or "none").strip().lower()
    if provider_name in ("", "none"):
        print(
            "ERROR: JAW_LLM_PROVIDER is not set (or is 'none') in your environment/.env.\n"
            "This command requires a real provider. Set in .env:\n"
            "  JAW_LLM_PROVIDER=openai_compatible\n"
            "  JAW_LLM_BASE_URL=https://api.openai.com/v1\n"
            "  JAW_LLM_API_KEY=sk-...\n"
            "  JAW_LLM_MODEL=gpt-4o-mini",
            file=sys.stderr,
        )
        return 1

    provider = build_provider_from_settings(Settings)
    if isinstance(provider, NullProvider):
        print(
            f"ERROR: JAW_LLM_PROVIDER={provider_name!r} but JAW_LLM_BASE_URL and/or JAW_LLM_MODEL are missing "
            "or empty - check your .env.",
            file=sys.stderr,
        )
        return 1

    print(f"Provider: {provider_name} | base_url={getattr(provider, 'base_url', '?')} | model={getattr(provider, 'model', '?')}")
    print("Running a one-call smoke test before processing the corpus...")
    try:
        result = provider.complete(
            system="You are a JSON API test endpoint.",
            user='Respond with exactly {"ok": true}.',
            response_schema=SMOKE_TEST_SCHEMA,
        )
        print(f"  smoke test OK: {result}\n")
    except ProviderRequestError as exc:
        print(f"ERROR: LLM provider smoke test failed - configuration is invalid or the endpoint is unreachable:\n  {exc}", file=sys.stderr)
        return 1

    if not args.evidence_root.exists():
        print(f"ERROR: evidence root does not exist: {args.evidence_root}", file=sys.stderr)
        return 1

    effective_provider = provider
    if not args.no_cache:
        cache = CacheManager(args.cache_dir, enabled=True)
        effective_provider = CachingLLMProvider(provider, cache)
        print(f"LLM response cache: {args.cache_dir} (re-runs over the same evidence won't re-call the LLM)")

    evidence_limit = None if args.all else args.limit
    if evidence_limit is not None:
        print(f"Processing at most {evidence_limit} evidence items (pass --all to process the entire corpus).\n")
    else:
        print("Processing the ENTIRE evidence corpus - this may be many LLM calls.\n")

    batch_size = None if args.batch_size <= 1 else args.batch_size
    print(f"Extraction batching: {'one call per fragment' if batch_size is None else f'up to {batch_size} fragments per call'}\n")

    system = build_system(
        evidence_root=args.evidence_root,
        provider=effective_provider,
        duckdb_path=args.duckdb_path,
        qdrant_location=args.qdrant_location,
        device=args.device,
        evidence_limit=evidence_limit,
        batch_size=batch_size,
        force_reindex=args.force_reindex,
    )

    world_model = system.world_model
    print("=== World model coverage ===")
    for key, value in world_model.coverage().items():
        print(f"  {key}: {value}")

    print("\n=== Counts ===")
    print(f"  entities:      {len(world_model.canonical_entities)}")
    print(f"  mentions:      {len(world_model.mentions)}")
    print(f"  relationships: {len(world_model.relationships)}")
    print(f"  attributes:    {len(world_model.attributes)}")

    if world_model.relationships:
        relationship = world_model.relationships[0]
        evidence = system.pipeline.get_evidence(relationship.evidence_id)
        subject = next(e for e in world_model.canonical_entities if e.entity_id == relationship.subject_entity_id)
        object_entity = next(e for e in world_model.canonical_entities if e.entity_id == relationship.object_entity_id)
        print("\n=== Example provenance chain ===")
        print(f"  {subject.canonical_name} --{relationship.predicate}--> {object_entity.canonical_name}")
        print(f"  relationship_id={relationship.relationship_id}")
        print(f"  evidence_id={relationship.evidence_id}  document_id={relationship.document_id}")
        if evidence is not None:
            print(f"  evidence text: {(evidence.content.text or evidence.content.raw_value)!r}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
