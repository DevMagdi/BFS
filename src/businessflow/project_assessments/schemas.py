from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from businessflow.metrics.calculation import MAX_METRIC_WEEKS
from businessflow.schemas import StrictModel

MAX_WEEKLY_TEAM_HOURS = Decimal("1000000.00")
MAX_TOTAL_TEAM_HOURS = MAX_WEEKLY_TEAM_HOURS * MAX_METRIC_WEEKS
ProjectCode = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]*$",
    ),
]
ProjectName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Hours = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        le=MAX_WEEKLY_TEAM_HOURS,
        max_digits=12,
        decimal_places=2,
    ),
]
TotalHours = Annotated[
    Decimal,
    Field(
        ge=Decimal("0"),
        le=MAX_TOTAL_TEAM_HOURS,
        max_digits=12,
        decimal_places=2,
    ),
]


def _week_dates(start_week_date: date, week_count: int) -> tuple[date, ...]:
    try:
        start_week_date + timedelta(days=week_count * 7 - 1)
        return tuple(start_week_date + timedelta(days=7 * index) for index in range(week_count))
    except OverflowError as exc:
        raise ValueError("requested period is outside the supported calendar range") from exc


class WeeklyDemandInput(StrictModel):
    week_start_date: date
    hours: Hours

    @model_validator(mode="after")
    def monday_boundary(self) -> WeeklyDemandInput:
        if self.week_start_date.isoweekday() != 1:
            raise ValueError("weekly demand week_start_date must be a Monday")
        return self


class ProjectIntakeData(StrictModel):
    project_code: ProjectCode
    name: ProjectName
    start_week_date: date
    week_count: Annotated[int, Field(ge=1, le=MAX_METRIC_WEEKS)]
    total_demand_hours: TotalHours
    demand_unit: Literal["team_hours"] = "team_hours"
    weekly_demand: Annotated[list[WeeklyDemandInput], Field(max_length=MAX_METRIC_WEEKS)] | None = (
        None
    )
    demand_distribution_confirmed: bool = False
    notes: Annotated[str, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def validate_intake(self) -> ProjectIntakeData:
        self.project_code = self.project_code.upper()
        if self.start_week_date.isoweekday() != 1:
            raise ValueError("start_week_date must be a Monday")
        expected_weeks = _week_dates(self.start_week_date, self.week_count)
        if self.weekly_demand is not None:
            actual_weeks = [item.week_start_date for item in self.weekly_demand]
            if len(actual_weeks) != len(set(actual_weeks)):
                raise ValueError("weekly_demand must not contain duplicate weeks")
            if any(week not in expected_weeks for week in actual_weeks):
                raise ValueError("weekly_demand weeks must fall within the intake period")
        if self.demand_distribution_confirmed:
            if self.weekly_demand is None:
                raise ValueError("a confirmed demand distribution requires weekly_demand values")
            if {item.week_start_date for item in self.weekly_demand} != set(expected_weeks):
                raise ValueError("a confirmed demand distribution must cover every intake week")
            total = sum((item.hours for item in self.weekly_demand), Decimal("0.00"))
            if total != self.total_demand_hours:
                raise ValueError("confirmed weekly demand must sum to total_demand_hours")
        return self


class ProjectIntakeUpdate(StrictModel):
    expected_version: Annotated[int, Field(ge=1, le=1_000_000)]
    data: ProjectIntakeData


class CapacityAssessmentAssumptions(StrictModel):
    capacity_data_current_confirmed: bool = False
    recorded_commitments_complete_confirmed: bool = False


class CapacityAssessmentScenario(StrictModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
    weekly_demand: Annotated[
        list[WeeklyDemandInput], Field(min_length=1, max_length=MAX_METRIC_WEEKS)
    ]

    @model_validator(mode="after")
    def unique_scenario_weeks(self) -> CapacityAssessmentScenario:
        weeks = [item.week_start_date for item in self.weekly_demand]
        if len(weeks) != len(set(weeks)):
            raise ValueError("scenario weekly_demand must not contain duplicate weeks")
        return self


class CapacityAssessmentCreate(StrictModel):
    intake_version: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    assumptions: CapacityAssessmentAssumptions = Field(
        default_factory=CapacityAssessmentAssumptions
    )
    scenario: CapacityAssessmentScenario | None = None


class ProjectIntakeResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    created_by_user_id: UUID
    project_code: str
    current_version: int
    current_version_id: UUID
    current_version_hash: str
    data: ProjectIntakeData
    created_at: datetime
    updated_at: datetime


class ProjectIntakeVersionResponse(StrictModel):
    project_intake_id: UUID
    tenant_id: UUID
    company_id: UUID
    version: int
    version_id: UUID
    created_by_user_id: UUID
    content_hash: str
    data: ProjectIntakeData
    created_at: datetime


class ProjectIntakePage(StrictModel):
    items: list[ProjectIntakeResponse]
    total: int
    limit: int
    offset: int


class CapacityAssessmentSummaryResponse(StrictModel):
    id: UUID
    project_intake_id: UUID
    tenant_id: UUID
    company_id: UUID
    intake_version: int
    requested_by_user_id: UUID
    metric_run_id: UUID
    assessment_version: str
    metric_catalog_version: str
    metric_calculation_version: str
    start_week_date: date
    end_week_date: date
    week_count: int
    capacity_status: Literal["feasible", "feasible_with_conditions", "not_feasible", "incomplete"]
    business_feasibility_status: Literal["incomplete"]
    metric_input_hash: str
    metric_result_hash: str
    input_hash: str
    result_hash: str
    created_at: datetime


class CapacityAssessmentResponse(CapacityAssessmentSummaryResponse):
    input_snapshot: dict[str, object]
    result_snapshot: dict[str, object]


class CapacityAssessmentPage(StrictModel):
    items: list[CapacityAssessmentSummaryResponse]
    total: int
    limit: int
    offset: int
