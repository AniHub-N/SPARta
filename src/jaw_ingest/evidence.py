from __future__ import annotations

import hashlib
from decimal import Decimal
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

EVIDENCE_SCHEMA_VERSION = "1.0"
NORMALIZATION_VERSION = "1.0"
EXTRACTION_VERSION = "1.0"


def stable_hex(*parts: Any) -> str:
    digest = hashlib.sha256()
    for part in parts:
        digest.update(str(part).encode("utf-8"))
    return digest.hexdigest()


class DocumentRecord(BaseModel):
    document_id: str
    filename: str
    source_path: Path
    document_type: str
    extension: str
    size_bytes: int
    checksum: str | None = None
    page_count: int | None = None
    sheet_count: int | None = None
    extraction_status: Literal["success", "failure"] = "success"
    extraction_version: str = EXTRACTION_VERSION
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceLocation(BaseModel):
    source_type: Literal["pdf", "xlsx"]


class PDFEvidenceLocation(EvidenceLocation):
    page_number: int
    block_id: str
    bbox: list[float]


class XLSXEvidenceLocation(EvidenceLocation):
    workbook_id: str
    sheet_name: str
    cell_address: str
    row: int
    column: int


class EvidenceContent(BaseModel):
    raw_value: Any
    text: str | None = None
    formula: str | None = None
    note: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Evidence(BaseModel):
    evidence_id: str
    source_type: Literal["pdf", "xlsx"]
    document_id: str
    source_path: Path
    filename: str
    extraction_method: str
    extraction_version: str = EXTRACTION_VERSION
    extraction_confidence: float | None = None
    content: EvidenceContent
    location: PDFEvidenceLocation | XLSXEvidenceLocation
    metadata: dict[str, Any] = Field(default_factory=dict)


class FactProvenance(BaseModel):
    evidence_id: str
    document_id: str
    source_path: Path
    location: EvidenceLocation


class Fact(BaseModel):
    fact_id: str
    evidence_id: str
    document_id: str
    source_path: Path
    subject_mention: str | None = None
    predicate: str
    raw_value: Any
    normalized_value: Any | None = None
    normalized_type: str
    normalized_unit: str | None = None
    original_unit: str | None = None
    extraction_method: str
    normalization_method: str
    extraction_confidence: float | None = None
    normalization_confidence: Decimal = Decimal("1.0")
    validation_status: Literal["valid", "ambiguous", "failed", "conflicting"] = "valid"
    provenance: FactProvenance
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceCollection(BaseModel):
    documents: list[DocumentRecord] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    facts: list[Fact] = Field(default_factory=list)


class ProcessingReport(BaseModel):
    documents_processed: int
    evidence_created: int
    facts_created: int
    normalization_successes: int
    normalization_ambiguities: int
    normalization_failures: int
    conflicting_facts: int
    unsupported_values: int
    skipped_documents: int
    extraction_version: str = EXTRACTION_VERSION
    evidence_schema_version: str = EVIDENCE_SCHEMA_VERSION
    normalization_version: str = NORMALIZATION_VERSION
    input_checksum: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
