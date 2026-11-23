from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, model_validator

from businessflow.company_context.domain import is_monday
from businessflow.metrics.calculation import MAX_METRIC_WEEKS
from businessflow.schemas import StrictModel


class MetricRunCreate(StrictModel):
    start_week_date: date
    week_count: Annotated[int, Field(ge=1, le=MAX_METRIC_WEEKS)]

    @model_validator(mode="after")
    def validate_period(self) -> MetricRunCreate:
        if not is_monday(self.start_week_date):
            raise ValueError("start_week_date must be a Monday")
        try:
            self.start_week_date + timedelta(days=self.week_count * 7 - 1)
        except OverflowError as exc:
            raise ValueError("requested period is outside the supported calendar range") from exc
        return self


class MetricDefinitionResponse(StrictModel):
    metric_id: str
    version: str
    name: str
    unit: str
    formula: str
    description: str
    limitations: list[str]


class MetricCatalogResponse(StrictModel):
    catalog_version: str
    catalog_status: Literal["implementation_draft"]
    decision_use: Literal["observational_only_no_recommendation"]
    calculation_version: str
    week_convention: str
    metrics: list[MetricDefinitionResponse]


class MetricRunResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    start_week_date: date
    end_week_date: date
    week_count: int
    metric_catalog_version: str
    calculation_version: str
    reliability_status: str
    input_hash: str
    result_hash: str
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]
    created_at: datetime


class MetricInsightRunCreate(StrictModel):
    baseline_metric_run_id: UUID
    comparison_metric_run_id: UUID
    metric_id: Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")]

    @model_validator(mode="after")
    def require_distinct_runs(self) -> MetricInsightRunCreate:
        if self.baseline_metric_run_id == self.comparison_metric_run_id:
            raise ValueError("baseline and comparison runs must be distinct")
        return self


class MetricInsightRequiredContract(StrictModel):
    dataset_type: str
    contract_key: str
    version: str


class MetricInsightPercentageChangeContract(StrictModel):
    formula: str
    availability_rule: str
    zero_baseline: str
    negative_baseline: str
    missing_or_incompatible_values: str
    rounding: str


class MetricInsightComparisonDefinition(StrictModel):
    metric_id: str
    metric_version: str
    name: str
    unit: str
    formula: str
    definition_hash: str
    required_input_groups: list[str]
    required_dataset_types: list[str]
    required_contracts: list[MetricInsightRequiredContract]
    comparison_definition_version: str
    comparison_behavior: str
    percentage_change_contract: MetricInsightPercentageChangeContract
    limitations: list[str]


class MetricInsightUnavailableDefinition(StrictModel):
    metric_id: str
    reason: str


class MetricInsightQuestionStatus(StrictModel):
    status: Literal["unavailable"]
    reason: str


class MetricInsightCatalogResponse(StrictModel):
    catalog_version: str
    engine_version: str
    comparison_definition_version: str
    source_metric_catalog_version: str
    source_metric_catalog_status: Literal["implementation_draft"]
    decision_use: Literal["observational_only_no_recommendation"]
    framework_status: Literal["descriptive_comparison_only"]
    supported_comparisons: list[MetricInsightComparisonDefinition]
    unavailable_comparisons: list[MetricInsightUnavailableDefinition]
    owner_approved_question: MetricInsightQuestionStatus
    interpretation_status: Literal["unavailable"]
    causal_analysis_status: Literal["not_available"]
    direction_semantics: str
    period_convention: str


class MetricInsightRunResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    requested_by_user_id: UUID
    baseline_metric_run_id: UUID
    comparison_metric_run_id: UUID
    metric_id: str
    insight_catalog_version: str
    engine_version: str
    comparison_definition_version: str
    availability_status: Literal["available", "partial", "abstained"]
    input_hash: str
    result_hash: str
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]
    created_at: datetime


class MetricInsightReplayResponse(StrictModel):
    insight_run_id: UUID
    input_hash: str
    saved_result_hash: str
    replayed_result_hash: str
    matches_saved_result: Literal[True]
    result_snapshot: dict[str, object]
