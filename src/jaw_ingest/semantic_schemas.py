from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

SEMANTIC_SCHEMA_VERSION = "1.0"


class AssertionProvenance(BaseModel):
    evidence_id: str
    document_id: str
    extraction_method: str = "llm_semantic_extraction"
    extraction_version: str = SEMANTIC_SCHEMA_VERSION


class EntityMentionAssertion(BaseModel):
    """A candidate entity mention as extracted by the LLM, before resolution."""

    mention_text: str
    entity_type: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    source_ref: str = Field(
        default="",
        description="For batched (multi-fragment) extraction only: the short fragment tag "
        "(e.g. 'E3') this assertion came from. Empty when extracting one evidence item at a time.",
    )


class RelationshipAssertionRaw(BaseModel):
    """A candidate relationship as extracted, referencing mentions by their surface text."""

    subject_mention_text: str
    predicate: str
    object_mention_text: str
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    source_ref: str = Field(default="", description="Same as EntityMentionAssertion.source_ref.")


class AttributeAssertionRaw(BaseModel):
    """A candidate attribute (entity -> literal value) as extracted."""

    subject_mention_text: str
    predicate: str
    value: Any
    value_type: str = "text"
    confidence: float = Field(ge=0.0, le=1.0, default=0.8)
    source_ref: str = Field(default="", description="Same as EntityMentionAssertion.source_ref.")


class SemanticExtractionResult(BaseModel):
    """The structured object an LLM call must validate against for one piece of evidence."""

    entities: list[EntityMentionAssertion] = Field(default_factory=list)
    relationships: list[RelationshipAssertionRaw] = Field(default_factory=list)
    attributes: list[AttributeAssertionRaw] = Field(default_factory=list)

    @staticmethod
    def json_schema() -> dict[str, Any]:
        return SemanticExtractionResult.model_json_schema()


class ExtractionFailure(BaseModel):
    """Explicit failure record for a semantic extraction attempt. Never silently dropped."""

    reason: str
    detail: str | None = None
    raw_output: str | None = None


# --- Post-resolution, persisted world-model types -----------------------------------


class EntityMention(BaseModel):
    """A grounded, provenance-bearing mention, prior to entity resolution/merging."""

    mention_id: str
    mention_text: str
    entity_type: str
    document_id: str
    evidence_id: str
    extraction_confidence: float = Field(ge=0.0, le=1.0)
    provenance: AssertionProvenance


class CanonicalEntity(BaseModel):
    """A resolved, canonical entity. Multiple mentions may resolve to one canonical entity."""

    entity_id: str
    entity_type: str
    canonical_name: str
    aliases: list[str] = Field(default_factory=list)
    mention_ids: list[str] = Field(default_factory=list)
    resolution_status: str = "resolved"  # resolved | ambiguous | unresolved
    resolution_confidence: float = Field(ge=0.0, le=1.0, default=0.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class Relationship(BaseModel):
    """A generic subject-predicate-object relationship between two canonical entities."""

    relationship_id: str
    subject_entity_id: str
    predicate: str
    object_entity_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_id: str
    document_id: str
    provenance: dict[str, Any] = Field(default_factory=dict)


class Attribute(BaseModel):
    """A generic entity -> literal-value attribute, distinct from an entity-to-entity relationship."""

    attribute_id: str
    entity_id: str
    predicate: str
    value: Any
    value_type: str = "text"
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_id: str
    document_id: str
    provenance: dict[str, Any] = Field(default_factory=dict)
