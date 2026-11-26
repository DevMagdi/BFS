from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.schemas import StrictModel

UniversalSourceType = Literal[
    "manual_entry",
    "local_directory",
    "csv",
    "xlsx",
    "pdf",
    "rest_api",
    "relational_database",
    "erp",
    "crm",
    "accounting",
    "hr",
    "other",
]
SourceStatus = Literal["enabled", "disabled"]
AuthorityClassification = Literal[
    "unclassified", "owner_asserted_primary", "supporting", "third_party", "manual_assertion"
]
CanonicalEntityType = Literal[
    "customer",
    "supplier",
    "product",
    "sales_transaction",
    "expense",
    "business_activity",
    "operational_record",
]
IngestionStatus = Literal[
    "staged", "mapped", "validated", "review_required", "blocked", "failed", "promoted"
]
RowStatus = Literal["pending", "valid", "warning", "invalid", "duplicate"]
CsvEncoding = Literal["utf-8-sig", "utf-16", "cp1252"]

_SOURCE_KEY = r"^[a-z0-9][a-z0-9._-]{1,63}$"
_CONNECTION_REFERENCE = r"^(config|ref)://[A-Za-z0-9._/-]{1,220}$"
_CREDENTIAL_REFERENCE = r"^(secret|vault|kms)://[A-Za-z0-9._/-]{1,220}$"


def _safe_reference(value: str | None, *, credential: bool) -> str | None:
    if value is None:
        return None
    if ".." in value or "?" in value or "#" in value:
        raise ValueError("references must be opaque, path-free configuration identifiers")
    import re

    pattern = _CREDENTIAL_REFERENCE if credential else _CONNECTION_REFERENCE
    if re.fullmatch(pattern, value) is None:
        expected = "secret://, vault://, or kms://" if credential else "config:// or ref://"
        raise ValueError(f"use an opaque {expected} reference, not a URL or plaintext secret")
    return value


class UniversalSourceCreateRequest(StrictModel):
    source_key: Annotated[str, StringConstraints(min_length=2, max_length=64, pattern=_SOURCE_KEY)]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    source_type: UniversalSourceType
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    business_domain: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    owner_user_id: UUID | None = None
    source_status: SourceStatus = "enabled"
    connection_reference: Annotated[str, StringConstraints(max_length=256)] | None = None
    credential_reference: Annotated[str, StringConstraints(max_length=256)] | None = None
    authority_classification: AuthorityClassification = "unclassified"
    freshness_max_age_hours: int | None = Field(default=None, gt=0)

    @field_validator("connection_reference")
    @classmethod
    def validate_connection_reference(cls, value: str | None) -> str | None:
        return _safe_reference(value, credential=False)

    @field_validator("credential_reference")
    @classmethod
    def validate_credential_reference(cls, value: str | None) -> str | None:
        return _safe_reference(value, credential=True)


class UniversalConnectorSyncRequest(StrictModel):
    relative_path: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    csv_delimiter: Literal[",", ";", "\t", "|"] = ","
    csv_encoding: CsvEncoding = "utf-8-sig"
    header_row: int = Field(default=1, ge=1, le=25)


class UniversalSourceProfileUpdateRequest(StrictModel):
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)] | None = None
    source_type: UniversalSourceType | None = None
    description: Annotated[str, StringConstraints(max_length=2000)] | None = None
    business_domain: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    owner_user_id: UUID | None = None
    source_status: SourceStatus | None = None
    connection_reference: Annotated[str, StringConstraints(max_length=256)] | None = None
    credential_reference: Annotated[str, StringConstraints(max_length=256)] | None = None
    authority_classification: AuthorityClassification | None = None
    freshness_max_age_hours: int | None = Field(default=None, gt=0)

    @field_validator("connection_reference")
    @classmethod
    def validate_connection_reference(cls, value: str | None) -> str | None:
        return _safe_reference(value, credential=False)

    @field_validator("credential_reference")
    @classmethod
    def validate_credential_reference(cls, value: str | None) -> str | None:
        return _safe_reference(value, credential=True)

    @model_validator(mode="after")
    def require_change(self) -> UniversalSourceProfileUpdateRequest:
        if not self.model_fields_set:
            raise ValueError("at least one source profile field must be supplied")
        return self


class UniversalSourceProfileSummary(StrictModel):
    source_id: UUID
    source_key: str
    dataset_type: Literal["universal_records"]
    contract_key: str
    contract_version: str
    contract_hash: str
    version: int
    source_type: UniversalSourceType
    display_name: str
    description: str | None
    business_domain: str | None
    owner_user_id: UUID
    source_status: SourceStatus
    connection_reference_configured: bool
    credential_reference_configured: bool
    authority_classification: AuthorityClassification
    freshness_max_age_hours: int | None
    created_by_user_id: UUID
    created_at: datetime
    last_attempted_at: datetime | None
    last_successful_at: datetime | None
    last_error_code: str | None


class UniversalSourcePage(StrictModel):
    items: list[UniversalSourceProfileSummary]
    total: int
    limit: int
    offset: int


class UniversalSourceProfileHistory(StrictModel):
    items: list[UniversalSourceProfileSummary]
    total: int
    limit: int
    offset: int


class UniversalEntityField(StrictModel):
    name: str
    type: Literal["text", "date", "decimal"]
    required: bool
    description: str


class UniversalEntitySchema(StrictModel):
    entity_type: CanonicalEntityType
    label: str
    description: str
    key_field: str
    fields: list[UniversalEntityField]


class UniversalEntityCatalog(StrictModel):
    contract_key: str
    contract_version: str
    contract_hash: str
    items: list[UniversalEntitySchema]
    note: str


class UniversalIngestionCounts(StrictModel):
    total_rows: int = Field(ge=0)
    valid_rows: int = Field(ge=0)
    invalid_rows: int = Field(ge=0)
    warning_rows: int = Field(ge=0)
    duplicate_rows: int = Field(ge=0)
    promoted_rows: int = Field(ge=0)
    created_records: int = Field(ge=0)
    revised_records: int = Field(ge=0)
    unchanged_records: int = Field(ge=0)


class UniversalIngestionSummary(StrictModel):
    id: UUID
    source_id: UUID
    snapshot_id: UUID
    source_profile_version_id: UUID
    initiated_by_user_id: UUID
    promoted_by_user_id: UUID | None
    file_name: str
    file_format: Literal["csv", "xlsx"]
    input_fingerprint: str
    selected_worksheet: str | None
    mapping_profile_id: UUID | None
    mapping_profile_version_id: UUID | None
    mapping_profile_name: str | None
    mapping_version: int | None
    entity_type: CanonicalEntityType | None
    status: IngestionStatus
    counts: UniversalIngestionCounts
    validation_hash: str | None
    failure_code: str | None
    created_at: datetime
    updated_at: datetime
    promoted_at: datetime | None


class UniversalFileIngestionResponse(StrictModel):
    ingestion: UniversalIngestionSummary
    inspection: dict[str, object]
    replayed: bool = False


class UniversalIngestionDetail(StrictModel):
    ingestion: UniversalIngestionSummary
    inspection: dict[str, object]
    validation_snapshot: dict[str, object] | None
    mapping_profile_version: dict[str, object] | None
    available_worksheets: list[dict[str, object]]
    evidence_retained: bool = True
    source_authority_note: str


class UniversalIngestionPage(StrictModel):
    items: list[UniversalIngestionSummary]
    total: int
    limit: int
    offset: int


class FieldMappingOptions(StrictModel):
    date_format: Literal["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"] | None = None
    decimal_separator: Literal[".", ","] | None = None
    thousands_separator: Literal[",", ".", " ", "'"] | None = None
    trim_whitespace: bool = True
    text_case: Literal["preserve", "upper", "lower"] = "preserve"

    @model_validator(mode="after")
    def separators_must_differ(self) -> FieldMappingOptions:
        if (
            self.decimal_separator is not None
            and self.decimal_separator == self.thousands_separator
        ):
            raise ValueError("decimal and thousands separators must be different")
        return self


class UniversalMappingCreateRequest(StrictModel):
    profile_key: Annotated[str, StringConstraints(min_length=2, max_length=64, pattern=_SOURCE_KEY)]
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    entity_type: CanonicalEntityType
    worksheet_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    field_mapping: dict[str, Annotated[str, StringConstraints(min_length=1, max_length=128)]]
    ignored_columns: list[Annotated[str, StringConstraints(min_length=1, max_length=128)]]
    field_options: dict[str, FieldMappingOptions] = Field(default_factory=dict)

    @field_validator("ignored_columns")
    @classmethod
    def ignored_columns_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("ignored_columns must not contain duplicates")
        return value

    @model_validator(mode="after")
    def mapping_and_ignored_columns_do_not_overlap(self) -> UniversalMappingCreateRequest:
        mapped_columns = set(self.field_mapping.values())
        if mapped_columns.intersection(self.ignored_columns):
            raise ValueError("a source column cannot be both mapped and ignored")
        return self


class UniversalMappingApplyRequest(StrictModel):
    mapping_profile_version_id: UUID


class UniversalMappingProfileVersionResponse(StrictModel):
    id: UUID
    profile_id: UUID
    profile_key: str
    display_name: str
    version: int
    entity_type: CanonicalEntityType
    worksheet_name: str
    field_mapping: dict[str, str]
    ignored_columns: list[str]
    field_options: dict[str, dict[str, object]]
    missing_required_fields: list[str]
    contract_hash: str
    created_by_user_id: UUID
    created_at: datetime


class UniversalMappingProfileList(StrictModel):
    items: list[UniversalMappingProfileVersionResponse]
    total: int
    limit: int
    offset: int


class UniversalValidationResult(StrictModel):
    ingestion: UniversalIngestionSummary
    dataset_findings: list[dict[str, object]]
    validation_snapshot: dict[str, object]


class UniversalRowPreview(StrictModel):
    source_row_id: UUID
    worksheet_name: str
    row_number: int
    row_hash: str
    raw_values: dict[str, str | None]
    mapped_values: dict[str, object]
    row_status: RowStatus
    findings: list[dict[str, object]]
    source_record_id: str | None


class UniversalRowPage(StrictModel):
    items: list[UniversalRowPreview]
    total: int
    limit: int
    offset: int
    counts: UniversalIngestionCounts


class UniversalPromotionRequest(StrictModel):
    acknowledge_warnings: bool = False
    correction_reason: Annotated[str, StringConstraints(min_length=1, max_length=500)] | None = None

    @field_validator("correction_reason")
    @classmethod
    def correction_reason_must_not_be_blank(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("correction_reason must contain a non-whitespace character")
        return normalized


class UniversalPromotionResult(StrictModel):
    ingestion: UniversalIngestionSummary
    created_records: int
    revised_records: int
    unchanged_records: int
    withheld_duplicate_rows: int
    replayed: bool = False
    note: str


class CanonicalRecordSummary(StrictModel):
    id: UUID
    entity_type: CanonicalEntityType
    source_id: UUID
    source_record_id: str
    revision: int
    attributes: dict[str, object]
    attributes_hash: str
    source_snapshot_id: UUID
    source_row_id: UUID
    ingestion_id: UUID
    mapping_profile_version_id: UUID
    source_profile_version_id: UUID
    created_at: datetime


class CanonicalRecordPage(StrictModel):
    items: list[CanonicalRecordSummary]
    total: int
    limit: int
    offset: int


class UniversalLineageItem(StrictModel):
    record_id: UUID
    entity_type: CanonicalEntityType
    source_id: UUID
    source_record_id: str
    revision: int
    attributes_hash: str
    source_snapshot_id: UUID
    input_fingerprint: str
    file_name: str
    worksheet_name: str
    row_number: int
    row_hash: str
    ingestion_id: UUID
    initiated_by_user_id: UUID
    promoted_by_user_id: UUID
    mapping_profile_id: UUID
    mapping_profile_version_id: UUID
    mapping_version: int
    contract_key: str
    contract_version: str
    contract_hash: str
    validation_hash: str
    source_profile_version_id: UUID
    source_profile_version: int
    captured_at: datetime
    promoted_at: datetime


class UniversalLineagePage(StrictModel):
    items: list[UniversalLineageItem]
    total: int
    limit: int
    offset: int
