from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from decimal import Decimal
from .evidence import (
    DocumentRecord,
    EVIDENCE_SCHEMA_VERSION,
    Evidence,
    EvidenceContent,
    Fact,
    FactProvenance,
    NORMALIZATION_VERSION,
    PDFEvidenceLocation,
    ProcessingReport,
    XLSXEvidenceLocation,
    EXTRACTION_VERSION,
    stable_hex,
)
from .document_index import load_document_index
from .normalization import NormalizationError, infer_normalization

logger = logging.getLogger(__name__)


def _jsonl_path(root: Path, category: str) -> Path:
    return root / f"{category}.jsonl"


def _subdir(root: Path, category: str) -> Path:
    path = root / category
    path.mkdir(parents=True, exist_ok=True)
    return path


class EvidenceBuilder:
    def __init__(
        self,
        input_path: Path,
        output_dir: Path,
        cache_dir: Path | None = None,
        force: bool = False,
        no_cache: bool = False,
        document_index_path: Path | None = None,
    ) -> None:
        self.input_path = input_path
        self.output_dir = output_dir
        self.force = force
        self.no_cache = no_cache
        self.cache_dir = cache_dir or output_dir
        self.state_path = self.output_dir / ".state.json"
        self.state: dict[str, Any] = {}
        self.xlsx_header_map: dict[tuple[str, str], dict[int, str]] = {}
        self.document_index = load_document_index(document_index_path) if document_index_path else {}
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._load_state()

    def _load_state(self) -> None:
        if self.force or self.no_cache or not self.state_path.exists():
            self.state = {}
            return
        try:
            with self.state_path.open("r", encoding="utf-8") as handle:
                self.state = json.load(handle)
        except (OSError, ValueError):
            self.state = {}

    def _save_state(self) -> None:
        with self.state_path.open("w", encoding="utf-8") as handle:
            json.dump(self.state, handle, indent=2, ensure_ascii=False)

    def _compute_file_checksum(self, path: Path) -> str | None:
        if not path.exists():
            return None
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(8192), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def build(self) -> ProcessingReport:
        payload = json.loads(self.input_path.read_text(encoding="utf-8"))
        successes = payload.get("success", [])
        failures = payload.get("failures", [])

        documents_dir = _subdir(self.output_dir, "documents")
        evidence_dir = _subdir(self.output_dir, "evidence")
        facts_dir = _subdir(self.output_dir, "facts")

        documents_processed = 0
        evidence_created = 0
        facts_created = 0
        normalization_successes = 0
        normalization_ambiguities = 0
        normalization_failures = 0
        conflicting_facts = 0
        unsupported_values = 0
        skipped_documents = 0

        self.xlsx_header_map = {}
        for document_payload in successes:
            document_id = document_payload["document_id"]
            source_path = Path(document_payload["source_path"])
            doc_checksum = self._compute_file_checksum(source_path)
            state_entry = self.state.get(document_id, {})
            needs_rebuild = self.force or self.no_cache or state_entry.get("checksum") != doc_checksum

            doc_output_path = documents_dir / f"{document_id}.jsonl"
            evidence_output_path = evidence_dir / f"{document_id}.jsonl"
            facts_output_path = facts_dir / f"{document_id}.jsonl"

            if not needs_rebuild and doc_output_path.exists() and evidence_output_path.exists() and facts_output_path.exists():
                logger.info("Skipping unchanged document %s", document_id)
                skipped_documents += 1
                documents_processed += 1
                continue

            documents_processed += 1
            record = self._build_document_record(document_payload, doc_checksum)
            evidence_items = self._build_evidence(document_payload)
            facts = self._build_candidate_facts(document_payload, evidence_items)

            normalization_successes += sum(1 for fact in facts if fact.validation_status == "valid" and fact.normalized_value is not None)
            normalization_ambiguities += sum(1 for fact in facts if fact.validation_status == "ambiguous")
            normalization_failures += sum(1 for fact in facts if fact.validation_status == "failed")
            unsupported_values += sum(1 for fact in facts if fact.normalized_type == "text" and fact.normalized_value is None)

            evidence_created += len(evidence_items)
            facts_created += len(facts)

            self._write_jsonl(doc_output_path, [record])
            self._write_jsonl(evidence_output_path, evidence_items)
            self._write_jsonl(facts_output_path, facts)

            self.state[document_id] = {
                "checksum": doc_checksum,
                "source_path": str(source_path),
                "extraction_version": document_payload.get("extraction_version", EXTRACTION_VERSION),
                "evidence_count": len(evidence_items),
                "facts_count": len(facts),
            }

        self._save_state()
        self._aggregate_outputs(documents_dir, evidence_dir, facts_dir)

        report = ProcessingReport(
            documents_processed=documents_processed,
            evidence_created=evidence_created,
            facts_created=facts_created,
            normalization_successes=normalization_successes,
            normalization_ambiguities=normalization_ambiguities,
            normalization_failures=normalization_failures,
            conflicting_facts=conflicting_facts,
            unsupported_values=unsupported_values,
            skipped_documents=skipped_documents,
            extraction_version=EXTRACTION_VERSION,
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            normalization_version=NORMALIZATION_VERSION,
            input_checksum=self._compute_file_checksum(self.input_path),
        )

        report_path = self.output_dir / "report.json"
        with report_path.open("w", encoding="utf-8") as handle:
            json.dump(report.model_dump(mode="json"), handle, indent=2, ensure_ascii=False)

        return report

    def _write_jsonl(self, path: Path, records: list[BaseModel]) -> None:
        with path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def _aggregate_outputs(self, documents_dir: Path, evidence_dir: Path, facts_dir: Path) -> None:
        for category, directory in [("documents", documents_dir), ("evidence", evidence_dir), ("facts", facts_dir)]:
            output_path = _jsonl_path(self.output_dir, category)
            with output_path.open("w", encoding="utf-8") as handle:
                for item_path in sorted(directory.iterdir()):
                    text = item_path.read_text(encoding="utf-8")
                    handle.write(text)

    def _build_document_record(self, payload: dict[str, Any], checksum: str | None) -> DocumentRecord:
        filename = payload["filename"]
        source_path = Path(payload["source_path"])
        metadata = dict(payload.get("metadata", {}))
        # doc_id in document_index.csv matches our document_id (file stem) for PDFs,
        # but not for workbooks (synthetic doc_id vs descriptive filename) - fall back
        # to matching on the bare filename for those. See document_index.py.
        hackathon_doc_type = self.document_index.get(payload["document_id"]) or self.document_index.get(filename)
        if hackathon_doc_type:
            metadata["hackathon_doc_type"] = hackathon_doc_type
        return DocumentRecord(
            document_id=payload["document_id"],
            filename=filename,
            source_path=source_path,
            document_type=payload["document_type"],
            extension=Path(filename).suffix.lower(),
            size_bytes=payload["metadata"].get("size_bytes", 0),
            checksum=checksum,
            page_count=len(payload.get("pages", [])) if payload["document_type"] == "pdf" else None,
            sheet_count=len(payload.get("sheets", [])) if payload["document_type"] == "xlsx" else None,
            extraction_status="success",
            extraction_version=payload.get("extraction_version", EXTRACTION_VERSION),
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            metadata=metadata,
        )

    def _build_evidence(self, payload: dict[str, Any]) -> list[Evidence]:
        source_type = payload["document_type"]
        if source_type == "pdf":
            return self._build_pdf_evidence(payload)
        if source_type == "xlsx":
            return self._build_xlsx_evidence(payload)
        raise ValueError(f"Unsupported document type: {source_type}")

    def _build_pdf_evidence(self, payload: dict[str, Any]) -> list[Evidence]:
        document_id = payload["document_id"]
        source_path = Path(payload["source_path"])
        evidence_items: list[Evidence] = []
        for page in payload.get("pages", []):
            page_num = page["page_number"]
            for block in page.get("blocks", []):
                coords = block["coordinates"] or {}
                bbox = [coords.get("x0", 0.0), coords.get("y0", 0.0), coords.get("x1", 0.0), coords.get("y1", 0.0)]
                raw_value = block.get("text", "")
                evidence_id = stable_hex(
                    document_id,
                    source_path,
                    "pdf",
                    page_num,
                    block.get("block_id", ""),
                    raw_value,
                    EXTRACTION_VERSION,
                )
                evidence_items.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_type="pdf",
                        document_id=document_id,
                        source_path=source_path,
                        filename=payload["filename"],
                        extraction_method="native_text",
                        extraction_version=payload.get("extraction_version", EXTRACTION_VERSION),
                        extraction_confidence=block.get("metadata", {}).get("confidence"),
                        content=EvidenceContent(
                            raw_value=raw_value,
                            text=raw_value,
                            metadata=block.get("metadata", {}),
                        ),
                        location=PDFEvidenceLocation(
                            source_type="pdf",
                            page_number=page_num,
                            block_id=block.get("block_id", ""),
                            bbox=bbox,
                        ),
                        metadata={
                            "page_number": page_num,
                            "block_id": block.get("block_id"),
                            "image_path": block.get("image_path"),
                        },
                    )
                )
        return evidence_items

    def _build_xlsx_evidence(self, payload: dict[str, Any]) -> list[Evidence]:
        document_id = payload["document_id"]
        source_path = Path(payload["source_path"])
        evidence_items: list[Evidence] = []
        for sheet in payload.get("sheets", []):
            workbook_id = sheet["workbook_id"]
            sheet_name = sheet["sheet_name"]
            header_map: dict[int, str] = {}
            for cell in sheet.get("cells", []):
                address = cell["cell_address"]
                row = int(re.sub(r"[^0-9]", "", address)) if address else 0
                column = self._column_index(address)
                if row == 1 and isinstance(cell.get("value"), str):
                    header_map[column] = cell["value"].strip()
            self.xlsx_header_map[(document_id, sheet_name)] = header_map

            for cell in sheet.get("cells", []):
                address = cell["cell_address"]
                row = int(re.sub(r"[^0-9]", "", address)) if address else 0
                column = self._column_index(address)
                raw_value = cell.get("value")
                evidence_id = stable_hex(
                    document_id,
                    source_path,
                    "xlsx",
                    workbook_id,
                    sheet_name,
                    address,
                    raw_value,
                    cell.get("formula"),
                    EXTRACTION_VERSION,
                )
                evidence_items.append(
                    Evidence(
                        evidence_id=evidence_id,
                        source_type="xlsx",
                        document_id=document_id,
                        source_path=source_path,
                        filename=payload["filename"],
                        extraction_method="native_workbook",
                        extraction_version=payload.get("extraction_version", EXTRACTION_VERSION),
                        content=EvidenceContent(
                            raw_value=raw_value,
                            formula=cell.get("formula"),
                            note=cell.get("note"),
                            metadata=cell.get("metadata", {}),
                        ),
                        location=XLSXEvidenceLocation(
                            source_type="xlsx",
                            workbook_id=workbook_id,
                            sheet_name=sheet_name,
                            cell_address=address,
                            row=row,
                            column=column,
                        ),
                        metadata={
                            "number_format": cell.get("metadata", {}).get("number_format"),
                            "data_type": cell.get("metadata", {}).get("data_type"),
                        },
                    )
                )
        return evidence_items

    def _build_candidate_facts(self, document_payload: dict[str, Any], evidence_items: list[Evidence]) -> list[Fact]:
        candidate_facts: list[Fact] = []
        for evidence in evidence_items:
            predicate = self._predicate_for_evidence(document_payload, evidence)
            if predicate is None:
                continue
            fact = self._build_fact(evidence, predicate)
            if fact is not None:
                candidate_facts.append(fact)
        return candidate_facts

    def _predicate_for_evidence(self, document_payload: dict[str, Any], evidence: Evidence) -> str | None:
        if evidence.source_type == "xlsx":
            return self._predicate_for_xlsx_evidence(evidence)
        if evidence.source_type == "pdf":
            return self._predicate_for_pdf_evidence(document_payload, evidence)
        return None

    def _predicate_for_xlsx_evidence(self, evidence: Evidence) -> str | None:
        sheet_key = (evidence.document_id, evidence.location.sheet_name)
        header_map = self.xlsx_header_map.get(sheet_key, {})
        header_text = header_map.get(evidence.location.column)

        if evidence.content.formula:
            if header_text:
                predicate = self._predicate_from_header(header_text)
                if predicate and self._predicate_matches_value(predicate, evidence.content.raw_value):
                    return predicate
            return "computed_value"

        if evidence.location.row <= 1:
            return None
        if header_text is None:
            return None

        predicate = self._predicate_from_header(header_text)
        if predicate is None:
            return None
        if self._predicate_matches_value(predicate, evidence.content.raw_value):
            return predicate
        return None

    def _predicate_for_pdf_evidence(self, document_payload: dict[str, Any], evidence: Evidence) -> str | None:
        raw_text = str(evidence.content.raw_value or "").strip()
        page = next(
            (page for page in document_payload.get("pages", []) if page.get("page_number") == evidence.location.page_number),
            None,
        )

        if page is not None and raw_text:
            escaped_value = re.escape(raw_text)
            match = re.search(rf"(?P<label>[^\n:]+):\s*{escaped_value}", str(page.get("text", "")), flags=re.IGNORECASE)
            if match:
                predicate = self._predicate_from_header(match.group("label"))
                if predicate is not None:
                    return predicate

        if ":" in raw_text:
            label, _, value = raw_text.partition(":")
            if value.strip():
                predicate = self._predicate_from_header(label)
                if predicate is not None:
                    return predicate
        return None

    def _predicate_from_header(self, header_text: str) -> str | None:
        normalized = header_text.strip().lower()
        normalized = re.sub(r"[\(\)%₹,.]", "", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip()
        if any(keyword in normalized for keyword in ["contract value", "contract amount"]):
            return "contract_value"
        if any(keyword in normalized for keyword in ["amount", "price", "total", "value", "cost"]):
            return "amount"
        if any(keyword in normalized for keyword in ["rate", "unit rate", "rate inr"]):
            return "rate"
        if any(keyword in normalized for keyword in ["quantity", "qty"]):
            return "quantity"
        if any(keyword in normalized for keyword in ["item no", "item number", "item", "sr no", "s no", "s/no"]):
            return "item_number"
        if any(keyword in normalized for keyword in ["description", "details", "particulars"]):
            return "description"
        if any(keyword in normalized for keyword in ["date", "day", "month", "year"]):
            return "date"
        if any(keyword in normalized for keyword in ["unit", "uom"]):
            return "unit"
        return None

    def _predicate_matches_value(self, predicate: str, raw_value: Any) -> bool:
        if raw_value is None:
            return False

        if predicate == "computed_value":
            return True

        normalized_type = None
        try:
            result = infer_normalization(raw_value)
            normalized_type = result.normalized_type
        except NormalizationError:
            if isinstance(raw_value, str) and raw_value.strip():
                normalized_type = "text"
            else:
                return False

        if predicate == "amount":
            return normalized_type in {"currency_inr", "number"}
        if predicate == "rate":
            return normalized_type in {"number", "currency_inr", "percentage"}
        if predicate == "quantity":
            return normalized_type == "number"
        if predicate == "contract_value":
            return normalized_type in {"currency_inr", "number"}
        if predicate == "date":
            return normalized_type == "date"
        if predicate == "unit":
            return normalized_type in {"unit", "text"}
        if predicate == "description":
            return normalized_type == "text"
        if predicate == "item_number":
            return normalized_type in {"text", "number"}
        return False

    def _column_index(self, address: str | None) -> int:
        if not address:
            return 0
        letters = ""
        for char in address:
            if char.isalpha():
                letters += char.upper()
        result = 0
        for char in letters:
            result = result * 26 + (ord(char) - ord("A") + 1)
        return result

    def _build_fact(self, evidence: Evidence, predicate: str) -> Fact | None:
        raw_value = evidence.content.raw_value
        if raw_value is None:
            raw_value = evidence.content.text or ""

        normalization_method = "infer_normalization"
        normalized_value = None
        normalized_type = "text"
        original_unit = None
        normalized_unit = None
        validation_status = "failed"
        confidence = Decimal("0.0")

        result = None
        try:
            result = infer_normalization(raw_value)
            normalized_value = result.normalized_value
            normalized_type = result.normalized_type
            original_unit = result.original_unit
            normalized_unit = result.normalized_unit
            validation_status = result.status
            confidence = result.confidence
            normalization_method = f"infer_{result.normalized_type}"
        except NormalizationError:
            if isinstance(raw_value, str) and raw_value.strip():
                normalized_type = "text"
                validation_status = "valid"
                normalization_method = "none"
                confidence = Decimal("1.0")
            else:
                return None

        fact_id = stable_hex(
            evidence.evidence_id,
            predicate,
            raw_value,
            normalized_value,
            NORMALIZATION_VERSION,
        )
        return Fact(
            fact_id=fact_id,
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            source_path=evidence.source_path,
            subject_mention=None,
            predicate=predicate,
            raw_value=raw_value,
            normalized_value=normalized_value,
            normalized_type=normalized_type,
            normalized_unit=normalized_unit,
            original_unit=original_unit,
            extraction_method=evidence.extraction_method,
            normalization_method=normalization_method,
            extraction_confidence=evidence.extraction_confidence,
            normalization_confidence=confidence,
            validation_status=validation_status,
            provenance=FactProvenance(
                evidence_id=evidence.evidence_id,
                document_id=evidence.document_id,
                source_path=evidence.source_path,
                location=evidence.location,
            ),
            metadata={},
        )

    def _predicate_for_type(self, normalized_type: str) -> str:
        return {
            "currency_inr": "monetary_value",
            "percentage": "percentage",
            "date": "date",
            "number": "number",
            "unit": "unit",
        }.get(normalized_type, "text")

    def _mark_conflicts(self, facts: list[Fact]) -> int:
        # Conflict detection is deferred to later chunks with semantic identity.
        return 0
