from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_context.domain import (
    ASSIGNMENT_TRANSITIONS,
    PROJECT_TRANSITIONS,
    transition_allowed,
)
from businessflow.company_context.models import Employee, Project, ResourceAssignment
from businessflow.company_context.schemas import (
    ProjectCreate,
    ProjectUpdate,
    ResourceAssignmentCreate,
    ResourceAssignmentUpdate,
)
from businessflow.errors import ApiError
from businessflow.models import AuditEvent, UserAccount
from businessflow.services.company_context_access import require_company_context_access


def _get_project(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    project_id: UUID,
    *,
    lock: bool = False,
) -> Project:
    statement = select(Project).where(
        Project.id == project_id,
        Project.tenant_id == tenant_id,
        Project.company_id == company_id,
    )
    if lock:
        statement = statement.with_for_update()
    project = session.scalar(statement)
    if project is None:
        raise ApiError(404, "project_not_found", "Project was not found.")
    return project


def _validate_project_manager(
    session: Session, tenant_id: UUID, company_id: UUID, employee_id: UUID | None
) -> None:
    if employee_id is None:
        return
    manager = session.scalar(
        select(Employee).where(
            Employee.id == employee_id,
            Employee.tenant_id == tenant_id,
            Employee.company_id == company_id,
            Employee.employment_status == "active",
        )
    )
    if manager is None:
        raise ApiError(404, "employee_not_found", "Project manager was not found.")


def _ensure_project_has_no_open_assignments(
    session: Session, tenant_id: UUID, company_id: UUID, project_id: UUID
) -> None:
    open_assignments = (
        session.scalar(
            select(func.count())
            .select_from(ResourceAssignment)
            .where(
                ResourceAssignment.tenant_id == tenant_id,
                ResourceAssignment.company_id == company_id,
                ResourceAssignment.project_id == project_id,
                ResourceAssignment.status.in_(("proposed", "committed")),
            )
        )
        or 0
    )
    if open_assignments:
        raise ApiError(
            409,
            "project_has_open_assignments",
            "Complete or cancel proposed and committed assignments before closing the project.",
        )


def list_projects(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    status: str | None,
    manager_employee_id: UUID | None,
    start_after: date | None,
    end_before: date | None,
    limit: int,
    offset: int,
) -> tuple[list[Project], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [Project.tenant_id == tenant_id, Project.company_id == access.company.id]
    if status is not None:
        filters.append(Project.status == status)
    if manager_employee_id is not None:
        filters.append(Project.project_manager_employee_id == manager_employee_id)
    if start_after is not None:
        filters.append(Project.start_date >= start_after)
    if end_before is not None:
        filters.append(Project.planned_end_date <= end_before)
    total = session.scalar(select(func.count()).select_from(Project).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(Project)
            .where(*filters)
            .order_by(Project.start_date.nulls_last(), Project.project_code_key, Project.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_project(session: Session, user: UserAccount, tenant_id: UUID, project_id: UUID) -> Project:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    return _get_project(session, tenant_id, access.company.id, project_id)


def create_project(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: ProjectCreate,
    request_id: UUID,
) -> Project:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _validate_project_manager(
        session, tenant_id, access.company.id, body.project_manager_employee_id
    )
    project = Project(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_code=body.project_code,
        name=body.name,
        description=body.description,
        status=body.status,
        start_date=body.start_date,
        planned_end_date=body.planned_end_date,
        project_manager_employee_id=body.project_manager_employee_id,
    )
    session.add(project)
    session.flush()
    record_event(
        session,
        "project.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project",
        target_id=project.id,
        request_id=request_id,
        details={"project_code": project.project_code, "status": project.status},
    )
    return project


def update_project(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    project_id: UUID,
    changes: ProjectUpdate,
    request_id: UUID,
) -> Project:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    project = _get_project(session, tenant_id, access.company.id, project_id, lock=True)
    patch = changes.model_dump(exclude_unset=True)
    if project.status in {"completed", "cancelled"} and any(
        getattr(project, field) != value for field, value in patch.items()
    ):
        raise ApiError(409, "project_terminal", "Completed or cancelled projects are immutable.")
    requested_status = patch.get("status", project.status)
    if not transition_allowed(project.status, requested_status, PROJECT_TRANSITIONS):
        raise ApiError(
            409, "invalid_project_transition", "The project status transition is not allowed."
        )
    if "project_manager_employee_id" in patch:
        _validate_project_manager(
            session,
            tenant_id,
            access.company.id,
            patch["project_manager_employee_id"],
        )
    start_date = patch.get("start_date", project.start_date)
    planned_end_date = patch.get("planned_end_date", project.planned_end_date)
    actual_end_date = patch.get("actual_end_date", project.actual_end_date)
    if start_date is not None and planned_end_date is not None and planned_end_date < start_date:
        raise ApiError(422, "invalid_project_dates", "Project planned dates are invalid.")
    if start_date is not None and actual_end_date is not None and actual_end_date < start_date:
        raise ApiError(422, "invalid_project_dates", "Project actual dates are invalid.")
    if requested_status == "completed" and actual_end_date is None:
        raise ApiError(
            422,
            "actual_end_date_required",
            "Provide the actual end date when completing a project.",
        )
    if actual_end_date is not None and requested_status != "completed":
        raise ApiError(
            422,
            "actual_end_requires_completed_project",
            "An actual end date is valid only for a completed project.",
        )
    if requested_status in {"completed", "cancelled"} and project.status != requested_status:
        _ensure_project_has_no_open_assignments(session, tenant_id, access.company.id, project.id)

    changed = [field for field, value in patch.items() if getattr(project, field) != value]
    if changed:
        previous_status = project.status
        for field, value in patch.items():
            setattr(project, field, value)
        record_event(
            session,
            "project.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="project",
            target_id=project.id,
            request_id=request_id,
            details={
                "changed_fields": changed,
                "previous_status": previous_status,
                "status": project.status,
                "previous_start_date": project.start_date.isoformat()
                if project.start_date
                else None,
                "start_date": start_date.isoformat() if start_date else None,
                "planned_end_date": planned_end_date.isoformat() if planned_end_date else None,
                "actual_end_date": actual_end_date.isoformat() if actual_end_date else None,
            },
        )
        session.flush()
    return project


def deactivate_project(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    project_id: UUID,
    request_id: UUID,
) -> None:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    project = _get_project(session, tenant_id, access.company.id, project_id, lock=True)
    if project.status == "cancelled":
        return
    if not transition_allowed(project.status, "cancelled", PROJECT_TRANSITIONS):
        raise ApiError(409, "invalid_project_transition", "The project can no longer be cancelled.")
    _ensure_project_has_no_open_assignments(session, tenant_id, access.company.id, project.id)
    previous_status = project.status
    project.status = "cancelled"
    record_event(
        session,
        "project.cancelled",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project",
        target_id=project.id,
        request_id=request_id,
        details={"previous_status": previous_status, "status": "cancelled"},
    )
    session.flush()


def _get_assignment(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    assignment_id: UUID,
    *,
    lock: bool = False,
) -> ResourceAssignment:
    statement = select(ResourceAssignment).where(
        ResourceAssignment.id == assignment_id,
        ResourceAssignment.tenant_id == tenant_id,
        ResourceAssignment.company_id == company_id,
    )
    if lock:
        statement = statement.with_for_update()
    assignment = session.scalar(statement)
    if assignment is None:
        raise ApiError(404, "resource_assignment_not_found", "Resource assignment was not found.")
    return assignment


def list_resource_assignments(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    project_id: UUID | None,
    employee_id: UUID | None,
    status: str | None,
    period_start: date | None,
    period_end: date | None,
    limit: int,
    offset: int,
) -> tuple[list[ResourceAssignment], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    if period_start is not None and period_end is not None and period_end < period_start:
        raise ApiError(
            422, "invalid_assignment_filter", "The end date must not precede the start date."
        )
    filters = [
        ResourceAssignment.tenant_id == tenant_id,
        ResourceAssignment.company_id == access.company.id,
    ]
    if project_id is not None:
        filters.append(ResourceAssignment.project_id == project_id)
    if employee_id is not None:
        filters.append(ResourceAssignment.employee_id == employee_id)
    if status is not None:
        filters.append(ResourceAssignment.status == status)
    if period_start is not None:
        filters.append(ResourceAssignment.end_date >= period_start)
    if period_end is not None:
        filters.append(ResourceAssignment.start_date <= period_end)
    total = (
        session.scalar(select(func.count()).select_from(ResourceAssignment).where(*filters)) or 0
    )
    rows = list(
        session.scalars(
            select(ResourceAssignment)
            .where(*filters)
            .order_by(ResourceAssignment.start_date, ResourceAssignment.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_resource_assignment(
    session: Session, user: UserAccount, tenant_id: UUID, assignment_id: UUID
) -> ResourceAssignment:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    return _get_assignment(session, tenant_id, access.company.id, assignment_id)


def _validate_assignment_context(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    assignment: ResourceAssignment,
    requested_status: str,
    *,
    lock: bool = False,
) -> None:
    project = _get_project(session, tenant_id, company_id, assignment.project_id, lock=lock)
    employee_statement = select(Employee).where(
        Employee.id == assignment.employee_id,
        Employee.tenant_id == tenant_id,
        Employee.company_id == company_id,
    )
    if lock:
        employee_statement = employee_statement.with_for_update()
    employee = session.scalar(employee_statement)
    if employee is None:
        raise ApiError(404, "employee_not_found", "Employee was not found.")
    if project.status in {"completed", "cancelled"}:
        raise ApiError(
            409, "project_not_assignable", "Completed or cancelled projects cannot be assigned."
        )
    if requested_status == "committed":
        if project.status not in {"planned", "active"}:
            raise ApiError(
                409,
                "project_not_committable",
                "Only planned or active projects can have committed assignments.",
            )
        if employee.employment_status != "active":
            raise ApiError(
                409,
                "employee_not_active",
                "Committed assignments require an active employee.",
            )
        if (
            employee.employment_start_date
            and assignment.start_date < employee.employment_start_date
        ):
            raise ApiError(
                409,
                "assignment_before_employment",
                "The assignment starts before the employee's employment date.",
            )
        if employee.employment_end_date and assignment.end_date > employee.employment_end_date:
            raise ApiError(
                409,
                "assignment_after_employment",
                "The assignment ends after the employee's employment date.",
            )


def _assignment_audit_values(assignment: ResourceAssignment) -> dict[str, str | None]:
    return {
        "start_date": assignment.start_date.isoformat(),
        "end_date": assignment.end_date.isoformat(),
        "planned_hours_per_week": str(assignment.planned_hours_per_week),
        "status": assignment.status,
    }


def create_resource_assignment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    project_id: UUID,
    body: ResourceAssignmentCreate,
    request_id: UUID,
) -> ResourceAssignment:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    project = _get_project(session, tenant_id, access.company.id, project_id, lock=True)
    employee = session.scalar(
        select(Employee).where(
            Employee.id == body.employee_id,
            Employee.tenant_id == tenant_id,
            Employee.company_id == access.company.id,
        )
    )
    if employee is None:
        raise ApiError(404, "employee_not_found", "Employee was not found.")
    assignment = ResourceAssignment(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_id=project.id,
        employee_id=employee.id,
        start_date=body.start_date,
        end_date=body.end_date,
        planned_hours_per_week=body.planned_hours_per_week,
        status=body.status,
        notes=body.notes,
        source=body.source,
    )
    _validate_assignment_context(
        session, tenant_id, access.company.id, assignment, body.status, lock=True
    )
    session.add(assignment)
    session.flush()
    record_event(
        session,
        "resource_assignment.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="resource_assignment",
        target_id=assignment.id,
        request_id=request_id,
        details={
            "project_id": project.id,
            "employee_id": employee.id,
            **_assignment_audit_values(assignment),
        },
    )
    return assignment


def update_resource_assignment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    assignment_id: UUID,
    changes: ResourceAssignmentUpdate,
    request_id: UUID,
) -> ResourceAssignment:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    assignment = _get_assignment(session, tenant_id, access.company.id, assignment_id, lock=True)
    patch = changes.model_dump(exclude_unset=True)
    if (
        assignment.status in {"completed", "cancelled"}
        and patch
        and any(getattr(assignment, key) != value for key, value in patch.items())
    ):
        raise ApiError(
            409, "assignment_terminal", "Completed or cancelled assignments are immutable."
        )
    requested_status = patch.get("status", assignment.status)
    if not transition_allowed(assignment.status, requested_status, ASSIGNMENT_TRANSITIONS):
        raise ApiError(
            409, "invalid_assignment_transition", "The assignment status transition is not allowed."
        )
    start_date = patch.get("start_date", assignment.start_date)
    end_date = patch.get("end_date", assignment.end_date)
    if end_date < start_date:
        raise ApiError(422, "invalid_assignment_period", "Assignment dates are invalid.")
    before = _assignment_audit_values(assignment)
    changed = [field for field, value in patch.items() if getattr(assignment, field) != value]
    if changed:
        for field, value in patch.items():
            setattr(assignment, field, value)
        _validate_assignment_context(
            session, tenant_id, access.company.id, assignment, assignment.status, lock=True
        )
        after = _assignment_audit_values(assignment)
        details: dict[str, object] = {"changed_fields": changed}
        for key, value in before.items():
            details[f"previous_{key}"] = value
        details.update(after)
        record_event(
            session,
            "resource_assignment.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="resource_assignment",
            target_id=assignment.id,
            request_id=request_id,
            details=details,
        )
        session.flush()
    return assignment


def cancel_resource_assignment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    assignment_id: UUID,
    request_id: UUID,
) -> None:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    assignment = _get_assignment(session, tenant_id, access.company.id, assignment_id, lock=True)
    if assignment.status == "cancelled":
        return
    if not transition_allowed(assignment.status, "cancelled", ASSIGNMENT_TRANSITIONS):
        raise ApiError(409, "assignment_terminal", "The assignment can no longer be cancelled.")
    before = _assignment_audit_values(assignment)
    assignment.status = "cancelled"
    record_event(
        session,
        "resource_assignment.cancelled",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="resource_assignment",
        target_id=assignment.id,
        request_id=request_id,
        details={
            **{f"previous_{key}": value for key, value in before.items()},
            **_assignment_audit_values(assignment),
        },
    )
    session.flush()


def list_resource_assignment_history(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    assignment_id: UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[AuditEvent], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    assignment = _get_assignment(session, tenant_id, access.company.id, assignment_id)
    filters = (
        AuditEvent.tenant_id == tenant_id,
        AuditEvent.target_type == "resource_assignment",
        AuditEvent.target_id == assignment.id,
        AuditEvent.event_type.in_(
            (
                "resource_assignment.created",
                "resource_assignment.updated",
                "resource_assignment.cancelled",
            )
        ),
    )
    total = session.scalar(select(func.count()).select_from(AuditEvent).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(AuditEvent)
            .where(*filters)
            .order_by(AuditEvent.created_at, AuditEvent.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total
