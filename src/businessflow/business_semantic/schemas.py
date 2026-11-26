from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from businessflow.schemas import StrictModel

SemanticEntityType = Literal[
    "business_unit",
    "department",
    "employee",
    "project",
    "customer",
    "supplier",
    "product",
    "sales_transaction",
    "expense",
    "business_activity",
    "operational_record",
]
SemanticReferenceKind = Literal[
    "universal_record", "employee", "department", "business_unit", "project"
]
SemanticRelationshipType = Literal[
    "customer_has_sales_transaction",
    "supplier_has_expense",
    "employee_belongs_to_department",
    "department_in_business_unit",
    "project_assigned_to_employee",
]


class SemanticCatalogResponse(StrictModel):
    catalog_version: str
    entities: list[dict[str, object]]
    relationships: list[dict[str, object]]
    note: str


class SemanticEntityCreateRequest(StrictModel):
    entity_type: SemanticEntityType
    entity_key: Annotated[
        str, StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    ]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None


class SemanticEntityResponse(StrictModel):
    id: UUID
    entity_type: SemanticEntityType
    entity_key: str
    display_name: str
    description: str | None
    created_by_user_id: UUID
    created_at: datetime


class SemanticEntityPage(StrictModel):
    items: list[SemanticEntityResponse]
    total: int
    limit: int
    offset: int


class SemanticMappingCreateRequest(StrictModel):
    reference_kind: SemanticReferenceKind
    reference_id: UUID
    semantic_entity_id: UUID
    expected_version: int = Field(ge=0)
    review_note: Annotated[str, StringConstraints(min_length=1, max_length=500)]
    correction_reason: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None

    @model_validator(mode="after")
    def normalize_notes(self) -> SemanticMappingCreateRequest:
        review_note = self.review_note.strip()
        if not review_note:
            raise ValueError("review_note must contain a non-whitespace character")
        object.__setattr__(self, "review_note", review_note)
        if self.correction_reason is not None:
            reason = self.correction_reason.strip()
            if not reason:
                raise ValueError("correction_reason must contain a non-whitespace character")
            object.__setattr__(self, "correction_reason", reason)
        return self


class SemanticMappingResponse(StrictModel):
    id: UUID
    semantic_entity_id: UUID
    entity_type: SemanticEntityType
    reference_kind: SemanticReferenceKind
    reference_id: UUID
    source_id: UUID | None
    source_mapping_profile_version_id: UUID | None
    source_revision: int | None
    source_revision_current: bool | None
    version: int
    supersedes_mapping_version_id: UUID | None
    review_note: str
    correction_reason: str | None
    created_by_user_id: UUID
    created_at: datetime
    is_current: bool


class SemanticMappingPage(StrictModel):
    items: list[SemanticMappingResponse]
    total: int
    limit: int
    offset: int


class SemanticRelationshipCreateRequest(StrictModel):
    subject_entity_id: UUID
    relationship_type: SemanticRelationshipType
    object_entity_id: UUID
    expected_version: int = Field(ge=0)
    status: Literal["active", "retracted"] = "active"
    evidence_kind: Literal["source_verified", "manual_assertion"]
    subject_mapping_version_id: UUID | None = None
    object_mapping_version_id: UUID | None = None
    evidence_note: Annotated[str, StringConstraints(min_length=1, max_length=500)]

    @model_validator(mode="after")
    def validate_evidence_shape(self) -> SemanticRelationshipCreateRequest:
        note = self.evidence_note.strip()
        if not note:
            raise ValueError("evidence_note must contain a non-whitespace character")
        object.__setattr__(self, "evidence_note", note)
        has_subject = self.subject_mapping_version_id is not None
        has_object = self.object_mapping_version_id is not None
        if has_subject != has_object:
            raise ValueError("supply both mapping versions or neither")
        if self.evidence_kind == "source_verified" and not has_subject:
            raise ValueError("source_verified relationships require both reviewed mappings")
        return self


class SemanticRelationshipResponse(StrictModel):
    id: UUID
    subject_entity_id: UUID
    relationship_type: SemanticRelationshipType
    object_entity_id: UUID
    version: int
    status: Literal["active", "retracted"]
    evidence_kind: Literal["source_verified", "manual_assertion"]
    subject_mapping_version_id: UUID | None
    object_mapping_version_id: UUID | None
    evidence_note: str
    supersedes_relationship_version_id: UUID | None
    created_by_user_id: UUID
    created_at: datetime
    is_current: bool
    analytically_usable: bool


class SemanticRelationshipPage(StrictModel):
    items: list[SemanticRelationshipResponse]
    total: int
    limit: int
    offset: int
