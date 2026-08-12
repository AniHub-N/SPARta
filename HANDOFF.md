# SPArta (JAW) — Handoff

Proof-driven, non-RAG QA system over the 687-doc BITS Hackathon corpus.
Pipeline: docs → DuckDB facts/metadata → lexical+semantic retrieval → LLM-extracted
entity/relationship world model → NetworkX+DuckDB graph → LLM query planner →
deterministic executor → verified answer. The LLM only plans, extracts structure, and
phrases results — it never answers directly from retrieved text.

## Status right now

**All engineering work is done and tested. Nothing is left to build — the only
blocker is Gemini API billing (see below).**

- **Retrieval-index persistence: done.** DuckDB (`.cache/retrieval_index/index.duckdb`)
  and Qdrant (`.cache/retrieval_index/qdrant`) are real on-disk stores, reused across
  runs via a corpus-fingerprint check (SHA256 of documents/evidence/facts.jsonl +
  embedding model name). `--force-reindex` bypasses reuse. `:memory:` still works for
  tests/ephemeral runs. Embeddings are computed exactly once per index build (no
  duplicate embedding pass).
- **Lazy graph / DISCOVER flow: done.** World model starts empty; `DISCOVER` (planner
  op) → `discover_evidence`/`extract_documents` (MCP tools) → `WorldModelBuilder.
  ensure_extracted()` grows it incrementally and idempotently, only for documents a
  question's hops actually touch — never a blind full-corpus extraction.
- **Mentions/attributes persistence: done.** `DuckDBStore.ingest_mentions`/
  `ingest_attributes`, wired into `WorldModelBuilder.persist()`.
- **Completeness checks: done.** `check_completeness` does a full-corpus cross-check
  (not just against what's been extracted so far), so population/count questions can
  detect an incomplete world model instead of silently under-counting.
- **Retries: done.** `OpenAICompatibleProvider._post` retries transient timeouts/
  connection errors with backoff.
- **Full test suite: 200 passed, 1 skipped, 0 failed**, re-confirmed with a clean run
  after this pass — no LLM/API calls involved (all fake/injected providers). Run with
  `python -m pytest -q`.

## Blocked — not an engineering gap

- **The 21-question benchmark cannot be scored.** The one real attempt hit Gemini's
  billing wall (`RESOURCE_EXHAUSTED`, HTTP 429, "prepayment credits are depleted")
  after question 1 — every subsequent planning call failed, so 20/21 rows are
  `UNRESOLVED` placeholders, not real answers. This has nothing to do with the code;
  it needs a billing top-up at https://ai.studio/projects before it means anything.
- Evaluating with the official `evaluate.py`, diagnosing wrong answers by layer, and
  the full 333-question run are all gated on that top-up — no code work is pending
  ahead of them.

## Key commands

```
python -m pytest -q                                    # full test suite
python -m jaw_ingest.submit_cli \
  --questions data/sample_questions.json \
  --evidence-root data/evidence_full \
  --output submission_sample21.csv --device cpu        # 21Q benchmark (lazy by default)
python evaluate.py submission_sample21.csv ...          # official evaluator (check its --help)
```

## Key files

- `src/jaw_ingest/system.py` — `build_system()`, wires everything together.
- `src/jaw_ingest/chunk4.py` — retrieval infra: DuckDB/Qdrant/lexical/semantic/graph,
  `Chunk4Pipeline.index()` (persistence + reuse logic), `.close()`.
- `src/jaw_ingest/world_model.py` — `WorldModelBuilder`, `ensure_extracted` (lazy,
  idempotent LLM extraction).
- `src/jaw_ingest/mcp_tools.py` — tool dispatcher incl. `DISCOVER` (`discover_evidence`,
  `extract_documents`).
- `src/jaw_ingest/planner.py`, `executor.py`, `query_engine.py` — plan/execute/verify loop.
- `src/jaw_ingest/submit_cli.py` — batch-answers a questions.json, writes submission CSV.
- `tests/test_retrieval_index.py` — proves index reuse/invalidation/force_reindex/`:memory:`.

## Constraints to respect (don't relitigate these)

- Never turn this into RAG (LLM answering directly from retrieved chunks).
- Never hardcode the sample reasoning chains — the planner must stay generic.
- Arithmetic, joins, graph traversal, aggregation, verification stay deterministic
  (SQL/graph code), not LLM judgment.
- Don't launch a redundant full-corpus LLM run while one is already in flight — check
  `.cache/llm/` growth and process liveness first.
- Don't launch any new LLM run until Gemini billing is topped up — it will just burn
  through the retry budget and produce more `UNRESOLVED` placeholders.
