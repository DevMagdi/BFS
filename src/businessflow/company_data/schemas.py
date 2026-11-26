from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator

from businessflow.data_imports.schemas import ImportDataset
from businessflow.schemas import StrictModel

CompanyDataQualityStatus = Literal["passed", "warning", "failed"]
CompanyDataIngestionStatus = Literal["succeeded", "succeeded_with_warnings", "blocked"]
CompanyDataFreshnessStatus = Literal[
    "fresh", "stale", "policy_missing", "source_as_of_missing", "future_as_of"
]
CompanyDataDatasetType = Literal[
    "employees",
    "skills",
    "employee_skills",
    "weekly_capacity",
    "projects",
    "resource_assignments",
    "customer_commitments",
    "universal_records",
]


class CompanyDataContractResponse(StrictModel):
    contract_key: str
    version: str
    dataset_type: CompanyDataDatasetType
    definition: dict[str, object]
    definition_hash: str
    created_at: datetime


class CompanyDataContractListResponse(StrictModel):
    items: list[CompanyDataContractResponse]


class CompanyDataContractVersionSummary(StrictModel):
    contract_key: str
    version: str
    definition_hash: str


class CompanyDataCoverageIngestionCounts(StrictModel):
    succeeded: int = 0
    succeeded_with_warnings: int = 0
    blocked: int = 0


class CompanyDataCoverageQualityCounts(StrictModel):
    passed: int = 0
    warning: int = 0
    failed: int = 0


class CompanyDataCoverageFreshnessCounts(StrictModel):
    fresh: int = 0
    stale: int = 0
    policy_missing: int = 0
    source_as_of_missing: int = 0
    future_as_of: int = 0


class CompanyDataDatasetCoverage(StrictModel):
    dataset_type: ImportDataset
    contract_versions: list[CompanyDataContractVersionSummary]
    registered_source_count: int = Field(ge=0)
    sources_without_ingestion_count: int = Field(ge=0)
    latest_ingestion_counts: CompanyDataCoverageIngestionCounts
    latest_quality_counts: CompanyDataCoverageQualityCounts
    current_freshness_counts: CompanyDataCoverageFreshnessCounts
    latest_ingestion_at: datetime | None


class CompanyDataCoverageResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    evaluated_at: datetime
    note: str
    datasets: list[CompanyDataDatasetCoverage]


class CompanyDataSourceCreateRequest(StrictModel):
    source_key: Annotated[
        str, StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    ]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    dataset_type: CompanyDataDatasetType
    contract_version: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    owner_user_id: UUID | None = None
    freshness_max_age_hours: int | None = Field(default=None, gt=0)


class CompanyDataSourceSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    source_key: str
    display_name: str
    dataset_type: CompanyDataDatasetType
    contract_key: str
    contract_version: str
    owner_user_id: UUID
    registered_by_user_id: UUID
    freshness_max_age_hours: int | None
    created_at: datetime
    latest_ingestion_id: UUID | None
    latest_ingestion_at: datetime | None
    latest_ingestion_status: CompanyDataIngestionStatus | None
    latest_quality_status: CompanyDataQualityStatus | None
    latest_freshness_status: CompanyDataFreshnessStatus | None
    latest_recorded_freshness_status: CompanyDataFreshnessStatus | None
    last_successful_ingestion_id: UUID | None
    last_successful_ingestion_at: datetime | None


class CompanyDataSourceListResponse(StrictModel):
    items: list[CompanyDataSourceSummary]
    total: int
    limit: int
    offset: int


class CompanyDataIngestionCreateRequest(StrictModel):
    import_id: UUID
    source_as_of_at: datetime | None = None

    @field_validator("source_as_of_at")
    @classmethod
    def require_timezone_for_source_as_of(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_as_of_at must include an explicit timezone")
        return value


class CompanyDataIngestionSummary(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    source_id: UUID
    import_id: UUID
    ingested_by_user_id: UUID
    contract_key: str
    contract_version: str
    ingestion_status: CompanyDataIngestionStatus
    quality_status: CompanyDataQualityStatus
    freshness_status: CompanyDataFreshnessStatus
    current_freshness_status: CompanyDataFreshnessStatus
    source_as_of_at: datetime | None
    freshness_max_age_hours: int | None
    freshness_evaluated_at: datetime
    contract_hash: str
    file_hash: str
    validation_hash: str | None
    mapping_version: int
    total_rows: int
    snapshot_hash: str
    created_at: datetime


class CompanyDataIngestionResponse(StrictModel):
    ingestion: CompanyDataIngestionSummary
    quality_findings: list[dict[str, object]]
    contract_snapshot: dict[str, object]
    snapshot: dict[str, object]
    replayed: bool = False


class CompanyDataIngestionListResponse(StrictModel):
    items: list[CompanyDataIngestionSummary]
    total: int
    limit: int
    offset: int


class CompanyDataLineageItem(StrictModel):
    import_id: UUID
    source_row_id: UUID
    worksheet_name: str
    row_number: int
    row_hash: str
    row_status: str
    canonical_type: str
    canonical_id: UUID
    canonical_fingerprint: str


class CompanyDataLineagePage(StrictModel):
    items: list[CompanyDataLineageItem]
    total: int
    limit: int
    offset: int
