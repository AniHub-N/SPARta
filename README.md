# JAW Ingestion

A production-quality ingestion layer for the JAW document corpus.

Features:
- PDF ingestion via PyMuPDF
- XLSX ingestion via openpyxl
- Canonical evidence representation with typed Pydantic models
- Deterministic money/date/number normalization utilities
- Structured logging and environment-based configuration
- File caching to avoid repeated reprocessing
- CLI entry point for batch ingestion

## Install

```bash
python -m pip install -e .
```

## Usage

```bash
jaw-ingest ingest --source /path/to/corpus --output /path/to/output.json
```

## Project structure

- `src/jaw_ingest`: ingestion package
- `tests`: unit tests for extraction, normalization, and caching
