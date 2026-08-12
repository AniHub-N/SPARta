from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


class DocumentType(str, Enum):
    pdf = "pdf"
    xlsx = "xlsx"
    unknown = "unknown"


class Coordinate(BaseModel):
    x0: float = Field(..., ge=0)
    y0: float = Field(..., ge=0)
    x1: float = Field(..., ge=0)
    y1: float = Field(..., ge=0)


class EvidenceBlock(BaseModel):
    block_id: str
    document_id: str
    page_number: int | None = None
    text: str = ""
    coordinates: Coordinate | None = None
    image_path: Path | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Page(BaseModel):
    page_number: int
    text: str = ""
    blocks: list[EvidenceBlock] = Field(default_factory=list)
    image_path: Path | None = None
    embedded_image_paths: list[Path] = Field(default_factory=list)


class BaseDocument(BaseModel):
    document_id: str
    filename: Path
    document_type: DocumentType
    source_path: Path
    metadata: dict[str, Any] = Field(default_factory=dict)


class PDFDocument(BaseDocument):
    pages: list[Page] = Field(default_factory=list)


class Workbook(BaseDocument):
    sheets: list[Sheet] = Field(default_factory=list)

    @property
    def workbook_id(self) -> str:
        return self.document_id


class Cell(BaseModel):
    workbook_id: str
    sheet_name: str
    cell_address: str
    value: Any | None = None
    formula: str | None = None
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Sheet(BaseModel):
    workbook_id: str
    sheet_name: str
    cells: list[Cell] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
