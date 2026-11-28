from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from businessflow.calculation_core.decimal import parse_decimal_text, validate_currency_code

HASH_PATTERN = r"^[0-9a-f]{64}$"
DECIMAL_PATTERN = r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$"
KEY_PATTERN = r"^[a-z][a-z0-9._-]{1,63}$"

HashText = Annotated[str, StringConstraints(strict=True, pattern=HASH_PATTERN)]
UUIDText = Annotated[UUID, Field(strict=False)]
DecimalText = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=64, pattern=DECIMAL_PATTERN),
]
CodeText = Annotated[
    str, StringConstraints(strict=True, min_length=2, max_length=64, pattern=KEY_PATTERN)
]
LabelText = Annotated[str, StringConstraints(strict=True, min_length=1, max_length=160)]
DimensionValue = Annotated[
    str,
    StringConstraints(strict=True, min_length=1, max_length=128, pattern=r"^[^\x00-\x1f]+$"),
]


def _parse_period_date(value: object) -> date:
    if type(value) is date:
        return value
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value) is None:
        raise ValueError("period_start must be an ISO-8601 calendar date")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("period_start must be a real calendar date") from exc


def _parse_aware_datetime(value: object) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        if (
            not isinstance(value, str)
            or re.fullmatch(
                r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
                r"(?:\.[0-9]{1,6})?(?:Z|[+-][0-9]{2}:[0-9]{2})",
                value,
            )
            is None
        ):
            raise ValueError("timestamp must be an ISO-8601 datetime with a timezone")
        try:
            parsed = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError as exc:
            raise ValueError("timestamp must be a real ISO-8601 datetime") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


PeriodDate = Annotated[date, BeforeValidator(_parse_period_date)]
AwareDateTime = Annotated[datetime, BeforeValidator(_parse_aware_datetime)]


class ForecastContractModel(BaseModel):
    """Strict, versioned wire contracts; decimals remain plain text, never binary floats."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class ForecastDimensionDefinition(ForecastContractModel):
    key: CodeText
    label: LabelText


class ForecastSourceReference(ForecastContractModel):
    """Caller-supplied provenance reference; it is not source authentication."""

    evidence_type: Literal["caller_assertion", "synthetic_fixture"]
    source_id: CodeText
    source_hash: HashText
    record_locator: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=1,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
        ),
    ]
    record_hash: HashText


class ForecastDatasetObservation(ForecastContractModel):
    period_start: PeriodDate
    dimension_values: dict[CodeText, DimensionValue] = Field(default_factory=dict)
    value: DecimalText | None
    missing_reason: Annotated[
        str | None, StringConstraints(strict=True, min_length=1, max_length=256)
    ] = None
    period_coverage: Literal["complete", "incomplete", "unknown"] | None = None
    coverage_evidence_hash: HashText | None = None
    source_reference: ForecastSourceReference

    @model_validator(mode="after")
    def validate_missing_value(self) -> ForecastDatasetObservation:
        if self.value is None and self.missing_reason is None:
            raise ValueError("missing observations require an explicit missing_reason")
        if self.value is not None:
            parse_decimal_text(self.value)
            if self.missing_reason is not None:
                raise ValueError("observed values cannot include a missing_reason")
        return self


class ForecastDatasetSourceLineage(ForecastContractModel):
    """Bounded synthetic Operations lineage; never authenticates a real source."""

    schema_version: Literal[1] = 1
    target_contract_key: Literal["operations.weekly_work_requests.first_received.v1"]
    company_id: UUIDText
    source_id: CodeText
    source_profile_version_id: UUIDText
    source_snapshot_id: UUIDText
    source_snapshot_hash: HashText
    source_authority: Literal["synthetic_only_not_independent_source_verification"]
    workflow_key: CodeText
    workflow_support_evidence_hash: HashText
    calendar_timezone: Annotated[
        str,
        StringConstraints(
            strict=True,
            min_length=1,
            max_length=64,
            pattern=r"^[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+.-]+)*$",
        ),
    ]
    week_start: Literal["monday"]
    snapshot_as_of: AwareDateTime
    first_received_semantics: Literal["explicit_synthetic_fixture_timestamp"]
    replayed_event_version_count: Annotated[int, Field(strict=True, ge=0)]
    duplicate_request_stream_count: Annotated[int, Field(strict=True, ge=0)]
    corrected_request_count: Annotated[int, Field(strict=True, ge=0)]
    readiness_status: Literal["ready_for_synthetic_evaluation", "blocked"]
    readiness_reason_codes: Annotated[list[CodeText], Field(max_length=16)] = Field(
        default_factory=list
    )
    point_in_time_status: Literal["eligible", "blocked"]
    point_in_time_reason_codes: Annotated[list[CodeText], Field(max_length=16)] = Field(
        default_factory=list
    )

    @field_validator("calendar_timezone")
    @classmethod
    def known_calendar_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("calendar_timezone must be a recognized IANA timezone") from exc
        return value

    @field_validator("snapshot_as_of")
    @classmethod
    def require_snapshot_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("snapshot_as_of must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_lineage_status(self) -> ForecastDatasetSourceLineage:
        if len(self.readiness_reason_codes) != len(set(self.readiness_reason_codes)):
            raise ValueError("readiness reason codes must be unique")
        if self.readiness_status == "ready_for_synthetic_evaluation":
            if self.readiness_reason_codes or self.point_in_time_status != "eligible":
                raise ValueError("ready lineage cannot include readiness or point-in-time blockers")
        elif not self.readiness_reason_codes:
            raise ValueError("blocked readiness lineage requires an explicit reason")
        if len(self.point_in_time_reason_codes) != len(set(self.point_in_time_reason_codes)):
            raise ValueError("point-in-time reason codes must be unique")
        if self.point_in_time_status == "eligible" and self.point_in_time_reason_codes:
            raise ValueError("eligible point-in-time lineage cannot include blockers")
        if self.point_in_time_status == "blocked" and not self.point_in_time_reason_codes:
            raise ValueError("blocked point-in-time lineage requires an explicit reason")
        return self


class ForecastDatasetCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    dataset_key: CodeText
    expected_version: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] | None = None
    display_name: LabelText
    data_origin: Literal["synthetic_fixture", "caller_assertion"]
    source_lineage: ForecastDatasetSourceLineage | None = None
    target_key: CodeText
    target_label: LabelText
    unit: Annotated[
        str,
        StringConstraints(strict=True, min_length=1, max_length=32, pattern=r"^[A-Za-z0-9._/-]+$"),
    ]
    currency_code: Annotated[str, StringConstraints(strict=True, pattern=r"^[A-Z]{3}$")] | None = (
        None
    )
    frequency: Literal["daily", "weekly", "monthly"]
    dimensions: Annotated[list[ForecastDimensionDefinition], Field(max_length=8)] = Field(
        default_factory=list
    )
    observations: Annotated[
        list[ForecastDatasetObservation], Field(min_length=1, max_length=20_000)
    ]

    @model_validator(mode="after")
    def validate_dataset(self) -> ForecastDatasetCreate:
        if self.currency_code is not None:
            validate_currency_code(self.currency_code)
        dimension_keys = [dimension.key for dimension in self.dimensions]
        if len(dimension_keys) != len(set(dimension_keys)):
            raise ValueError("dataset dimension keys must be unique")
        expected_dimensions = set(dimension_keys)
        identities: set[tuple[date, tuple[tuple[str, str], ...]]] = set()
        expected_evidence = (
            "synthetic_fixture" if self.data_origin == "synthetic_fixture" else "caller_assertion"
        )
        if self.source_lineage is not None and (
            self.data_origin != "synthetic_fixture"
            or self.target_key != "operations.weekly_work_requests"
            or self.unit != "requests/week"
            or self.frequency != "weekly"
            or self.currency_code is not None
            or dimension_keys != ["workflow"]
        ):
            raise ValueError("synthetic work-request lineage does not match the dataset contract")
        for observation in self.observations:
            if set(observation.dimension_values) != expected_dimensions:
                raise ValueError("each observation must provide exactly the declared dimensions")
            if self.frequency == "weekly" and observation.period_start.isoweekday() != 1:
                raise ValueError("weekly observations must begin on Monday")
            if self.frequency == "monthly" and observation.period_start.day != 1:
                raise ValueError("monthly observations must begin on the first calendar day")
            identity = (
                observation.period_start,
                tuple(sorted(observation.dimension_values.items())),
            )
            if identity in identities:
                raise ValueError("duplicate observation for period and dimension values")
            identities.add(identity)
            if observation.source_reference.evidence_type != expected_evidence:
                raise ValueError("source evidence type must match the dataset's declared origin")
            if self.source_lineage is not None:
                if (
                    observation.dimension_values.get("workflow") != self.source_lineage.workflow_key
                    or observation.source_reference.evidence_type != "synthetic_fixture"
                    or observation.source_reference.source_id != self.source_lineage.source_id
                    or observation.source_reference.source_hash
                    != self.source_lineage.source_snapshot_hash
                    or observation.period_coverage is None
                ):
                    raise ValueError(
                        "observation source reference or period coverage does not match lineage"
                    )
                if observation.period_coverage == "complete":
                    if observation.value is None or observation.coverage_evidence_hash is None:
                        raise ValueError("complete work-request periods require value and evidence")
                    count = parse_decimal_text(observation.value)
                    if count < 0 or count != count.to_integral_value():
                        raise ValueError("work-request counts must be non-negative integers")
                elif observation.value is not None:
                    raise ValueError("incomplete work-request periods cannot be assigned a count")
                if (
                    self.source_lineage.readiness_status == "ready_for_synthetic_evaluation"
                    and observation.period_coverage != "complete"
                ):
                    raise ValueError("ready work-request lineage requires complete periods")
        return self


class NaiveLastValueDefinition(ForecastContractModel):
    kind: Literal["naive_last_value"]


class SeasonalNaiveDefinition(ForecastContractModel):
    kind: Literal["seasonal_naive"]
    season_length: Annotated[int, Field(strict=True, ge=2, le=366)]


class MovingAverageDefinition(ForecastContractModel):
    kind: Literal["moving_average"]
    window_periods: Annotated[int, Field(strict=True, ge=1, le=366)]


ForecastModelDefinition = Annotated[
    NaiveLastValueDefinition | SeasonalNaiveDefinition | MovingAverageDefinition,
    Field(discriminator="kind"),
]


class ForecastModelCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    model_key: CodeText
    expected_version: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] | None = None
    display_name: LabelText
    definition: ForecastModelDefinition


class ForecastIntervalProtocol(ForecastContractModel):
    method: Literal["validation_absolute_error_nearest_rank"]
    coverage_level: DecimalText
    minimum_calibration_points: Annotated[int, Field(strict=True, ge=1, le=10_000)]

    @model_validator(mode="after")
    def validate_interval(self) -> ForecastIntervalProtocol:
        coverage = parse_decimal_text(self.coverage_level)
        if not Decimal("0") < coverage < Decimal("1"):
            raise ValueError("coverage_level must be greater than zero and less than one")
        return self


class ForecastEvaluationProtocolCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    protocol_key: CodeText
    expected_version: Annotated[int, Field(strict=True, ge=1, le=1_000_000)] | None = None
    display_name: LabelText
    target_key: CodeText
    frequency: Literal["daily", "weekly", "monthly"]
    horizon_periods: Annotated[int, Field(strict=True, ge=1, le=366)]
    validation_periods: Annotated[int, Field(strict=True, ge=1, le=3_660)]
    holdout_periods: Annotated[int, Field(strict=True, ge=1, le=3_660)]
    metrics: Annotated[
        list[Literal["mae", "rmse", "mape", "smape"]], Field(min_length=1, max_length=4)
    ]
    # These are technical defaults, not company-approved business policy.
    missing_actual_policy: Literal["exclude_and_report", "block_evaluation_if_missing_actuals"] = (
        "exclude_and_report"
    )
    outlier_method: Literal["not_assessed", "tukey_iqr_flag_only"] = "not_assessed"
    outlier_iqr_multiplier: DecimalText | None = None
    interval: ForecastIntervalProtocol | None = None

    @model_validator(mode="after")
    def validate_protocol(self) -> ForecastEvaluationProtocolCreate:
        if len(self.metrics) != len(set(self.metrics)):
            raise ValueError("evaluation metrics must be unique")
        if self.outlier_method == "tukey_iqr_flag_only":
            if self.outlier_iqr_multiplier is None:
                raise ValueError("tukey_iqr_flag_only requires an explicit IQR multiplier")
            if parse_decimal_text(self.outlier_iqr_multiplier) <= Decimal("0"):
                raise ValueError("outlier_iqr_multiplier must be positive")
        elif self.outlier_iqr_multiplier is not None:
            raise ValueError("outlier_iqr_multiplier is only valid for Tukey IQR flagging")
        return self


class ForecastEvaluationCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    dataset_version_id: UUIDText
    protocol_version_id: UUIDText
    model_version_ids: Annotated[list[UUIDText], Field(min_length=1, max_length=8)]
    stage: Literal["validation", "holdout"]
    calibration_evaluation_id: UUIDText | None = None

    @model_validator(mode="after")
    def validate_models_and_calibration(self) -> ForecastEvaluationCreate:
        if len(self.model_version_ids) != len(set(self.model_version_ids)):
            raise ValueError("model_version_ids must be unique")
        if self.stage == "validation" and self.calibration_evaluation_id is not None:
            raise ValueError("validation evaluations cannot use calibration evaluations")
        if self.stage == "holdout" and self.calibration_evaluation_id is None:
            raise ValueError("holdout evaluations must reference a prior validation evaluation")
        return self


class ForecastRunCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    dataset_version_id: UUIDText
    protocol_version_id: UUIDText
    model_version_id: UUIDText
    calibration_evaluation_id: UUIDText | None = None


class ForecastPromotionCreate(ForecastContractModel):
    schema_version: Literal[1] = 1
    evaluation_id: UUIDText
    model_version_id: UUIDText
    expected_evaluation_hash: HashText
    expected_model_hash: HashText
    decision: Literal["approve", "reject"]
    rationale: Annotated[str, StringConstraints(strict=True, min_length=10, max_length=2000)]

    @model_validator(mode="after")
    def validate_rationale(self) -> ForecastPromotionCreate:
        if len(self.rationale.strip()) < 10:
            raise ValueError("rationale must contain at least 10 non-whitespace characters")
        return self


class ForecastDatasetSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    dataset_key: CodeText
    version: int
    display_name: LabelText
    data_origin: Literal["synthetic_fixture", "caller_assertion"]
    target_key: CodeText
    target_label: LabelText
    unit: str
    currency_code: str | None
    frequency: Literal["daily", "weekly", "monthly"]
    start_period: date
    end_period: date
    observation_count: int
    series_count: int
    snapshot_hash: HashText
    created_by_user_id: UUID
    created_at: str


class ForecastDatasetEligibility(ForecastContractModel):
    status: Literal["eligible", "eligible_with_warnings", "not_evaluable"]
    scope: Literal["technical_input_assessment_not_business_approval"]
    eligibility_for_backtesting: Literal["candidate", "blocked"]
    period_count: int
    series_count: int
    panel_cell_count: int
    observed_value_count: int
    missing_or_unobserved_period_count: int
    reason_codes: list[str]


class ForecastDatasetDetail(ForecastContractModel):
    dataset: ForecastDatasetSummary
    snapshot: dict[str, object]
    eligibility: ForecastDatasetEligibility


class ForecastModelSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    model_key: CodeText
    version: int
    display_name: LabelText
    definition: ForecastModelDefinition
    model_hash: HashText
    created_by_user_id: UUID
    created_at: str


class ForecastProtocolSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    protocol_key: CodeText
    version: int
    display_name: LabelText
    target_key: CodeText
    frequency: Literal["daily", "weekly", "monthly"]
    definition: dict[str, object]
    protocol_hash: HashText
    policy_approval_status: Literal["not_established"]
    created_by_user_id: UUID
    created_at: str


class ForecastEvaluationSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    dataset_version_id: UUID
    protocol_version_id: UUID
    calibration_evaluation_id: UUID | None
    stage: Literal["validation", "holdout"]
    evaluation_status: Literal["evaluated", "partial", "not_evaluable", "failed"]
    model_version_ids: list[UUID]
    dataset_hash: HashText
    protocol_hash: HashText
    input_hash: HashText
    result_hash: HashText
    evaluation_hash: HashText
    created_by_user_id: UUID
    created_at: str


class ForecastEvaluationResponse(ForecastContractModel):
    evaluation: ForecastEvaluationSummary
    result_snapshot: dict[str, object]
    comparison: dict[str, object]


class ForecastRunSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    dataset_version_id: UUID
    protocol_version_id: UUID
    model_version_id: UUID
    calibration_evaluation_id: UUID | None
    run_status: Literal["forecasted", "partial", "not_evaluable", "failed"]
    horizon_periods: int
    dataset_hash: HashText
    model_hash: HashText
    protocol_hash: HashText
    input_hash: HashText
    result_hash: HashText
    run_hash: HashText
    created_by_user_id: UUID
    created_at: str


class ForecastRunResponse(ForecastContractModel):
    run: ForecastRunSummary
    result_snapshot: dict[str, object]


class ForecastPromotionSummary(ForecastContractModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    evaluation_id: UUID
    model_version_id: UUID
    decision: Literal["approve", "reject"]
    promotion_scope: Literal["nonproduction_only"]
    production_activation_status: Literal["blocked_pending_governance"]
    evaluation_hash: HashText
    model_hash: HashText
    rationale: str
    decided_by_user_id: UUID
    promotion_hash: HashText
    created_at: str


class ForecastPromotionResponse(ForecastContractModel):
    promotion: ForecastPromotionSummary


class ForecastPolicyDefaults(ForecastContractModel):
    policy_origin: Literal["system_default_not_company_approved"]
    split_method: Literal["chronological_expanding_window_rolling_origin"]
    missing_actuals: Literal["exclude_and_report_without_imputation"]
    zero_actual_mape: Literal["undefined_not_zero_filled"]
    zero_actual_and_forecast_smape: Literal["undefined_not_zero_filled"]
    sparse_or_insufficient_history: Literal["return_not_evaluable_or_abstain"]
    outliers: Literal["not_assessed_or_flag_only_never_silently_removed"]
    uncertainty: Literal["not_configured_unless_validation_calibration_is_available"]
    model_selection: Literal["no_automatic_ranking_or_recommendation"]
    production_activation: Literal["blocked_pending_governance_and_approval"]
    source_authority: Literal["caller_assertions_are_not_independently_verified"]


class ForecastCatalogResponse(ForecastContractModel):
    schema_version: Literal[1]
    engine_version: str
    supported_frequencies: list[Literal["daily", "weekly", "monthly"]]
    supported_baselines: list[Literal["naive_last_value", "seasonal_naive", "moving_average"]]
    supported_metrics: list[Literal["mae", "rmse", "mape", "smape"]]
    maximum_observations_per_dataset: int
    maximum_models_per_evaluation: int
    maximum_evaluation_points: int
    maximum_forecast_points: int
    policy_defaults: ForecastPolicyDefaults


class ForecastPage(ForecastContractModel):
    items: list[dict[str, object]]
    total: int
    limit: int
    offset: int


class ForecastDatasetPage(ForecastContractModel):
    items: list[ForecastDatasetSummary]
    total: int
    limit: int
    offset: int


class ForecastModelPage(ForecastContractModel):
    items: list[ForecastModelSummary]
    total: int
    limit: int
    offset: int


class ForecastProtocolPage(ForecastContractModel):
    items: list[ForecastProtocolSummary]
    total: int
    limit: int
    offset: int


class ForecastEvaluationPage(ForecastContractModel):
    items: list[ForecastEvaluationSummary]
    total: int
    limit: int
    offset: int


class ForecastRunPage(ForecastContractModel):
    items: list[ForecastRunSummary]
    total: int
    limit: int
    offset: int


class ForecastPromotionPage(ForecastContractModel):
    items: list[ForecastPromotionSummary]
    total: int
    limit: int
    offset: int


class ForecastEvaluationReplayResponse(ForecastContractModel):
    evaluation_id: UUID
    replay_status: Literal["verified", "mismatch"]
    stored_input_hash: HashText
    replayed_input_hash: HashText
    stored_result_hash: HashText
    replayed_result_hash: HashText
    stored_evaluation_hash: HashText
    replayed_evaluation_hash: HashText
    reason_codes: list[str]


class ForecastRunReplayResponse(ForecastContractModel):
    forecast_run_id: UUID
    replay_status: Literal["verified", "mismatch"]
    stored_input_hash: HashText
    replayed_input_hash: HashText
    stored_result_hash: HashText
    replayed_result_hash: HashText
    reason_codes: list[str]
