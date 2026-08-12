from pathlib import Path

import json
import pytest

from jaw_ingest.evidence_builder import EvidenceBuilder
from jaw_ingest.evidence import EVIDENCE_SCHEMA_VERSION, NORMALIZATION_VERSION


def test_evidence_builder_preserves_pdf_block_provenance(tmp_path: Path) -> None:
    input_data = {
        "success": [
            {
                "document_id": "sample-pdf",
                "filename": "sample.pdf",
                "document_type": "pdf",
                "source_path": str(tmp_path / "sample.pdf"),
                "metadata": {"size_bytes": 0},
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Contract Value: INR 33.38 Cr",
                        "blocks": [
                            {
                                "block_id": "sample-pdf-p1-b1",
                                "document_id": "sample-pdf",
                                "page_number": 1,
                                "text": "INR 33.38 Cr",
                                "coordinates": {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 20.0},
                                "image_path": None,
                                "metadata": {},
                            }
                        ],
                        "image_path": None,
                        "embedded_image_paths": [],
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    builder = EvidenceBuilder(input_file, output_dir, force=True)
    report = builder.build()

    assert report.documents_processed == 1
    assert report.evidence_created == 1
    assert report.facts_created == 1

    evidence_file = output_dir / "evidence.jsonl"
    facts_file = output_dir / "facts.jsonl"
    assert evidence_file.exists()
    assert facts_file.exists()

    evidence = json.loads(evidence_file.read_text(encoding="utf-8").splitlines()[0])
    fact = json.loads(facts_file.read_text(encoding="utf-8").splitlines()[0])

    assert evidence["source_type"] == "pdf"
    assert evidence["location"]["page_number"] == 1
    assert evidence["location"]["block_id"] == "sample-pdf-p1-b1"
    assert evidence["content"]["raw_value"] == "INR 33.38 Cr"
    assert fact["provenance"]["evidence_id"] == evidence["evidence_id"]
    assert fact["normalized_type"] == "currency_inr"
    assert fact["normalized_value"] == "333800000"


def test_evidence_builder_splits_tabular_pdf_row(tmp_path: Path) -> None:
    # A two-column certificate row arrives from PyMuPDF "blocks" mode as one string with
    # the column gap collapsed to a single space, no colon. The builder must split the
    # label off and normalize only the value.
    input_data = {
        "success": [
            {
                "document_id": "cert",
                "filename": "cert.pdf",
                "document_type": "pdf",
                "source_path": str(tmp_path / "cert.pdf"),
                "metadata": {"size_bytes": 0},
                "pages": [
                    {
                        "page_number": 1,
                        "text": "Contract Value (Original) INR 33.38 Cr\nCompletion Date 2011-02-06",
                        "blocks": [
                            {
                                "block_id": "cert-p1-b1",
                                "document_id": "cert",
                                "page_number": 1,
                                "text": "Contract Value (Original) INR 33.38 Cr",
                                "coordinates": {"x0": 0.0, "y0": 0.0, "x1": 10.0, "y1": 20.0},
                                "image_path": None,
                                "metadata": {},
                            },
                            {
                                "block_id": "cert-p1-b2",
                                "document_id": "cert",
                                "page_number": 1,
                                "text": "Completion Date 2011-02-06",
                                "coordinates": {"x0": 0.0, "y0": 25.0, "x1": 10.0, "y1": 45.0},
                                "image_path": None,
                                "metadata": {},
                            },
                            {
                                "block_id": "cert-p1-b3",
                                "document_id": "cert",
                                "page_number": 1,
                                "text": "This is to certify that the work has been completed.",
                                "coordinates": {"x0": 0.0, "y0": 50.0, "x1": 10.0, "y1": 70.0},
                                "image_path": None,
                                "metadata": {},
                            },
                        ],
                        "image_path": None,
                        "embedded_image_paths": [],
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    report = EvidenceBuilder(input_file, output_dir, force=True).build()

    # the two labelled rows become facts; the prose block does not
    assert report.facts_created == 2
    facts = [json.loads(line) for line in (output_dir / "facts.jsonl").read_text(encoding="utf-8").splitlines()]
    by_pred = {f["predicate"]: f for f in facts}
    assert by_pred["contract_value"]["normalized_value"] == "333800000"
    assert by_pred["contract_value"]["raw_value"] == "INR 33.38 Cr"
    assert by_pred["date"]["normalized_value"] == "2011-02-06"


def test_evidence_builder_preserves_xlsx_cell_formula(tmp_path: Path) -> None:
    input_data = {
        "success": [
            {
                "document_id": "sample-xlsx",
                "filename": "sample.xlsx",
                "document_type": "xlsx",
                "source_path": str(tmp_path / "sample.xlsx"),
                "metadata": {"size_bytes": 0},
                "sheets": [
                    {
                        "workbook_id": "sample-xlsx",
                        "sheet_name": "Sheet1",
                        "cells": [
                            {
                                "workbook_id": "sample-xlsx",
                                "sheet_name": "Sheet1",
                                "cell_address": "A1",
                                "value": 42,
                                "formula": "=SUM(A2:A3)",
                                "note": "Test note",
                                "metadata": {"number_format": "General", "data_type": "n"},
                            }
                        ],
                        "metadata": {},
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    builder = EvidenceBuilder(input_file, output_dir, force=True)
    report = builder.build()

    assert report.documents_processed == 1
    assert report.evidence_created == 1
    assert report.facts_created == 1

    evidence = json.loads((output_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert evidence["location"]["sheet_name"] == "Sheet1"
    assert evidence["location"]["cell_address"] == "A1"
    assert evidence["content"]["formula"] == "=SUM(A2:A3)"
    assert evidence["content"]["raw_value"] == 42


def test_evidence_builder_header_semantics_create_facts(tmp_path: Path) -> None:
    input_data = {
        "success": [
            {
                "document_id": "sample-xlsx-semantics",
                "filename": "sample.xlsx",
                "document_type": "xlsx",
                "source_path": str(tmp_path / "sample.xlsx"),
                "metadata": {"size_bytes": 0},
                "sheets": [
                    {
                        "workbook_id": "sample-xlsx-semantics",
                        "sheet_name": "Sheet1",
                        "cells": [
                            {
                                "workbook_id": "sample-xlsx-semantics",
                                "sheet_name": "Sheet1",
                                "cell_address": "A1",
                                "value": "Amount (INR)",
                                "formula": None,
                                "note": None,
                                "metadata": {"number_format": "General", "data_type": "s"},
                            },
                            {
                                "workbook_id": "sample-xlsx-semantics",
                                "sheet_name": "Sheet1",
                                "cell_address": "A2",
                                "value": 1000,
                                "formula": None,
                                "note": None,
                                "metadata": {"number_format": "General", "data_type": "n"},
                            },
                        ],
                        "metadata": {},
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    builder = EvidenceBuilder(input_file, output_dir, force=True)
    report = builder.build()

    assert report.evidence_created == 2
    assert report.facts_created == 1
    facts = (output_dir / "facts.jsonl").read_text(encoding="utf-8").splitlines()
    fact = json.loads(facts[0])
    assert fact["predicate"] == "amount"
    assert fact["raw_value"] == 1000


def test_evidence_builder_generic_numeric_does_not_create_fact(tmp_path: Path) -> None:
    input_data = {
        "success": [
            {
                "document_id": "sample-xlsx-generic",
                "filename": "sample.xlsx",
                "document_type": "xlsx",
                "source_path": str(tmp_path / "sample.xlsx"),
                "metadata": {"size_bytes": 0},
                "sheets": [
                    {
                        "workbook_id": "sample-xlsx-generic",
                        "sheet_name": "Sheet1",
                        "cells": [
                            {
                                "workbook_id": "sample-xlsx-generic",
                                "sheet_name": "Sheet1",
                                "cell_address": "A1",
                                "value": "Description",
                                "formula": None,
                                "note": None,
                                "metadata": {"number_format": "General", "data_type": "s"},
                            },
                            {
                                "workbook_id": "sample-xlsx-generic",
                                "sheet_name": "Sheet1",
                                "cell_address": "A2",
                                "value": 203,
                                "formula": None,
                                "note": None,
                                "metadata": {"number_format": "General", "data_type": "n"},
                            },
                        ],
                        "metadata": {},
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    builder = EvidenceBuilder(input_file, output_dir, force=True)
    report = builder.build()

    assert report.evidence_created == 2
    assert report.facts_created == 0


def test_evidence_builder_idempotent(tmp_path: Path) -> None:
    input_data = {
        "success": [
            {
                "document_id": "sample-idempotent",
                "filename": "sample.xlsx",
                "document_type": "xlsx",
                "source_path": str(tmp_path / "sample.xlsx"),
                "metadata": {"size_bytes": 0},
                "sheets": [
                    {
                        "workbook_id": "sample-idempotent",
                        "sheet_name": "Sheet1",
                        "cells": [
                            {
                                "workbook_id": "sample-idempotent",
                                "sheet_name": "Sheet1",
                                "cell_address": "B2",
                                "value": "33,38,00,000",
                                "formula": None,
                                "note": None,
                                "metadata": {"number_format": "General", "data_type": "n"},
                            }
                        ],
                        "metadata": {},
                    }
                ],
            }
        ],
        "failures": [],
    }
    input_file = tmp_path / "results.json"
    input_file.write_text(json.dumps(input_data), encoding="utf-8")
    output_dir = tmp_path / "evidence_out"
    builder = EvidenceBuilder(input_file, output_dir, force=True)
    report1 = builder.build()
    assert report1.evidence_created == 1

    builder2 = EvidenceBuilder(input_file, output_dir, force=False)
    report2 = builder2.build()
    assert report2.skipped_documents == 1
    assert report2.evidence_created == 0
    assert report2.facts_created == 0
