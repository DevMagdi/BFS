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
