from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, Field, StringConstraints, model_validator

from businessflow.schemas import ORMModel, StrictModel

UnitStatus = Literal["active", "inactive"]
EmploymentStatus = Literal["active", "on_leave", "inactive", "terminated"]
SkillProficiency = Annotated[int, Field(ge=1, le=5)]
CapacityStatus = Literal["confirmed", "estimated", "unknown"]
CapacitySource = Literal["manual", "calendar", "manager_adjustment"]
ProjectStatus = Literal["draft", "planned", "active", "on_hold", "completed", "cancelled"]
AssignmentStatus = Literal["proposed", "committed", "completed", "cancelled"]
Name120 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Name160 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]
Code64 = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]

Hours = Annotated[
    Decimal, Field(ge=Decimal("0"), le=Decimal("168"), max_digits=6, decimal_places=2)
]
PositiveWeeklyHours = Annotated[
    Decimal, Field(gt=Decimal("0"), le=Decimal("168"), max_digits=6, decimal_places=2)
]


def _validate_patch(model: BaseModel, *, nullable_fields: frozenset[str] = frozenset()) -> None:
    if not model.model_fields_set:
        raise ValueError("at least one field must be provided")
    for field in model.model_fields_set:
        if getattr(model, field) is None and field not in nullable_fields:
            raise ValueError(f"{field} may not be null")


class BusinessUnitCreate(StrictModel):
    name: Name120
    description: Annotated[str, Field(max_length=2000)] | None = None


class BusinessUnitUpdate(StrictModel):
    name: Name120 | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    status: UnitStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> BusinessUnitUpdate:
        _validate_patch(self, nullable_fields=frozenset({"description"}))
        return self


class BusinessUnitResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    name: str
    description: str | None
    status: UnitStatus
    created_at: datetime
    updated_at: datetime


class BusinessUnitPage(BaseModel):
    items: list[BusinessUnitResponse]
    total: int
    limit: int
    offset: int


class DepartmentCreate(StrictModel):
    name: Name120
    business_unit_id: UUID | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None


class DepartmentUpdate(StrictModel):
    name: Name120 | None = None
    business_unit_id: UUID | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    status: UnitStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> DepartmentUpdate:
        _validate_patch(self, nullable_fields=frozenset({"business_unit_id", "description"}))
        return self


class DepartmentResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    business_unit_id: UUID | None
    name: str
    description: str | None
    status: UnitStatus
    created_at: datetime
    updated_at: datetime


class DepartmentPage(BaseModel):
    items: list[DepartmentResponse]
    total: int
    limit: int
    offset: int


class EmployeeCreate(StrictModel):
    employee_code: Code64
    full_name: Name160
    job_title: Name120
    department_id: UUID | None = None
    employment_status: EmploymentStatus = "active"
    employment_start_date: date | None = None
    employment_end_date: date | None = None
    standard_weekly_capacity_hours: Annotated[
        Decimal,
        Field(gt=Decimal("0"), le=Decimal("168"), max_digits=6, decimal_places=2),
    ]

    @model_validator(mode="after")
    def validate_employment_dates(self) -> EmployeeCreate:
        if (
            self.employment_start_date is not None
            and self.employment_end_date is not None
            and self.employment_end_date < self.employment_start_date
        ):
            raise ValueError("employment end date must not precede employment start date")
        if self.employment_status == "terminated" and self.employment_end_date is None:
            raise ValueError("terminated employees require an employment end date")
        return self


class EmployeeUpdate(StrictModel):
    employee_code: Code64 | None = None
    full_name: Name160 | None = None
    job_title: Name120 | None = None
    department_id: UUID | None = None
    employment_status: EmploymentStatus | None = None
    employment_start_date: date | None = None
    employment_end_date: date | None = None
    standard_weekly_capacity_hours: (
        Annotated[
            Decimal,
            Field(gt=Decimal("0"), le=Decimal("168"), max_digits=6, decimal_places=2),
        ]
        | None
    ) = None

    @model_validator(mode="after")
    def validate_patch(self) -> EmployeeUpdate:
        _validate_patch(
            self,
            nullable_fields=frozenset(
                {"department_id", "employment_start_date", "employment_end_date"}
            ),
        )
        if (
            self.employment_start_date is not None
            and self.employment_end_date is not None
            and self.employment_end_date < self.employment_start_date
        ):
            raise ValueError("employment end date must not precede employment start date")
        if (
            self.employment_status == "terminated"
            and "employment_end_date" in self.model_fields_set
            and self.employment_end_date is None
        ):
            raise ValueError("terminated employees require an employment end date")
        return self


class EmployeeResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    employee_code: str
    full_name: str
    job_title: str
    department_id: UUID | None
    business_unit_id: UUID | None
    employment_status: EmploymentStatus
    employment_start_date: date | None
    employment_end_date: date | None
    standard_weekly_capacity_hours: Decimal
    created_at: datetime
    updated_at: datetime


class EmployeePage(BaseModel):
    items: list[EmployeeResponse]
    total: int
    limit: int
    offset: int


class SkillCreate(StrictModel):
    name: Name120
    description: Annotated[str, Field(max_length=2000)] | None = None


class SkillUpdate(StrictModel):
    name: Name120 | None = None
    description: Annotated[str, Field(max_length=2000)] | None = None
    status: UnitStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> SkillUpdate:
        _validate_patch(self, nullable_fields=frozenset({"description"}))
        return self


class SkillResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    name: str
    description: str | None
    status: UnitStatus
    created_at: datetime
    updated_at: datetime


class SkillPage(BaseModel):
    items: list[SkillResponse]
    total: int
    limit: int
    offset: int


class EmployeeSkillCreate(StrictModel):
    skill_id: UUID
    proficiency: SkillProficiency


class EmployeeSkillUpdate(StrictModel):
    proficiency: SkillProficiency | None = None
    status: UnitStatus | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> EmployeeSkillUpdate:
        _validate_patch(self)
        return self


class EmployeeSkillResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    employee_id: UUID
    skill_id: UUID
    skill_name: str
    proficiency: SkillProficiency
    proficiency_label: str
    status: UnitStatus
    created_at: datetime
    updated_at: datetime


class EmployeeSkillPage(BaseModel):
    items: list[EmployeeSkillResponse]
    total: int
    limit: int
    offset: int


class WeeklyCapacityCreate(StrictModel):
    availability_status: CapacityStatus
    scheduled_hours: Hours | None = None
    non_project_hours: Hours | None = None
    unavailable_hours: Hours | None = None
    source: CapacitySource = "manual"
    reason: Annotated[str, Field(max_length=500)] | None = None

    @model_validator(mode="after")
    def validate_capacity(self) -> WeeklyCapacityCreate:
        values = (self.scheduled_hours, self.non_project_hours, self.unavailable_hours)
        if self.availability_status == "unknown":
            if any(value is not None for value in values):
                raise ValueError("unknown availability must not include capacity hours")
        else:
            if any(value is None for value in values):
                raise ValueError("confirmed or estimated availability requires all capacity hours")
            assert self.scheduled_hours is not None
            assert self.non_project_hours is not None
            assert self.unavailable_hours is not None
            if self.non_project_hours + self.unavailable_hours > self.scheduled_hours:
                raise ValueError("non-project and unavailable hours cannot exceed scheduled hours")
        if self.source == "manager_adjustment" and (self.reason is None or not self.reason.strip()):
            raise ValueError("manager adjustments require a reason")
        return self


class WeeklyCapacityResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    employee_id: UUID
    week_start_date: date
    version: int
    supersedes_version: int | None
    availability_status: CapacityStatus
    scheduled_hours: Decimal | None
    non_project_hours: Decimal | None
    unavailable_hours: Decimal | None
    available_hours: Decimal | None
    source: CapacitySource
    reason: str | None
    created_by_user_id: UUID
    created_at: datetime


class WeeklyCapacityPage(BaseModel):
    items: list[WeeklyCapacityResponse]
    total: int
    limit: int
    offset: int


class ProjectCreate(StrictModel):
    project_code: Code64
    name: Name160
    description: Annotated[str, Field(max_length=4000)] | None = None
    status: Literal["draft", "planned"] = "draft"
    start_date: date | None = None
    planned_end_date: date | None = None
    project_manager_employee_id: UUID | None = None

    @model_validator(mode="after")
    def validate_project_dates(self) -> ProjectCreate:
        if (
            self.start_date is not None
            and self.planned_end_date is not None
            and self.planned_end_date < self.start_date
        ):
            raise ValueError("planned end date must not precede project start date")
        return self


class ProjectUpdate(StrictModel):
    project_code: Code64 | None = None
    name: Name160 | None = None
    description: Annotated[str, Field(max_length=4000)] | None = None
    status: ProjectStatus | None = None
    start_date: date | None = None
    planned_end_date: date | None = None
    actual_end_date: date | None = None
    project_manager_employee_id: UUID | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> ProjectUpdate:
        _validate_patch(
            self,
            nullable_fields=frozenset(
                {
                    "description",
                    "start_date",
                    "planned_end_date",
                    "actual_end_date",
                    "project_manager_employee_id",
                }
            ),
        )
        if (
            self.start_date is not None
            and self.planned_end_date is not None
            and self.planned_end_date < self.start_date
        ):
            raise ValueError("planned end date must not precede project start date")
        if (
            self.start_date is not None
            and self.actual_end_date is not None
            and self.actual_end_date < self.start_date
        ):
            raise ValueError("actual end date must not precede project start date")
        return self


class ProjectResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    project_code: str
    name: str
    description: str | None
    status: ProjectStatus
    start_date: date | None
    planned_end_date: date | None
    actual_end_date: date | None
    project_manager_employee_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ProjectPage(BaseModel):
    items: list[ProjectResponse]
    total: int
    limit: int
    offset: int


class ResourceAssignmentCreate(StrictModel):
    employee_id: UUID
    start_date: date
    end_date: date
    planned_hours_per_week: PositiveWeeklyHours
    status: Literal["proposed", "committed"] = "proposed"
    notes: Annotated[str, Field(max_length=2000)] | None = None
    source: Literal["manual", "manager_adjustment"] = "manual"

    @model_validator(mode="after")
    def validate_period(self) -> ResourceAssignmentCreate:
        if self.end_date < self.start_date:
            raise ValueError("assignment end date must not precede start date")
        if self.source == "manager_adjustment" and (self.notes is None or not self.notes.strip()):
            raise ValueError("manager adjustments require an explanatory note")
        return self


class ResourceAssignmentUpdate(StrictModel):
    start_date: date | None = None
    end_date: date | None = None
    planned_hours_per_week: PositiveWeeklyHours | None = None
    status: AssignmentStatus | None = None
    notes: Annotated[str, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> ResourceAssignmentUpdate:
        _validate_patch(self, nullable_fields=frozenset({"notes"}))
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("assignment end date must not precede start date")
        return self


class ResourceAssignmentResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    project_id: UUID
    employee_id: UUID
    start_date: date
    end_date: date
    planned_hours_per_week: Decimal
    status: AssignmentStatus
    notes: str | None
    source: str
    created_at: datetime
    updated_at: datetime


class ResourceAssignmentPage(BaseModel):
    items: list[ResourceAssignmentResponse]
    total: int
    limit: int
    offset: int


class ResourceAssignmentHistoryItem(BaseModel):
    id: UUID
    event_type: str
    actor_user_id: UUID | None
    created_at: datetime
    details: dict[str, object]


class ResourceAssignmentHistoryPage(BaseModel):
    items: list[ResourceAssignmentHistoryItem]
    total: int
    limit: int
    offset: int


class CompanySummary(BaseModel):
    id: UUID
    tenant_id: UUID
    legal_name: str
    base_currency: str
    timezone: str


class OrganizationSummary(BaseModel):
    business_units: list[BusinessUnitResponse]
    departments: list[DepartmentResponse]
    business_units_truncated: bool
    departments_truncated: bool


class EmployeeCounts(BaseModel):
    total_count: int
    active_count: int
    on_leave_count: int
    inactive_count: int
    terminated_count: int


class SkillCoverageSummary(BaseModel):
    active_catalog_count: int
    catalog: list[SkillResponse]
    catalog_truncated: bool
    employees_with_active_skills: int
    employees_without_active_skills: int
    coverage_complete: bool


class WeeklyCapacityCoverage(BaseModel):
    week_start_date: date
    week_end_date: date
    active_employee_count: int
    records_count: int
    confirmed_count: int
    estimated_count: int
    unknown_count: int
    missing_count: int
    confirmed_available_hours_total: Decimal | None
    estimated_available_hours_total: Decimal | None
    coverage_complete: bool


class ProjectSummaryItem(BaseModel):
    id: UUID
    project_code: str
    name: str
    status: ProjectStatus
    start_date: date | None
    planned_end_date: date | None


class ProjectContextSummary(BaseModel):
    total_count: int
    draft_count: int
    planned_count: int
    active_count: int
    on_hold_count: int
    completed_count: int
    cancelled_count: int
    projects: list[ProjectSummaryItem]
    truncated: bool


class AssignmentSummaryItem(BaseModel):
    id: UUID
    project_id: UUID
    project_code: str
    employee_id: UUID
    employee_code: str
    start_date: date
    end_date: date
    planned_hours_per_week: Decimal
    status: AssignmentStatus


class AssignmentContextSummary(BaseModel):
    week_start_date: date
    week_end_date: date
    committed_count: int
    proposed_count: int
    known_committed_hours_per_week_total: Decimal | None
    coverage_status: Literal["recorded", "not_recorded"]
    assignments: list[AssignmentSummaryItem]
    truncated: bool


class CompanyContextSummary(BaseModel):
    company: CompanySummary
    week_start_date: date
    organization: OrganizationSummary
    employees: EmployeeCounts
    skills: SkillCoverageSummary
    weekly_capacity: WeeklyCapacityCoverage
    projects: ProjectContextSummary
    assignments: AssignmentContextSummary
    missing_or_incomplete_indicators: list[str]
