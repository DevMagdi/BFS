from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.schemas import StrictModel

EvidenceKind = Literal["manual_assertion", "canonical_record_revision"]


class OperationsEventFields(StrictModel):
    occurred_at: datetime
    event_type: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    source_subject_reference: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    process_reference: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    state_before: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    state_after: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    description: Annotated[str, StringConstraints(max_length=1000)] | None = None
    evidence_kind: EvidenceKind
    canonical_record_id: UUID | None = None
    canonical_record_revision: int | None = Field(default=None, ge=1)

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("occurred_at must include a timezone")
        return value

    @field_validator(
        "event_type",
        "source_subject_reference",
        "process_reference",
        "state_before",
        "state_after",
        "description",
        mode="before",
    )
    @classmethod
    def reject_control_characters(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(character) < 32 for character in value):
            raise ValueError("text fields cannot contain control characters")
        return value

    @field_validator(
        "process_reference", "state_before", "state_after", "description", mode="before"
    )
    @classmethod
    def blank_optional_text_is_missing(cls, value: object) -> object:
        if isinstance(value, str):
            return value.strip() or None
        return value

    @model_validator(mode="after")
    def evidence_reference_matches_kind(self) -> OperationsEventFields:
        has_record_id = self.canonical_record_id is not None
        has_revision = self.canonical_record_revision is not None
        if self.evidence_kind == "manual_assertion" and (has_record_id or has_revision):
            raise ValueError("manual assertions cannot include a Universal Data record reference")
        if self.evidence_kind == "canonical_record_revision" and not (
            has_record_id and has_revision
        ):
            raise ValueError("a linked Universal Data revision requires a record id and revision")
        return self


class OperationsEventCreateRequest(OperationsEventFields):
    source_id: UUID
    source_event_key: Annotated[str, StringConstraints(min_length=1, max_length=160)]

    @field_validator("source_event_key", mode="before")
    @classmethod
    def reject_event_key_controls(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(character) < 32 for character in value):
            raise ValueError("source_event_key cannot contain control characters")
        return value


class OperationsEventCorrectionRequest(OperationsEventFields):
    expected_version: Annotated[int, Field(ge=1)]
    correction_reason: Annotated[str, StringConstraints(min_length=10, max_length=500)]

    @field_validator("correction_reason", mode="before")
    @classmethod
    def validate_correction_reason(cls, value: object) -> object:
        if isinstance(value, str) and any(ord(character) < 32 for character in value):
            raise ValueError("correction_reason cannot contain control characters")
        return value


class OperationsAnalysisCreateRequest(StrictModel):
    source_id: UUID
    start_at: datetime
    end_at: datetime

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("analysis period timestamps must include a timezone")
        return value

    @model_validator(mode="after")
    def require_half_open_period(self) -> OperationsAnalysisCreateRequest:
        if self.end_at <= self.start_at:
            raise ValueError("end_at must be later than start_at")
        return self


class OperationsEventVersionSummary(StrictModel):
    id: UUID
    event_id: UUID
    source_id: UUID
    source_event_key: str
    version: Annotated[int, Field(ge=1)]
    previous_version_id: UUID | None
    previous_event_hash: str | None
    occurred_at: datetime
    event_type: str
    source_subject_reference: str
    process_reference: str | None
    state_before: str | None
    state_after: str | None
    description: str | None
    evidence_kind: EvidenceKind
    source_profile_version_id: UUID
    canonical_record_id: UUID | None
    canonical_record_revision: int | None
    canonical_attributes_hash: str | None
    correction_reason: str | None
    created_by_user_id: UUID
    created_at: datetime
    event_hash: str


class OperationsEventSummary(StrictModel):
    id: UUID
    source_id: UUID
    source_event_key: str
    created_by_user_id: UUID
    created_at: datetime
    version: OperationsEventVersionSummary
    is_current: bool = True
    replayed: bool = False


class OperationsEventPage(StrictModel):
    items: list[OperationsEventSummary]
    total: int
    limit: int
    offset: int


class OperationsEventHistoryPage(StrictModel):
    event_id: UUID
    items: list[OperationsEventVersionSummary]
    total: int
    limit: int
    offset: int


class OperationsLabelCount(StrictModel):
    label: str
    count: Annotated[int, Field(ge=1)]


class OperationsAnalysisResult(StrictModel):
    status: Literal["observations_available", "no_matching_evidence"]
    observed_event_count: Annotated[int, Field(ge=0, le=500)]
    event_type_counts: list[OperationsLabelCount]
    state_before_counts: list[OperationsLabelCount]
    state_after_counts: list[OperationsLabelCount]
    interpretation: Literal["recorded_observations_only"]
    source_authority: Literal["not_assessed"]
    source_completeness: Literal["not_assessed"]
    source_freshness: Literal["not_assessed"]
    source_accuracy: Literal["not_independently_verified"]
    hypothesis: Literal["not_produced"]
    forecast: Literal["not_produced"]
    recommendation: Literal["not_produced"]
    human_decision: Literal["not_produced"]


class OperationsAnalysisResponse(StrictModel):
    id: UUID
    source_id: UUID
    start_at: datetime
    end_at: datetime
    input_count: Annotated[int, Field(ge=0, le=500)]
    calculation_version: Literal["operations-observations-v1"]
    input_hash: str
    result_hash: str
    created_by_user_id: UUID
    created_at: datetime
    result: OperationsAnalysisResult
    integrity_verified: bool = True
    replayed: bool = False


class OperationsAnalysisEvidenceItem(StrictModel):
    event_id: UUID
    event_version_id: UUID
    source_id: UUID
    source_event_key: str
    version: Annotated[int, Field(ge=1)]
    occurred_at: datetime
    event_type: str
    source_subject_reference: str
    process_reference: str | None
    state_before: str | None
    state_after: str | None
    evidence_kind: EvidenceKind
    source_profile_version_id: UUID
    canonical_record_id: UUID | None
    canonical_record_revision: int | None
    canonical_attributes_hash: str | None
    event_hash: str
    created_by_user_id: UUID
    created_at: datetime


class OperationsAnalysisEvidencePage(StrictModel):
    items: list[OperationsAnalysisEvidenceItem]
    total: int
    limit: int
    offset: int
