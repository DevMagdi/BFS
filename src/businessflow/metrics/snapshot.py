from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from typing import cast

from businessflow.metrics.catalog import SNAPSHOT_SCHEMA_VERSION
from businessflow.metrics.types import (
    AssignmentInput,
    CapacityInput,
    EmployeeInput,
    EmployeeSkillInput,
    MetricInputSnapshot,
    SkillInput,
    SourceReference,
)

HOURS_QUANTUM = Decimal("0.01")


def decimal_text(value: Decimal) -> str:
    """Serialize business hours without binary floats and at the canonical scale."""
    return format(value.quantize(HOURS_QUANTUM), "f")


def canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_hash(payload: object) -> str:
    return sha256(canonical_json_bytes(payload)).hexdigest()


def _source_to_json(reference: SourceReference) -> dict[str, object]:
    return {
        "import_id": reference.import_id,
        "import_status": reference.import_status,
        "dataset_type": reference.dataset_type,
        "mapping_version": reference.mapping_version,
        "file_hash": reference.file_hash,
        "source_row_id": reference.source_row_id,
        "worksheet_name": reference.worksheet_name,
        "row_number": reference.row_number,
        "row_hash": reference.row_hash,
        "canonical_fingerprint_at_import": reference.canonical_fingerprint_at_import,
    }


def _sorted_lineage(references: tuple[SourceReference, ...]) -> list[dict[str, object]]:
    return [
        _source_to_json(reference)
        for reference in sorted(
            references,
            key=lambda item: (
                item.import_id,
                item.worksheet_name,
                item.row_number,
                item.source_row_id,
            ),
        )
    ]


def serialize_input_snapshot(snapshot: MetricInputSnapshot) -> dict[str, object]:
    """Return the exact JSON-compatible input persisted with a calculation run."""
    employees = sorted(
        snapshot.employees, key=lambda item: (item.employee_code.casefold(), item.id)
    )
    capacities = sorted(
        snapshot.capacities,
        key=lambda item: (item.week_start_date, item.employee_id, item.version, item.id),
    )
    assignments = sorted(
        snapshot.assignments,
        key=lambda item: (
            item.start_date,
            item.end_date,
            item.project_id,
            item.employee_id,
            item.id,
        ),
    )
    skills = sorted(snapshot.skills, key=lambda item: (item.name.casefold(), item.id))
    employee_skills = sorted(
        snapshot.employee_skills,
        key=lambda item: (item.employee_id, item.skill_id, item.id),
    )
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "scope": {
            "tenant_id": snapshot.tenant_id,
            "company_id": snapshot.company_id,
            "company_timezone": snapshot.company_timezone,
        },
        "period": {
            "start_week_date": snapshot.start_week_date.isoformat(),
            "week_count": snapshot.week_count,
            "week_convention": "Monday-Sunday",
        },
        "employees": [
            {
                "id": item.id,
                "employee_code": item.employee_code,
                "employment_status": item.employment_status,
                "lineage": _sorted_lineage(item.lineage),
            }
            for item in employees
        ],
        "weekly_capacity": [
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "week_start_date": item.week_start_date.isoformat(),
                "version": item.version,
                "supersedes_version": item.supersedes_version,
                "availability_status": item.availability_status,
                "scheduled_hours": (
                    decimal_text(item.scheduled_hours) if item.scheduled_hours is not None else None
                ),
                "non_project_hours": (
                    decimal_text(item.non_project_hours)
                    if item.non_project_hours is not None
                    else None
                ),
                "unavailable_hours": (
                    decimal_text(item.unavailable_hours)
                    if item.unavailable_hours is not None
                    else None
                ),
                "source": item.source,
                "created_at": item.created_at,
                "lineage": _sorted_lineage(item.lineage),
            }
            for item in capacities
        ],
        "resource_assignments": [
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "project_id": item.project_id,
                "project_code": item.project_code,
                "project_status": item.project_status,
                "start_date": item.start_date.isoformat(),
                "end_date": item.end_date.isoformat(),
                "planned_hours_per_week": decimal_text(item.planned_hours_per_week),
                "status": item.status,
                "source": item.source,
                "created_at": item.created_at,
                "updated_at": item.updated_at,
                "lineage": _sorted_lineage(item.lineage),
            }
            for item in assignments
        ],
        "skills": [
            {
                "id": item.id,
                "name": item.name,
                "status": item.status,
                "lineage": _sorted_lineage(item.lineage),
            }
            for item in skills
        ],
        "employee_skills": [
            {
                "id": item.id,
                "employee_id": item.employee_id,
                "skill_id": item.skill_id,
                "proficiency": item.proficiency,
                "status": item.status,
                "lineage": _sorted_lineage(item.lineage),
            }
            for item in employee_skills
        ],
    }


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"snapshot {name} must be an object")
    return cast(Mapping[str, object], value)


def _string(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f"snapshot field {key} must be a string")
    return value


def _optional_string(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is not None and not isinstance(value, str):
        raise ValueError(f"snapshot field {key} must be a string or null")
    return value


def _integer(mapping: Mapping[str, object], key: str) -> int:
    value = mapping.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"snapshot field {key} must be an integer")
    return value


def _optional_integer(mapping: Mapping[str, object], key: str) -> int | None:
    value = mapping.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"snapshot field {key} must be an integer or null")
    return value


def _date(mapping: Mapping[str, object], key: str) -> date:
    return date.fromisoformat(_string(mapping, key))


def _decimal(mapping: Mapping[str, object], key: str) -> Decimal:
    value = mapping.get(key)
    if not isinstance(value, str):
        raise ValueError(f"snapshot field {key} must be a decimal string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"snapshot field {key} is not a valid decimal") from exc


def _optional_decimal(mapping: Mapping[str, object], key: str) -> Decimal | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"snapshot field {key} must be a decimal string or null")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"snapshot field {key} is not a valid decimal") from exc


def _list(value: object, name: str) -> list[object]:
    if not isinstance(value, list):
        raise ValueError(f"snapshot {name} must be an array")
    return cast(list[object], value)


def _lineage(value: object) -> tuple[SourceReference, ...]:
    result: list[SourceReference] = []
    for raw_reference in _list(value, "lineage"):
        reference = _mapping(raw_reference, "lineage item")
        result.append(
            SourceReference(
                import_id=_string(reference, "import_id"),
                import_status=_string(reference, "import_status"),
                dataset_type=_optional_string(reference, "dataset_type"),
                mapping_version=_integer(reference, "mapping_version"),
                file_hash=_string(reference, "file_hash"),
                source_row_id=_string(reference, "source_row_id"),
                worksheet_name=_string(reference, "worksheet_name"),
                row_number=_integer(reference, "row_number"),
                row_hash=_string(reference, "row_hash"),
                canonical_fingerprint_at_import=_string(
                    reference, "canonical_fingerprint_at_import"
                ),
            )
        )
    return tuple(result)


def deserialize_input_snapshot(payload: Mapping[str, object]) -> MetricInputSnapshot:
    """Validate and reconstruct a snapshot read from immutable run persistence."""
    if payload.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise ValueError("unsupported metric input snapshot schema version")
    scope = _mapping(payload.get("scope"), "scope")
    period = _mapping(payload.get("period"), "period")
    if _string(period, "week_convention") != "Monday-Sunday":
        raise ValueError("unsupported metric week convention")

    employees = tuple(
        EmployeeInput(
            id=_string(item, "id"),
            employee_code=_string(item, "employee_code"),
            employment_status=_string(item, "employment_status"),
            lineage=_lineage(item.get("lineage")),
        )
        for item in (
            _mapping(raw, "employee") for raw in _list(payload.get("employees"), "employees")
        )
    )
    capacities = tuple(
        CapacityInput(
            id=_string(item, "id"),
            employee_id=_string(item, "employee_id"),
            week_start_date=_date(item, "week_start_date"),
            version=_integer(item, "version"),
            supersedes_version=_optional_integer(item, "supersedes_version"),
            availability_status=_string(item, "availability_status"),
            scheduled_hours=_optional_decimal(item, "scheduled_hours"),
            non_project_hours=_optional_decimal(item, "non_project_hours"),
            unavailable_hours=_optional_decimal(item, "unavailable_hours"),
            source=_string(item, "source"),
            created_at=_string(item, "created_at"),
            lineage=_lineage(item.get("lineage")),
        )
        for item in (
            _mapping(raw, "weekly capacity")
            for raw in _list(payload.get("weekly_capacity"), "weekly_capacity")
        )
    )
    assignments = tuple(
        AssignmentInput(
            id=_string(item, "id"),
            employee_id=_string(item, "employee_id"),
            project_id=_string(item, "project_id"),
            project_code=_string(item, "project_code"),
            project_status=_string(item, "project_status"),
            start_date=_date(item, "start_date"),
            end_date=_date(item, "end_date"),
            planned_hours_per_week=_decimal(item, "planned_hours_per_week"),
            status=_string(item, "status"),
            source=_string(item, "source"),
            created_at=_string(item, "created_at"),
            updated_at=_string(item, "updated_at"),
            lineage=_lineage(item.get("lineage")),
        )
        for item in (
            _mapping(raw, "resource assignment")
            for raw in _list(payload.get("resource_assignments"), "resource_assignments")
        )
    )
    skills = tuple(
        SkillInput(
            id=_string(item, "id"),
            name=_string(item, "name"),
            status=_string(item, "status"),
            lineage=_lineage(item.get("lineage")),
        )
        for item in (_mapping(raw, "skill") for raw in _list(payload.get("skills"), "skills"))
    )
    employee_skills = tuple(
        EmployeeSkillInput(
            id=_string(item, "id"),
            employee_id=_string(item, "employee_id"),
            skill_id=_string(item, "skill_id"),
            proficiency=_integer(item, "proficiency"),
            status=_string(item, "status"),
            lineage=_lineage(item.get("lineage")),
        )
        for item in (
            _mapping(raw, "employee skill")
            for raw in _list(payload.get("employee_skills"), "employee_skills")
        )
    )
    return MetricInputSnapshot(
        tenant_id=_string(scope, "tenant_id"),
        company_id=_string(scope, "company_id"),
        company_timezone=_string(scope, "company_timezone"),
        start_week_date=_date(period, "start_week_date"),
        week_count=_integer(period, "week_count"),
        employees=employees,
        capacities=capacities,
        assignments=assignments,
        skills=skills,
        employee_skills=employee_skills,
    )
