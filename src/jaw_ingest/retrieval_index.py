from __future__ import annotations

import hashlib
from pathlib import Path


def compute_corpus_fingerprint(evidence_root: Path) -> str:
    """Deterministic fingerprint of the evidence corpus a retrieval index was (or
    would be) built from: the content of documents.jsonl + evidence.jsonl +
    facts.jsonl under `evidence_root`. Used to detect whether a persisted DuckDB/
    Qdrant retrieval index still matches the corpus currently on disk, or is stale
    and must be rebuilt - comparing this is cheap (a streaming file read) relative to
    re-embedding tens of thousands of fragments, which is what staleness would
    otherwise force unconditionally on every run.

    Missing files are hashed as an explicit sentinel (not skipped), so a corpus that
    goes from "file present" to "file absent" (or vice versa) is correctly seen as a
    different corpus rather than silently matching by omission.
    """
    digest = hashlib.sha256()
    for name in ("documents.jsonl", "evidence.jsonl", "facts.jsonl"):
        path = evidence_root / name
        digest.update(name.encode("utf-8"))
        if path.exists():
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(chunk)
        else:
            digest.update(b"<missing>")
    return digest.hexdigest()
