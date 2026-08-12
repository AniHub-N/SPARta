from __future__ import annotations

from pathlib import Path

from jaw_ingest.document_index import load_document_index


def test_missing_file_returns_empty_mapping_not_an_error(tmp_path: Path) -> None:
    assert load_document_index(tmp_path / "nope.csv") == {}


def test_loads_doc_id_and_filename_keyed_mapping(tmp_path: Path) -> None:
    # PDFs: doc_id equals our own document_id (the file stem). Workbooks: doc_id is a
    # synthetic code that does NOT match the file stem - only the bare filename does.
    # Both key spaces must be populated so callers can try either.
    csv_path = tmp_path / "document_index.csv"
    csv_path.write_text(
        "doc_id,doc_type,filename,size_bytes\n"
        "DOC-AR-2024,annual_report,annual_report/DOC-AR-2024.pdf,118743\n"
        "DOC-XL-BOQ-071,boq_workbook,workbooks/BOQ_and_Measurements_Contract_71.xlsx,9917\n",
        encoding="utf-8",
    )

    mapping = load_document_index(csv_path)

    assert mapping["DOC-AR-2024"] == "annual_report"
    assert mapping["DOC-XL-BOQ-071"] == "boq_workbook"
    assert mapping["BOQ_and_Measurements_Contract_71.xlsx"] == "boq_workbook"
    # The workbook's file stem (our own document_id) is NOT a doc_id in this corpus -
    # it must not accidentally resolve to anything.
    assert "BOQ_and_Measurements_Contract_71" not in mapping


def test_skips_rows_missing_doc_type(tmp_path: Path) -> None:
    csv_path = tmp_path / "document_index.csv"
    csv_path.write_text(
        "doc_id,doc_type,filename,size_bytes\n"
        "DOC-1,,x/DOC-1.pdf,100\n"
        "DOC-3,completion_certificate,x/DOC-3.pdf,100\n",
        encoding="utf-8",
    )

    mapping = load_document_index(csv_path)

    assert mapping == {"DOC-3": "completion_certificate", "DOC-3.pdf": "completion_certificate"}
