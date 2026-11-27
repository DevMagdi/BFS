from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from businessflow.schemas import StrictModel

SourceRevocationReason = Literal[
    "owner_request", "source_replaced", "contract_ended", "security_concern", "other"
]


class UniversalRetentionPolicyCreateRequest(StrictModel):
    retention_days: Annotated[int, Field(strict=True, ge=1, le=36_500)]


class UniversalRetentionPolicySummary(StrictModel):
    policy_version_id: UUID
    version: int
    retention_days: int
    created_by_user_id: UUID
    created_at: datetime
    approved_by_user_id: UUID | None
    approved_at: datetime | None
    revoked_by_user_id: UUID | None
    revoked_at: datetime | None
    active: bool


class UniversalRetentionPolicyPage(StrictModel):
    items: list[UniversalRetentionPolicySummary]
    total: int
    active_policy_version_id: UUID | None
    purge_enabled: bool


class UniversalSourceRevocationRequest(StrictModel):
    reason_code: SourceRevocationReason


class UniversalSourceRevocationResponse(StrictModel):
    source_id: UUID
    revoked: bool
    reason_code: SourceRevocationReason
    revoked_by_user_id: UUID
    revoked_at: datetime


class UniversalPurgePlanPolicy(StrictModel):
    policy_version_id: UUID
    version: int
    retention_days: int
    cutoff_at: datetime


class UniversalPurgePlanIngestion(StrictModel):
    ingestion_id: UUID
    created_at: datetime
    status: str
    snapshot_id: UUID
    row_count: int
    validation_count: int
    event_count: int
    record_link_count: int


class UniversalPurgePlanBlockedIngestion(StrictModel):
    ingestion_id: UUID
    created_at: datetime
    blockers: list[str]
    dependency_counts: dict[str, int]


class UniversalPurgePlanSnapshot(StrictModel):
    snapshot_id: UUID
    file_name: str
    file_format: Literal["csv", "xlsx"]
    created_at: datetime
    file_size_bytes: int
    content_hash: str
    referencing_ingestions_retained_after_page_purge: int
    canonical_revision_references: int
    will_be_deleted_if_all_eligible_ingestions_are_purged: bool


class UniversalPurgePlanResponse(StrictModel):
    source_id: UUID
    source_revoked: bool
    purge_enabled: bool
    dry_run: Literal[True]
    reason: str | None
    as_of: datetime
    policy: UniversalPurgePlanPolicy | None
    eligible_ingestions: list[UniversalPurgePlanIngestion]
    blocked_ingestions: list[UniversalPurgePlanBlockedIngestion]
    total_eligible: int
    total_blocked: int
    offset: int
    limit: int
    has_more: bool
    snapshots: list[UniversalPurgePlanSnapshot]


class UniversalPurgeExecutionRequest(StrictModel):
    policy_version_id: UUID
    plan_limit: Annotated[int, Field(strict=True, ge=1, le=100)] = 100
    plan_offset: Annotated[int, Field(strict=True, ge=0, le=1_000_000)] = 0
    ingestion_ids: Annotated[list[UUID], Field(min_length=1, max_length=100)]

    @field_validator("ingestion_ids")
    @classmethod
    def require_unique_ingestions(cls, value: list[UUID]) -> list[UUID]:
        if len(set(value)) != len(value):
            raise ValueError("ingestion_ids must be unique")
        return value


class UniversalPurgeExecutionResponse(StrictModel):
    source_id: UUID
    policy_version_id: UUID
    purged_ingestion_count: int
    purged_row_count: int
    purged_validation_count: int
    purged_event_count: int
    purged_link_count: int
    purged_snapshot_count: int
    purged_snapshot_bytes: int
