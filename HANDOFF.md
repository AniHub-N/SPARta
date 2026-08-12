# SPArta (JAW) — Handoff

Proof-driven, non-RAG QA system over the 687-doc BITS Hackathon corpus.
Pipeline: docs → DuckDB facts/metadata → lexical+semantic retrieval → LLM-extracted
entity/relationship world model → NetworkX+DuckDB graph → LLM query planner →
deterministic executor → verified answer. The LLM only plans, extracts structure, and
phrases results — it never answers directly from retrieved text.

## Status right now

- **Retrieval-index persistence: done.** DuckDB (`.cache/retrieval_index/index.duckdb`)
  and Qdrant (`.cache/retrieval_index/qdrant`) are now real on-disk stores, reused
  across runs via a corpus-fingerprint check (SHA256 of documents/evidence/facts.jsonl
  + embedding model name). `--force-reindex` bypasses reuse. `:memory:` still works for
  tests/ephemeral runs.
- **Full test suite: 200 passed, 1 skipped, 0 failed.** Run with `python -m pytest -q`.
- **21-question sample benchmark: IN PROGRESS**, running lazy/DISCOVER-driven against
  the full 687-doc corpus with Gemini. Started before the persistence fix landed but is
  unaffected (single continuous process, no restart needed). No `submission_sample21.csv`
  yet — check `.cache/llm/` file count for liveness (currently 101 cached responses and
  growing).

## Not started yet

- Evaluating the 21Q run with the official `evaluate.py` once it finishes.
- Diagnosing and fixing any wrong answers (trace: retrieval → extraction → entity
  resolution → graph → planning → execution → verification).
- The full 333-question benchmark (gated on 21Q being clean).
- Final scored report.

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
