from __future__ import annotations

from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_context.models import BusinessUnit, Department, Employee
from businessflow.company_context.schemas import (
    BusinessUnitCreate,
    BusinessUnitUpdate,
    DepartmentCreate,
    DepartmentUpdate,
)
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access


def list_business_units(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[BusinessUnit], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [
        BusinessUnit.tenant_id == tenant_id,
        BusinessUnit.company_id == access.company.id,
    ]
    if status is not None:
        filters.append(BusinessUnit.status == status)
    total = session.scalar(select(func.count()).select_from(BusinessUnit).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(BusinessUnit)
            .where(*filters)
            .order_by(BusinessUnit.name_key, BusinessUnit.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_business_unit(
    session: Session, user: UserAccount, tenant_id: UUID, unit_id: UUID
) -> BusinessUnit:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    unit = session.scalar(
        select(BusinessUnit).where(
            BusinessUnit.id == unit_id,
            BusinessUnit.tenant_id == tenant_id,
            BusinessUnit.company_id == access.company.id,
        )
    )
    if unit is None:
        raise ApiError(404, "business_unit_not_found", "Business unit was not found.")
    return unit


def create_business_unit(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: BusinessUnitCreate,
    request_id: UUID,
) -> BusinessUnit:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    unit = BusinessUnit(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        name=body.name,
        description=body.description,
        status="active",
    )
    session.add(unit)
    session.flush()
    record_event(
        session,
        "business_unit.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_unit",
        target_id=unit.id,
        request_id=request_id,
    )
    return unit


def update_business_unit(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    unit_id: UUID,
    changes: BusinessUnitUpdate,
    request_id: UUID,
) -> BusinessUnit:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    unit = session.scalar(
        select(BusinessUnit)
        .where(
            BusinessUnit.id == unit_id,
            BusinessUnit.tenant_id == tenant_id,
            BusinessUnit.company_id == access.company.id,
        )
        .with_for_update()
    )
    if unit is None:
        raise ApiError(404, "business_unit_not_found", "Business unit was not found.")

    patch = changes.model_dump(exclude_unset=True)
    if patch.get("status") == "inactive" and unit.status != "inactive":
        active_departments = (
            session.scalar(
                select(func.count())
                .select_from(Department)
                .where(
                    Department.tenant_id == tenant_id,
                    Department.company_id == access.company.id,
                    Department.business_unit_id == unit.id,
                    Department.status == "active",
                )
            )
            or 0
        )
        if active_departments:
            raise ApiError(
                409,
                "business_unit_has_active_departments",
                "Deactivate or move active departments before deactivating this business unit.",
            )

    changed = [field for field, value in patch.items() if getattr(unit, field) != value]
    if changed:
        previous_status = unit.status
        for field, value in patch.items():
            setattr(unit, field, value)
        record_event(
            session,
            "business_unit.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="business_unit",
            target_id=unit.id,
            request_id=request_id,
            details={
                "changed_fields": changed,
                "previous_status": previous_status,
                "status": unit.status,
            },
        )
        session.flush()
    return unit


def list_departments(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    business_unit_id: UUID | None,
    status: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Department], int]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [Department.tenant_id == tenant_id, Department.company_id == access.company.id]
    if business_unit_id is not None:
        filters.append(Department.business_unit_id == business_unit_id)
    if status is not None:
        filters.append(Department.status == status)
    total = session.scalar(select(func.count()).select_from(Department).where(*filters)) or 0
    rows = list(
        session.scalars(
            select(Department)
            .where(*filters)
            .order_by(Department.name_key, Department.id)
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_department(
    session: Session, user: UserAccount, tenant_id: UUID, department_id: UUID
) -> Department:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    department = session.scalar(
        select(Department).where(
            Department.id == department_id,
            Department.tenant_id == tenant_id,
            Department.company_id == access.company.id,
        )
    )
    if department is None:
        raise ApiError(404, "department_not_found", "Department was not found.")
    return department


def _validate_active_business_unit(
    session: Session, tenant_id: UUID, company_id: UUID, business_unit_id: UUID | None
) -> None:
    if business_unit_id is None:
        return
    unit = session.scalar(
        select(BusinessUnit).where(
            BusinessUnit.id == business_unit_id,
            BusinessUnit.tenant_id == tenant_id,
            BusinessUnit.company_id == company_id,
            BusinessUnit.status == "active",
        )
    )
    if unit is None:
        raise ApiError(404, "business_unit_not_found", "Business unit was not found.")


def create_department(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: DepartmentCreate,
    request_id: UUID,
) -> Department:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _validate_active_business_unit(session, tenant_id, access.company.id, body.business_unit_id)
    department = Department(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        business_unit_id=body.business_unit_id,
        name=body.name,
        description=body.description,
        status="active",
    )
    session.add(department)
    session.flush()
    record_event(
        session,
        "department.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="department",
        target_id=department.id,
        request_id=request_id,
        details={"business_unit_id": body.business_unit_id},
    )
    return department


def update_department(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    department_id: UUID,
    changes: DepartmentUpdate,
    request_id: UUID,
) -> Department:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    department = session.scalar(
        select(Department)
        .where(
            Department.id == department_id,
            Department.tenant_id == tenant_id,
            Department.company_id == access.company.id,
        )
        .with_for_update()
    )
    if department is None:
        raise ApiError(404, "department_not_found", "Department was not found.")

    patch = changes.model_dump(exclude_unset=True)
    effective_business_unit_id = patch.get("business_unit_id", department.business_unit_id)
    if "business_unit_id" in patch or patch.get("status") == "active":
        _validate_active_business_unit(
            session, tenant_id, access.company.id, effective_business_unit_id
        )
    if patch.get("status") == "inactive" and department.status != "inactive":
        active_employees = (
            session.scalar(
                select(func.count())
                .select_from(Employee)
                .where(
                    Employee.tenant_id == tenant_id,
                    Employee.company_id == access.company.id,
                    Employee.department_id == department.id,
                    Employee.employment_status.in_(("active", "on_leave")),
                )
            )
            or 0
        )
        if active_employees:
            raise ApiError(
                409,
                "department_has_active_employees",
                "Move or deactivate assigned employees before deactivating this department.",
            )

    changed = [field for field, value in patch.items() if getattr(department, field) != value]
    if changed:
        previous_status = department.status
        for field, value in patch.items():
            setattr(department, field, value)
        record_event(
            session,
            "department.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="department",
            target_id=department.id,
            request_id=request_id,
            details={
                "changed_fields": changed,
                "previous_status": previous_status,
                "status": department.status,
                "business_unit_id": department.business_unit_id,
            },
        )
        session.flush()
    return department
