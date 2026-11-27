from __future__ import annotations

from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.company_context.models import (
    Employee,
    EmployeeSkill,
    EmployeeWeeklyCapacity,
    Project,
    ResourceAssignment,
    Skill,
)
from businessflow.data_imports.models import DataImport, DataImportRecordLink, DataImportRow
from businessflow.errors import ApiError
from businessflow.metrics.calculation import (
    MAX_ACTIVE_EMPLOYEES,
    MAX_ACTIVE_SKILLS,
    MAX_ASSIGNMENTS,
    MAX_EMPLOYEE_SKILL_RECORDS,
    MAX_IMPORT_LINEAGE_REFERENCES,
    calculate_metrics,
)
from businessflow.metrics.catalog import CALCULATION_VERSION, METRIC_CATALOG_VERSION
from businessflow.metrics.models import MetricRun
from businessflow.metrics.snapshot import content_hash, serialize_input_snapshot
from businessflow.metrics.types import (
    AssignmentInput,
    CapacityInput,
    EmployeeInput,
    EmployeeSkillInput,
    MetricInputSnapshot,
    SkillInput,
    SourceReference,
)
from businessflow.models import UserAccount
from businessflow.services.company_context_access import require_company_context_access


def _metric_input_limit_error() -> ApiError:
    return ApiError(
        422,
        "metric_input_limit_exceeded",
        "The company context exceeds supported limits for this metric calculation.",
    )


def _iso_datetime(value: datetime) -> str:
    return value.isoformat(timespec="microseconds")


def _load_import_lineage(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    canonical_ids: dict[str, set[UUID]],
) -> dict[tuple[str, UUID], tuple[SourceReference, ...]]:
    references: dict[tuple[str, UUID], list[SourceReference]] = defaultdict(list)
    total_rows = 0
    for canonical_type, ids in sorted(canonical_ids.items()):
        if not ids:
            continue
        rows = session.execute(
            select(DataImportRecordLink, DataImportRow, DataImport)
            .join(
                DataImportRow,
                and_(
                    DataImportRow.id == DataImportRecordLink.source_row_id,
                    DataImportRow.tenant_id == DataImportRecordLink.tenant_id,
                    DataImportRow.company_id == DataImportRecordLink.company_id,
                    DataImportRow.import_id == DataImportRecordLink.import_id,
                ),
            )
            .join(
                DataImport,
                and_(
                    DataImport.id == DataImportRecordLink.import_id,
                    DataImport.tenant_id == DataImportRecordLink.tenant_id,
                    DataImport.company_id == DataImportRecordLink.company_id,
                ),
            )
            .where(
                DataImportRecordLink.tenant_id == tenant_id,
                DataImportRecordLink.company_id == company_id,
                DataImportRecordLink.canonical_type == canonical_type,
                DataImportRecordLink.canonical_id.in_(sorted(ids, key=str)),
            )
            .order_by(
                DataImportRecordLink.canonical_id,
                DataImportRecordLink.import_id,
                DataImportRow.worksheet_name,
                DataImportRow.row_number,
                DataImportRecordLink.id,
            )
            .limit(MAX_IMPORT_LINEAGE_REFERENCES - total_rows + 1)
        ).all()
        total_rows += len(rows)
        if total_rows > MAX_IMPORT_LINEAGE_REFERENCES:
            raise _metric_input_limit_error()
        for link, source_row, import_record in rows:
            references[(canonical_type, link.canonical_id)].append(
                SourceReference(
                    import_id=str(import_record.id),
                    import_status=import_record.status,
                    dataset_type=import_record.dataset_type,
                    mapping_version=import_record.mapping_version,
                    file_hash=import_record.file_hash,
                    source_row_id=str(source_row.id),
                    worksheet_name=source_row.worksheet_name,
                    row_number=source_row.row_number,
                    row_hash=source_row.row_hash,
                    canonical_fingerprint_at_import=link.canonical_fingerprint,
                )
            )
    return {key: tuple(value) for key, value in references.items()}


def _load_metric_inputs(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    company_timezone: str,
    start_week_date: date,
    week_count: int,
) -> MetricInputSnapshot:
    end_week_date = start_week_date + timedelta(days=7 * (week_count - 1))
    period_end_date = end_week_date + timedelta(days=6)
    active_employees = list(
        session.scalars(
            select(Employee)
            .where(
                Employee.tenant_id == tenant_id,
                Employee.company_id == company_id,
                Employee.employment_status == "active",
            )
            .order_by(Employee.employee_code_key, Employee.id)
            .limit(MAX_ACTIVE_EMPLOYEES + 1)
        )
    )
    if len(active_employees) > MAX_ACTIVE_EMPLOYEES:
        raise _metric_input_limit_error()
    active_employee_ids = {employee.id for employee in active_employees}

    assignment_rows = session.execute(
        select(
            ResourceAssignment,
            Employee.employee_code,
            Employee.employment_status,
            Project.project_code,
            Project.status,
        )
        .join(
            Employee,
            and_(
                Employee.id == ResourceAssignment.employee_id,
                Employee.tenant_id == ResourceAssignment.tenant_id,
                Employee.company_id == ResourceAssignment.company_id,
            ),
        )
        .join(
            Project,
            and_(
                Project.id == ResourceAssignment.project_id,
                Project.tenant_id == ResourceAssignment.tenant_id,
                Project.company_id == ResourceAssignment.company_id,
            ),
        )
        .where(
            ResourceAssignment.tenant_id == tenant_id,
            ResourceAssignment.company_id == company_id,
            ResourceAssignment.start_date <= period_end_date,
            ResourceAssignment.end_date >= start_week_date,
            ResourceAssignment.status.in_(("proposed", "committed")),
        )
        .order_by(ResourceAssignment.start_date, ResourceAssignment.end_date, ResourceAssignment.id)
        .limit(MAX_ASSIGNMENTS + 1)
    ).all()
    if len(assignment_rows) > MAX_ASSIGNMENTS:
        raise _metric_input_limit_error()

    employees_by_id: dict[UUID, EmployeeInput] = {
        employee.id: EmployeeInput(
            id=str(employee.id),
            employee_code=employee.employee_code,
            employment_status=employee.employment_status,
        )
        for employee in active_employees
    }
    assignments: list[AssignmentInput] = []
    for (
        assignment,
        employee_code,
        employment_status,
        project_code,
        project_status,
    ) in assignment_rows:
        employees_by_id.setdefault(
            assignment.employee_id,
            EmployeeInput(
                id=str(assignment.employee_id),
                employee_code=employee_code,
                employment_status=employment_status,
            ),
        )
        assignments.append(
            AssignmentInput(
                id=str(assignment.id),
                employee_id=str(assignment.employee_id),
                project_id=str(assignment.project_id),
                project_code=project_code,
                project_status=project_status,
                start_date=assignment.start_date,
                end_date=assignment.end_date,
                planned_hours_per_week=assignment.planned_hours_per_week,
                status=assignment.status,
                source=assignment.source,
                created_at=_iso_datetime(assignment.created_at),
                updated_at=_iso_datetime(assignment.updated_at),
            )
        )

    capacity_rows: list[EmployeeWeeklyCapacity] = []
    if active_employee_ids:
        latest_capacity = (
            select(
                EmployeeWeeklyCapacity.employee_id.label("employee_id"),
                EmployeeWeeklyCapacity.week_start_date.label("week_start_date"),
                func.max(EmployeeWeeklyCapacity.version).label("version"),
            )
            .where(
                EmployeeWeeklyCapacity.tenant_id == tenant_id,
                EmployeeWeeklyCapacity.company_id == company_id,
                EmployeeWeeklyCapacity.employee_id.in_(sorted(active_employee_ids, key=str)),
                EmployeeWeeklyCapacity.week_start_date >= start_week_date,
                EmployeeWeeklyCapacity.week_start_date <= end_week_date,
            )
            .group_by(EmployeeWeeklyCapacity.employee_id, EmployeeWeeklyCapacity.week_start_date)
            .subquery()
        )
        capacity_rows = list(
            session.scalars(
                select(EmployeeWeeklyCapacity)
                .join(
                    latest_capacity,
                    and_(
                        EmployeeWeeklyCapacity.employee_id == latest_capacity.c.employee_id,
                        EmployeeWeeklyCapacity.week_start_date == latest_capacity.c.week_start_date,
                        EmployeeWeeklyCapacity.version == latest_capacity.c.version,
                        EmployeeWeeklyCapacity.tenant_id == tenant_id,
                        EmployeeWeeklyCapacity.company_id == company_id,
                    ),
                )
                .order_by(
                    EmployeeWeeklyCapacity.week_start_date,
                    EmployeeWeeklyCapacity.employee_id,
                    EmployeeWeeklyCapacity.version,
                )
                .limit(MAX_ACTIVE_EMPLOYEES * 52 + 1)
            )
        )
        if len(capacity_rows) > MAX_ACTIVE_EMPLOYEES * 52:
            raise _metric_input_limit_error()

    skills = list(
        session.scalars(
            select(Skill)
            .where(Skill.tenant_id == tenant_id, Skill.status == "active")
            .order_by(Skill.name_key, Skill.id)
            .limit(MAX_ACTIVE_SKILLS + 1)
        )
    )
    if len(skills) > MAX_ACTIVE_SKILLS:
        raise _metric_input_limit_error()
    active_skill_ids = {skill.id for skill in skills}

    employee_skill_rows: list[EmployeeSkill] = []
    if active_employee_ids and active_skill_ids:
        employee_skill_rows = list(
            session.scalars(
                select(EmployeeSkill)
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
                        Skill.id == EmployeeSkill.skill_id,
                        Skill.tenant_id == EmployeeSkill.tenant_id,
                    ),
                )
                .where(
                    EmployeeSkill.tenant_id == tenant_id,
                    EmployeeSkill.company_id == company_id,
                    EmployeeSkill.employee_id.in_(sorted(active_employee_ids, key=str)),
                    EmployeeSkill.skill_id.in_(sorted(active_skill_ids, key=str)),
                    EmployeeSkill.status == "active",
                    Employee.employment_status == "active",
                    Skill.status == "active",
                )
                .order_by(EmployeeSkill.employee_id, EmployeeSkill.skill_id, EmployeeSkill.id)
                .limit(MAX_EMPLOYEE_SKILL_RECORDS + 1)
            )
        )
        if len(employee_skill_rows) > MAX_EMPLOYEE_SKILL_RECORDS:
            raise _metric_input_limit_error()

    canonical_ids: dict[str, set[UUID]] = {
        "employee": set(employees_by_id),
        "weekly_capacity": {row.id for row in capacity_rows},
        "resource_assignment": {row[0].id for row in assignment_rows},
        "skill": {row.id for row in skills},
        "employee_skill": {row.id for row in employee_skill_rows},
    }
    lineage = _load_import_lineage(session, tenant_id, company_id, canonical_ids)

    employees = tuple(
        replace(
            employee,
            lineage=lineage.get(("employee", UUID(employee.id)), ()),
        )
        for employee in sorted(
            employees_by_id.values(), key=lambda item: (item.employee_code.casefold(), item.id)
        )
    )
    capacities = tuple(
        CapacityInput(
            id=str(row.id),
            employee_id=str(row.employee_id),
            week_start_date=row.week_start_date,
            version=row.version,
            supersedes_version=row.supersedes_version,
            availability_status=row.availability_status,
            scheduled_hours=row.scheduled_hours,
            non_project_hours=row.non_project_hours,
            unavailable_hours=row.unavailable_hours,
            source=row.source,
            created_at=_iso_datetime(row.created_at),
            lineage=lineage.get(("weekly_capacity", row.id), ()),
        )
        for row in capacity_rows
    )
    linked_assignments = tuple(
        replace(
            assignment,
            lineage=lineage.get(("resource_assignment", UUID(assignment.id)), ()),
        )
        for assignment in assignments
    )
    skill_inputs = tuple(
        SkillInput(
            id=str(row.id),
            name=row.name,
            status=row.status,
            lineage=lineage.get(("skill", row.id), ()),
        )
        for row in skills
    )
    employee_skill_inputs = tuple(
        EmployeeSkillInput(
            id=str(row.id),
            employee_id=str(row.employee_id),
            skill_id=str(row.skill_id),
            proficiency=row.proficiency,
            status=row.status,
            lineage=lineage.get(("employee_skill", row.id), ()),
        )
        for row in employee_skill_rows
    )
    return MetricInputSnapshot(
        tenant_id=str(tenant_id),
        company_id=str(company_id),
        company_timezone=company_timezone,
        start_week_date=start_week_date,
        week_count=week_count,
        employees=employees,
        capacities=capacities,
        assignments=linked_assignments,
        skills=skill_inputs,
        employee_skills=employee_skill_inputs,
    )


def read_metric_catalog(session: Session, user: UserAccount, tenant_id: UUID) -> dict[str, object]:
    from businessflow.metrics.catalog import metric_catalog_payload

    require_company_context_access(session, user, tenant_id, "company_context:read")
    return metric_catalog_payload()


def create_metric_run(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    start_week_date: date,
    week_count: int,
    request_id: UUID,
) -> MetricRun:
    if start_week_date.isoweekday() != 1 or not 1 <= week_count <= 52:
        raise ApiError(
            422, "invalid_metric_period", "Metric runs require a Monday start and 1-52 weeks."
        )
    try:
        end_week_date = start_week_date + timedelta(days=7 * week_count - 1)
    except OverflowError:
        raise ApiError(
            422, "invalid_metric_period", "The metric period is outside the calendar range."
        ) from None
    access = require_company_context_access(
        session, user, tenant_id, "company_context:read", request_id=request_id
    )
    require_company_context_access(
        session, user, tenant_id, "workforce:read", request_id=request_id
    )
    input_snapshot = _load_metric_inputs(
        session,
        tenant_id,
        access.company.id,
        access.company.timezone,
        start_week_date,
        week_count,
    )
    input_payload = serialize_input_snapshot(input_snapshot)
    result_payload = calculate_metrics(input_snapshot)
    input_hash = content_hash(input_payload)
    result_hash = content_hash(result_payload)
    reliability_status = cast(str, result_payload["reliability_status"])

    run = MetricRun(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        requested_by_user_id=user.id,
        start_week_date=start_week_date,
        end_week_date=end_week_date,
        week_count=week_count,
        metric_catalog_version=METRIC_CATALOG_VERSION,
        calculation_version=CALCULATION_VERSION,
        reliability_status=reliability_status,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=input_payload,
        result_snapshot=result_payload,
    )
    session.add(run)
    session.flush()
    record_event(
        session,
        "metric_run.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="metric_run",
        target_id=run.id,
        request_id=request_id,
        details={
            "metric_catalog_version": METRIC_CATALOG_VERSION,
            "calculation_version": CALCULATION_VERSION,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "start_week_date": start_week_date.isoformat(),
            "week_count": week_count,
            "reliability_status": reliability_status,
        },
    )
    return run


def get_metric_run(session: Session, user: UserAccount, tenant_id: UUID, run_id: UUID) -> MetricRun:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    require_company_context_access(session, user, tenant_id, "workforce:read")
    run = session.scalar(
        select(MetricRun).where(
            MetricRun.id == run_id,
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == access.company.id,
        )
    )
    if run is None:
        raise ApiError(404, "metric_run_not_found", "Metric run was not found.")
    return run
