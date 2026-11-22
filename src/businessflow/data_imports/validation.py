from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel, ValidationError
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from businessflow.company_context.models import (
    Department,
    Employee,
    EmployeeSkill,
    EmployeeWeeklyCapacity,
    Project,
    ResourceAssignment,
    Skill,
)
from businessflow.company_context.schemas import (
    EmployeeCreate,
    EmployeeSkillCreate,
    ProjectCreate,
    ResourceAssignmentCreate,
    SkillCreate,
    WeeklyCapacityCreate,
)
from businessflow.data_imports.catalog import DATASETS
from businessflow.data_imports.models import DataImport, DataImportRow
from businessflow.data_imports.schemas import ImportDataset
from businessflow.errors import ApiError

T = TypeVar("T")


class CanonicalEntity(Protocol):
    id: UUID


@dataclass(slots=True)
class EntityContext:
    employees_by_id: dict[UUID, Employee] = field(default_factory=dict)
    employees_by_code: dict[str, Employee] = field(default_factory=dict)
    departments_by_id: dict[UUID, Department] = field(default_factory=dict)
    departments_by_name: dict[str, Department] = field(default_factory=dict)
    skills_by_id: dict[UUID, Skill] = field(default_factory=dict)
    skills_by_name: dict[str, Skill] = field(default_factory=dict)
    projects_by_id: dict[UUID, Project] = field(default_factory=dict)
    projects_by_code: dict[str, Project] = field(default_factory=dict)


@dataclass(slots=True)
class Candidate:
    canonical_type: str
    body: BaseModel
    identity: tuple[str, ...]
    lookup_key: tuple[object, ...]
    references: list[object]


@dataclass(slots=True)
class PlannedRow:
    source_row: DataImportRow
    mapped_values: dict[str, str | None]
    issues: list[dict[str, object]]
    candidate: Candidate | None = None
    status: str = "invalid"
    canonical_id: UUID | None = None
    existing_entity: object | None = None
    context_fingerprint: str = ""


@dataclass(frozen=True, slots=True)
class ValidationResult:
    validation_hash: str
    report: dict[str, object]
    total_rows: int
    valid_rows: int
    invalid_rows: int
    warning_rows: int
    duplicate_rows: int
    eligible_rows: int
    rows: list[PlannedRow]


def _normalize(value: object) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value if text_value else None


def _key(value: str) -> str:
    return value.strip().lower()


def _issue(
    code: str,
    severity: str,
    category: str,
    field_name: str | None,
    message: str,
    correction: str,
) -> dict[str, object]:
    return {
        "code": code,
        "severity": severity,
        "category": category,
        "field": field_name,
        "message": message,
        "correction": correction,
    }


def _pydantic_issues(exc: ValidationError) -> list[dict[str, object]]:
    issues: list[dict[str, object]] = []
    for error in exc.errors(include_url=False, include_input=False):
        location = error.get("loc", ())
        field_name = str(location[0]) if location else None
        error_type = str(error.get("type", ""))
        if error_type == "missing":
            code, category = "required_value_missing", "required"
            message, correction = "A required value is missing.", "Provide a value for this field."
        elif "date" in error_type:
            code, category = "invalid_date", "format"
            message, correction = (
                "The date value is invalid.",
                "Use an ISO date such as 2026-11-23.",
            )
        elif "decimal" in error_type or "int" in error_type or "float" in error_type:
            code, category = "invalid_number", "format"
            message, correction = (
                "The numeric value is invalid.",
                "Provide a valid number using a period as the decimal separator.",
            )
        elif "literal" in error_type:
            code, category = "invalid_choice", "format"
            message, correction = (
                "The value is not an allowed choice.",
                "Use one of the choices documented for this field.",
            )
        elif "greater_than" in error_type or "less_than" in error_type or "too_long" in error_type:
            code, category = "value_out_of_range", "business_rule"
            message, correction = (
                "The value is outside the supported range.",
                "Adjust the value to the documented field limits.",
            )
        else:
            code, category = "invalid_value", "format"
            message, correction = (
                "The value is invalid for this field.",
                "Correct the format or value and validate again.",
            )
        issues.append(_issue(code, "error", category, field_name, message, correction))
    return issues


def _safe_uuid(value: str | None, field_name: str, issues: list[dict[str, object]]) -> UUID | None:
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError:
        issues.append(
            _issue(
                "invalid_reference_format",
                "error",
                "reference",
                field_name,
                "The identifier is not a valid UUID.",
                "Provide a UUID from this tenant and company, "
                "or map a natural-key reference column.",
            )
        )
        return None


def _row_fields(
    source_row: DataImportRow, headers: list[str], field_mapping: dict[str, str]
) -> tuple[dict[str, str | None], dict[str, str | None]]:
    if source_row.raw_values is None:
        raise ApiError(410, "import_staging_expired", "The staged row data is no longer available.")
    if len(source_row.raw_values) != len(headers):
        raise ApiError(
            409, "import_snapshot_invalid", "The staged source snapshot is inconsistent."
        )
    raw_values = {
        header: _normalize(source_row.raw_values[index]) for index, header in enumerate(headers)
    }
    return raw_values, {
        field_name: raw_values.get(header) for field_name, header in field_mapping.items()
    }


def _collect_context_keys(
    rows: list[tuple[DataImportRow, dict[str, str | None]]], dataset_type: ImportDataset
) -> dict[str, set[object]]:
    keys: dict[str, set[object]] = {
        "employee_ids": set(),
        "employee_codes": set(),
        "department_ids": set(),
        "department_names": set(),
        "skill_ids": set(),
        "skill_names": set(),
        "project_ids": set(),
        "project_codes": set(),
    }

    for _, values in rows:
        employee_fields = {"employee_id", "project_manager_employee_id"}
        employee_code_fields = {"employee_code", "project_manager_employee_code"}
        if dataset_type == "employees":
            employee_code_fields.add("employee_code")
        for field_name in employee_fields:
            parsed = _safe_uuid(values.get(field_name), field_name, [])
            if parsed is not None:
                keys["employee_ids"].add(parsed)
        for field_name in employee_code_fields:
            value = values.get(field_name)
            if value is not None:
                keys["employee_codes"].add(_key(value))
        value = values.get("department_id")
        parsed = _safe_uuid(value, "department_id", [])
        if parsed is not None:
            keys["department_ids"].add(parsed)
        value = values.get("department_name")
        if value is not None:
            keys["department_names"].add(_key(value))
        value = values.get("skill_id")
        parsed = _safe_uuid(value, "skill_id", [])
        if parsed is not None:
            keys["skill_ids"].add(parsed)
        value = values.get("skill_name")
        if value is not None:
            keys["skill_names"].add(_key(value))
        if dataset_type == "skills":
            value = values.get("name")
            if value is not None:
                keys["skill_names"].add(_key(value))
        value = values.get("project_id")
        parsed = _safe_uuid(value, "project_id", [])
        if parsed is not None:
            keys["project_ids"].add(parsed)
        for field_name in {"project_code"}:
            value = values.get(field_name)
            if value is not None:
                keys["project_codes"].add(_key(value))
    return keys


def _chunks(values: set[T], size: int = 10_000) -> list[list[T]]:
    items = list(values)
    return [items[index : index + size] for index in range(0, len(items), size)]


def _load_context(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    keys: dict[str, set[object]],
) -> EntityContext:
    context = EntityContext()

    for employee_ids in _chunks(keys["employee_ids"]):
        employees = session.scalars(
            select(Employee).where(
                Employee.tenant_id == tenant_id,
                Employee.company_id == company_id,
                Employee.id.in_(cast(list[UUID], employee_ids)),
            )
        )
        for employee in employees:
            context.employees_by_id[employee.id] = employee
            context.employees_by_code[employee.employee_code_key] = employee
    for employee_codes in _chunks(keys["employee_codes"]):
        employees = session.scalars(
            select(Employee).where(
                Employee.tenant_id == tenant_id,
                Employee.company_id == company_id,
                Employee.employee_code_key.in_(cast(list[str], employee_codes)),
            )
        )
        for employee in employees:
            context.employees_by_id[employee.id] = employee
            context.employees_by_code[employee.employee_code_key] = employee

    for department_ids in _chunks(keys["department_ids"]):
        departments = session.scalars(
            select(Department).where(
                Department.tenant_id == tenant_id,
                Department.company_id == company_id,
                Department.id.in_(cast(list[UUID], department_ids)),
            )
        )
        for department in departments:
            context.departments_by_id[department.id] = department
            context.departments_by_name[department.name_key] = department
    for department_names in _chunks(keys["department_names"]):
        departments = session.scalars(
            select(Department).where(
                Department.tenant_id == tenant_id,
                Department.company_id == company_id,
                Department.name_key.in_(cast(list[str], department_names)),
            )
        )
        for department in departments:
            context.departments_by_id[department.id] = department
            context.departments_by_name[department.name_key] = department

    for skill_ids in _chunks(keys["skill_ids"]):
        skills = session.scalars(
            select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.id.in_(cast(list[UUID], skill_ids)),
            )
        )
        for skill in skills:
            context.skills_by_id[skill.id] = skill
            context.skills_by_name[skill.name_key] = skill
    for skill_names in _chunks(keys["skill_names"]):
        skills = session.scalars(
            select(Skill).where(
                Skill.tenant_id == tenant_id,
                Skill.name_key.in_(cast(list[str], skill_names)),
            )
        )
        for skill in skills:
            context.skills_by_id[skill.id] = skill
            context.skills_by_name[skill.name_key] = skill

    for project_ids in _chunks(keys["project_ids"]):
        projects = session.scalars(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.company_id == company_id,
                Project.id.in_(cast(list[UUID], project_ids)),
            )
        )
        for project in projects:
            context.projects_by_id[project.id] = project
            context.projects_by_code[project.project_code_key] = project
    for project_codes in _chunks(keys["project_codes"]):
        projects = session.scalars(
            select(Project).where(
                Project.tenant_id == tenant_id,
                Project.company_id == company_id,
                Project.project_code_key.in_(cast(list[str], project_codes)),
            )
        )
        for project in projects:
            context.projects_by_id[project.id] = project
            context.projects_by_code[project.project_code_key] = project
    return context


def _employee_reference(
    values: dict[str, str | None],
    context: EntityContext,
    issues: list[dict[str, object]],
    *,
    required: bool,
    id_field: str = "employee_id",
    code_field: str = "employee_code",
    require_active: bool = False,
) -> Employee | None:
    identifier = _safe_uuid(values.get(id_field), id_field, issues) if id_field in values else None
    code = values.get(code_field)
    employee = context.employees_by_id.get(identifier) if identifier is not None else None
    if employee is None and code is not None:
        employee = context.employees_by_code.get(_key(code))
    if employee is None and (identifier is not None or code is not None):
        issues.append(
            _issue(
                "unknown_employee_reference",
                "error",
                "reference",
                id_field if identifier is not None else code_field,
                "The employee reference does not resolve within this company.",
                "Use an employee UUID or employee code belonging to this tenant's company.",
            )
        )
        return None
    if employee is None and required:
        issues.append(
            _issue(
                "required_reference_missing",
                "error",
                "required",
                code_field,
                "An employee reference is required.",
                "Provide a mapped employee code or UUID.",
            )
        )
        return None
    if employee is not None and require_active and employee.employment_status != "active":
        issues.append(
            _issue(
                "employee_not_active",
                "error",
                "business_rule",
                code_field,
                "This operation requires an active employee.",
                "Use an active employee or correct the employee's status before importing.",
            )
        )
        return None
    return employee


def _department_reference(
    values: dict[str, str | None], context: EntityContext, issues: list[dict[str, object]]
) -> Department | None:
    identifier = _safe_uuid(values.get("department_id"), "department_id", issues)
    name = values.get("department_name")
    department = context.departments_by_id.get(identifier) if identifier is not None else None
    if department is None and name is not None:
        department = context.departments_by_name.get(_key(name))
    if department is None and (identifier is not None or name is not None):
        issues.append(
            _issue(
                "unknown_department_reference",
                "error",
                "reference",
                "department_id" if identifier is not None else "department_name",
                "The department reference does not resolve within this company.",
                "Use an existing department UUID or exact department name from this company.",
            )
        )
        return None
    if department is not None and department.status != "active":
        issues.append(
            _issue(
                "department_inactive",
                "error",
                "business_rule",
                "department_id" if identifier is not None else "department_name",
                "Employees may only be assigned to an active department.",
                "Reactivate the department or choose an active department.",
            )
        )
        return None
    return department


def _skill_reference(
    values: dict[str, str | None], context: EntityContext, issues: list[dict[str, object]]
) -> Skill | None:
    identifier = _safe_uuid(values.get("skill_id"), "skill_id", issues)
    name = values.get("skill_name")
    skill = context.skills_by_id.get(identifier) if identifier is not None else None
    if skill is None and name is not None:
        skill = context.skills_by_name.get(_key(name))
    if skill is None:
        is_missing = identifier is None and name is None
        issues.append(
            _issue(
                "required_reference_missing" if is_missing else "unknown_skill_reference",
                "error",
                "required" if is_missing else "reference",
                "skill_id" if identifier is not None else "skill_name",
                "A skill reference is required."
                if is_missing
                else "The skill reference does not resolve within this tenant.",
                "Provide an existing skill UUID or exact skill name from this tenant.",
            )
        )
        return None
    if skill.status != "active":
        issues.append(
            _issue(
                "skill_inactive",
                "error",
                "business_rule",
                "skill_id" if identifier is not None else "skill_name",
                "Employee skills can only reference an active skill.",
                "Reactivate the skill or select an active skill.",
            )
        )
        return None
    return skill


def _project_reference(
    values: dict[str, str | None], context: EntityContext, issues: list[dict[str, object]]
) -> Project | None:
    identifier = _safe_uuid(values.get("project_id"), "project_id", issues)
    code = values.get("project_code")
    project = context.projects_by_id.get(identifier) if identifier is not None else None
    if project is None and code is not None:
        project = context.projects_by_code.get(_key(code))
    if project is None:
        is_missing = identifier is None and code is None
        issues.append(
            _issue(
                "required_reference_missing" if is_missing else "unknown_project_reference",
                "error",
                "required" if is_missing else "reference",
                "project_id" if identifier is not None else "project_code",
                "A project reference is required."
                if is_missing
                else "The project reference does not resolve within this company.",
                "Provide an existing project UUID or project code from this company.",
            )
        )
        return None
    return project


def _make_candidate(
    dataset_type: ImportDataset,
    values: dict[str, str | None],
    context: EntityContext,
    issues: list[dict[str, object]],
) -> Candidate | None:
    references: list[object] = []
    try:
        if dataset_type == "employees":
            department = _department_reference(values, context, issues)
            if department is not None:
                references.append(department)
            status = values.get("employment_status")
            if status is None:
                issues.append(
                    _issue(
                        "default_applied",
                        "warning",
                        "default",
                        "employment_status",
                        "Employment status defaults to active.",
                        "Map employment_status to use a different status, or "
                        "acknowledge this warning.",
                    )
                )
            employee_body = EmployeeCreate(
                employee_code=values.get("employee_code"),
                full_name=values.get("full_name"),
                job_title=values.get("job_title"),
                department_id=department.id if department is not None else None,
                employment_status=status or "active",
                employment_start_date=values.get("employment_start_date"),
                employment_end_date=values.get("employment_end_date"),
                standard_weekly_capacity_hours=values.get("standard_weekly_capacity_hours"),
            )
            return Candidate(
                "employee",
                employee_body,
                (_key(employee_body.employee_code),),
                (_key(employee_body.employee_code),),
                references,
            )

        if dataset_type == "skills":
            skill_body = SkillCreate(name=values.get("name"), description=values.get("description"))
            return Candidate(
                "skill",
                skill_body,
                (_key(skill_body.name),),
                (_key(skill_body.name),),
                references,
            )

        if dataset_type == "employee_skills":
            employee = _employee_reference(values, context, issues, required=True)
            skill = _skill_reference(values, context, issues)
            if employee is not None:
                references.append(employee)
            if skill is not None:
                references.append(skill)
            if employee is None or skill is None:
                return None
            employee_skill_body = EmployeeSkillCreate(
                skill_id=skill.id, proficiency=values.get("proficiency")
            )
            return Candidate(
                "employee_skill",
                employee_skill_body,
                (str(employee.id), str(skill.id)),
                (employee.id, skill.id),
                references,
            )

        if dataset_type == "weekly_capacity":
            employee = _employee_reference(values, context, issues, required=True)
            if employee is not None:
                references.append(employee)
            if employee is None:
                return None
            source = values.get("source")
            if source is None:
                issues.append(
                    _issue(
                        "default_applied",
                        "warning",
                        "default",
                        "source",
                        "Capacity source defaults to manual.",
                        "Map source to calendar or manager_adjustment, or "
                        "acknowledge this warning.",
                    )
                )
            raw_week_start = values.get("week_start_date")
            try:
                parsed_date = date.fromisoformat(raw_week_start or "")
            except ValueError:
                issues.append(
                    _issue(
                        "invalid_date",
                        "error",
                        "format",
                        "week_start_date",
                        "The week start date is invalid.",
                        "Use an ISO date such as 2026-11-23.",
                    )
                )
                return None
            if parsed_date.isoweekday() != 1:
                issues.append(
                    _issue(
                        "invalid_week_start",
                        "error",
                        "business_rule",
                        "week_start_date",
                        "Weekly capacity records must start on a Monday.",
                        "Set the week start date to the Monday for that week.",
                    )
                )
            capacity_body = WeeklyCapacityCreate(
                availability_status=values.get("availability_status"),
                scheduled_hours=values.get("scheduled_hours"),
                non_project_hours=values.get("non_project_hours"),
                unavailable_hours=values.get("unavailable_hours"),
                source=source or "manual",
                reason=values.get("reason"),
            )
            return Candidate(
                "weekly_capacity",
                capacity_body,
                (str(employee.id), parsed_date.isoformat()),
                (employee.id, parsed_date),
                references,
            )

        if dataset_type == "projects":
            manager = _employee_reference(
                values,
                context,
                issues,
                required=False,
                id_field="project_manager_employee_id",
                code_field="project_manager_employee_code",
                require_active=True,
            )
            if manager is not None:
                references.append(manager)
            status = values.get("status")
            if status is None:
                issues.append(
                    _issue(
                        "default_applied",
                        "warning",
                        "default",
                        "status",
                        "Project status defaults to draft.",
                        "Map status to draft or planned, or acknowledge this warning.",
                    )
                )
            project_body = ProjectCreate(
                project_code=values.get("project_code"),
                name=values.get("name"),
                description=values.get("description"),
                status=status or "draft",
                start_date=values.get("start_date"),
                planned_end_date=values.get("planned_end_date"),
                project_manager_employee_id=manager.id if manager is not None else None,
            )
            return Candidate(
                "project",
                project_body,
                (_key(project_body.project_code),),
                (_key(project_body.project_code),),
                references,
            )

        if dataset_type == "resource_assignments":
            project = _project_reference(values, context, issues)
            employee = _employee_reference(values, context, issues, required=True)
            if project is not None:
                references.append(project)
            if employee is not None:
                references.append(employee)
            if project is None or employee is None:
                return None
            status = values.get("status")
            if status is None:
                issues.append(
                    _issue(
                        "default_applied",
                        "warning",
                        "default",
                        "status",
                        "Assignment status defaults to proposed.",
                        "Map status to proposed or committed, or acknowledge this warning.",
                    )
                )
            source = values.get("source")
            if source is None:
                issues.append(
                    _issue(
                        "default_applied",
                        "warning",
                        "default",
                        "source",
                        "Assignment source defaults to manual.",
                        "Map source to manager_adjustment if appropriate, or "
                        "acknowledge this warning.",
                    )
                )
            assignment_body = ResourceAssignmentCreate(
                employee_id=employee.id,
                start_date=values.get("start_date"),
                end_date=values.get("end_date"),
                planned_hours_per_week=values.get("planned_hours_per_week"),
                status=status or "proposed",
                notes=values.get("notes"),
                source=source or "manual",
            )
            if project.status in {"completed", "cancelled"}:
                issues.append(
                    _issue(
                        "project_not_assignable",
                        "error",
                        "business_rule",
                        "project_code",
                        "Completed or cancelled projects cannot receive assignments.",
                        "Choose a project that is not completed or cancelled.",
                    )
                )
            if assignment_body.status == "committed":
                if project.status not in {"planned", "active"}:
                    issues.append(
                        _issue(
                            "project_not_committable",
                            "error",
                            "business_rule",
                            "status",
                            "Only planned or active projects can have committed assignments.",
                            "Use proposed status or move the project through an allowed "
                            "lifecycle transition.",
                        )
                    )
                if employee.employment_status != "active":
                    issues.append(
                        _issue(
                            "employee_not_active",
                            "error",
                            "business_rule",
                            "employee_code",
                            "Committed assignments require an active employee.",
                            "Use an active employee or change status through an authorized "
                            "workflow.",
                        )
                    )
                if (
                    employee.employment_start_date
                    and assignment_body.start_date < employee.employment_start_date
                ):
                    issues.append(
                        _issue(
                            "assignment_before_employment",
                            "error",
                            "business_rule",
                            "start_date",
                            "The assignment starts before the employee's employment date.",
                            "Move the start date into the employment period or "
                            "correct the source date.",
                        )
                    )
                if (
                    employee.employment_end_date
                    and assignment_body.end_date > employee.employment_end_date
                ):
                    issues.append(
                        _issue(
                            "assignment_after_employment",
                            "error",
                            "business_rule",
                            "end_date",
                            "The assignment ends after the employee's employment date.",
                            "Move the end date into the employment period or "
                            "correct the source date.",
                        )
                    )
            return Candidate(
                "resource_assignment",
                assignment_body,
                (
                    str(project.id),
                    str(employee.id),
                    assignment_body.start_date.isoformat(),
                    assignment_body.end_date.isoformat(),
                ),
                (project.id, employee.id, assignment_body.start_date, assignment_body.end_date),
                references,
            )
    except ValidationError as exc:
        issues.extend(_pydantic_issues(exc))
        return None
    except (TypeError, ValueError, OverflowError):
        issues.append(
            _issue(
                "invalid_value",
                "error",
                "format",
                None,
                "One or more values could not be parsed for this dataset.",
                "Correct the row to match the dataset field requirements and validate again.",
            )
        )
        return None
    return None


def _query_existing_relations(
    session: Session, import_record: DataImport, candidates: list[Candidate]
) -> dict[tuple[str, ...], object]:
    dataset_type = cast(ImportDataset, import_record.dataset_type)
    existing: dict[tuple[str, ...], object] = {}
    if dataset_type == "employee_skills":
        employee_skill_keys = {
            cast(tuple[UUID, UUID], candidate.lookup_key) for candidate in candidates
        }
        for employee_skill_chunk in _chunks(employee_skill_keys):
            employee_skill_rows = session.scalars(
                select(EmployeeSkill).where(
                    EmployeeSkill.tenant_id == import_record.tenant_id,
                    EmployeeSkill.company_id == import_record.company_id,
                    tuple_(EmployeeSkill.employee_id, EmployeeSkill.skill_id).in_(
                        employee_skill_chunk
                    ),
                )
            )
            for employee_skill in employee_skill_rows:
                existing[(str(employee_skill.employee_id), str(employee_skill.skill_id))] = (
                    employee_skill
                )
    elif dataset_type == "weekly_capacity":
        capacity_keys = {cast(tuple[UUID, date], candidate.lookup_key) for candidate in candidates}
        for capacity_chunk in _chunks(capacity_keys):
            capacity_rows = session.scalars(
                select(EmployeeWeeklyCapacity).where(
                    EmployeeWeeklyCapacity.tenant_id == import_record.tenant_id,
                    EmployeeWeeklyCapacity.company_id == import_record.company_id,
                    tuple_(
                        EmployeeWeeklyCapacity.employee_id,
                        EmployeeWeeklyCapacity.week_start_date,
                    ).in_(capacity_chunk),
                )
            )
            for capacity in capacity_rows:
                capacity_key = (str(capacity.employee_id), capacity.week_start_date.isoformat())
                prior = existing.get(capacity_key)
                if prior is None or capacity.version > cast(EmployeeWeeklyCapacity, prior).version:
                    existing[capacity_key] = capacity
    elif dataset_type == "resource_assignments":
        assignment_keys = {
            cast(tuple[UUID, UUID, date, date], candidate.lookup_key) for candidate in candidates
        }
        for assignment_chunk in _chunks(assignment_keys):
            assignment_rows = session.scalars(
                select(ResourceAssignment).where(
                    ResourceAssignment.tenant_id == import_record.tenant_id,
                    ResourceAssignment.company_id == import_record.company_id,
                    tuple_(
                        ResourceAssignment.project_id,
                        ResourceAssignment.employee_id,
                        ResourceAssignment.start_date,
                        ResourceAssignment.end_date,
                    ).in_(assignment_chunk),
                )
            )
            for assignment in assignment_rows:
                assignment_key = (
                    str(assignment.project_id),
                    str(assignment.employee_id),
                    assignment.start_date.isoformat(),
                    assignment.end_date.isoformat(),
                )
                existing[assignment_key] = assignment
    return existing


def _existing_for_candidate(
    dataset_type: ImportDataset,
    candidate: Candidate,
    context: EntityContext,
    relation_existing: dict[tuple[str, ...], object],
) -> object | None:
    if dataset_type == "employees":
        return context.employees_by_code.get(candidate.identity[0])
    if dataset_type == "skills":
        return context.skills_by_name.get(candidate.identity[0])
    if dataset_type == "projects":
        return context.projects_by_code.get(candidate.identity[0])
    return relation_existing.get(candidate.identity)


def _json_value(value: object) -> object:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _entity_fingerprint(entity: object) -> str:
    table = getattr(entity, "__table__", None)
    if table is None:
        return ""
    values = {column.key: _json_value(getattr(entity, column.key)) for column in table.columns}
    encoded = json.dumps(values, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _context_fingerprint(references: list[object], existing: object | None) -> str:
    fingerprints = [_entity_fingerprint(entity) for entity in references]
    if existing is not None:
        fingerprints.append(_entity_fingerprint(existing))
    payload = json.dumps(sorted(fingerprints), separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _body_json(candidate: Candidate | None) -> object:
    if candidate is None:
        return None
    return candidate.body.model_dump(mode="json", exclude_unset=False)


def _validation_hash(import_record: DataImport, planned_rows: list[PlannedRow]) -> str:
    snapshot: list[dict[str, object]] = []
    for plan in planned_rows:
        snapshot.append(
            {
                "row_id": str(plan.source_row.id),
                "row_hash": plan.source_row.row_hash,
                "status": plan.status,
                "issues": plan.issues,
                "candidate": _body_json(plan.candidate),
                "identity": list(plan.candidate.identity) if plan.candidate else None,
                "canonical_id": str(plan.canonical_id) if plan.canonical_id else None,
                "context_fingerprint": plan.context_fingerprint,
            }
        )
    payload = {
        "file_hash": import_record.file_hash,
        "mapping_version": import_record.mapping_version,
        "dataset_type": import_record.dataset_type,
        "selected_worksheet": import_record.selected_worksheet,
        "field_mapping": import_record.field_mapping,
        "ignored_columns": import_record.ignored_columns,
        "rows": snapshot,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_staged_import(session: Session, import_record: DataImport) -> ValidationResult:
    if import_record.dataset_type not in DATASETS or not import_record.selected_worksheet:
        raise ApiError(
            409, "import_mapping_required", "Choose a dataset and complete its field mapping first."
        )
    dataset_type = cast(ImportDataset, import_record.dataset_type)
    field_mapping = import_record.field_mapping
    if not isinstance(field_mapping, dict):
        raise ApiError(
            409, "import_mapping_required", "Choose a dataset and complete its field mapping first."
        )
    worksheets = import_record.inspection.get("worksheets", [])
    if not isinstance(worksheets, list):
        raise ApiError(409, "import_snapshot_invalid", "The staged worksheet metadata is invalid.")
    sheet = next(
        (
            item
            for item in worksheets
            if isinstance(item, dict) and item.get("name") == import_record.selected_worksheet
        ),
        None,
    )
    if not isinstance(sheet, dict) or not isinstance(sheet.get("headers"), list):
        raise ApiError(
            409,
            "import_snapshot_invalid",
            "The selected worksheet is not present in the staged file.",
        )
    headers = cast(list[str], sheet["headers"])
    rows = list(
        session.scalars(
            select(DataImportRow)
            .where(
                DataImportRow.tenant_id == import_record.tenant_id,
                DataImportRow.company_id == import_record.company_id,
                DataImportRow.import_id == import_record.id,
                DataImportRow.worksheet_name == import_record.selected_worksheet,
            )
            .order_by(DataImportRow.row_number, DataImportRow.id)
        )
    )
    raw_rows = [
        (source_row, _row_fields(source_row, headers, field_mapping)[1]) for source_row in rows
    ]
    keys = _collect_context_keys(raw_rows, dataset_type)
    context = _load_context(session, import_record.tenant_id, import_record.company_id, keys)

    plans: list[PlannedRow] = []
    for source_row, values in raw_rows:
        issues: list[dict[str, object]] = []
        candidate = _make_candidate(dataset_type, values, context, issues)
        # Preserve reference fingerprints even for rows with unrelated field errors.
        references: list[object] = []
        if candidate is not None:
            references = candidate.references
        else:
            for value in values.values():
                if value is None:
                    continue
                try:
                    identifier = UUID(value)
                except ValueError:
                    continue
                for lookup in (
                    context.employees_by_id,
                    context.departments_by_id,
                    context.skills_by_id,
                    context.projects_by_id,
                ):
                    if identifier in lookup:
                        references.append(lookup[identifier])
                        break
        plans.append(
            PlannedRow(
                source_row=source_row,
                mapped_values=values,
                issues=issues,
                candidate=candidate,
                status="invalid"
                if any(issue["severity"] == "error" for issue in issues)
                else "valid",
                context_fingerprint=_context_fingerprint(references, None),
            )
        )

    candidates = [plan.candidate for plan in plans if plan.candidate is not None]
    relation_existing = _query_existing_relations(session, import_record, candidates)
    seen: dict[tuple[str, ...], UUID | None] = {}
    for plan in plans:
        candidate = plan.candidate
        if candidate is None or any(issue["severity"] == "error" for issue in plan.issues):
            plan.status = "invalid"
            plan.source_row.canonical_type = None
            plan.source_row.canonical_id = None
            plan.source_row.issues = plan.issues
            plan.source_row.row_status = plan.status
            continue
        existing = _existing_for_candidate(dataset_type, candidate, context, relation_existing)
        plan.existing_entity = existing
        if existing is not None:
            plan.canonical_id = cast(CanonicalEntity, existing).id
        plan.context_fingerprint = _context_fingerprint(candidate.references, existing)
        if candidate.identity in seen:
            plan.status = "duplicate"
            plan.canonical_id = seen[candidate.identity] or plan.canonical_id
            plan.issues.append(
                _issue(
                    "duplicate_in_file",
                    "warning",
                    "duplicate",
                    None,
                    "An earlier valid row has the same canonical identity; "
                    "this row will be skipped.",
                    "Keep one row for this identity. Imports never overwrite existing records.",
                )
            )
        elif existing is not None:
            plan.status = "duplicate"
            plan.issues.append(
                _issue(
                    "duplicate_existing",
                    "warning",
                    "duplicate",
                    None,
                    "A record with this identity exists; this row will be skipped without updates.",
                    "Review the existing record separately; imports do not update duplicates.",
                )
            )
        else:
            plan.status = (
                "warning"
                if any(issue["severity"] == "warning" for issue in plan.issues)
                else "valid"
            )
        seen.setdefault(candidate.identity, plan.canonical_id)
        plan.source_row.canonical_type = candidate.canonical_type
        plan.source_row.canonical_id = plan.canonical_id
        plan.source_row.issues = plan.issues
        plan.source_row.row_status = plan.status

    issue_counter: Counter[tuple[str, str]] = Counter()
    for plan in plans:
        for issue in plan.issues:
            issue_counter[(str(issue["code"]), str(issue["severity"]))] += 1
    total_rows = len(plans)
    invalid_rows = sum(plan.status == "invalid" for plan in plans)
    duplicate_rows = sum(plan.status == "duplicate" for plan in plans)
    valid_rows = total_rows - invalid_rows
    warning_rows = sum(
        any(issue["severity"] == "warning" for issue in plan.issues) for plan in plans
    )
    eligible_rows = sum(plan.status in {"valid", "warning"} for plan in plans)
    validation_hash = _validation_hash(import_record, plans)
    report: dict[str, object] = {
        "totals": {
            "total_rows": total_rows,
            "valid_rows": valid_rows,
            "invalid_rows": invalid_rows,
            "warning_rows": warning_rows,
            "duplicate_rows": duplicate_rows,
            "eligible_rows": eligible_rows,
        },
        "issue_counts": [
            {"code": code, "severity": severity, "count": count}
            for (code, severity), count in sorted(issue_counter.items())
        ],
        "validation_hash": validation_hash,
    }
    import_record.total_rows = total_rows
    import_record.valid_rows = valid_rows
    import_record.invalid_rows = invalid_rows
    import_record.warning_rows = warning_rows
    import_record.duplicate_rows = duplicate_rows
    import_record.eligible_rows = eligible_rows
    import_record.skipped_rows = 0
    import_record.created_rows = 0
    import_record.updated_rows = 0
    import_record.validation_hash = validation_hash
    import_record.validation_report = report
    import_record.status = "ready" if invalid_rows == 0 and eligible_rows > 0 else "validated"
    return ValidationResult(
        validation_hash=validation_hash,
        report=report,
        total_rows=total_rows,
        valid_rows=valid_rows,
        invalid_rows=invalid_rows,
        warning_rows=warning_rows,
        duplicate_rows=duplicate_rows,
        eligible_rows=eligible_rows,
        rows=plans,
    )
