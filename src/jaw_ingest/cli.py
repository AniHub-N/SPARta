from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any

from .cache import CacheManager
from .config import configure_logging
from .extraction import extract_document

logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="JAW ingestion CLI")
    parser.add_argument("--source", required=True, type=Path, help="Source directory containing PDF and XLSX files.")
    parser.add_argument("--output", required=True, type=Path, help="Output JSON file for extracted evidence.")
    parser.add_argument("--cache-dir", type=Path, default=Path(".cache"), help="Cache directory for extracted documents.")
    parser.add_argument("--no-cache", action="store_true", help="Disable cache reads and writes.")
    parser.add_argument("--log-level", default="INFO", help="Logging level.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    configure_logging(args.log_level)
    cache_manager = CacheManager(args.cache_dir, enabled=not args.no_cache)
    output: dict[str, list[dict[str, Any]]] = {
        "success": [],
        "failures": [],
    }

    source_path = args.source
    if not source_path.exists() or not source_path.is_dir():
        logger.error("Source directory %s does not exist or is not a directory.", source_path)
        return 1

    # Recursive: a corpus laid out as documents/<type>/*.pdf (one subdirectory per
    # document type, as in the JAW hackathon corpus) has no files at the top level -
    # a non-recursive iterdir() would silently find nothing.
    for path in sorted(source_path.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in {".pdf", ".xlsx", ".xlsm", ".xltx", ".xltm"}:
            logger.debug("Skipping unsupported file: %s", path)
            continue
        try:
            extracted = extract_document(path, cache_manager=cache_manager)
            output["success"].append(extracted.model_dump(mode="json"))
        except Exception as exc:
            logger.exception("Failed to extract %s", path)
            output["failures"].append(
                {
                    "document_id": path.stem,
                    "path": str(path),
                    "error_type": type(exc).__name__,
                    "error_message": str(exc),
                }
            )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with args.output.open("w", encoding="utf-8") as handle:
            json.dump(output, handle, indent=2, ensure_ascii=False)
    except TypeError as exc:
        logger.exception("Failed to serialize extraction output to JSON: %s", exc)
        return 1

    logger.info("Extraction complete. Output written to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
