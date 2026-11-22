from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class SourceReference:
    import_id: str
    import_status: str
    dataset_type: str | None
    mapping_version: int
    file_hash: str
    source_row_id: str
    worksheet_name: str
    row_number: int
    row_hash: str
    canonical_fingerprint_at_import: str


@dataclass(frozen=True, slots=True)
class EmployeeInput:
    id: str
    employee_code: str
    employment_status: str
    lineage: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class CapacityInput:
    id: str
    employee_id: str
    week_start_date: date
    version: int
    supersedes_version: int | None
    availability_status: str
    scheduled_hours: Decimal | None
    non_project_hours: Decimal | None
    unavailable_hours: Decimal | None
    source: str
    created_at: str
    lineage: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class AssignmentInput:
    id: str
    employee_id: str
    project_id: str
    project_code: str
    project_status: str
    start_date: date
    end_date: date
    planned_hours_per_week: Decimal
    status: str
    source: str
    created_at: str
    updated_at: str
    lineage: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class SkillInput:
    id: str
    name: str
    status: str
    lineage: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class EmployeeSkillInput:
    id: str
    employee_id: str
    skill_id: str
    proficiency: int
    status: str
    lineage: tuple[SourceReference, ...] = ()


@dataclass(frozen=True, slots=True)
class MetricInputSnapshot:
    tenant_id: str
    company_id: str
    company_timezone: str
    start_week_date: date
    week_count: int
    employees: tuple[EmployeeInput, ...]
    capacities: tuple[CapacityInput, ...]
    assignments: tuple[AssignmentInput, ...]
    skills: tuple[SkillInput, ...]
    employee_skills: tuple[EmployeeSkillInput, ...]
