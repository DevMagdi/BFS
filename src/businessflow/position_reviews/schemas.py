from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from businessflow.schemas import StrictModel

OperationalDataset = Literal["employees", "projects", "weekly_capacity", "resource_assignments"]
DemoTemplateDataset = Literal[
    "employees", "projects", "weekly_capacity", "resource_assignments", "financial_export"
]
FinancialImportStatus = Literal["invalid", "validated", "confirmed"]
ReviewDisposition = Literal["noted", "follow_up_required", "deferred"]


class PositionDemoSeedResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    created_by_user_id: UUID
    baseline_metric_run_id: UUID
    seed_version: str
    manifest_hash: str
    current_template_hashes: dict[str, str]
    synthetic_label: str
    baseline_start_week_date: date
    baseline_end_week_date: date
    current_start_week_date: date
    baseline_metric_catalog_version: str
    baseline_calculation_version: str
    baseline_input_hash: str
    baseline_result_hash: str
    created_at: datetime
    reused: bool = False


class PositionDemoWorkspaceResponse(StrictModel):
    synthetic_label: str
    demo_seed: PositionDemoSeedResponse | None
    next_step: Literal["seed_synthetic_workspace", "upload_current_operational_pack"]


class FinancialImportSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    uploaded_by_user_id: UUID
    confirmed_by_user_id: UUID | None
    file_name: str
    file_format: Literal["csv"]
    file_hash: str
    file_size_bytes: int
    worksheet_name: str
    schema_version: str
    status: FinancialImportStatus
    row_count: int
    invalid_row_count: int
    validation_hash: str
    created_at: datetime
    confirmed_at: datetime | None


class FinancialObservationRow(StrictModel):
    row_number: int
    row_hash: str
    worksheet_name: str
    source_report: str | None
    exported_at: str | None
    source_label: str | None
    source_value_text: str | None
    currency_code: str | None
    period_start: str | None
    period_end: str | None
    source_record_id: str | None
    issues: list[dict[str, object]] = Field(default_factory=list)


class FinancialImportResponse(StrictModel):
    financial_import: FinancialImportSummary
    validation_report: dict[str, object]
    observations: list[FinancialObservationRow]
    retention_note: str


class FinancialImportConfirmRequest(StrictModel):
    validation_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class FinancialImportListResponse(StrictModel):
    items: list[FinancialImportSummary]
    total: int
    limit: int
    offset: int


class OperationalImportSelection(StrictModel):
    employees: UUID
    projects: UUID
    weekly_capacity: UUID
    resource_assignments: UUID

    def by_dataset(self) -> dict[str, UUID]:
        return {
            "employees": self.employees,
            "projects": self.projects,
            "weekly_capacity": self.weekly_capacity,
            "resource_assignments": self.resource_assignments,
        }


class PositionReviewAttestationRequest(StrictModel):
    m5_definition_acknowledged: bool = False
    active_cohort_comparability_attested: bool = False
    current_capacity_currentness_attested: bool = False
    baseline_capacity_currentness_attested: bool = False
    committed_assignment_completeness_attested: bool = False


class PositionReviewCreateRequest(StrictModel):
    start_week_date: date
    baseline_metric_run_id: UUID
    operational_imports: OperationalImportSelection
    financial_import_id: UUID | None = None
    synthetic_data_acknowledged: bool = False
    attestations: PositionReviewAttestationRequest = Field(
        default_factory=PositionReviewAttestationRequest
    )


class PositionReviewDispositionRequest(StrictModel):
    review_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    disposition: ReviewDisposition
    rationale: Annotated[str, StringConstraints(min_length=1, max_length=2000)]

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("rationale must not be blank")
        return normalized


class PositionReviewDispositionResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    review_id: UUID
    actor_user_id: UUID
    actor_display_name: str
    review_hash: str
    disposition: ReviewDisposition
    rationale: str
    created_at: datetime


class PositionReviewOperationalImportResponse(StrictModel):
    dataset_type: OperationalDataset
    import_id: UUID
    file_name: str
    file_hash: str
    validation_hash: str
    mapping_version: int
    status: str
    total_rows: int
    invalid_rows: int
    warning_rows: int
    duplicate_rows: int
    created_rows: int
    skipped_rows: int


class PositionReviewSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    baseline_metric_run_id: UUID
    current_metric_run_id: UUID
    financial_import_id: UUID | None
    start_week_date: date
    end_week_date: date
    calculation_version: str
    input_hash: str
    result_hash: str
    result_state: str
    created_at: datetime
    disposition_count: int


class PositionReviewResponse(PositionReviewSummary):
    demo_seed_id: UUID
    metric_catalog_version: str
    metric_calculation_version: str
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]
    operational_imports: list[PositionReviewOperationalImportResponse]
    dispositions: list[PositionReviewDispositionResponse]


class PositionReviewListResponse(StrictModel):
    items: list[PositionReviewSummary]
    total: int
    limit: int
    offset: int
