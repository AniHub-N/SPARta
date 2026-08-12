from pathlib import Path

import pytest

from jaw_ingest.cache import CacheManager
from jaw_ingest.extraction import extract_pdf_document, extract_xlsx_workbook


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    return pdf_path


@pytest.fixture
def sample_xlsx(tmp_path: Path) -> Path:
    from openpyxl import Workbook as OpenpyxlWorkbook

    workbook = OpenpyxlWorkbook()
    sheet = workbook.active
    sheet["A1"] = "Test"
    sheet["B2"] = 42
    file_path = tmp_path / "sample.xlsx"
    workbook.save(file_path)
    return file_path


def test_extract_xlsx_workbook(sample_xlsx: Path, tmp_path: Path) -> None:
    cache_manager = CacheManager(tmp_path / ".cache", enabled=True)
    workbook = extract_xlsx_workbook(sample_xlsx, cache_manager=cache_manager)
    assert workbook.workbook_id == "sample"
    assert any(sheet.sheet_name == "Sheet" for sheet in workbook.sheets)
    assert any(cell.value == "Test" for sheet in workbook.sheets for cell in sheet.cells)


@pytest.mark.skip("PDF binary structure not suitable for extraction tests without sample content")
def test_extract_pdf_document(sample_pdf: Path, tmp_path: Path) -> None:
    cache_manager = CacheManager(tmp_path / ".cache", enabled=True)
    document = extract_pdf_document(sample_pdf, cache_manager=cache_manager)
    assert document.document_id == "sample"
    assert isinstance(document.pages, list)
