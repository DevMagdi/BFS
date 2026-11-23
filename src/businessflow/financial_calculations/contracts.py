from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.calculation_core.contracts import (
    CalculationTrace as CalculationCoreTrace,
)
from businessflow.calculation_core.contracts import (
    ContractModel,
    HashText,
    Identifier,
    NonEmptyText,
    OutputDecimalText,
    VersionText,
)
from businessflow.calculation_core.decimal import parse_output_decimal_text, validate_currency_code

MAX_FINANCIAL_CALCULATION_INPUTS = 10_000
MAX_FINANCIAL_CALCULATION_SNAPSHOT_ROWS = 20_000

SourceKey = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=2,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$",
    ),
]
UnparsedAmountText = Annotated[str, StringConstraints(strict=True, max_length=64)]
SourceRowStatus = Literal["valid", "duplicate", "invalid"]
InputStatus = Literal["valid", "missing", "invalid", "duplicate", "conflicting"]
IntakeQualityStatus = Literal["passed", "warning", "failed"]
IntakeFreshnessStatus = Literal[
    "fresh", "stale", "policy_missing", "source_as_of_missing", "future_as_of"
]
IntakeReconciliationStatus = Literal["passed", "failed", "not_verifiable"]
SourceEvidenceIntegrityStatus = Literal["verified", "incomplete", "not_verifiable"]
FormulaExecutionStatus = Literal["executable_mechanical_only", "deferred"]


class FormulaCatalogEntry(ContractModel):
    """BFS-owned versioned formula contract or explicitly deferred candidate."""

    formula_id: Identifier
    formula_version: VersionText | None
    status: FormulaExecutionStatus
    name: NonEmptyText
    formula_expression: NonEmptyText | None
    meaning: NonEmptyText
    input_contract: NonEmptyText
    period_rule: NonEmptyText
    currency_rule: NonEmptyText
    precision_rule: NonEmptyText
    rounding_rule: NonEmptyText
    missing_data_rule: NonEmptyText
    policy_state: Literal["mechanical_only_not_accounting_policy", "required_not_approved"]
    deferral_reason: NonEmptyText | None
    required_decisions: tuple[NonEmptyText, ...]

    @model_validator(mode="after")
    def validate_execution_boundary(self) -> FormulaCatalogEntry:
        if self.status == "executable_mechanical_only":
            if self.formula_version is None or self.formula_expression is None:
                raise ValueError("executable mechanical formulas require a version and expression")
            if self.policy_state != "mechanical_only_not_accounting_policy":
                raise ValueError("mechanical formulas cannot imply a finance-owner approval")
            if self.deferral_reason is not None or self.required_decisions:
                raise ValueError("executable formulas cannot include deferred policy decisions")
        else:
            if self.formula_version is not None or self.formula_expression is not None:
                raise ValueError("deferred candidates cannot publish an executable formula")
            if self.policy_state != "required_not_approved" or self.deferral_reason is None:
                raise ValueError("deferred candidates must state their unapproved policy boundary")
            if not self.required_decisions:
                raise ValueError("deferred candidates must identify required decisions")
        return self


class FinanceSourceIntakeReference(ContractModel):
    """Minimal immutable M14 intake evidence binding, without source-row values."""

    schema_version: Literal[1] = 1
    tenant_id: UUID
    company_id: UUID
    intake_id: UUID
    source_key: SourceKey
    file_hash: HashText
    input_hash: HashText
    result_hash: HashText
    contract_key: Identifier
    contract_version: VersionText
    contract_hash: HashText
    mapping_version: Annotated[int, Field(strict=True, ge=1)]
    mapping_hash: HashText
    quality_status: IntakeQualityStatus
    freshness_status: IntakeFreshnessStatus
    source_as_of_at: datetime | None
    freshness_max_age_hours: Annotated[int, Field(strict=True, ge=1)] | None
    reconciliation_status: IntakeReconciliationStatus

    @field_validator("source_as_of_at")
    @classmethod
    def source_time_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("source_as_of_at must include a timezone")
        return value


class FinanceSourceEvidenceReference(ContractModel):
    """M14 row hashes and status used to bind a supplied Decimal to its source row."""

    schema_version: Literal[1] = 1
    row_evidence_id: UUID
    source_row_number: Annotated[int, Field(strict=True, ge=2)]
    source_row_hash: HashText
    source_record_identity_hash: HashText
    reconciliation_group_hash: HashText | None
    amount_commitment_hash: HashText | None
    amount_commitment_key_version: Literal["app-secret.v1"] | None
    row_status: SourceRowStatus


class FinancialReconciliationGroupReference(ContractModel):
    """An exact M14 reconciliation group, not an accounting/fiscal-period assertion."""

    schema_version: Literal[1] = 1
    reconciliation_group_hash: HashText
    period_start: date
    period_end: date
    currency_code: str
    source_row_count: Annotated[int, Field(strict=True, ge=0)]

    @field_validator("currency_code")
    @classmethod
    def group_currency_must_be_supported(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_group_period(self) -> FinancialReconciliationGroupReference:
        if self.period_end < self.period_start:
            raise ValueError("source period end must not precede its start")
        return self


class FinancialAmountRequestRow(ContractModel):
    """Caller resubmits one amount; it is never accepted as verified without an M14 HMAC."""

    source_row_number: Annotated[int, Field(strict=True, ge=2, le=100_002)]
    amount_text: UnparsedAmountText | None = None


class FinancialCalculationRequest(ContractModel):
    schema_version: Literal[1] = 1
    snapshot_id: UUID = Field(strict=False)
    reconciliation_group_hash: HashText
    observations: tuple[FinancialAmountRequestRow, ...] = Field(
        default_factory=tuple,
        max_length=MAX_FINANCIAL_CALCULATION_INPUTS,
        strict=False,
    )


class FinancialAmountObservation(ContractModel):
    """One exact request input and the server-derived evidence state for replay."""

    schema_version: Literal[1] = 1
    source_row_number: Annotated[int, Field(strict=True, ge=2, le=100_002)]
    amount_text: UnparsedAmountText | None
    status: InputStatus
    evidence: FinanceSourceEvidenceReference | None


class FinancialCalculationInputSnapshot(ContractModel):
    """Server-constructed immutable snapshot binding M14 and exact caller-supplied values."""

    schema_version: Literal[1] = 1
    snapshot_id: UUID
    tenant_id: UUID
    company_id: UUID
    captured_at: datetime
    intake: FinanceSourceIntakeReference
    requested_group_hash: HashText
    source_evidence_integrity_status: SourceEvidenceIntegrityStatus
    group: FinancialReconciliationGroupReference | None
    observations: tuple[FinancialAmountObservation, ...] = Field(
        default_factory=tuple, max_length=MAX_FINANCIAL_CALCULATION_SNAPSHOT_ROWS
    )

    @field_validator("captured_at")
    @classmethod
    def capture_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_input_scope(self) -> FinancialCalculationInputSnapshot:
        if self.intake.tenant_id != self.tenant_id or self.intake.company_id != self.company_id:
            raise ValueError(
                "finance intake evidence must match the calculation tenant and company"
            )
        if self.intake.intake_id.int == 0 or self.snapshot_id.int == 0:
            raise ValueError("snapshot and intake identifiers must not be empty UUIDs")
        if self.group is not None and (
            self.group.reconciliation_group_hash != self.requested_group_hash
        ):
            raise ValueError("resolved group must match the requested group hash")
        for item in self.observations:
            if (
                item.evidence is not None
                and item.evidence.source_row_number != item.source_row_number
            ):
                raise ValueError("source row evidence must match its amount observation")
        return self


class FinancialCalculationOutput(ContractModel):
    schema_version: Literal[1] = 1
    tenant_id: UUID
    company_id: UUID
    key: Identifier
    label: NonEmptyText
    value: OutputDecimalText
    currency_code: str
    period_start: date
    period_end: date
    intake_id: UUID
    source_key: SourceKey
    source_file_hash: HashText
    contract_key: Identifier
    contract_version: VersionText
    contract_hash: HashText
    mapping_version: Annotated[int, Field(strict=True, ge=1)]
    mapping_hash: HashText
    reconciliation_group_hash: HashText
    source_row_numbers: tuple[Annotated[int, Field(strict=True, ge=2)], ...] = Field(
        max_length=MAX_FINANCIAL_CALCULATION_INPUTS
    )
    amount_origin: Literal["hmac_verified_against_m14_numeric_commitment"] = (
        "hmac_verified_against_m14_numeric_commitment"
    )
    rounding_applied: Literal[False] = False
    accounting_interpretation: Literal[
        "mechanical_subtotal_only_not_revenue_cost_profit_or_accounting_truth"
    ] = "mechanical_subtotal_only_not_revenue_cost_profit_or_accounting_truth"

    @field_validator("value")
    @classmethod
    def output_value_must_be_finite_decimal_text(cls, value: str) -> str:
        parse_output_decimal_text(value)
        return value

    @field_validator("currency_code")
    @classmethod
    def output_currency_must_be_supported(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_output_period(self) -> FinancialCalculationOutput:
        if self.period_end < self.period_start:
            raise ValueError("output period end must not precede its start")
        if len(self.source_row_numbers) != len(set(self.source_row_numbers)):
            raise ValueError("calculated source row numbers must be unique")
        return self


class FinancialCalculationTrace(ContractModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    engine_version: VersionText
    catalog_version: VersionText
    formula: FormulaCatalogEntry
    formula_hash: HashText
    input_snapshot: FinancialCalculationInputSnapshot
    input_hash: HashText
    core_trace: CalculationCoreTrace | None
    state: Literal["calculated", "incomplete"]
    reason_codes: tuple[Identifier, ...]
    output: FinancialCalculationOutput | None
    result_hash: HashText
    trace_hash: HashText

    @model_validator(mode="after")
    def validate_scope_and_state(self) -> FinancialCalculationTrace:
        if (
            self.input_snapshot.tenant_id != self.input_snapshot.intake.tenant_id
            or self.input_snapshot.company_id != self.input_snapshot.intake.company_id
        ):
            raise ValueError("calculation trace scope must match its finance intake")
        if self.output is not None and (
            self.output.tenant_id != self.input_snapshot.tenant_id
            or self.output.company_id != self.input_snapshot.company_id
            or self.output.intake_id != self.input_snapshot.intake.intake_id
        ):
            raise ValueError("calculation output must match the input scope and intake")
        if self.formula.status != "executable_mechanical_only":
            raise ValueError("a deferred formula cannot produce a financial calculation trace")
        if self.state == "calculated":
            if self.output is None or self.reason_codes or self.core_trace is None:
                raise ValueError("calculated traces require output, core trace and no reasons")
            if self.core_trace.state != "calculated" or self.core_trace.output is None:
                raise ValueError("calculated traces require a calculated M11 core trace")
            if (
                self.core_trace.output.value != self.output.value
                or self.core_trace.output.currency_code != self.output.currency_code
            ):
                raise ValueError("M15 output must match the exact M11 mechanical sum")
        elif self.output is not None or not self.reason_codes:
            raise ValueError("incomplete traces require reason codes and no output")
        elif self.core_trace is not None and self.core_trace.state != "abstained":
            raise ValueError("incomplete traces cannot contain a calculated M11 core trace")
        return self


class FinancialCalculationReplayVerificationReport(ContractModel):
    schema_version: Literal[1] = 1
    passed: bool
    reason_codes: tuple[Identifier, ...]
    run_id: UUID
    recorded_trace_hash: HashText
    replayed_trace_hash: HashText
    recorded_input_hash: HashText
    replayed_input_hash: HashText
    core_replay_passed: bool
    recorded_evidence_integrity_valid: bool


class FinancialCalculationRunResponse(ContractModel):
    """Persisted run metadata deliberately excludes raw amount and output values."""

    schema_version: Literal[1] = 1
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    intake_id: UUID
    source_key: SourceKey
    file_hash: HashText
    contract_key: Identifier
    contract_version: VersionText
    contract_hash: HashText
    mapping_version: Annotated[int, Field(strict=True, ge=1)]
    mapping_hash: HashText
    reconciliation_group_hash: HashText
    formula_id: Identifier
    formula_version: VersionText
    engine_version: VersionText
    catalog_version: VersionText
    state: Literal["calculated", "incomplete"]
    reason_codes: tuple[Identifier, ...]
    formula_hash: HashText
    input_hash: HashText
    result_hash: HashText
    trace_hash: HashText
    evidence_snapshot_hash: HashText
    evidence_snapshot: dict[str, object]
    created_at: datetime


class FinancialCalculationCreateResponse(ContractModel):
    schema_version: Literal[1] = 1
    run: FinancialCalculationRunResponse
    trace: FinancialCalculationTrace


class FinancialCalculationFormulaCatalogResponse(ContractModel):
    schema_version: Literal[1] = 1
    catalog_version: VersionText
    items: tuple[FormulaCatalogEntry, ...]
