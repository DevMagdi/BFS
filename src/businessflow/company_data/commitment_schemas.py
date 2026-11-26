from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from businessflow.schemas import StrictModel

CommitmentStatus = Literal["draft", "submitted", "changes_requested", "accepted", "rejected"]
ReviewDecision = Literal["accept", "request_correction", "reject"]


class CustomerCommitmentFields(StrictModel):
    customer_name: Annotated[str | None, StringConstraints(max_length=200)] = None
    customer_reference: Annotated[str | None, StringConstraints(max_length=128)] = None
    order_date: date | None = None
    requested_delivery_date: date | None = None
    summary: Annotated[str | None, StringConstraints(max_length=1000)] = None
    notes: Annotated[str | None, StringConstraints(max_length=4000)] = None

    @field_validator("customer_name", "customer_reference", "summary", "notes", mode="before")
    @classmethod
    def blank_text_is_missing(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class CustomerCommitmentCreateRequest(CustomerCommitmentFields):
    pass


class CustomerCommitmentUpdateRequest(CustomerCommitmentFields):
    expected_revision: Annotated[int, Field(ge=1)]


class CustomerCommitmentSubmitRequest(StrictModel):
    expected_revision: Annotated[int, Field(ge=1)]
    acknowledge_warnings: bool = False


class CustomerCommitmentReviewRequest(StrictModel):
    expected_revision: Annotated[int, Field(ge=1)]
    decision: ReviewDecision
    comment: Annotated[str | None, StringConstraints(max_length=2000)] = None
    criteria_confirmed: bool = False
    acknowledge_warnings: bool = False

    @field_validator("comment", mode="before")
    @classmethod
    def blank_comment_is_missing(cls, value: object) -> object:
        if isinstance(value, str):
            normalized = value.strip()
            return normalized or None
        return value


class CustomerCommitmentFinding(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    field: str | None = None
    message: str


class CustomerCommitmentValidation(StrictModel):
    errors: list[CustomerCommitmentFinding]
    warnings: list[CustomerCommitmentFinding]


class CustomerCommitmentSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    source_id: UUID
    source_key: str
    contract_key: str
    contract_version: str
    contract_hash: str
    origin: Literal["manual"] = "manual"
    status: CommitmentStatus
    customer_name: str | None
    customer_reference: str | None
    order_date: date | None
    requested_delivery_date: date | None
    summary: str | None
    notes: str | None
    current_revision: Annotated[int, Field(ge=1)]
    created_by_user_id: UUID
    updated_by_user_id: UUID
    submitted_by_user_id: UUID | None
    reviewed_by_user_id: UUID | None
    submitted_at: datetime | None
    reviewed_at: datetime | None
    review_comment: str | None
    created_at: datetime
    updated_at: datetime


class ManualCustomerCommitmentEvidenceSummary(StrictModel):
    id: UUID
    file_name: str
    media_type: Literal["application/pdf", "image/jpeg", "image/png", "text/plain"]
    file_size_bytes: Annotated[int, Field(ge=1, le=10 * 1024 * 1024)]
    file_hash: str
    uploaded_by_user_id: UUID
    created_at: datetime


class ManualCustomerCommitmentEventResponse(StrictModel):
    id: UUID
    commitment_id: UUID
    current_revision: Annotated[int, Field(ge=1)]
    event_type: str
    actor_user_id: UUID
    status_before: CommitmentStatus | None
    status_after: CommitmentStatus
    comment: str | None
    snapshot: dict[str, object]
    snapshot_hash: str
    previous_event_hash: str | None
    event_hash: str
    occurred_at: datetime


class CustomerCommitmentDetailResponse(StrictModel):
    commitment: CustomerCommitmentSummary
    validation: CustomerCommitmentValidation
    evidence: list[ManualCustomerCommitmentEvidenceSummary]


class CustomerCommitmentPage(StrictModel):
    items: list[CustomerCommitmentSummary]
    total: int
    limit: int
    offset: int


class CustomerCommitmentHistoryPage(StrictModel):
    items: list[ManualCustomerCommitmentEventResponse]
    total: int
    limit: int
    offset: int


class ManualCustomerCommitmentEvidenceResponse(StrictModel):
    evidence: ManualCustomerCommitmentEvidenceSummary
