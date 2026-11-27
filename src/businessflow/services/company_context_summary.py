from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import cast
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from businessflow.company_context.domain import is_monday, monday_for
from businessflow.company_context.models import (
    BusinessUnit,
    Department,
    Employee,
    EmployeeSkill,
    EmployeeWeeklyCapacity,
    Project,
    ResourceAssignment,
    Skill,
)
from businessflow.company_context.schemas import (
    AssignmentContextSummary,
    AssignmentSummaryItem,
    CompanyContextSummary,
    CompanySummary,
    EmployeeCounts,
    OrganizationSummary,
    ProjectContextSummary,
    ProjectSummaryItem,
    SkillCoverageSummary,
    WeeklyCapacityCoverage,
)
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.services.company_workforce import available_hours

SUMMARY_ITEMS_LIMIT = 100


def _week_start_in_company_timezone(timezone_name: str) -> date:
    local_today = datetime.now(ZoneInfo(timezone_name)).date()
    return monday_for(local_today)


def company_context_summary(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    week_start_date: date | None,
) -> CompanyContextSummary:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    require_company_context_access(session, user, tenant_id, "workforce:read")
    requested_week = week_start_date or _week_start_in_company_timezone(access.company.timezone)
    if not is_monday(requested_week):
        raise ApiError(422, "invalid_week_start", "Weekly summaries must start on a Monday.")
    week_end = requested_week + timedelta(days=6)
    company_id = access.company.id

    business_units = list(
        session.scalars(
            select(BusinessUnit)
            .where(BusinessUnit.tenant_id == tenant_id, BusinessUnit.company_id == company_id)
            .order_by(BusinessUnit.name_key, BusinessUnit.id)
            .limit(SUMMARY_ITEMS_LIMIT + 1)
        )
    )
    departments = list(
        session.scalars(
            select(Department)
            .where(Department.tenant_id == tenant_id, Department.company_id == company_id)
            .order_by(Department.name_key, Department.id)
            .limit(SUMMARY_ITEMS_LIMIT + 1)
        )
    )

    employee_status_counts = Counter(
        {
            status: count
            for status, count in session.execute(
                select(Employee.employment_status, func.count())
                .where(Employee.tenant_id == tenant_id, Employee.company_id == company_id)
                .group_by(Employee.employment_status)
            ).all()
        }
    )
    total_employees = sum(employee_status_counts.values())
    active_employees = employee_status_counts["active"]

    active_skill_count = (
        session.scalar(
            select(func.count())
            .select_from(Skill)
            .where(Skill.tenant_id == tenant_id, Skill.status == "active")
        )
        or 0
    )
    skills = list(
        session.scalars(
            select(Skill)
            .where(Skill.tenant_id == tenant_id, Skill.status == "active")
            .order_by(Skill.name_key, Skill.id)
            .limit(SUMMARY_ITEMS_LIMIT + 1)
        )
    )
    employees_with_skills = (
        session.scalar(
            select(func.count(func.distinct(EmployeeSkill.employee_id)))
            .select_from(EmployeeSkill)
            .join(
                Employee,
                and_(
                    Employee.id == EmployeeSkill.employee_id,
                    Employee.tenant_id == EmployeeSkill.tenant_id,
                    Employee.company_id == EmployeeSkill.company_id,
                ),
            )
            .join(
                Skill,
                and_(
                    Skill.id == EmployeeSkill.skill_id, Skill.tenant_id == EmployeeSkill.tenant_id
                ),
            )
            .where(
                EmployeeSkill.tenant_id == tenant_id,
                EmployeeSkill.company_id == company_id,
                EmployeeSkill.status == "active",
                Skill.status == "active",
                Employee.employment_status == "active",
            )
        )
        or 0
    )
    employees_without_skills = max(0, active_employees - employees_with_skills)

    latest_capacity = (
        select(
            EmployeeWeeklyCapacity.employee_id.label("employee_id"),
            func.max(EmployeeWeeklyCapacity.version).label("version"),
        )
        .join(
            Employee,
            and_(
                Employee.id == EmployeeWeeklyCapacity.employee_id,
                Employee.tenant_id == EmployeeWeeklyCapacity.tenant_id,
                Employee.company_id == EmployeeWeeklyCapacity.company_id,
            ),
        )
        .where(
            EmployeeWeeklyCapacity.tenant_id == tenant_id,
            EmployeeWeeklyCapacity.company_id == company_id,
            EmployeeWeeklyCapacity.week_start_date == requested_week,
            Employee.employment_status == "active",
        )
        .group_by(EmployeeWeeklyCapacity.employee_id)
        .subquery()
    )
    capacity_rows = list(
        session.scalars(
            select(EmployeeWeeklyCapacity).join(
                latest_capacity,
                and_(
                    EmployeeWeeklyCapacity.employee_id == latest_capacity.c.employee_id,
                    EmployeeWeeklyCapacity.version == latest_capacity.c.version,
                    EmployeeWeeklyCapacity.tenant_id == tenant_id,
                    EmployeeWeeklyCapacity.company_id == company_id,
                    EmployeeWeeklyCapacity.week_start_date == requested_week,
                ),
            )
        )
    )
    capacity_counts = Counter(row.availability_status for row in capacity_rows)
    confirmed_rows = [row for row in capacity_rows if row.availability_status == "confirmed"]
    estimated_rows = [row for row in capacity_rows if row.availability_status == "estimated"]
    confirmed_hours = (
        sum((cast(Decimal, available_hours(row)) for row in confirmed_rows), Decimal("0"))
        if confirmed_rows
        else None
    )
    estimated_hours = (
        sum((cast(Decimal, available_hours(row)) for row in estimated_rows), Decimal("0"))
        if estimated_rows
        else None
    )
    missing_capacity = max(0, active_employees - len(capacity_rows))
    capacity_complete = (
        active_employees > 0
        and missing_capacity == 0
        and capacity_counts["unknown"] == 0
        and capacity_counts["estimated"] == 0
    )

    project_status_counts = Counter(
        {
            status: count
            for status, count in session.execute(
                select(Project.status, func.count())
                .where(Project.tenant_id == tenant_id, Project.company_id == company_id)
                .group_by(Project.status)
            ).all()
        }
    )
    projects_total = sum(project_status_counts.values())
    project_rows = session.execute(
        select(
            Project.id,
            Project.project_code,
            Project.name,
            Project.status,
            Project.start_date,
            Project.planned_end_date,
        )
        .where(
            Project.tenant_id == tenant_id,
            Project.company_id == company_id,
            Project.status.in_(("draft", "planned", "active", "on_hold")),
        )
        .order_by(Project.start_date.nulls_last(), Project.project_code_key, Project.id)
        .limit(SUMMARY_ITEMS_LIMIT + 1)
    ).all()

    assignment_filters = [
        ResourceAssignment.tenant_id == tenant_id,
        ResourceAssignment.company_id == company_id,
        ResourceAssignment.start_date <= week_end,
        ResourceAssignment.end_date >= requested_week,
        ResourceAssignment.status.in_(("proposed", "committed")),
    ]
    assignment_counts = Counter(
        {
            status: count
            for status, count in session.execute(
                select(ResourceAssignment.status, func.count())
                .where(*assignment_filters)
                .group_by(ResourceAssignment.status)
            ).all()
        }
    )
    committed_hours = session.scalar(
        select(func.sum(ResourceAssignment.planned_hours_per_week)).where(
            *assignment_filters,
            ResourceAssignment.status == "committed",
        )
    )
    assignment_total = sum(assignment_counts.values())
    assignment_rows = session.execute(
        select(
            ResourceAssignment.id,
            ResourceAssignment.project_id,
            Project.project_code,
            ResourceAssignment.employee_id,
            Employee.employee_code,
            ResourceAssignment.start_date,
            ResourceAssignment.end_date,
            ResourceAssignment.planned_hours_per_week,
            ResourceAssignment.status,
        )
        .join(
            Project,
            and_(
                Project.id == ResourceAssignment.project_id,
                Project.tenant_id == ResourceAssignment.tenant_id,
                Project.company_id == ResourceAssignment.company_id,
            ),
        )
        .join(
            Employee,
            and_(
                Employee.id == ResourceAssignment.employee_id,
                Employee.tenant_id == ResourceAssignment.tenant_id,
                Employee.company_id == ResourceAssignment.company_id,
            ),
        )
        .where(*assignment_filters)
        .order_by(ResourceAssignment.start_date, ResourceAssignment.id)
        .limit(SUMMARY_ITEMS_LIMIT + 1)
    ).all()

    indicators: list[str] = []
    active_units = (
        session.scalar(
            select(func.count())
            .select_from(BusinessUnit)
            .where(
                BusinessUnit.tenant_id == tenant_id,
                BusinessUnit.company_id == company_id,
                BusinessUnit.status == "active",
            )
        )
        or 0
    )
    active_departments = (
        session.scalar(
            select(func.count())
            .select_from(Department)
            .where(
                Department.tenant_id == tenant_id,
                Department.company_id == company_id,
                Department.status == "active",
            )
        )
        or 0
    )
    if active_units == 0:
        indicators.append("no_active_business_units_recorded")
    if active_departments == 0:
        indicators.append("no_active_departments_recorded")
    if active_employees == 0:
        indicators.append("no_active_employees_recorded")
    if active_skill_count == 0:
        indicators.append("no_active_skills_recorded")
    if employees_without_skills:
        indicators.append("active_employees_missing_active_skills")
    if missing_capacity:
        indicators.append("weekly_capacity_missing_for_some_active_employees")
    if capacity_counts["unknown"]:
        indicators.append("weekly_capacity_explicitly_unknown_for_some_active_employees")
    if capacity_counts["estimated"]:
        indicators.append("weekly_capacity_contains_estimates")
    if project_status_counts["active"] + project_status_counts["planned"] == 0:
        indicators.append("no_active_or_planned_projects_recorded")
    if assignment_total == 0 and (
        project_status_counts["active"] + project_status_counts["planned"] > 0
    ):
        indicators.append("no_current_resource_assignments_recorded")

    return CompanyContextSummary(
        company=CompanySummary(
            id=access.company.id,
            tenant_id=tenant_id,
            legal_name=access.company.legal_name,
            base_currency=access.company.base_currency,
            timezone=access.company.timezone,
        ),
        week_start_date=requested_week,
        organization=OrganizationSummary(
            business_units=business_units[:SUMMARY_ITEMS_LIMIT],
            departments=departments[:SUMMARY_ITEMS_LIMIT],
            business_units_truncated=len(business_units) > SUMMARY_ITEMS_LIMIT,
            departments_truncated=len(departments) > SUMMARY_ITEMS_LIMIT,
        ),
        employees=EmployeeCounts(
            total_count=total_employees,
            active_count=active_employees,
            on_leave_count=employee_status_counts["on_leave"],
            inactive_count=employee_status_counts["inactive"],
            terminated_count=employee_status_counts["terminated"],
        ),
        skills=SkillCoverageSummary(
            active_catalog_count=active_skill_count,
            catalog=skills[:SUMMARY_ITEMS_LIMIT],
            catalog_truncated=len(skills) > SUMMARY_ITEMS_LIMIT,
            employees_with_active_skills=employees_with_skills,
            employees_without_active_skills=employees_without_skills,
            coverage_complete=(active_employees > 0 and employees_without_skills == 0),
        ),
        weekly_capacity=WeeklyCapacityCoverage(
            week_start_date=requested_week,
            week_end_date=week_end,
            active_employee_count=active_employees,
            records_count=len(capacity_rows),
            confirmed_count=capacity_counts["confirmed"],
            estimated_count=capacity_counts["estimated"],
            unknown_count=capacity_counts["unknown"],
            missing_count=missing_capacity,
            confirmed_available_hours_total=confirmed_hours,
            estimated_available_hours_total=estimated_hours,
            coverage_complete=capacity_complete,
        ),
        projects=ProjectContextSummary(
            total_count=projects_total,
            draft_count=project_status_counts["draft"],
            planned_count=project_status_counts["planned"],
            active_count=project_status_counts["active"],
            on_hold_count=project_status_counts["on_hold"],
            completed_count=project_status_counts["completed"],
            cancelled_count=project_status_counts["cancelled"],
            projects=[
                ProjectSummaryItem(
                    id=row.id,
                    project_code=row.project_code,
                    name=row.name,
                    status=row.status,
                    start_date=row.start_date,
                    planned_end_date=row.planned_end_date,
                )
                for row in project_rows[:SUMMARY_ITEMS_LIMIT]
            ],
            truncated=len(project_rows) > SUMMARY_ITEMS_LIMIT,
        ),
        assignments=AssignmentContextSummary(
            week_start_date=requested_week,
            week_end_date=week_end,
            committed_count=assignment_counts["committed"],
            proposed_count=assignment_counts["proposed"],
            known_committed_hours_per_week_total=(
                cast(Decimal, committed_hours) if assignment_counts["committed"] else None
            ),
            coverage_status="recorded" if assignment_total else "not_recorded",
            assignments=[
                AssignmentSummaryItem(
                    id=row.id,
                    project_id=row.project_id,
                    project_code=row.project_code,
                    employee_id=row.employee_id,
                    employee_code=row.employee_code,
                    start_date=row.start_date,
                    end_date=row.end_date,
                    planned_hours_per_week=row.planned_hours_per_week,
                    status=row.status,
                )
                for row in assignment_rows[:SUMMARY_ITEMS_LIMIT]
            ],
            truncated=len(assignment_rows) > SUMMARY_ITEMS_LIMIT,
        ),
        missing_or_incomplete_indicators=indicators,
    )
