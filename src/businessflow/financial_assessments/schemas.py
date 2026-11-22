from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from iso4217 import Currency
from pydantic import Field, StringConstraints, field_validator, model_validator

from businessflow.schemas import StrictModel

FinancialVerdict = Literal[
    "financially_feasible",
    "financially_feasible_with_conditions",
    "financially_not_feasible",
    "incomplete",
]
FinancialFreshness = Literal["fresh", "stale", "invalid"]
FinancialInputFreshness = Literal["fresh", "stale", "missing", "invalid"]
FinancialPolicyStatus = Literal["no_approved_policy"]
FinancialHandoffStatus = Literal["ready", "missing", "not_approved", "stale", "invalid"]
FinancialDecisionOutcome = Literal["proceed_to_financial_evaluation"]
FinancialApprovalOutcome = Literal["approved"]
DecimalString = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=64,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]
SourceReference = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)
]
ShortDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)
]
LongDescription = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)
]

SourceKind = Literal[
    "contract",
    "proposal",
    "invoice",
    "supplier_quote",
    "estimate",
    "user_attestation",
    "other",
]
CommercialAmountKind = Literal["contract_value", "reported_revenue"]


class FinancialAssumptionInput(StrictModel):
    rationale: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=500)
    ]


class FinancialSourceInput(StrictModel):
    source_kind: SourceKind
    source_reference: SourceReference
    observed_at: datetime | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    assumption: FinancialAssumptionInput | None = None

    @field_validator("observed_at")
    @classmethod
    def observed_at_must_be_timezone_aware(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("observed_at must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_effective_dates(self) -> FinancialSourceInput:
        if (
            self.effective_from is not None
            and self.effective_to is not None
            and self.effective_to < self.effective_from
        ):
            raise ValueError("effective_to must not precede effective_from")
        return self


class CommercialAmountInput(StrictModel):
    kind: CommercialAmountKind
    amount: DecimalString = Field(
        description=(
            "Exact base-10 decimal text; numeric JSON values and exponent notation are rejected."
        )
    )
    currency: CurrencyCode
    source: FinancialSourceInput

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso_4217(cls, value: str) -> str:
        try:
            Currency(value)
        except ValueError as exc:
            raise ValueError("currency must be an ISO 4217 currency code") from exc
        return value


class CostObservationInput(StrictModel):
    label: ShortDescription
    amount: DecimalString = Field(
        description="Exact base-10 decimal text; no rounding, default, or aggregation is applied."
    )
    currency: CurrencyCode
    source: FinancialSourceInput

    @field_validator("currency")
    @classmethod
    def currency_must_be_iso_4217(cls, value: str) -> str:
        try:
            Currency(value)
        except ValueError as exc:
            raise ValueError("currency must be an ISO 4217 currency code") from exc
        return value


class EffortHoursInput(StrictModel):
    hours: DecimalString = Field(
        description=(
            "Exact base-10 decimal text in hours; this is an observation, not a cost formula."
        )
    )
    source: FinancialSourceInput


class FinancialTextInput(StrictModel):
    value: LongDescription
    source: FinancialSourceInput


class KnownRiskInput(StrictModel):
    description: LongDescription
    source: FinancialSourceInput


class ProjectFinancialInputCreate(StrictModel):
    expected_version: Annotated[int, Field(ge=1, le=1_000_000)] | None = Field(
        default=None,
        description=(
            "Omit only when creating the first version; otherwise supply the current version."
        ),
    )
    intake_version: Annotated[int, Field(ge=1, le=1_000_000)]
    commercial_amounts: (
        Annotated[list[CommercialAmountInput], Field(min_length=1, max_length=100)] | None
    ) = None
    cost_observations: (
        Annotated[list[CostObservationInput], Field(min_length=1, max_length=100)] | None
    ) = None
    effort_hours: EffortHoursInput | None = None
    payment_terms: FinancialTextInput | None = None
    known_risks: Annotated[list[KnownRiskInput], Field(min_length=1, max_length=100)] | None = None

    @model_validator(mode="after")
    def require_at_least_one_observation(self) -> ProjectFinancialInputCreate:
        if all(
            value is None
            for value in (
                self.commercial_amounts,
                self.cost_observations,
                self.effort_hours,
                self.payment_terms,
                self.known_risks,
            )
        ):
            raise ValueError("at least one sourced financial observation is required")
        return self


class FinancialInputVersionResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    project_intake_id: UUID
    input_case_id: UUID
    version: int
    supersedes_version_id: UUID | None
    intake_version: int
    created_by_user_id: UUID
    content_hash: str
    input_snapshot: dict[str, object]
    created_at: datetime
    is_current: bool


class FinancialInputPage(StrictModel):
    items: list[FinancialInputVersionResponse]
    total: int
    limit: int
    offset: int
    current_version: int | None


class FinancialPolicyCatalogResponse(StrictModel):
    catalog_version: str
    status: FinancialPolicyStatus
    approved_policies: list[dict[str, object]]
    formula_definitions: list[dict[str, object]]
    supported_outcomes: list[FinancialVerdict]
    emitted_outcome_without_approval: Literal["incomplete"]
    currency_handling: str
    decimal_handling: str
    policy_approval: dict[str, object] | None
    policy_effective_dates: dict[str, object] | None
    administration_available: bool


class FinancialReadinessResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    project_intake_id: UUID
    current_intake_version: int
    current_input_version: int | None
    input_intake_version: int | None
    input_freshness: FinancialInputFreshness
    m7_decision_id: UUID | None
    m7_decision_version: int | None
    m7_decision_status: Literal["pending_approval", "approved", "changes_requested"] | None
    m7_decision_outcome: str | None
    m7_evidence_freshness: FinancialFreshness | None
    handoff_status: FinancialHandoffStatus
    can_run_assessment: bool
    policy_status: FinancialPolicyStatus
    financial_verdict_if_run: Literal["incomplete"]
    missing_inputs: list[str]
    unprovided_observations: list[str]
    warnings: list[str]


class FinancialAssessmentResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    project_intake_id: UUID
    requested_by_user_id: UUID
    intake_version: int
    intake_version_hash: str
    financial_input_case_id: UUID | None
    financial_input_version_id: UUID | None
    financial_input_version: int | None
    financial_input_hash: str | None
    capacity_assessment_id: UUID
    capacity_assessment_version: str
    capacity_assessment_input_hash: str
    capacity_assessment_result_hash: str
    metric_run_id: UUID
    metric_catalog_version: str
    metric_calculation_version: str
    metric_input_hash: str
    metric_result_hash: str
    decision_case_id: UUID
    decision_version_id: UUID
    decision_version: int
    decision_outcome: FinancialDecisionOutcome
    decision_hash: str
    decision_evidence_hash: str
    decision_approval_id: UUID
    decision_approval_outcome: FinancialApprovalOutcome
    decision_approval_hash: str
    formula_catalog_version: str
    approved_policy_id: str | None
    approved_policy_version: str | None
    currency_codes: list[str]
    financial_verdict: FinancialVerdict
    input_hash: str
    result_hash: str
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]
    policy_status: FinancialPolicyStatus
    calculated_metrics: list[dict[str, object]]
    observations: dict[str, object]
    assumptions: list[dict[str, object]]
    missing_inputs: list[str]
    unprovided_observations: list[str]
    warnings: list[str]
    evidence_freshness: FinancialFreshness
    staleness_reasons: list[str]
    created_at: datetime


class FinancialAssessmentPage(StrictModel):
    items: list[FinancialAssessmentResponse]
    total: int
    limit: int
    offset: int
