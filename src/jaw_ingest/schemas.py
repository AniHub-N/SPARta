from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class FieldSpec(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    value_type: str = "string"
    description: str | None = None

    def matches_label(self, label: str) -> bool:
        normalized = label.strip().lower()
        if normalized == self.name.replace("_", " "):
            return True
        return any(normalized == alias.strip().lower() for alias in self.aliases)


class ExtractionSchema(BaseModel):
    name: str
    fields: list[FieldSpec] = Field(default_factory=list)
    description: str | None = None

    def field_for_label(self, label: str) -> FieldSpec | None:
        for field in self.fields:
            if field.matches_label(label):
                return field
        return None

    def field_names(self) -> list[str]:
        return [field.name for field in self.fields]


class SchemaRegistry:
    def __init__(self) -> None:
        self._schemas: dict[str, ExtractionSchema] = {}
        self.register_default_schemas()

    def register_schema(self, schema: ExtractionSchema) -> None:
        self._schemas[schema.name] = schema

    def get_schema(self, schema_name: str) -> ExtractionSchema | None:
        return self._schemas.get(schema_name)

    def get_schema_for_document(self, document: Any) -> ExtractionSchema:
        schema_name = document.metadata.get("schema_name") or document.metadata.get("extraction_schema")
        if schema_name and schema_name in self._schemas:
            return self._schemas[schema_name]

        if isinstance(document, dict):
            filename = document.get("filename", "")
        else:
            filename = getattr(document, "filename", "")

        normalized = str(filename).lower()
        for schema_name, schema in self._schemas.items():
            if schema_name in normalized:
                return schema

        return self._schemas["generic_document"]

    def register_default_schemas(self) -> None:
        self.register_schema(
            ExtractionSchema(
                name="generic_document",
                fields=[
                    FieldSpec(name="project_name", aliases=["project", "project name"], value_type="string"),
                    FieldSpec(name="client_name", aliases=["client", "client name"], value_type="string"),
                    FieldSpec(name="contract_value", aliases=["contract value", "value"], value_type="currency"),
                    FieldSpec(name="completion_date", aliases=["completion date", "date of completion"], value_type="date"),
                    FieldSpec(name="issue_date", aliases=["issue date", "date"], value_type="date"),
                ],
                description="A generic schema for document-level extraction.",
            )
        )

        self.register_schema(
            ExtractionSchema(
                name="completion_certificate",
                fields=[
                    FieldSpec(name="project_name", aliases=["project", "project name"], value_type="string"),
                    FieldSpec(name="client_name", aliases=["client", "client name"], value_type="string"),
                    FieldSpec(name="contract_value", aliases=["contract value", "value"], value_type="currency"),
                    FieldSpec(name="completion_date", aliases=["completion date", "date of completion"], value_type="date"),
                    FieldSpec(name="start_date", aliases=["start date", "date of commencement"], value_type="date"], description="Project start date."),
                    FieldSpec(name="performance_grade", aliases=["performance grade", "grade"], value_type="string"),
                    FieldSpec(name="officer_name", aliases=["officer name", "issued by"], value_type="string"),
                ],
                description="Schema for completion certificates.",
            )
        )

        self.register_schema(
            ExtractionSchema(
                name="personnel_certificate",
                fields=[
                    FieldSpec(name="person_name", aliases=["name", "certificate holder"], value_type="string"),
                    FieldSpec(name="certification", aliases=["certification", "certificate"], value_type="string"),
                    FieldSpec(name="issue_date", aliases=["issue date", "date of issue"], value_type="date"], value_type="date"),
                    FieldSpec(name="project_reference", aliases=["project", "project reference"], value_type="string"),
                ],
                description="Schema for personnel certificates.",
            )
        )

        self.register_schema(
            ExtractionSchema(
                name="performance_bond",
                fields=[
                    FieldSpec(name="project_name", aliases=["project", "project name"], value_type="string"),
                    FieldSpec(name="beneficiary", aliases=["beneficiary", "beneficiary name"], value_type="string"),
                    FieldSpec(name="guarantee_amount", aliases=["guarantee amount", "amount"], value_type="currency"),
                    FieldSpec(name="issue_date", aliases=["issue date", "date of issue"], value_type="date"], value_type="date"),
                    FieldSpec(name="expiry_date", aliases=["expiry date", "expiration date"], value_type="date"], value_type="date"),
                ],
                description="Schema for performance bonds.",
            )
        )
