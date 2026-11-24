from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.calculation_core.contracts import (
    ContractModel,
    HashText,
    Identifier,
    NonEmptyText,
    OutputDecimalText,
)
from businessflow.calculation_core.decimal import parse_output_decimal_text, validate_currency_code
from businessflow.financial_calculations.contracts import (
    FinanceSourceEvidenceReference,
    FinanceSourceIntakeReference,
    FinancialReconciliationGroupReference,
)

MAX_DIRECT_CASH_MOVEMENTS = 10_000
MAX_DIRECT_CASH_SNAPSHOT_ITEMS = 20_001
MAX_SOURCE_ROW_NUMBER = 100_002


def _parse_wire_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        raise ValueError("cash dates must be calendar dates, not timestamps")
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        if "T" in value or "t" in value or " " in value:
            raise ValueError("cash dates must be calendar dates, not timestamps")
        try:
            parsed = date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("dates must use canonical YYYY-MM-DD form") from exc
        if parsed.isoformat() != value:
            raise ValueError("dates must use canonical YYYY-MM-DD form")
        return parsed
    raise ValueError("dates must use canonical YYYY-MM-DD form")


RawAmountText = Annotated[str, StringConstraints(strict=True, max_length=64)]
ReasonCode = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=128,
        pattern=r"^[a-z][a-z0-9_]{0,127}$",
    ),
]
CashFlowType = Literal["receipt", "disbursement", "unknown", "unsupported"]
MovementBasis = Literal["actual", "forecast", "unknown", "unsupported"]
AmountCommitmentStatus = Literal[
    "verified", "missing", "invalid", "duplicate", "conflicting", "not_submitted"
]
InputOrigin = Literal["opening_cash", "movement", "missing_source_row"]
CashItemDispositionStatus = Literal[
    "included_in_known_subtotal",
    "superseded_actual_excluded",
    "historical_actual_excluded",
    "out_of_horizon_excluded",
    "overdue_forecast_unresolved",
    "no_date_unresolved",
    "unknown_classification",
    "unsupported_classification",
    "invalid_evidence",
    "duplicate_request",
    "conflicting_request",
    "missing_request",
    "invalid_supersession",
]


class DirectCashOpeningRequest(ContractModel):
    """One caller-designated opening-cash row; M14 does not authenticate its cash meaning."""

    source_row_number: Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)]
    amount_text: RawAmountText | None


class DirectCashMovementRequest(ContractModel):
    """One caller-classified M14 row; cash semantics remain separate from M14 evidence."""

    source_row_number: Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)]
    amount_text: RawAmountText | None
    cash_flow_type: CashFlowType
    basis: MovementBasis
    cash_date: date | None
    supersedes_source_row_number: (
        Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)] | None
    ) = None

    @field_validator("cash_date", mode="before")
    @classmethod
    def cash_date_must_be_canonical_local_date(cls, value: object) -> date | None:
        return _parse_wire_date(value)

    @model_validator(mode="after")
    def validate_supersession_reference(self) -> DirectCashMovementRequest:
        if self.supersedes_source_row_number == self.source_row_number:
            raise ValueError("a movement cannot supersede itself")
        return self


class DirectCashForecastRequest(ContractModel):
    """Exact, single-M14-group request for the fixed 13-week M16 mechanical schedule."""

    schema_version: Literal[1] = 1
    snapshot_id: UUID = Field(strict=False)
    reconciliation_group_hash: HashText
    as_of_date: date
    opening_cash: DirectCashOpeningRequest | None = None
    movements: tuple[DirectCashMovementRequest, ...] = Field(
        default_factory=tuple,
        max_length=MAX_DIRECT_CASH_MOVEMENTS,
        strict=False,
    )

    @field_validator("as_of_date", mode="before")
    @classmethod
    def as_of_date_must_be_canonical_local_date(cls, value: object) -> date:
        parsed = _parse_wire_date(value)
        if parsed is None:
            raise ValueError("as_of_date is required")
        return parsed


class DirectCashForecastInputItem(ContractModel):
    """Redacted M16 row assertion plus M14 evidence; it never stores the submitted amount."""

    schema_version: Literal[1] = 1
    input_id: Identifier
    origin: InputOrigin
    source_row_number: Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)]
    amount_fingerprint: HashText
    amount_fingerprint_key_version: Literal["app-secret.v1"] = "app-secret.v1"
    amount_commitment_status: AmountCommitmentStatus
    cash_flow_type: Literal["opening_cash", "receipt", "disbursement", "unknown", "unsupported"]
    basis: MovementBasis | None
    cash_date: date | None
    supersedes_source_row_number: (
        Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)] | None
    )
    evidence: FinanceSourceEvidenceReference | None
    issue_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)

    @model_validator(mode="after")
    def validate_input_item(self) -> DirectCashForecastInputItem:
        if self.origin == "opening_cash":
            if (
                self.cash_flow_type != "opening_cash"
                or self.basis is not None
                or self.cash_date is not None
                or self.supersedes_source_row_number is not None
            ):
                raise ValueError("opening-cash evidence cannot carry movement semantics")
        elif self.cash_flow_type == "opening_cash":
            raise ValueError("only the opening_cash input may use the opening_cash role")
        if self.origin == "missing_source_row" and not self.issue_codes:
            raise ValueError("an omitted source row must carry an explicit issue code")
        if self.evidence is not None and self.evidence.source_row_number != self.source_row_number:
            raise ValueError("M14 row evidence must match its M16 source row number")
        if self.supersedes_source_row_number == self.source_row_number:
            raise ValueError("a movement cannot supersede itself")
        return self


class DirectCashForecastInputSnapshot(ContractModel):
    """Immutable M14 references and M16 assertions, with no raw amount strings."""

    schema_version: Literal[1] = 1
    snapshot_id: UUID
    tenant_id: UUID
    company_id: UUID
    captured_at: datetime
    as_of_date: date
    company_timezone: NonEmptyText
    horizon_weeks: Literal[13] = 13
    intake: FinanceSourceIntakeReference
    requested_group_hash: HashText
    source_evidence_integrity_status: Literal["verified", "incomplete", "not_verifiable"]
    group: FinancialReconciliationGroupReference | None
    opening_cash: DirectCashForecastInputItem | None
    movements: tuple[DirectCashForecastInputItem, ...] = Field(
        default_factory=tuple, max_length=MAX_DIRECT_CASH_SNAPSHOT_ITEMS, strict=False
    )

    @field_validator("captured_at")
    @classmethod
    def capture_time_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("captured_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_snapshot_scope(self) -> DirectCashForecastInputSnapshot:
        if self.intake.tenant_id != self.tenant_id or self.intake.company_id != self.company_id:
            raise ValueError("M14 intake scope must match the direct-cash forecast scope")
        if self.snapshot_id.int == 0 or self.intake.intake_id.int == 0:
            raise ValueError("snapshot and intake identifiers must not be empty UUIDs")
        if self.group is not None and (
            self.group.reconciliation_group_hash != self.requested_group_hash
        ):
            raise ValueError("resolved M14 group must match the requested group hash")
        if self.opening_cash is not None and self.opening_cash.origin != "opening_cash":
            raise ValueError("opening_cash must reference an opening-cash input item")
        if any(item.origin not in {"movement", "missing_source_row"} for item in self.movements):
            raise ValueError("forecast movements cannot contain opening-cash input items")
        input_ids = [item.input_id for item in self.movements]
        if self.opening_cash is not None:
            input_ids.append(self.opening_cash.input_id)
        if len(input_ids) != len(set(input_ids)):
            raise ValueError("direct-cash snapshot input identifiers must be unique")
        return self


class CashFlowSubtotal(ContractModel):
    """Exact subtotal of explicit caller-classified rows, never a completeness assertion."""

    status: Literal["known_subtotal", "partial", "unknown"]
    known_subtotal: OutputDecimalText | None
    source_row_numbers: tuple[Annotated[int, Field(strict=True, ge=2)], ...] = Field(
        default_factory=tuple, max_length=MAX_DIRECT_CASH_SNAPSHOT_ITEMS, strict=False
    )
    unresolved_source_row_numbers: tuple[Annotated[int, Field(strict=True, ge=2)], ...] = Field(
        default_factory=tuple, max_length=MAX_DIRECT_CASH_SNAPSHOT_ITEMS, strict=False
    )
    reason_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)

    @field_validator("known_subtotal")
    @classmethod
    def subtotal_must_be_finite_decimal_text(cls, value: str | None) -> str | None:
        if value is not None:
            parse_output_decimal_text(value)
        return value

    @model_validator(mode="after")
    def validate_subtotal_status(self) -> CashFlowSubtotal:
        if self.status == "known_subtotal" and (
            self.known_subtotal is None or not self.source_row_numbers
        ):
            raise ValueError("known subtotals require at least one explicit source row")
        if self.status == "unknown" and self.known_subtotal is not None:
            raise ValueError("unknown subtotals cannot contain a numeric value")
        if len(self.source_row_numbers) != len(set(self.source_row_numbers)):
            raise ValueError("subtotal source row numbers must be unique")
        if len(self.unresolved_source_row_numbers) != len(set(self.unresolved_source_row_numbers)):
            raise ValueError("unresolved source row numbers must be unique")
        return self


class CashForecastWeek(ContractModel):
    schema_version: Literal[1] = 1
    week_number: Annotated[int, Field(strict=True, ge=1, le=13)]
    period_start: date
    period_end: date
    opening_cash: OutputDecimalText | None
    opening_cash_status: Literal["known", "missing_or_invalid", "previous_week_closing_unknown"]
    actual_receipts: CashFlowSubtotal
    forecast_receipts: CashFlowSubtotal
    actual_disbursements: CashFlowSubtotal
    forecast_disbursements: CashFlowSubtotal
    receipts_known_subtotal: OutputDecimalText | None
    receipts_status: Literal["known_subtotal", "partial", "unknown"]
    disbursements_known_subtotal: OutputDecimalText | None
    disbursements_status: Literal["known_subtotal", "partial", "unknown"]
    closing_cash_known_item_rollforward: OutputDecimalText | None
    closing_cash_status: Literal["known_item_rollforward", "unknown"]
    reason_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)

    @field_validator(
        "opening_cash",
        "receipts_known_subtotal",
        "disbursements_known_subtotal",
        "closing_cash_known_item_rollforward",
    )
    @classmethod
    def weekly_values_must_be_finite_decimal_text(cls, value: str | None) -> str | None:
        if value is not None:
            parse_output_decimal_text(value)
        return value

    @model_validator(mode="after")
    def validate_week_bounds_and_statuses(self) -> CashForecastWeek:
        if (self.period_end - self.period_start).days != 6:
            raise ValueError("each M16 week must contain exactly seven inclusive calendar dates")
        if (self.opening_cash is None) != (self.opening_cash_status != "known"):
            raise ValueError("opening-cash status must match its numeric value")
        if (self.closing_cash_known_item_rollforward is None) != (
            self.closing_cash_status != "known_item_rollforward"
        ):
            raise ValueError("closing-cash status must match its numeric value")
        if self.receipts_status == "known_subtotal" and self.receipts_known_subtotal is None:
            raise ValueError("known receipt subtotals require a numeric value")
        if self.receipts_status == "unknown" and self.receipts_known_subtotal is not None:
            raise ValueError("unknown receipt subtotals cannot contain a numeric value")
        if (
            self.disbursements_status == "known_subtotal"
            and self.disbursements_known_subtotal is None
        ):
            raise ValueError("known disbursement subtotals require a numeric value")
        if self.disbursements_status == "unknown" and self.disbursements_known_subtotal is not None:
            raise ValueError("unknown disbursement subtotals cannot contain a numeric value")
        return self


class CashItemDisposition(ContractModel):
    schema_version: Literal[1] = 1
    input_id: Identifier
    source_row_number: Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)]
    cash_flow_type: Literal["receipt", "disbursement", "unknown", "unsupported"]
    basis: MovementBasis
    cash_date: date | None
    status: CashItemDispositionStatus
    evidence: FinanceSourceEvidenceReference | None
    reason_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)


class DirectCashForecastGovernanceStatus(ContractModel):
    source_authority: Literal["not_established"] = "not_established"
    m14_cash_semantics: Literal["caller_asserted_not_authenticated_by_m14"] = (
        "caller_asserted_not_authenticated_by_m14"
    )
    cash_population_completeness: Literal["not_established"] = "not_established"
    finance_owner_approval: Literal["not_established"] = "not_established"
    cash_timing_policy_approval: Literal["not_established"] = "not_established"
    correction_policy_approval: Literal["not_established"] = "not_established"
    retention_policy_approval: Literal["not_established"] = "not_established"
    forecast_accuracy: Literal["not_established"] = "not_established"
    production_readiness: Literal["not_established"] = "not_established"
    production_customer_data_authorization: Literal["not_authorized"] = "not_authorized"


class DirectCashForecastResultSnapshot(ContractModel):
    schema_version: Literal[1] = 1
    calculation_state: Literal["calculated", "incomplete"]
    forecast_completeness_status: Literal["not_established_by_m14"] = "not_established_by_m14"
    cash_semantics_origin: Literal["caller_asserted_not_authenticated_by_m14"] = (
        "caller_asserted_not_authenticated_by_m14"
    )
    as_of_date: date
    horizon_end_date: date
    horizon_weeks: Literal[13] = 13
    company_timezone: NonEmptyText
    currency_code: str | None
    opening_cash_status: Literal["known", "missing_or_invalid"]
    opening_cash_source_row_number: (
        Annotated[int, Field(strict=True, ge=2, le=MAX_SOURCE_ROW_NUMBER)] | None
    )
    weeks: tuple[CashForecastWeek, ...] = Field(min_length=13, max_length=13, strict=False)
    item_dispositions: tuple[CashItemDisposition, ...] = Field(
        default_factory=tuple, max_length=MAX_DIRECT_CASH_SNAPSHOT_ITEMS, strict=False
    )
    reason_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)
    limitations: tuple[NonEmptyText, ...] = Field(default_factory=tuple, strict=False)
    governance_status: DirectCashForecastGovernanceStatus = Field(
        default_factory=DirectCashForecastGovernanceStatus
    )

    @field_validator("currency_code")
    @classmethod
    def result_currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value

    @model_validator(mode="after")
    def validate_horizon(self) -> DirectCashForecastResultSnapshot:
        if self.horizon_end_date != self.as_of_date + timedelta(days=90):
            raise ValueError("M16 result horizon must end 90 dates after the inclusive as_of_date")
        expected_start = self.as_of_date
        for index, week in enumerate(self.weeks, start=1):
            if (
                week.week_number != index
                or week.period_start != expected_start
                or week.period_end + timedelta(days=1) != expected_start + timedelta(days=7)
            ):
                raise ValueError(
                    "M16 week boundaries must be contiguous seven-day local-date blocks"
                )
            expected_start = week.period_end + timedelta(days=1)
        first_week = self.weeks[0]
        if self.opening_cash_status == "known":
            if (
                self.opening_cash_source_row_number is None
                or first_week.opening_cash_status != "known"
                or first_week.opening_cash is None
            ):
                raise ValueError("known opening cash requires an evidenced week-one opening value")
        elif (
            first_week.opening_cash_status != "missing_or_invalid"
            or first_week.opening_cash is not None
        ):
            raise ValueError("missing opening cash must leave the week-one opening value unknown")
        for previous, current in zip(self.weeks, self.weeks[1:], strict=False):
            previous_closing = previous.closing_cash_known_item_rollforward
            current_opening = current.opening_cash
            if previous_closing is None:
                if (
                    current_opening is not None
                    or current.opening_cash_status != "previous_week_closing_unknown"
                ):
                    raise ValueError("unknown closing cash must remain unknown in the next week")
            elif (
                current_opening is None
                or current.opening_cash_status != "known"
                or parse_output_decimal_text(current_opening)
                != parse_output_decimal_text(previous_closing)
            ):
                raise ValueError("each week must carry forward the prior known closing cash")
        return self


class DirectCashForecastTrace(ContractModel):
    schema_version: Literal[1] = 1
    run_id: UUID
    engine_version: Identifier
    catalog_version: Identifier
    formula_id: Identifier
    formula_version: Identifier
    formula_definition: dict[str, object]
    formula_hash: HashText
    input_snapshot: DirectCashForecastInputSnapshot
    input_hash: HashText
    result_snapshot: DirectCashForecastResultSnapshot
    result_hash: HashText
    calculation_state: Literal["calculated", "incomplete"]
    trace_hash: HashText


class DirectCashForecastRunResponse(ContractModel):
    schema_version: Literal[1] = 1
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    intake_id: UUID
    source_key: Identifier
    source_file_hash: HashText
    contract_key: Identifier
    contract_version: Identifier
    contract_hash: HashText
    mapping_version: Annotated[int, Field(strict=True, ge=1)]
    mapping_hash: HashText
    reconciliation_group_hash: HashText
    formula_id: Identifier
    formula_version: Identifier
    formula_definition: dict[str, object]
    formula_hash: HashText
    engine_version: Identifier
    catalog_version: Identifier
    calculation_state: Literal["calculated", "incomplete"]
    forecast_completeness_status: Literal["not_established_by_m14"] = "not_established_by_m14"
    reason_codes: tuple[ReasonCode, ...] = Field(default_factory=tuple, strict=False)
    horizon_weeks: Literal[13] = 13
    as_of_date: date
    company_timezone: NonEmptyText
    currency_code: str | None
    input_hash: HashText
    result_hash: HashText
    trace_hash: HashText
    evidence_snapshot_hash: HashText
    input_snapshot: DirectCashForecastInputSnapshot
    result_snapshot: DirectCashForecastResultSnapshot
    captured_at: datetime
    created_at: datetime

    @field_validator("currency_code")
    @classmethod
    def response_currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value


class DirectCashForecastCreateResponse(ContractModel):
    schema_version: Literal[1] = 1
    run: DirectCashForecastRunResponse
    trace: DirectCashForecastTrace


class DirectCashForecastReplayVerificationReport(ContractModel):
    schema_version: Literal[1] = 1
    passed: bool
    reason_codes: tuple[ReasonCode, ...]
    run_id: UUID
    recorded_trace_hash: HashText
    replayed_trace_hash: HashText
    recorded_input_hash: HashText
    replayed_input_hash: HashText
    recorded_result_hash: HashText
    replayed_result_hash: HashText
    recorded_evidence_integrity_valid: bool
    current_formula_compatible: bool


class DirectCashForecastContractResponse(ContractModel):
    schema_version: Literal[1] = 1
    contract_key: Identifier
    contract_version: Identifier
    formula_id: Identifier
    formula_version: Identifier
    engine_version: Identifier
    catalog_version: Identifier
    definition: dict[str, object]
    definition_hash: HashText
    governance_status: DirectCashForecastGovernanceStatus = Field(
        default_factory=DirectCashForecastGovernanceStatus
    )
