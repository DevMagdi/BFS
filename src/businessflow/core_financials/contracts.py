from __future__ import annotations

import calendar
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.calculation_core.contracts import CurrencyCode, HashText, Identifier
from businessflow.calculation_core.decimal import (
    RoundingMode,
    parse_decimal_text,
    validate_currency_code,
)
from businessflow.schemas import StrictModel

SourceType = Literal["general_ledger", "bank_activity"]
PeriodCompleteness = Literal["complete", "partial", "unknown"]
ImportRowStatus = Literal["accepted", "rejected", "duplicate"]
TransactionKind = Literal["normal", "refund", "reversal", "adjustment", "transfer"]
ReportingBasis = Literal["accrual", "cash"]
FinancialCategory = Literal[
    "revenue",
    "revenue_reduction",
    "cost_of_sales",
    "operating_expense",
    "non_operating_income",
    "non_operating_expense",
    "tax_expense",
]
FinancialMeasure = Literal[
    "revenue",
    "expenses",
    "gross_profit",
    "operating_profit",
    "net_profit",
    "cash_flow",
]
ProfitAndLossMeasure = Literal[
    "revenue", "expenses", "gross_profit", "operating_profit", "net_profit"
]
CategoryCoverage = Literal["complete", "not_applicable", "unknown"]
NormalBalance = Literal["debit", "credit"]
RoundingTarget = Literal["none", "currency_minor_unit", "fixed_scale"]

MoneyText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=64,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
]
SignedAmountText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=65,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
]
OutputMoneyText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=512,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
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
SourceRecordId = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=256,
        pattern=r"^[^\s].*[^\s]$|^[^\s]$",
    ),
]
AccountCode = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r".*\S.*"),
]
HeaderName = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r".*\S.*"),
]

LEDGER_FIELDS: tuple[str, ...] = (
    "source_record_id",
    "transaction_date",
    "account_code",
    "debit",
    "credit",
    "currency",
    "event_type",
    "reversal_of_record_id",
)
BANK_FIELDS: tuple[str, ...] = (
    "source_record_id",
    "transaction_date",
    "bank_account_id",
    "cash_in",
    "cash_out",
    "currency",
    "event_type",
    "reversal_of_record_id",
)
FINANCIAL_CATEGORIES: tuple[FinancialCategory, ...] = (
    "revenue",
    "revenue_reduction",
    "cost_of_sales",
    "operating_expense",
    "non_operating_income",
    "non_operating_expense",
    "tax_expense",
)
P_AND_L_MEASURES: tuple[ProfitAndLossMeasure, ...] = (
    "revenue",
    "expenses",
    "gross_profit",
    "operating_profit",
    "net_profit",
)
FINANCIAL_MEASURES: tuple[FinancialMeasure, ...] = (
    *P_AND_L_MEASURES,
    "cash_flow",
)
PeriodAlignment = Literal["equal_day_count", "equal_calendar_month_count"]


def period_alignment(
    prior_start: date,
    prior_end: date,
    current_start: date,
    current_end: date,
) -> PeriodAlignment | None:
    """Match full calendar-month windows by month count and custom windows by inclusive days."""
    prior_whole_months = (
        prior_start.day == 1
        and prior_end.day == calendar.monthrange(prior_end.year, prior_end.month)[1]
    )
    current_whole_months = (
        current_start.day == 1
        and current_end.day == calendar.monthrange(current_end.year, current_end.month)[1]
    )
    if prior_whole_months and current_whole_months:
        prior_month_count = (
            (prior_end.year - prior_start.year) * 12 + prior_end.month - prior_start.month + 1
        )
        current_month_count = (
            (current_end.year - current_start.year) * 12
            + current_end.month
            - current_start.month
            + 1
        )
        return "equal_calendar_month_count" if prior_month_count == current_month_count else None
    if prior_whole_months or current_whole_months:
        return None
    prior_days = (prior_end - prior_start).days + 1
    current_days = (current_end - current_start).days + 1
    return "equal_day_count" if prior_days == current_days else None


class AccountRule(StrictModel):
    category: FinancialCategory
    normal_balance: NormalBalance


class FormulaTerm(StrictModel):
    category: FinancialCategory
    coefficient: Literal[-1, 1]


class MetricFormula(StrictModel):
    terms: tuple[FormulaTerm, ...] = Field(min_length=1, max_length=20)

    @model_validator(mode="after")
    def categories_must_be_unique(self) -> MetricFormula:
        categories = [term.category for term in self.terms]
        if len(categories) != len(set(categories)):
            raise ValueError("a metric formula may include each financial category only once")
        return self


class MonetaryRoundingPolicy(StrictModel):
    target: RoundingTarget
    mode: RoundingMode
    scale: int | None = Field(default=None, ge=0, le=18)

    @model_validator(mode="after")
    def validate_scale_for_target(self) -> MonetaryRoundingPolicy:
        if self.target == "fixed_scale" and self.scale is None:
            raise ValueError("fixed_scale rounding requires an explicit scale")
        if self.target != "fixed_scale" and self.scale is not None:
            raise ValueError("scale is only valid for fixed_scale rounding")
        return self


class CashFlowPolicyDefinition(StrictModel):
    """Explicit rules for a bounded observed bank-movement measure, not a cash-flow statement."""

    movement_rule: Literal["cash_in_less_cash_out"]
    refund_treatment: Literal["include_as_signed_bank_movement"]
    reversal_treatment: Literal["require_linked_exact_opposite_entry"]
    adjustment_treatment: Literal["include_as_signed_bank_movement"]
    transfer_treatment: Literal[
        "exclude_marked_transfers", "include_marked_transfers_as_observed_movements"
    ]
    opening_balance_treatment: Literal["not_reported_no_balance_fields"]
    closing_balance_treatment: Literal["not_reported_no_balance_fields"]


class CoreFinancialPolicyCreateRequest(StrictModel):
    """Versioned management-reporting policy; never represents accounting approval."""

    policy_key: Identifier
    reporting_framework: Literal["management_reporting"]
    ledger_basis: ReportingBasis
    account_map: dict[AccountCode, AccountRule] = Field(min_length=1, max_length=5000)
    category_coverage: dict[FinancialCategory, CategoryCoverage]
    metric_formulas: dict[ProfitAndLossMeasure, MetricFormula]
    rounding: MonetaryRoundingPolicy
    currency_policy: Literal["one_currency_no_fx"] = "one_currency_no_fx"
    period_policy: Literal["inclusive_transaction_date"] = "inclusive_transaction_date"
    refund_policy: Literal["signed_source_entry_uses_explicit_account_mapping"] = (
        "signed_source_entry_uses_explicit_account_mapping"
    )
    reversal_policy: Literal["require_linked_exact_opposite_entry"] = (
        "require_linked_exact_opposite_entry"
    )
    adjustment_policy: Literal["include_only_when_explicitly_account_mapped"] = (
        "include_only_when_explicitly_account_mapped"
    )
    cash_flow_definition: Literal["net_observed_bank_movement_not_cash_flow_statement"] = (
        "net_observed_bank_movement_not_cash_flow_statement"
    )
    # Optional only for validating historical v2 policies. Cash Flow abstains unless a new
    # immutable policy version supplies these explicit source and transfer rules.
    cash_flow_policy: CashFlowPolicyDefinition | None = None

    @model_validator(mode="after")
    def validate_policy_coverage(self) -> CoreFinancialPolicyCreateRequest:
        if set(self.category_coverage) != set(FINANCIAL_CATEGORIES):
            raise ValueError("category_coverage must explicitly classify every P&L category")
        if set(self.metric_formulas) != set(P_AND_L_MEASURES):
            raise ValueError("metric_formulas must define all five P&L measures exactly once")
        mapped_categories = {rule.category for rule in self.account_map.values()}
        referenced_categories = {
            term.category for formula in self.metric_formulas.values() for term in formula.terms
        }
        undeclared = referenced_categories - set(self.category_coverage)
        if undeclared:
            raise ValueError("every formula category requires an explicit coverage status")
        complete_without_accounts = {
            category
            for category, coverage in self.category_coverage.items()
            if coverage == "complete" and category not in mapped_categories
        }
        if complete_without_accounts:
            raise ValueError("complete category coverage requires at least one mapped account")
        mapped_but_not_applicable = {
            category
            for category, coverage in self.category_coverage.items()
            if coverage == "not_applicable" and category in mapped_categories
        }
        if mapped_but_not_applicable:
            raise ValueError("not_applicable categories cannot have mapped accounts")
        if not referenced_categories.intersection(mapped_categories):
            raise ValueError("account_map must map at least one formula category")
        return self


class CoreFinancialPolicyResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    created_by_user_id: UUID
    policy_key: str
    version: int
    authority: Literal["illustrative_not_authoritative"]
    accounting_policy_status: Literal["not_established"]
    definition_hash: HashText
    definition: CoreFinancialPolicyCreateRequest
    created_at: datetime


class CoreFinancialPolicyPage(StrictModel):
    items: list[CoreFinancialPolicyResponse]
    total: int
    limit: int
    offset: int


class IgnoredSourceColumn(StrictModel):
    header: HeaderName
    rationale: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=500, pattern=r".*\S.*"),
    ]


class CoreFinancialMappingCreateRequest(StrictModel):
    source_key: SourceKey
    source_type: SourceType
    field_mapping: dict[str, HeaderName] = Field(min_length=1, max_length=8)
    ignored_columns: tuple[IgnoredSourceColumn, ...] = Field(default_factory=tuple, max_length=256)

    @model_validator(mode="after")
    def validate_mapping_fields(self) -> CoreFinancialMappingCreateRequest:
        required = set(LEDGER_FIELDS if self.source_type == "general_ledger" else BANK_FIELDS)
        if set(self.field_mapping) != required:
            raise ValueError("field_mapping must map every source adapter field exactly once")
        headers = list(self.field_mapping.values())
        if len(headers) != len(set(headers)):
            raise ValueError("each source header may map to only one canonical field")
        ignored = [column.header for column in self.ignored_columns]
        if len(ignored) != len(set(ignored)) or set(ignored).intersection(headers):
            raise ValueError("ignored columns must be unique and cannot also be mapped")
        return self


class CoreFinancialMappingResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    created_by_user_id: UUID
    source_key: str
    source_type: SourceType
    version: int
    field_mapping: dict[str, str]
    ignored_columns: tuple[IgnoredSourceColumn, ...]
    mapping_hash: HashText
    contract_version: Literal[1] = 1
    created_at: datetime


class CoreFinancialMappingPage(StrictModel):
    items: list[CoreFinancialMappingResponse]
    total: int
    limit: int
    offset: int


class CoreFinancialImportMetadata(StrictModel):
    """Caller-declared import scope; completeness is not independently verified."""

    source_key: SourceKey
    source_type: SourceType
    mapping_id: UUID
    policy_id: UUID
    period_start: date
    period_end: date
    period_completeness: PeriodCompleteness

    @model_validator(mode="after")
    def validate_period(self) -> CoreFinancialImportMetadata:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


class CoreFinancialImportResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    source_key: str
    source_type: SourceType
    file_name: str
    file_hash: HashText
    file_size_bytes: int
    mapping_id: UUID
    mapping_version: int
    mapping_hash: HashText
    policy_id: UUID
    policy_version: int
    policy_hash: HashText
    period_start: date
    period_end: date
    period_completeness: PeriodCompleteness
    completeness_authority: Literal["caller_asserted_not_verified"]
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    quality_status: Literal["passed", "incomplete"]
    input_hash: HashText
    result_hash: HashText
    created_at: datetime
    replayed: bool = False


class CoreFinancialImportPage(StrictModel):
    items: list[CoreFinancialImportResponse]
    total: int
    limit: int
    offset: int


class CoreFinancialRecordResponse(StrictModel):
    id: UUID
    source_row_number: int
    source_record_id: str | None
    transaction_date: date | None
    currency_code: str | None
    account_code: str | None
    category: FinancialCategory | None
    event_type: TransactionKind | None
    signed_amount: SignedAmountText | None
    row_status: ImportRowStatus
    row_hash: HashText
    issues: tuple[str, ...]
    reverses_record_id: str | None


class CoreFinancialRecordPage(StrictModel):
    items: list[CoreFinancialRecordResponse]
    total: int
    limit: int
    offset: int


class ControlSource(StrictModel):
    source_id: Identifier
    source_hash: HashText
    record_hash: HashText | None = None
    record_locator: Annotated[
        str, StringConstraints(strict=True, min_length=1, max_length=256, pattern=r".*\S.*")
    ]


class MetricControl(StrictModel):
    metric: FinancialMeasure
    expected_value: MoneyText
    currency_code: CurrencyCode
    absolute_tolerance: MoneyText = "0"
    relative_tolerance: MoneyText = "0"
    source: ControlSource

    @field_validator("expected_value", "absolute_tolerance", "relative_tolerance")
    @classmethod
    def decimal_text_is_valid(cls, value: str) -> str:
        parse_decimal_text(value)
        return value

    @model_validator(mode="after")
    def tolerances_are_non_negative(self) -> MetricControl:
        if parse_decimal_text(self.absolute_tolerance) < 0:
            raise ValueError("absolute_tolerance must be non-negative")
        if parse_decimal_text(self.relative_tolerance) < 0:
            raise ValueError("relative_tolerance must be non-negative")
        return self


class CoreFinancialRunCreateRequest(StrictModel):
    """Persist one version-bound measure from its required source adapter."""

    policy_id: UUID
    measure: FinancialMeasure = "revenue"
    ledger_import_id: UUID | None = None
    bank_import_id: UUID | None = None
    period_start: date
    period_end: date
    currency_code: CurrencyCode
    controls: tuple[MetricControl, ...] = Field(default_factory=tuple, max_length=6)

    @field_validator("currency_code")
    @classmethod
    def supported_currency(cls, value: str) -> str:
        return validate_currency_code(value)

    @model_validator(mode="after")
    def validate_run_input(self) -> CoreFinancialRunCreateRequest:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        if self.measure == "cash_flow":
            if self.bank_import_id is None or self.ledger_import_id is not None:
                raise ValueError(
                    "cash_flow requires a bank import and must not use a ledger import"
                )
        elif self.ledger_import_id is None or self.bank_import_id is not None:
            raise ValueError("P&L measures require a ledger import and must not use a bank import")
        control_metrics = [control.metric for control in self.controls]
        if len(control_metrics) != len(set(control_metrics)):
            raise ValueError("only one reconciliation control per metric is supported")
        if any(control.metric != self.measure for control in self.controls):
            raise ValueError("reconciliation controls must match the requested measure")
        if any(control.currency_code != self.currency_code for control in self.controls):
            raise ValueError("control currency must match the requested report currency")
        return self


class MetricReconciliation(StrictModel):
    state: Literal["passed", "failed", "not_provided", "not_verifiable"]
    expected_value: MoneyText | None
    actual_value: OutputMoneyText | None
    variance: OutputMoneyText | None
    effective_tolerance: OutputMoneyText | None
    control_source_hash: HashText | None
    source_independence: Literal["caller_asserted_not_verified"] = "caller_asserted_not_verified"
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)


class CoreFinancialMetricResult(StrictModel):
    metric: FinancialMeasure
    label: str
    status: Literal["calculated", "incomplete", "missing_source"]
    basis: Literal["accrual", "cash", "cash_movement"]
    currency_code: CurrencyCode
    exact_value: OutputMoneyText | None
    reported_value: OutputMoneyText | None
    rounding_target: RoundingTarget | None
    rounding_mode: RoundingMode | None
    rounding_scale: int | None
    included_record_count: int
    excluded_other_currency_count: int
    excluded_outside_period_count: int
    excluded_transfer_count: int = 0
    required_categories: tuple[FinancialCategory, ...] = ()
    category_totals: dict[str, OutputMoneyText] = Field(default_factory=dict)
    reason_codes: tuple[str, ...] = Field(default_factory=tuple)
    reconciliation: MetricReconciliation
    source_import_ids: tuple[UUID, ...] = Field(default_factory=tuple)


class CoreFinancialAnalysisStatement(StrictModel):
    kind: Literal["deterministic_observation", "deterministic_calculation", "limitation"]
    code: str
    text: str
    metric: FinancialMeasure | None = None


class CoreFinancialRunResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    policy_id: UUID
    policy_version: int
    policy_hash: HashText
    authority: Literal["illustrative_not_authoritative"]
    reporting_framework: Literal["management_reporting"]
    accounting_policy_status: Literal["not_established"]
    period_start: date
    period_end: date
    period_rule: Literal["inclusive_transaction_date"]
    currency_code: CurrencyCode
    fx_policy: Literal["one_currency_no_fx"]
    ledger_import_id: UUID | None
    bank_import_id: UUID | None
    state: Literal["calculated", "incomplete"]
    engine_version: str
    metrics: tuple[CoreFinancialMetricResult, ...]
    analysis: tuple[CoreFinancialAnalysisStatement, ...]
    input_hash: HashText
    result_hash: HashText
    trace_hash: HashText
    created_at: datetime


class CoreFinancialRunPage(StrictModel):
    items: list[CoreFinancialRunResponse]
    total: int
    limit: int
    offset: int


class CoreFinancialPeriodSelection(StrictModel):
    period_start: date
    period_end: date

    @model_validator(mode="after")
    def validate_period(self) -> CoreFinancialPeriodSelection:
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        return self


class CoreFinancialComparisonRequest(StrictModel):
    prior_period: CoreFinancialPeriodSelection
    current_period: CoreFinancialPeriodSelection

    @model_validator(mode="after")
    def validate_period_order(self) -> CoreFinancialComparisonRequest:
        if self.prior_period.period_end >= self.current_period.period_start:
            raise ValueError("prior_period must end before current_period starts")
        return self


class CoreFinancialOverviewSource(StrictModel):
    id: UUID
    source_key: str
    source_type: SourceType
    file_name: str
    file_hash: HashText
    input_hash: HashText
    result_hash: HashText
    mapping_version: int
    mapping_hash: HashText
    policy_id: UUID
    policy_version: int
    policy_hash: HashText
    period_start: date
    period_end: date
    period_completeness: PeriodCompleteness
    completeness_authority: Literal["caller_asserted_not_verified"]
    quality_status: Literal["passed", "incomplete"]
    total_rows: int
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int
    import_url: str


class CoreFinancialComparisonRunReference(StrictModel):
    run_id: UUID
    measure: FinancialMeasure
    run_url: str
    evidence_url: str
    metric_status: Literal["calculated", "incomplete", "missing_source"]
    exact_value: OutputMoneyText | None
    reported_value: OutputMoneyText | None
    period_start: date
    period_end: date
    currency_code: CurrencyCode
    basis: Literal["accrual", "cash", "cash_movement"]
    run_state: Literal["calculated", "incomplete"]
    engine_version: str
    policy_id: UUID
    policy_version: int
    policy_hash: HashText
    input_hash: HashText
    result_hash: HashText
    trace_hash: HashText
    evidence_count: int = Field(ge=0)
    source_import: CoreFinancialOverviewSource
    category_totals: dict[str, OutputMoneyText] = Field(default_factory=dict)
    formula_terms: tuple[FormulaTerm, ...] = ()
    reason_codes: tuple[str, ...] = ()


class CoreFinancialOverviewMeasure(StrictModel):
    measure: FinancialMeasure
    label: str
    status: Literal["available", "incomplete", "withheld", "missing"]
    metric_status: Literal["calculated", "incomplete", "missing_source"] | None
    reported_value: OutputMoneyText | None
    exact_value: OutputMoneyText | None
    currency_code: CurrencyCode | None
    basis: Literal["accrual", "cash", "cash_movement"] | None
    period_start: date | None
    period_end: date | None
    period_rule: Literal["inclusive_transaction_date"] | None
    fx_policy: Literal["one_currency_no_fx"] | None
    run_id: UUID | None
    run_url: str | None
    evidence_url: str | None
    evidence_status: Literal["available", "empty", "not_available"]
    evidence_count: int = Field(ge=0)
    run_state: Literal["calculated", "incomplete"] | None
    engine_version: str | None
    policy_id: UUID | None
    policy_version: int | None
    policy_hash: HashText | None
    policy_url: str | None
    policy_authority: Literal["illustrative_not_authoritative"] | None
    reporting_framework: Literal["management_reporting"] | None
    accounting_policy_status: Literal["not_established"] | None
    source_imports: tuple[CoreFinancialOverviewSource, ...] = ()
    reconciliation: MetricReconciliation | None
    required_categories: tuple[FinancialCategory, ...] = ()
    category_totals: dict[str, OutputMoneyText] = Field(default_factory=dict)
    formula_terms: tuple[FormulaTerm, ...] = ()
    policy_formulas: dict[ProfitAndLossMeasure, tuple[FormulaTerm, ...]] = Field(
        default_factory=dict
    )
    category_coverage: dict[FinancialCategory, CategoryCoverage] = Field(default_factory=dict)
    cash_flow_policy: CashFlowPolicyDefinition | None = None
    reason_codes: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


class CoreFinancialOverviewRunReference(StrictModel):
    measure: FinancialMeasure
    run_id: UUID
    run_url: str
    evidence_url: str
    reported_value: OutputMoneyText | None
    exact_value: OutputMoneyText | None
    status: Literal["available", "incomplete", "withheld"]
    metric_status: Literal["calculated", "incomplete", "missing_source"]
    period_start: date
    period_end: date
    currency_code: CurrencyCode
    basis: Literal["accrual", "cash", "cash_movement"]
    engine_version: str
    policy_id: UUID
    policy_version: int
    policy_hash: HashText
    policy_url: str
    source_imports: tuple[CoreFinancialOverviewSource, ...]
    evidence_status: Literal["available", "empty"]
    evidence_count: int = Field(ge=0)
    reconciliation: MetricReconciliation
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


class CoreFinancialOverviewCheck(StrictModel):
    key: Literal[
        "gross_profit_bridge",
        "revenue_expenses_gross_profit",
        "operating_profit_bridge",
        "net_profit_bridge",
    ]
    label: str
    equation: str
    status: Literal["consistent", "inconsistent", "unverifiable", "non_comparable"]
    policy_relation_supported: bool | None
    policy_difference_terms: dict[str, int] = Field(default_factory=dict)
    required_measures: tuple[FinancialMeasure, ...]
    run_references: tuple[CoreFinancialOverviewRunReference, ...] = ()
    period_start: date | None = None
    period_end: date | None = None
    currency_code: CurrencyCode | None = None
    basis: Literal["accrual", "cash", "cash_movement"] | None = None
    variance: OutputMoneyText | None = None
    reason_codes: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()


class CoreFinancialOverviewResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    selection_rule: Literal["latest_saved_run_per_measure"]
    measures: tuple[CoreFinancialOverviewMeasure, ...]
    checks: tuple[CoreFinancialOverviewCheck, ...]
    limitations: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_all_measures_in_catalog_order(self) -> CoreFinancialOverviewResponse:
        if tuple(item.measure for item in self.measures) != FINANCIAL_MEASURES:
            raise ValueError(
                "the integrated overview must contain all six measures in catalog order"
            )
        return self


class CoreFinancialRunVerificationResponse(StrictModel):
    run_id: UUID
    passed: bool
    reason_codes: tuple[str, ...]
    recorded_input_hash: HashText
    replayed_input_hash: HashText
    recorded_result_hash: HashText
    replayed_result_hash: HashText
    engine_version: str


class CoreFinancialRunEvidenceResponse(StrictModel):
    id: UUID
    metric: FinancialMeasure
    record_id: UUID
    source_import_id: UUID
    source_key: str
    source_type: SourceType
    source_file_hash: HashText
    source_row_number: int
    source_row_hash: HashText
    source_record_id: str
    transaction_date: date
    currency_code: CurrencyCode
    account_code: str | None
    category: FinancialCategory | None
    event_type: TransactionKind
    source_signed_amount: SignedAmountText
    formula_coefficient: Literal[-1, 1]
    contribution_amount: OutputMoneyText


class CoreFinancialRunEvidencePage(StrictModel):
    items: list[CoreFinancialRunEvidenceResponse]
    total: int
    limit: int
    offset: int


class CoreFinancialComparisonContributor(StrictModel):
    category: FinancialCategory
    prior_contribution: OutputMoneyText
    current_contribution: OutputMoneyText
    absolute_variance: OutputMoneyText
    direction: Literal["increase", "decrease", "unchanged"]
    prior_evidence_count: int = Field(ge=0)
    current_evidence_count: int = Field(ge=0)
    prior_evidence_url: str
    current_evidence_url: str


class CoreFinancialComparisonFinding(StrictModel):
    measure: FinancialMeasure
    disposition: Literal["calculated", "abstained"]
    reason_codes: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    prior_run: CoreFinancialComparisonRunReference | None = None
    current_run: CoreFinancialComparisonRunReference | None = None
    absolute_variance: OutputMoneyText | None = None
    percentage_variance: OutputMoneyText | None = None
    percentage_variance_status: Literal["calculated", "abstained"]
    percentage_variance_reason_codes: tuple[str, ...] = ()
    direction: Literal["increase", "decrease", "unchanged"] | None = None
    prior_margin: OutputMoneyText | None = None
    current_margin: OutputMoneyText | None = None
    margin_variance_percentage_points: OutputMoneyText | None = None
    margin_status: Literal["not_applicable", "calculated", "abstained"]
    margin_reason_codes: tuple[str, ...] = ()
    margin_caution_codes: tuple[str, ...] = ()
    contributors_status: Literal["not_applicable", "calculated", "abstained"]
    contributors_reason_codes: tuple[str, ...] = ()
    contributors: list[CoreFinancialComparisonContributor] = Field(default_factory=list)


class CoreFinancialComparisonResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    selection_rule: Literal["latest_persisted_run_per_measure_exact_period"]
    prior_period: CoreFinancialPeriodSelection
    current_period: CoreFinancialPeriodSelection
    period_alignment: PeriodAlignment | None
    period_reason_codes: tuple[str, ...] = ()
    findings: tuple[CoreFinancialComparisonFinding, ...]
    limitations: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    @model_validator(mode="after")
    def require_all_measures_in_catalog_order(self) -> CoreFinancialComparisonResponse:
        if tuple(item.measure for item in self.findings) != FINANCIAL_MEASURES:
            raise ValueError("a period comparison must contain all six measures in catalog order")
        return self


class CoreFinancialTemplateResponse(StrictModel):
    illustrative_policy: CoreFinancialPolicyCreateRequest
    policy_disclaimer: str
    general_ledger_headers: tuple[str, ...]
    bank_activity_headers: tuple[str, ...]


def parse_iso_date(value: str | None) -> date | None:
    if value is None or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.isoformat() == value else None


def parse_canonical_datetime(value: datetime | None) -> datetime | None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamp must include a timezone")
    return value
