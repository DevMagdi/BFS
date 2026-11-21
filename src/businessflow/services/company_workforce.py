from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_context.domain import is_monday, monday_for
from businessflow.company_context.models import (
    Department,
    Employee,
    EmployeeSkill,
    EmployeeWeeklyCapacity,
    Skill,
)
from businessflow.company_context.schemas import (
    EmployeeCreate,
    EmployeeSkillCreate,
    EmployeeSkillUpdate,
    EmployeeUpdate,
    SkillCreate,
    SkillUpdate,
    WeeklyCapacityCreate,
)
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access


@dataclass(frozen=True, slots=True)
class EmployeeView:
    employee: Employee
    business_unit_id: UUID | None


@dataclass(frozen=True, slots=True)
class EmployeeSkillView:
    employee_skill: EmployeeSkill
    skill_name: str


def _employee_query(tenant_id: UUID, company_id: UUID):
    return (
        select(Employee, Department.business_unit_id)
        .outerjoin(
            Department,
            and_(
                Department.id == Employee.department_id,
                Department.tenant_id == Employee.tenant_id,
                Department.company_id == Employee.company_id,
            ),
        )
        .where(Employee.tenant_id == tenant_id, Employee.company_id == company_id)
    )


def _get_employee(
    session: Session, tenant_id: UUID, company_id: UUID, employee_id: UUID, *, lock: bool = False
) -> Employee:
    statement = select(Employee).where(
        Employee.id == employee_id,
        Employee.tenant_id == tenant_id,
        Employee.company_id == company_id,
    )
    if lock:
        statement = statement.with_for_update()
    employee = session.scalar(statement)
    if employee is None:
        raise ApiError(404, "employee_not_found", "Employee was not found.")
    return employee


def _get_employee_view(
    session: Session, tenant_id: UUID, company_id: UUID, employee_id: UUID
) -> EmployeeView:
    row = session.execute(
        _employee_query(tenant_id, company_id).where(Employee.id == employee_id)
    ).one_or_none()
    if row is None:
        raise ApiError(404, "employee_not_found", "Employee was not found.")
    return EmployeeView(employee=row[0], business_unit_id=row[1])


def _validate_department(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    department_id: UUID | None,
) -> None:
    if department_id is None:
        return
    department = session.scalar(
        select(Department).where(
            Department.id == department_id,
            Department.tenant_id == tenant_id,
            Department.company_id == company_id,
            Department.status == "active",
        )
    )
    if department is None:
        raise ApiError(404, "department_not_found", "Department was not found.")


def list_employees(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    status: str | None,
    department_id: UUID | None,
    business_unit_id: UUID | None,
    skill_id: UUID | None,
    limit: int,
    offset: int,
    sort_by: str,
    descending: bool,
) -> tuple[list[EmployeeView], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    statement = _employee_query(tenant_id, access.company.id)
    if status is not None:
        statement = statement.where(Employee.employment_status == status)
    if department_id is not None:
        statement = statement.where(Employee.department_id == department_id)
    if business_unit_id is not None:
        statement = statement.where(Department.business_unit_id == business_unit_id)
    if skill_id is not None:
        has_skill = select(EmployeeSkill.id).where(
            EmployeeSkill.tenant_id == tenant_id,
            EmployeeSkill.company_id == access.company.id,
            EmployeeSkill.employee_id == Employee.id,
            EmployeeSkill.skill_id == skill_id,
            EmployeeSkill.status == "active",
        )
        statement = statement.where(has_skill.exists())

    count_statement = select(func.count()).select_from(statement.order_by(None).subquery())
    total = session.scalar(count_statement) or 0
    order_column = {
        "employee_code": Employee.employee_code_key,
        "full_name": Employee.full_name,
        "created_at": Employee.created_at,
        "employment_status": Employee.employment_status,
    }[sort_by]
    ordering = order_column.desc() if descending else order_column.asc()
    rows = session.execute(
        statement.order_by(ordering, Employee.id).limit(limit).offset(offset)
    ).all()
    return [EmployeeView(employee=row[0], business_unit_id=row[1]) for row in rows], total


def get_employee(
    session: Session, user: UserAccount, tenant_id: UUID, employee_id: UUID
) -> EmployeeView:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    return _get_employee_view(session, tenant_id, access.company.id, employee_id)


def create_employee(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: EmployeeCreate,
    request_id: UUID,
) -> EmployeeView:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _validate_department(session, tenant_id, access.company.id, body.department_id)
    employee = Employee(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        employee_code=body.employee_code,
        full_name=body.full_name,
        job_title=body.job_title,
        department_id=body.department_id,
        employment_status=body.employment_status,
        employment_start_date=body.employment_start_date,
        employment_end_date=body.employment_end_date,
        standard_weekly_capacity_hours=body.standard_weekly_capacity_hours,
    )
    session.add(employee)
    session.flush()
    record_event(
        session,
        "employee.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="employee",
        target_id=employee.id,
        request_id=request_id,
        details={
            "employee_code": employee.employee_code,
            "employment_status": employee.employment_status,
        },
    )
    return _get_employee_view(session, tenant_id, access.company.id, employee.id)


def update_employee(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    changes: EmployeeUpdate,
    request_id: UUID,
) -> EmployeeView:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    employee = _get_employee(session, tenant_id, access.company.id, employee_id, lock=True)
    patch = changes.model_dump(exclude_unset=True)
    if "department_id" in patch:
        _validate_department(session, tenant_id, access.company.id, patch["department_id"])
    effective_start = patch.get("employment_start_date", employee.employment_start_date)
    effective_end = patch.get("employment_end_date", employee.employment_end_date)
    effective_status = patch.get("employment_status", employee.employment_status)
    effective_department_id = patch.get("department_id", employee.department_id)
    if effective_status in {"active", "on_leave"}:
        _validate_department(session, tenant_id, access.company.id, effective_department_id)
    if (
        effective_start is not None
        and effective_end is not None
        and effective_end < effective_start
    ):
        raise ApiError(422, "invalid_employment_dates", "Employment dates are invalid.")
    if effective_status == "terminated" and effective_end is None:
        raise ApiError(
            422, "termination_date_required", "Terminated employees require an end date."
        )

    changed = [field for field, value in patch.items() if getattr(employee, field) != value]
    if changed:
        previous_status = employee.employment_status
        previous_capacity = employee.standard_weekly_capacity_hours
        for field, value in patch.items():
            setattr(employee, field, value)
        record_event(
            session,
            "employee.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="employee",
            target_id=employee.id,
            request_id=request_id,
            details={
                "changed_fields": changed,
                "previous_employment_status": previous_status,
                "employment_status": employee.employment_status,
                "previous_standard_capacity_hours": str(previous_capacity),
                "standard_capacity_hours": str(employee.standard_weekly_capacity_hours),
            },
        )
        session.flush()
    return _get_employee_view(session, tenant_id, access.company.id, employee.id)


def deactivate_employee(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    request_id: UUID,
) -> None:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    employee = _get_employee(session, tenant_id, access.company.id, employee_id, lock=True)
    if employee.employment_status in {"active", "on_leave"}:
        previous_status = employee.employment_status
        employee.employment_status = "inactive"
        record_event(
            session,
            "employee.deactivated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="employee",
            target_id=employee.id,
            request_id=request_id,
            details={
                "previous_employment_status": previous_status,
                "employment_status": "inactive",
            },
        )
        session.flush()


def list_skills(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Skill], int]:
    require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [Skill.tenant_id == tenant_id]
    if status is not None:
        filters.append(Skill.status == status)
    total = session.scalar(select(func.count()).select_from(Skill).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(Skill)
            .where(*filters)
            .order_by(Skill.name_key, Skill.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_skill(session: Session, user: UserAccount, tenant_id: UUID, skill_id: UUID) -> Skill:
    require_company_context_access(session, user, tenant_id, "company_context:read")
    skill = session.scalar(select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id))
    if skill is None:
        raise ApiError(404, "skill_not_found", "Skill was not found.")
    return skill


def create_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: SkillCreate,
    request_id: UUID,
) -> Skill:
    require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    skill = Skill(
        id=uuid4(),
        tenant_id=tenant_id,
        name=body.name,
        description=body.description,
        status="active",
    )
    session.add(skill)
    session.flush()
    record_event(
        session,
        "skill.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="skill",
        target_id=skill.id,
        request_id=request_id,
        details={"name": skill.name},
    )
    return skill


def update_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    skill_id: UUID,
    changes: SkillUpdate,
    request_id: UUID,
) -> Skill:
    require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    skill = session.scalar(
        select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id).with_for_update()
    )
    if skill is None:
        raise ApiError(404, "skill_not_found", "Skill was not found.")
    patch = changes.model_dump(exclude_unset=True)
    changed = [field for field, value in patch.items() if getattr(skill, field) != value]
    if changed:
        previous_status = skill.status
        for field, value in patch.items():
            setattr(skill, field, value)
        record_event(
            session,
            "skill.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="skill",
            target_id=skill.id,
            request_id=request_id,
            details={
                "changed_fields": changed,
                "previous_status": previous_status,
                "status": skill.status,
            },
        )
        session.flush()
    return skill


def deactivate_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    skill_id: UUID,
    request_id: UUID,
) -> None:
    require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    skill = session.scalar(
        select(Skill).where(Skill.id == skill_id, Skill.tenant_id == tenant_id).with_for_update()
    )
    if skill is None:
        raise ApiError(404, "skill_not_found", "Skill was not found.")
    if skill.status != "inactive":
        skill.status = "inactive"
        record_event(
            session,
            "skill.deactivated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="skill",
            target_id=skill.id,
            request_id=request_id,
            details={"status": "inactive"},
        )
        session.flush()


def _employee_skill_query(tenant_id: UUID, company_id: UUID, employee_id: UUID):
    return (
        select(EmployeeSkill, Skill.name)
        .join(
            Skill,
            and_(Skill.id == EmployeeSkill.skill_id, Skill.tenant_id == EmployeeSkill.tenant_id),
        )
        .where(
            EmployeeSkill.tenant_id == tenant_id,
            EmployeeSkill.company_id == company_id,
            EmployeeSkill.employee_id == employee_id,
        )
    )


def list_employee_skills(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[EmployeeSkillView], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    _get_employee(session, tenant_id, access.company.id, employee_id)
    statement = _employee_skill_query(tenant_id, access.company.id, employee_id)
    if status is not None:
        statement = statement.where(EmployeeSkill.status == status)
    total = (
        session.scalar(select(func.count()).select_from(statement.order_by(None).subquery())) or 0
    )
    rows = session.execute(
        statement.order_by(Skill.name_key, EmployeeSkill.id).limit(limit).offset(offset)
    ).all()
    return [EmployeeSkillView(employee_skill=row[0], skill_name=row[1]) for row in rows], total


def _get_employee_skill(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    employee_id: UUID,
    skill_id: UUID,
    *,
    lock: bool = False,
) -> EmployeeSkillView:
    statement = _employee_skill_query(tenant_id, company_id, employee_id).where(
        EmployeeSkill.skill_id == skill_id
    )
    if lock:
        statement = statement.with_for_update(of=EmployeeSkill)
    row = session.execute(statement).one_or_none()
    if row is None:
        raise ApiError(404, "employee_skill_not_found", "Employee skill was not found.")
    return EmployeeSkillView(employee_skill=row[0], skill_name=row[1])


def create_employee_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    body: EmployeeSkillCreate,
    request_id: UUID,
) -> EmployeeSkillView:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _get_employee(session, tenant_id, access.company.id, employee_id)
    skill = session.scalar(
        select(Skill).where(
            Skill.id == body.skill_id,
            Skill.tenant_id == tenant_id,
            Skill.status == "active",
        )
    )
    if skill is None:
        raise ApiError(404, "skill_not_found", "Skill was not found.")
    existing = session.scalar(
        select(EmployeeSkill.id).where(
            EmployeeSkill.tenant_id == tenant_id,
            EmployeeSkill.company_id == access.company.id,
            EmployeeSkill.employee_id == employee_id,
            EmployeeSkill.skill_id == body.skill_id,
        )
    )
    if existing is not None:
        raise ApiError(
            409,
            "employee_skill_exists",
            "This employee already has a record for that skill; update or reactivate it instead.",
        )
    relation = EmployeeSkill(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        employee_id=employee_id,
        skill_id=skill.id,
        proficiency=body.proficiency,
        status="active",
    )
    session.add(relation)
    session.flush()
    record_event(
        session,
        "employee_skill.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="employee_skill",
        target_id=relation.id,
        request_id=request_id,
        details={"employee_id": employee_id, "skill_id": skill.id, "proficiency": body.proficiency},
    )
    return EmployeeSkillView(employee_skill=relation, skill_name=skill.name)


def update_employee_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    skill_id: UUID,
    changes: EmployeeSkillUpdate,
    request_id: UUID,
) -> EmployeeSkillView:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    row = _get_employee_skill(
        session, tenant_id, access.company.id, employee_id, skill_id, lock=True
    )
    relation = row.employee_skill
    patch = changes.model_dump(exclude_unset=True)
    if patch.get("status") == "active" and relation.status != "active":
        skill = session.scalar(
            select(Skill).where(
                Skill.id == relation.skill_id,
                Skill.tenant_id == tenant_id,
                Skill.status == "active",
            )
        )
        if skill is None:
            raise ApiError(
                409, "skill_inactive", "Reactivate the skill before restoring this employee skill."
            )
    changed = [field for field, value in patch.items() if getattr(relation, field) != value]
    if changed:
        previous_proficiency, previous_status = relation.proficiency, relation.status
        for field, value in patch.items():
            setattr(relation, field, value)
        record_event(
            session,
            "employee_skill.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="employee_skill",
            target_id=relation.id,
            request_id=request_id,
            details={
                "employee_id": employee_id,
                "skill_id": skill_id,
                "previous_proficiency": previous_proficiency,
                "proficiency": relation.proficiency,
                "previous_status": previous_status,
                "status": relation.status,
            },
        )
        session.flush()
    return row


def remove_employee_skill(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    skill_id: UUID,
    request_id: UUID,
) -> None:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    row = _get_employee_skill(
        session, tenant_id, access.company.id, employee_id, skill_id, lock=True
    )
    relation = row.employee_skill
    if relation.status != "inactive":
        relation.status = "inactive"
        record_event(
            session,
            "employee_skill.removed",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="employee_skill",
            target_id=relation.id,
            request_id=request_id,
            details={"employee_id": employee_id, "skill_id": skill_id},
        )
        session.flush()


def _validate_week_start(week_start_date: date) -> None:
    if not is_monday(week_start_date):
        raise ApiError(422, "invalid_week_start", "Weekly capacity records must start on a Monday.")


def _validate_week_filter(start_week: date | None, end_week: date | None) -> None:
    if start_week is not None:
        _validate_week_start(start_week)
    if end_week is not None:
        _validate_week_start(end_week)
    if start_week is not None and end_week is not None and end_week < start_week:
        raise ApiError(422, "invalid_week_range", "The end week must not precede the start week.")


def record_weekly_capacity(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    week_start_date: date,
    body: WeeklyCapacityCreate,
    request_id: UUID,
) -> EmployeeWeeklyCapacity:
    _validate_week_start(week_start_date)
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    employee = _get_employee(session, tenant_id, access.company.id, employee_id, lock=True)
    previous_version = session.scalar(
        select(func.max(EmployeeWeeklyCapacity.version)).where(
            EmployeeWeeklyCapacity.tenant_id == tenant_id,
            EmployeeWeeklyCapacity.company_id == access.company.id,
            EmployeeWeeklyCapacity.employee_id == employee.id,
            EmployeeWeeklyCapacity.week_start_date == week_start_date,
        )
    )
    version = (previous_version or 0) + 1
    capacity = EmployeeWeeklyCapacity(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        employee_id=employee.id,
        week_start_date=week_start_date,
        version=version,
        supersedes_version=previous_version,
        availability_status=body.availability_status,
        scheduled_hours=body.scheduled_hours,
        non_project_hours=body.non_project_hours,
        unavailable_hours=body.unavailable_hours,
        source=body.source,
        reason=body.reason,
        created_by_user_id=user.id,
    )
    session.add(capacity)
    session.flush()
    recorded_available_hours = available_hours(capacity)
    record_event(
        session,
        "employee_capacity.version_recorded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="employee_weekly_capacity",
        target_id=capacity.id,
        request_id=request_id,
        details={
            "employee_id": employee.id,
            "week_start_date": week_start_date.isoformat(),
            "version": version,
            "availability_status": body.availability_status,
            "source": body.source,
            "available_hours": (
                str(recorded_available_hours) if recorded_available_hours is not None else None
            ),
        },
    )
    return capacity


def _latest_capacity_query(
    tenant_id: UUID,
    company_id: UUID,
    employee_id: UUID,
    start_week: date | None,
    end_week: date | None,
):
    filters = [
        EmployeeWeeklyCapacity.tenant_id == tenant_id,
        EmployeeWeeklyCapacity.company_id == company_id,
        EmployeeWeeklyCapacity.employee_id == employee_id,
    ]
    if start_week is not None:
        filters.append(EmployeeWeeklyCapacity.week_start_date >= start_week)
    if end_week is not None:
        filters.append(EmployeeWeeklyCapacity.week_start_date <= end_week)
    latest = (
        select(
            EmployeeWeeklyCapacity.week_start_date.label("week_start_date"),
            func.max(EmployeeWeeklyCapacity.version).label("version"),
        )
        .where(*filters)
        .group_by(EmployeeWeeklyCapacity.week_start_date)
        .subquery()
    )
    statement = select(EmployeeWeeklyCapacity).join(
        latest,
        and_(
            EmployeeWeeklyCapacity.week_start_date == latest.c.week_start_date,
            EmployeeWeeklyCapacity.version == latest.c.version,
            EmployeeWeeklyCapacity.tenant_id == tenant_id,
            EmployeeWeeklyCapacity.company_id == company_id,
            EmployeeWeeklyCapacity.employee_id == employee_id,
        ),
    )
    return statement, latest


def list_weekly_capacity(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    *,
    start_week: date | None,
    end_week: date | None,
    limit: int,
    offset: int,
) -> tuple[list[EmployeeWeeklyCapacity], int]:
    _validate_week_filter(start_week, end_week)
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    _get_employee(session, tenant_id, access.company.id, employee_id)
    statement, latest = _latest_capacity_query(
        tenant_id, access.company.id, employee_id, start_week, end_week
    )
    total = session.scalar(select(func.count()).select_from(latest)) or 0
    rows = list(
        session.scalars(
            statement.order_by(EmployeeWeeklyCapacity.week_start_date.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def list_weekly_capacity_history(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    employee_id: UUID,
    week_start_date: date,
    *,
    limit: int,
    offset: int,
) -> tuple[list[EmployeeWeeklyCapacity], int]:
    _validate_week_start(week_start_date)
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    _get_employee(session, tenant_id, access.company.id, employee_id)
    filters = (
        EmployeeWeeklyCapacity.tenant_id == tenant_id,
        EmployeeWeeklyCapacity.company_id == access.company.id,
        EmployeeWeeklyCapacity.employee_id == employee_id,
        EmployeeWeeklyCapacity.week_start_date == week_start_date,
    )
    total = (
        session.scalar(select(func.count()).select_from(EmployeeWeeklyCapacity).where(*filters))
        or 0
    )
    rows = list(
        session.scalars(
            select(EmployeeWeeklyCapacity)
            .where(*filters)
            .order_by(EmployeeWeeklyCapacity.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def available_hours(record: EmployeeWeeklyCapacity) -> Decimal | None:
    if record.availability_status == "unknown":
        return None
    scheduled = record.scheduled_hours
    non_project = record.non_project_hours
    unavailable = record.unavailable_hours
    if scheduled is None or non_project is None or unavailable is None:
        raise ValueError("known availability requires all capacity-hour values")
    return scheduled - non_project - unavailable


def monday_week_end(week_start_date: date) -> date:
    return monday_for(week_start_date).fromordinal(monday_for(week_start_date).toordinal() + 6)
