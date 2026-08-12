from pathlib import Path

import fitz
import pytest

from jaw_ingest.extraction import extract_pdf_document
from jaw_ingest.cache import CacheManager


def test_extract_pdf_document_handles_block_tuple_structure(tmp_path: Path) -> None:
    pdf_path = tmp_path / "sample.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n")
    cache_manager = CacheManager(tmp_path / ".cache", enabled=True)

    document = extract_pdf_document(pdf_path, cache_manager=cache_manager)
    assert document.document_id == "sample"
    assert isinstance(document.pages, list)
    assert all(isinstance(block.text, str) for page in document.pages for block in page.blocks)
    assert all(block.coordinates.x0 >= 0 for page in document.pages for block in page.blocks)


def test_fitx_blocks_tuple_length(tmp_path: Path) -> None:
    sample_pdf = tmp_path / "sample2.pdf"
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "Hello")
    doc.save(sample_pdf)
    doc.close()

    doc = fitz.open(sample_pdf)
    page = doc[0]
    blocks = page.get_text("blocks")
    assert blocks, "Expected at least one text block from the generated PDF"
    assert all(len(block) >= 5 for block in blocks)
    doc.close()
