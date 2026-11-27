from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from businessflow.financial_calculations.contracts import FinancialCalculationRequest
from businessflow.schemas import StrictModel

ReportFormat = Literal["json", "csv", "xlsx"]
ReportSourceKind = Literal[
    "business_intelligence_analysis",
    "forecast_run",
    "forecast_evaluation",
    "direct_cash_forecast_run",
    "financial_calculation_run",
]


class ReportSourceReference(StrictModel):
    """A reference to an already-saved BFS result; clients cannot supply report values."""

    kind: ReportSourceKind
    source_id: UUID
    intake_id: UUID | None = None
    replay_request: FinancialCalculationRequest | None = None

    @model_validator(mode="after")
    def validate_source_requirements(self) -> ReportSourceReference:
        finance_source = self.kind in {
            "direct_cash_forecast_run",
            "financial_calculation_run",
        }
        if finance_source != (self.intake_id is not None):
            raise ValueError("finance report sources require an intake_id")
        if (self.kind == "financial_calculation_run") != (self.replay_request is not None):
            raise ValueError(
                "financial calculation exports require exact replay inputs; "
                "other sources forbid them"
            )
        return self


class ReportExportCreateRequest(StrictModel):
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    format: ReportFormat
    sources: Annotated[list[ReportSourceReference], Field(min_length=1, max_length=20)]
    retention_days: Annotated[int, Field(strict=True, ge=1, le=30)] = 7

    @model_validator(mode="after")
    def validate_unique_sources(self) -> ReportExportCreateRequest:
        keys = [(item.kind, item.source_id, item.intake_id) for item in self.sources]
        if len(keys) != len(set(keys)):
            raise ValueError("report sources must be unique")
        return self


class ReportExportSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    title: str
    format: ReportFormat
    mode: Literal["nonproduction_prototype"]
    manifest_hash: str
    artifact_sha256: str
    artifact_size_bytes: int
    source_count: int
    created_by_user_id: UUID
    created_at: datetime
    expires_at: datetime
    artifact_status: Literal["available", "expired", "revoked", "purged"]


class ReportExportDetail(ReportExportSummary):
    manifest: dict[str, object]
    download_path: str | None


class ReportExportPage(StrictModel):
    items: list[ReportExportSummary]
    total: int
    limit: int
    offset: int


class ReportExportRevokeRequest(StrictModel):
    reason: Annotated[str, StringConstraints(min_length=10, max_length=500)]

    @model_validator(mode="after")
    def validate_reason(self) -> ReportExportRevokeRequest:
        if len(self.reason.strip()) < 10:
            raise ValueError("reason must contain at least 10 non-whitespace characters")
        return self


class ReportExportRevocationResponse(StrictModel):
    report_id: UUID
    revoked_by_user_id: UUID
    revoked_at: datetime
    artifact_status: Literal["revoked"]


class ReportArtifactPurgeResponse(StrictModel):
    purged_artifact_count: int = Field(ge=0)
    maximum_batch_size: int
