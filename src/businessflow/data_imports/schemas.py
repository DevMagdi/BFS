from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from businessflow.schemas import StrictModel

ImportDataset = Literal[
    "employees",
    "skills",
    "employee_skills",
    "weekly_capacity",
    "projects",
    "resource_assignments",
]
ImportStatus = Literal[
    "inspected",
    "mapped",
    "validated",
    "ready",
    "confirmed",
    "applied",
    "applied_with_warnings",
    "failed",
    "cancelled",
    "expired",
]
RowStatus = Literal["pending", "valid", "warning", "duplicate", "invalid", "applied", "skipped"]


class ImportMappingRequest(StrictModel):
    dataset_type: ImportDataset
    worksheet_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    field_mapping: dict[str, str] = Field(max_length=64)
    ignored_columns: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]] = Field(
        default_factory=list, max_length=256
    )

    @field_validator("ignored_columns")
    @classmethod
    def unique_ignored_columns(cls, value: list[str]) -> list[str]:
        if len(set(value)) != len(value):
            raise ValueError("ignored_columns may not contain duplicate headers")
        return value


class ImportConfirmRequest(StrictModel):
    validation_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
    allow_partial: bool = False
    acknowledge_warnings: bool = False


class ImportCounts(StrictModel):
    total_rows: int
    valid_rows: int
    invalid_rows: int
    warning_rows: int
    duplicate_rows: int
    eligible_rows: int
    skipped_rows: int
    created_rows: int
    updated_rows: int


class WorksheetInspection(StrictModel):
    name: str
    headers: list[str]
    row_count: int
    blank_rows_ignored: int


class ImportInspection(StrictModel):
    file_format: Literal["csv", "xlsx"]
    worksheets: list[WorksheetInspection]
    total_rows: int
    blank_rows_ignored: int
    max_rows: int


class ImportSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    uploaded_by_user_id: UUID
    confirmed_by_user_id: UUID | None
    applied_by_user_id: UUID | None
    file_name: str
    file_format: Literal["csv", "xlsx"]
    file_hash: str
    file_size_bytes: int
    dataset_type: ImportDataset | None
    selected_worksheet: str | None
    field_mapping: dict[str, str] | None
    ignored_columns: list[str]
    mapping_version: int
    status: ImportStatus
    counts: ImportCounts
    acknowledge_warnings: bool
    allow_partial: bool
    validation_hash: str | None
    staging_expires_at: datetime
    confirmed_at: datetime | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ImportSummaryPage(StrictModel):
    items: list[ImportSummary]
    total: int
    limit: int
    offset: int


class ImportInspectionResponse(StrictModel):
    import_record: ImportSummary = Field(serialization_alias="import")
    inspection: ImportInspection
    suggested_datasets: list[ImportDataset]


class ImportFieldDefinition(StrictModel):
    name: str
    required: bool
    description: str
    aliases: list[str]


class ImportMappingResponse(StrictModel):
    import_record: ImportSummary = Field(serialization_alias="import")
    field_definitions: list[ImportFieldDefinition]
    required_one_of: list[list[str]] = Field(default_factory=list)
    mutually_exclusive: list[list[str]] = Field(default_factory=list)
    suggested_mapping: dict[str, str]
    unmapped_columns: list[str]
    ignored_columns: list[str]


class ImportRowIssue(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    category: str
    field: str | None
    message: str
    correction: str


class ImportPreviewRow(StrictModel):
    row_number: int
    worksheet_name: str
    row_hash: str
    row_status: RowStatus
    source_values: dict[str, str | None] | None
    mapped_values: dict[str, object] | None
    canonical_type: str | None
    canonical_id: UUID | None
    issues: list[ImportRowIssue]


class ImportPreviewPage(StrictModel):
    items: list[ImportPreviewRow]
    total: int
    limit: int
    offset: int
    counts: ImportCounts


class ImportIssueCount(StrictModel):
    code: str
    severity: Literal["error", "warning"]
    count: int


class ImportReport(StrictModel):
    import_record: ImportSummary = Field(serialization_alias="import")
    inspection: ImportInspection
    issue_counts: list[ImportIssueCount]
    validation_report: dict[str, object] | None
    retention_note: str


class ImportApplyResult(StrictModel):
    import_record: ImportSummary = Field(serialization_alias="import")
    links_created: int
    replayed: bool = False


class ImportPurgeResult(StrictModel):
    imports_purged: int = Field(ge=0)
    source_rows_purged: int = Field(ge=0)
