from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest

from businessflow.metrics.calculation import calculate_metrics
from businessflow.metrics.snapshot import (
    content_hash,
    deserialize_input_snapshot,
    serialize_input_snapshot,
)
from businessflow.metrics.types import (
    AssignmentInput,
    CapacityInput,
    EmployeeInput,
    EmployeeSkillInput,
    MetricInputSnapshot,
    SkillInput,
    SourceReference,
)

START = date(2026, 11, 16)


def _employee(code: str, *, status: str = "active") -> EmployeeInput:
    return EmployeeInput(id=f"employee-{code}", employee_code=code, employment_status=status)


def _capacity(
    employee: EmployeeInput,
    *,
    week: date = START,
    status: str = "confirmed",
    version: int = 1,
    scheduled: str | None = "40.00",
    non_project: str | None = "8.00",
    unavailable: str | None = "2.00",
    lineage: tuple[SourceReference, ...] = (),
) -> CapacityInput:
    return CapacityInput(
        id=f"capacity-{employee.id}-{week.isoformat()}-{version}",
        employee_id=employee.id,
        week_start_date=week,
        version=version,
        supersedes_version=version - 1 if version > 1 else None,
        availability_status=status,
        scheduled_hours=Decimal(scheduled) if scheduled is not None else None,
        non_project_hours=Decimal(non_project) if non_project is not None else None,
        unavailable_hours=Decimal(unavailable) if unavailable is not None else None,
        source="manual",
        created_at="2026-11-01T00:00:00+00:00",
        lineage=lineage,
    )


def _assignment(
    employee: EmployeeInput,
    *,
    start: date = START,
    end: date = date(2026, 11, 22),
    hours: str = "12.00",
    status: str = "committed",
    project_status: str = "planned",
    assignment_id: str = "assignment-1",
) -> AssignmentInput:
    return AssignmentInput(
        id=assignment_id,
        employee_id=employee.id,
        project_id=f"project-{assignment_id}",
        project_code=f"P-{assignment_id}",
        project_status=project_status,
        start_date=start,
        end_date=end,
        planned_hours_per_week=Decimal(hours),
        status=status,
        source="manual",
        created_at="2026-11-01T00:00:00+00:00",
        updated_at="2026-11-01T00:00:00+00:00",
    )


def _snapshot(
    *,
    employees: tuple[EmployeeInput, ...],
    capacities: tuple[CapacityInput, ...] = (),
    assignments: tuple[AssignmentInput, ...] = (),
    skills: tuple[SkillInput, ...] = (),
    employee_skills: tuple[EmployeeSkillInput, ...] = (),
    weeks: int = 1,
) -> MetricInputSnapshot:
    return MetricInputSnapshot(
        tenant_id="tenant-1",
        company_id="company-1",
        company_timezone="Africa/Cairo",
        start_week_date=START,
        week_count=weeks,
        employees=employees,
        capacities=capacities,
        assignments=assignments,
        skills=skills,
        employee_skills=employee_skills,
    )


def test_confirmed_capacity_uses_exact_formula_and_surfaces_recorded_overload():
    employee = _employee("EMP-1")
    capacity = _capacity(employee)
    assignment = _assignment(employee, hours="30.01")
    result = calculate_metrics(
        _snapshot(employees=(employee,), capacities=(capacity,), assignments=(assignment,))
    )

    week = result["weekly"][0]
    assert week["capacity"]["complete_available_hours"] == "30.00"
    assert week["assignments"]["active_employee_committed_hours_per_week"] == "30.01"
    assert week["comparison"]["capacity_minus_recorded_commitments_hours"] == "-0.01"
    assert result["reliability_status"] == "complete"
    assert len(result["confirmed_overloads"]) == 1
    assert result["confirmed_overloads"][0]["overload_hours"] == "0.01"
    assert result["confirmed_overloads"][0]["reliability_state"] == "confirmed_capacity_overload"
    assert "assignment_completeness_not_attested" in week["comparison"]["reasons"]


def test_latest_unknown_capacity_does_not_fall_back_to_an_older_confirmed_version():
    employee = _employee("EMP-1")
    older = _capacity(employee, version=1)
    latest_unknown = _capacity(
        employee,
        version=2,
        status="unknown",
        scheduled=None,
        non_project=None,
        unavailable=None,
    )
    assignment = _assignment(employee, hours="8.00")
    result = calculate_metrics(
        _snapshot(
            employees=(employee,),
            capacities=(older, latest_unknown),
            assignments=(assignment,),
        )
    )

    week = result["weekly"][0]
    assert week["capacity"]["unknown_employee_count"] == 1
    assert week["capacity"]["complete_available_hours"] is None
    assert week["comparison"]["capacity_minus_recorded_commitments_hours"] is None
    assert week["comparison"]["unresolved_employee_comparison_count"] == 1
    assert result["confirmed_overloads"] == []
    unresolved = result["unresolved_employee_comparisons"][0]["weeks"][0]
    assert unresolved["capacity_state"] == "unknown"
    assert "weekly_capacity_explicitly_unknown_for_active_employee" in unresolved["reasons"]


def test_missing_and_unknown_capacity_are_not_zero_and_no_assignments_are_not_zero_hours():
    employee = _employee("EMP-1")
    unknown = _capacity(
        employee,
        status="unknown",
        scheduled=None,
        non_project=None,
        unavailable=None,
    )
    result = calculate_metrics(_snapshot(employees=(employee,), capacities=(unknown,)))
    week = result["weekly"][0]
    assert week["capacity"]["confirmed_available_hours_subtotal"] is None
    assert week["capacity"]["estimated_available_hours_subtotal"] is None
    assert week["capacity"]["complete_available_hours"] is None
    assert week["assignments"]["committed_assignment_count"] == 0
    assert week["assignments"]["committed_hours_per_week"] is None
    assert week["comparison"]["capacity_minus_recorded_commitments_hours"] is None
    assert week["comparison"]["state"] == "not_recorded"
    assert result["reliability_status"] == "unavailable"

    missing = calculate_metrics(_snapshot(employees=(employee,)))
    assert missing["weekly"][0]["capacity"]["missing_employee_count"] == 1
    assert missing["weekly"][0]["capacity"]["complete_available_hours"] is None
    assert missing["reliability"]["capacity"]["reasons"] == [
        "weekly_capacity_missing_for_active_employees"
    ]


def test_estimated_capacity_overload_is_separate_from_confirmed_overload():
    employee = _employee("EMP-1")
    estimate = _capacity(employee, status="estimated")
    assignment = _assignment(employee, hours="32.00")
    result = calculate_metrics(
        _snapshot(employees=(employee,), capacities=(estimate,), assignments=(assignment,))
    )

    assert result["reliability_status"] == "partial"
    assert result["confirmed_overloads"] == []
    assert result["estimated_overloads"][0]["overload_hours"] == "2.00"
    assert result["estimated_overloads"][0]["reliability_state"] == "estimated_capacity_overload"
    assert result["weekly"][0]["capacity"]["availability_status"] == "estimated"
    assert "weekly_capacity_contains_estimates" in result["reliability"]["capacity"]["reasons"]


def test_assignments_are_time_phased_by_monday_week_and_proposed_hours_stay_separate():
    employee = _employee("EMP-1")
    capacity_week_1 = _capacity(employee, week=START)
    capacity_week_2 = _capacity(employee, week=date(2026, 11, 23))
    partial_week_assignment = _assignment(
        employee,
        start=date(2026, 11, 20),
        end=date(2026, 11, 25),
        hours="12.00",
        assignment_id="cross-week",
    )
    proposal = _assignment(
        employee,
        start=START,
        end=date(2026, 11, 22),
        hours="8.00",
        status="proposed",
        assignment_id="proposal",
    )
    result = calculate_metrics(
        _snapshot(
            employees=(employee,),
            capacities=(capacity_week_1, capacity_week_2),
            assignments=(partial_week_assignment, proposal),
            weeks=2,
        )
    )

    first, second = result["weekly"]
    assert first["week_start_date"] == "2026-11-16"
    assert first["week_end_date"] == "2026-11-22"
    assert second["week_start_date"] == "2026-11-23"
    assert first["assignments"]["committed_hours_per_week"] == "12.00"
    assert second["assignments"]["committed_hours_per_week"] == "12.00"
    assert first["assignments"]["proposed_hours_per_week"] == "8.00"
    assert second["assignments"]["proposed_hours_per_week"] is None
    assert first["comparison"]["capacity_minus_recorded_commitments_hours"] == "18.00"
    assert second["comparison"]["capacity_minus_recorded_commitments_hours"] == "18.00"
    assert result["confirmed_overloads"] == []


def test_skill_inventory_reports_recorded_proficiency_without_claiming_requirements():
    employee_1 = _employee("EMP-1")
    employee_2 = _employee("EMP-2")
    python = SkillInput(id="skill-python", name="Python", status="active")
    rust = SkillInput(id="skill-rust", name="Rust", status="active")
    skill_record = EmployeeSkillInput(
        id="employee-skill-1",
        employee_id=employee_1.id,
        skill_id=python.id,
        proficiency=4,
        status="active",
    )
    result = calculate_metrics(
        _snapshot(
            employees=(employee_1, employee_2),
            skills=(python, rust),
            employee_skills=(skill_record,),
        )
    )

    inventory = result["skill_inventory"]
    assert inventory["state"] == "partial"
    assert inventory["active_skill_catalog_count"] == 2
    assert inventory["active_employee_skill_record_count"] == 1
    assert inventory["active_employees_with_skill_records"] == 1
    assert inventory["active_employees_without_skill_records"] == 1
    assert inventory["skills"][0]["skill_name"] == "Python"
    assert inventory["skills"][0]["proficiency_counts"]["4"] == 1
    assert inventory["skills"][1]["inventory_state"] == "not_recorded"
    assert "active_employees_missing_active_skill_records" in inventory["reasons"]


def test_no_skill_catalog_is_unavailable_and_proposed_assignments_do_not_create_commitments():
    employee = _employee("EMP-1")
    capacity = _capacity(employee)
    proposal = _assignment(employee, status="proposed", hours="15.00")
    result = calculate_metrics(
        _snapshot(employees=(employee,), capacities=(capacity,), assignments=(proposal,))
    )
    week = result["weekly"][0]

    assert result["skill_inventory"]["state"] == "unavailable"
    assert week["assignments"]["committed_state"] == "not_recorded"
    assert week["assignments"]["committed_hours_per_week"] is None
    assert week["assignments"]["proposed_hours_per_week"] == "15.00"
    assert week["comparison"]["state"] == "not_recorded"
    assert result["confirmed_overloads"] == []


def test_conflicting_latest_capacity_versions_are_unresolved_not_arbitrarily_selected():
    employee = _employee("EMP-1")
    first = _capacity(employee, version=1)
    conflicting_latest = replace(first, id="capacity-conflict")
    result = calculate_metrics(
        _snapshot(employees=(employee,), capacities=(first, conflicting_latest))
    )
    capacity = result["weekly"][0]["capacity"]
    assert capacity["invalid_employee_count"] == 1
    assert capacity["complete_available_hours"] is None
    assert capacity["reasons"] == ["weekly_capacity_record_invalid_or_conflicting"]


def test_serialized_snapshot_replays_deterministically_and_hashes_canonical_order():
    employee_1 = _employee("EMP-1")
    employee_2 = _employee("EMP-2")
    source = SourceReference(
        import_id="import-1",
        import_status="applied",
        dataset_type="weekly_capacity",
        mapping_version=2,
        file_hash="a" * 64,
        source_row_id="source-row-1",
        worksheet_name="CSV",
        row_number=2,
        row_hash="b" * 64,
        canonical_fingerprint_at_import="c" * 64,
    )
    first = _capacity(employee_1, lineage=(source,))
    second = _capacity(employee_2, week=date(2026, 11, 23))
    assignment = _assignment(
        employee_1, assignment_id="assignment-1", hours="9.99", status="committed"
    )
    skill = SkillInput(id="skill-1", name="Python", status="active", lineage=(source,))
    relation = EmployeeSkillInput(
        id="relation-1",
        employee_id=employee_1.id,
        skill_id=skill.id,
        proficiency=3,
        status="active",
    )
    original = _snapshot(
        employees=(employee_1, employee_2),
        capacities=(first, second),
        assignments=(assignment,),
        skills=(skill,),
        employee_skills=(relation,),
        weeks=2,
    )
    reversed_order = replace(
        original,
        employees=tuple(reversed(original.employees)),
        capacities=tuple(reversed(original.capacities)),
        assignments=tuple(reversed(original.assignments)),
        skills=tuple(reversed(original.skills)),
        employee_skills=tuple(reversed(original.employee_skills)),
    )

    payload = serialize_input_snapshot(original)
    reversed_payload = serialize_input_snapshot(reversed_order)
    assert payload == reversed_payload
    assert content_hash(payload) == content_hash(reversed_payload)
    assert payload["weekly_capacity"][0]["scheduled_hours"] == "40.00"
    replay = deserialize_input_snapshot(payload)
    assert calculate_metrics(original) == calculate_metrics(replay)
    assert calculate_metrics(original)["provenance"]["distinct_import_source_row_count"] == 1


def test_monday_period_validation_is_independent_of_api_validation():
    employee = _employee("EMP-1")
    invalid = replace(_snapshot(employees=(employee,)), start_week_date=date(2026, 11, 17))
    with pytest.raises(ValueError, match="Monday"):
        calculate_metrics(invalid)
