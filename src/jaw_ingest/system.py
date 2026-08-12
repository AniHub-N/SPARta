from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from .chunk4 import Chunk4Pipeline
from .entity_resolution import EntityResolver
from .llm_provider import LLMProvider, NullProvider
from .semantic_extraction import SemanticExtractor
from .world_model import WorldModelBuilder

logger = logging.getLogger(__name__)


@dataclass
class JAWSystem:
    """The composite runtime: Chunk4Pipeline's retrieval infrastructure (DuckDB, Qdrant,
    RapidFuzz, embeddings, coarse document/predicate/value graph) plus the resolved
    semantic world model (canonical entities and relationships) built on top of it.
    Both live in the same GraphStore/DuckDBStore, in disjoint ID namespaces, so evidence
    search and entity/relationship queries both work against one system.
    """

    pipeline: Chunk4Pipeline
    world_model: WorldModelBuilder


def build_system(
    evidence_root: Path,
    provider: LLMProvider | None = None,
    duckdb_path: str = ":memory:",
    qdrant_location: str | None = None,
    model_name: str = "all-MiniLM-L6-v2",
    device: str | None = None,
    evidence_limit: int | None = None,
    batch_size: int | None = None,
    lazy: bool = False,
    force_reindex: bool = False,
) -> JAWSystem:
    """Builds the full system from an evidence root: ingests evidence/facts into DuckDB,
    indexes lexical/semantic/Qdrant retrieval, builds the coarse document graph.

    `lazy=False` (the default, preserved for backward compatibility with existing
    callers/tests): also runs eager semantic extraction over `evidence_limit` items (or
    everything) up front, exactly as before.

    `lazy=True`: skips eager extraction entirely. The world model starts EMPTY, and is
    grown only by DISCOVER operations during query execution (see mcp_tools.py's
    discover_evidence/extract_documents, executor.py's _op_discover) - this is what
    makes a large corpus affordable, since only the documents a question's hops
    actually need ever get sent through the LLM. Retrieval indexing (DuckDB/Qdrant/
    lexical/semantic) still covers the FULL corpus either way - that part costs no LLM
    calls and DISCOVER depends on it being complete.

    If `provider` is None or a NullProvider, eager extraction (when not lazy) returns
    ExtractionFailure(reason="no_provider_configured") for everything - the world model
    ends up empty rather than fabricated, visible in world_model.coverage().

    `evidence_limit`/`batch_size` only affect eager (non-lazy) extraction.

    `duckdb_path`/`qdrant_location`, when set to real filesystem paths (not
    ":memory:"), make the retrieval index persistent: a subsequent build_system() call
    against the same paths and the same corpus reuses it instead of re-embedding
    everything (see Chunk4Pipeline.index()). `force_reindex=True` bypasses that reuse
    and always rebuilds, e.g. after intentionally changing the embedding model.
    """
    pipeline = Chunk4Pipeline(
        evidence_root=evidence_root,
        duckdb_path=duckdb_path,
        qdrant_location=qdrant_location,
        model_name=model_name,
        device=device,
    )
    pipeline.index(force_reindex=force_reindex)

    extractor = SemanticExtractor(provider or NullProvider())
    resolver = EntityResolver(pipeline.lexical_retriever, pipeline.semantic_retriever, pipeline.graph_store)
    world_model = WorldModelBuilder(extractor, resolver)

    if not lazy:
        evidence_items = pipeline.corpus.evidence
        if evidence_limit is not None:
            evidence_items = evidence_items[:evidence_limit]
        if batch_size is not None:
            world_model.process_documents_batched(evidence_items, documents_by_id=pipeline.corpus.documents_by_id, batch_size=batch_size)
        else:
            world_model.process_evidence(evidence_items, documents_by_id=pipeline.corpus.documents_by_id)
        world_model.persist(pipeline.graph_store, pipeline.duckdb_store)

        if world_model.report.extraction_failures.get("no_provider_configured"):
            logger.warning(
                "Semantic extraction ran with no LLM provider configured - the world model "
                "has no entities/relationships. Set JAW_LLM_PROVIDER to enable it."
            )

    return JAWSystem(pipeline=pipeline, world_model=world_model)
