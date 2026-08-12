from pathlib import Path
import json

import fitz
import pytest

from jaw_ingest import cli
from jaw_ingest.cli import main


def test_cli_writes_structured_failure(tmp_path: Path, monkeypatch) -> None:
    source_dir = tmp_path / "source"
    output_file = tmp_path / "results.json"
    source_dir.mkdir()
    valid_pdf = source_dir / "sample.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(valid_pdf)
    doc.close()

    def failing_extract(path, cache_manager):
        raise RuntimeError("test failure")

    monkeypatch.setattr(cli, "_parse_args", lambda: type(
        "Args",
        (),
        {
            "source": source_dir,
            "output": output_file,
            "cache_dir": tmp_path / ".cache",
            "no_cache": True,
            "log_level": "INFO",
        },
    )())
    monkeypatch.setattr(cli, "extract_document", failing_extract)

    exit_code = main()
    assert exit_code == 0
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert "success" in data
    assert "failures" in data
    assert len(data["success"]) == 0
    assert len(data["failures"]) == 1
    failure = data["failures"][0]
    assert failure["document_id"] == "sample"
    assert failure["path"].endswith("sample.pdf")
    assert failure["error_type"] == "RuntimeError"
    assert failure["error_message"] == "test failure"


def test_cli_serializes_success(tmp_path: Path, monkeypatch) -> None:
    source_dir = tmp_path / "source"
    output_file = tmp_path / "results.json"
    source_dir.mkdir()
    test_pdf = source_dir / "sample.pdf"
    test_pdf.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")

    monkeypatch.setattr("jaw_ingest.cli._parse_args", lambda: type(
        "Args",
        (),
        {
            "source": source_dir,
            "output": output_file,
            "cache_dir": tmp_path / ".cache",
            "no_cache": True,
            "log_level": "INFO",
        },
    )())

    exit_code = main()
    assert exit_code == 0
    data = json.loads(output_file.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert "success" in data
    assert "failures" in data


def test_cli_discovers_files_in_type_subdirectories(tmp_path: Path, monkeypatch) -> None:
    # The JAW hackathon corpus is laid out as documents/<type>/*.pdf - a corpus with
    # nothing at the top level. Non-recursive discovery would silently find zero files.
    source_dir = tmp_path / "source"
    (source_dir / "completion_certificate").mkdir(parents=True)
    (source_dir / "reference_letter").mkdir(parents=True)
    output_file = tmp_path / "results.json"

    doc = fitz.open()
    doc.new_page()
    doc.save(source_dir / "completion_certificate" / "cc-001.pdf")
    doc.close()

    doc2 = fitz.open()
    doc2.new_page()
    doc2.save(source_dir / "reference_letter" / "rl-001.pdf")
    doc2.close()

    processed_paths = []

    def fake_extract(path, cache_manager):
        processed_paths.append(path)
        raise RuntimeError("stop before real extraction - only checking discovery")

    monkeypatch.setattr(cli, "_parse_args", lambda: type(
        "Args",
        (),
        {
            "source": source_dir,
            "output": output_file,
            "cache_dir": tmp_path / ".cache",
            "no_cache": True,
            "log_level": "INFO",
        },
    )())
    monkeypatch.setattr(cli, "extract_document", fake_extract)

    exit_code = main()

    assert exit_code == 0
    assert len(processed_paths) == 2
    names = {p.name for p in processed_paths}
    assert names == {"cc-001.pdf", "rl-001.pdf"}
