from __future__ import annotations

import csv
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def load_document_index(path: Path) -> dict[str, str]:
    """Reads a document_index.csv (doc_id, doc_type, filename, size_bytes - the JAW
    hackathon corpus's external type classification, e.g. "completion_certificate",
    "reference_letter") into a combined lookup mapping to doc_type, keyed BOTH by
    doc_id and by the bare filename (no directory).

    Two different naming conventions exist in the real corpus:
    - PDFs: doc_id equals our own document_id (the file stem, e.g. "DOC-AR-2024" for
      documents/annual_report/DOC-AR-2024.pdf) - doc_id matching handles these.
    - Workbooks: doc_id is a synthetic code (e.g. "DOC-XL-BOQ-071") that does NOT match
      the file stem ("BOQ_and_Measurements_Contract_71") - only the filename lines up,
      via its basename (our own DocumentRecord.filename has no directory prefix, while
      the CSV's filename column does, e.g. "workbooks/BOQ_and_Measurements_Contract_71.xlsx").
    Callers should look up by document_id first, then by filename as a fallback - see
    evidence_builder.py's _build_document_record. doc_id and filename values don't
    collide as strings (one has no extension, the other does), so both key spaces can
    share this one dict safely.

    Returns {} (not an error) if the file doesn't exist, so callers can treat this as
    optional enrichment rather than a hard dependency.
    """
    if not path.exists():
        logger.info("No document_index.csv found at %s; documents will have no semantic doc_type.", path)
        return {}

    mapping: dict[str, str] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            doc_id = (row.get("doc_id") or "").strip()
            filename = (row.get("filename") or "").strip()
            doc_type = (row.get("doc_type") or "").strip()
            if not doc_type:
                continue
            if doc_id:
                mapping[doc_id] = doc_type
            if filename:
                mapping[Path(filename).name] = doc_type
    return mapping
