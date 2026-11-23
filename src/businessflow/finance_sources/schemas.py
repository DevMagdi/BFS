from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from businessflow.calculation_core.decimal import parse_decimal_text, validate_currency_code
from businessflow.schemas import StrictModel

FinanceField = Literal[
    "source_record_id",
    "reconciliation_key",
    "period_start",
    "period_end",
    "amount",
    "currency",
]
FINANCE_FIELDS: tuple[FinanceField, ...] = (
    "source_record_id",
    "reconciliation_key",
    "period_start",
    "period_end",
    "amount",
    "currency",
)
DecimalText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
]
HashText = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
HeaderName = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=128, pattern=r".*\S.*")
]
ReconciliationKey = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r".*\S.*")
]
SourceKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$",
    ),
]
SourceIdentifier = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$",
    ),
]
ControlIdentifier = SourceIdentifier
SourceLocator = Annotated[
    str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r".*\S.*")
]


class FinanceInputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FinanceIgnoredColumn(FinanceInputModel):
    header: HeaderName
    rationale: Annotated[
        str, StringConstraints(strict=True, min_length=1, max_length=500, pattern=r".*\S.*")
    ]


class FinanceControlSource(FinanceInputModel):
    source_id: SourceIdentifier
    source_hash: HashText
    record_hash: HashText | None = None
    record_locator: SourceLocator


class FinanceReconciliationControl(FinanceInputModel):
    control_id: ControlIdentifier
    reconciliation_key: ReconciliationKey
    period_start: date
    period_end: date
    currency: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]{3}$")]
    expected_amount: DecimalText
    absolute_tolerance: DecimalText
    relative_tolerance: DecimalText
    source: FinanceControlSource

    @field_validator("currency")
    @classmethod
    def valid_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @field_validator("expected_amount", "absolute_tolerance", "relative_tolerance")
    @classmethod
    def valid_decimal_text(cls, value: str) -> str:
        parse_decimal_text(value)
        return value

    @field_validator("reconciliation_key")
    @classmethod
    def exact_reconciliation_key(cls, value: str) -> str:
        if value != value.strip() or len(value) > 256:
            raise ValueError("reconciliation_key must be exact and at most 256 characters")
        return value

    @model_validator(mode="after")
    def valid_control(self) -> FinanceReconciliationControl:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        if parse_decimal_text(self.absolute_tolerance) < 0:
            raise ValueError("absolute_tolerance must be non-negative")
        if parse_decimal_text(self.relative_tolerance) < 0:
            raise ValueError("relative_tolerance must be non-negative")
        return self


class FinanceSourceIntakeMetadata(FinanceInputModel):
    source_key: SourceKey
    source_as_of_at: datetime | None = None
    freshness_max_age_hours: Annotated[int, Field(strict=True, ge=1, le=2_147_483_647)] | None = (
        None
    )
    field_mapping: dict[FinanceField, HeaderName] = Field(min_length=1, max_length=6)
    ignored_columns: list[FinanceIgnoredColumn] = Field(default_factory=list, max_length=256)
    controls: list[FinanceReconciliationControl] = Field(min_length=1, max_length=1000)

    @field_validator("source_as_of_at")
    @classmethod
    def as_of_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_as_of_at must include a timezone")
        return value

    @field_validator("ignored_columns")
    @classmethod
    def ignored_headers_must_be_unique(
        cls, value: list[FinanceIgnoredColumn]
    ) -> list[FinanceIgnoredColumn]:
        headers = [item.header for item in value]
        if len(headers) != len(set(headers)):
            raise ValueError("ignored_columns may not contain duplicate headers")
        return value

    @model_validator(mode="after")
    def complete_mapping_and_unique_controls(self) -> FinanceSourceIntakeMetadata:
        if set(self.field_mapping) != set(FINANCE_FIELDS):
            raise ValueError("field_mapping must map every finance contract field exactly once")
        mapped_headers = list(self.field_mapping.values())
        if len(mapped_headers) != len(set(mapped_headers)):
            raise ValueError("each source header may map to only one finance contract field")
        control_ids = [control.control_id for control in self.controls]
        if len(control_ids) != len(set(control_ids)):
            raise ValueError("control_id values must be unique within an intake")
        control_keys = [
            (
                control.reconciliation_key,
                control.period_start,
                control.period_end,
                control.currency,
            )
            for control in self.controls
        ]
        if len(control_keys) != len(set(control_keys)):
            raise ValueError("controls may not repeat an exact reconciliation group")
        return self


class FinanceSourceContractResponse(StrictModel):
    contract_key: str
    version: str
    definition: dict[str, object]
    definition_hash: str


class FinanceSourceContractPage(StrictModel):
    items: list[FinanceSourceContractResponse]


class FinanceSourceGovernanceStatus(StrictModel):
    finance_source_identity: Literal["caller_asserted_not_independently_authenticated"]
    control_source_identity: Literal["caller_asserted_not_independently_authenticated"]
    control_origin: Literal["caller_supplied"]
    control_independence: Literal["not_independently_verified"]
    finance_owner_approval: Literal["not_established"]
    authoritative_source_access_approval: Literal["not_established"]
    freshness_policy_approval: Literal["not_established"]
    correction_policy_approval: Literal["not_established"]
    retention_policy_approval: Literal["not_established"]
    accounting_correctness: Literal["not_established"]
    production_readiness: Literal["not_established"]
    production_customer_data_authorization: Literal["not_authorized"]


class FinanceSourceIntakeResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    source_key: str
    file_name: str
    file_format: Literal["csv"]
    file_hash: str
    file_size_bytes: int
    contract_key: str
    contract_version: str
    contract_hash: str
    mapping_version: int
    mapping_hash: str
    source_as_of_at: datetime | None
    freshness_max_age_hours: int | None
    freshness_status: Literal[
        "fresh", "stale", "policy_missing", "source_as_of_missing", "future_as_of"
    ]
    quality_status: Literal["passed", "warning", "failed"]
    reconciliation_status: Literal["passed", "failed", "not_verifiable"] = Field(
        description=(
            "Aggregate arithmetic comparison state only. A passed/failed status does not "
            "establish source authority, control independence, finance approval, or "
            "accounting correctness."
        )
    )
    reconciliation_method: Literal["caller_supplied_decimal_arithmetic_only"] = Field(
        description=(
            "Compares supplied Decimal values only; it does not authenticate either source, "
            "verify control independence, establish finance approval, or determine accounting "
            "correctness."
        )
    )
    governance_status: FinanceSourceGovernanceStatus
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    input_hash: str
    result_hash: str
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]
    created_at: datetime
    replayed: bool = False


class FinanceSourceIntakePage(StrictModel):
    items: list[FinanceSourceIntakeResponse]
    total: int
    limit: int
    offset: int


class FinanceSourceRowEvidenceResponse(StrictModel):
    source_row_number: int
    source_row_hash: str
    source_record_identity_hash: str
    reconciliation_group_hash: str | None
    row_status: Literal["valid", "duplicate", "invalid"]
    issues: list[dict[str, object]]


class FinanceSourceRowEvidencePage(StrictModel):
    items: list[FinanceSourceRowEvidenceResponse]
    total: int
    limit: int
    offset: int
