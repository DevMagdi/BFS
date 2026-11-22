from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

from businessflow.metrics.catalog import (
    CALCULATION_VERSION,
    METRIC_CATALOG_STATUS,
    METRIC_CATALOG_VERSION,
    METRIC_DECISION_USE,
    METRIC_DEFINITIONS,
    metric_definition_payloads,
)
from businessflow.metrics.snapshot import decimal_text
from businessflow.metrics.types import (
    AssignmentInput,
    CapacityInput,
    EmployeeInput,
    EmployeeSkillInput,
    MetricInputSnapshot,
    SkillInput,
)

MAX_METRIC_WEEKS = 52
MAX_ACTIVE_EMPLOYEES = 1_000
MAX_ASSIGNMENTS = 10_000
MAX_ACTIVE_SKILLS = 2_000
MAX_EMPLOYEE_SKILL_RECORDS = 20_000
MAX_IMPORT_LINEAGE_REFERENCES = 50_000


def _unique_reasons(*groups: list[str]) -> list[str]:
    return list(dict.fromkeys(reason for group in groups for reason in group))


def _sum_hours(values: list[Decimal]) -> Decimal:
    return sum(values, Decimal("0.00"))


def _capacity_values(record: CapacityInput) -> tuple[str, Decimal | None]:
    values = (record.scheduled_hours, record.non_project_hours, record.unavailable_hours)
    if record.availability_status == "unknown":
        return ("unknown", None) if values == (None, None, None) else ("invalid", None)
    if record.availability_status not in {"confirmed", "estimated"} or any(
        value is None for value in values
    ):
        return "invalid", None
    scheduled, non_project, unavailable = cast(tuple[Decimal, Decimal, Decimal], values)
    available = scheduled - non_project - unavailable
    if available < 0:
        return "invalid", None
    return record.availability_status, available


def _assignment_source(item: AssignmentInput) -> dict[str, object]:
    return {
        "assignment_id": item.id,
        "project_id": item.project_id,
        "project_code": item.project_code,
        "planned_hours_per_week": decimal_text(item.planned_hours_per_week),
    }


MetricFact = EmployeeInput | CapacityInput | AssignmentInput | SkillInput | EmployeeSkillInput


def _fact_records(snapshot: MetricInputSnapshot) -> tuple[MetricFact, ...]:
    return (
        *snapshot.employees,
        *snapshot.capacities,
        *snapshot.assignments,
        *snapshot.skills,
        *snapshot.employee_skills,
    )


def _validate_input_limits(snapshot: MetricInputSnapshot) -> None:
    if len(snapshot.employees) > MAX_ACTIVE_EMPLOYEES + MAX_ASSIGNMENTS:
        raise ValueError("employee input exceeds the supported calculation limit")
    if len(snapshot.capacities) > MAX_ACTIVE_EMPLOYEES * MAX_METRIC_WEEKS:
        raise ValueError("weekly capacity input exceeds the supported calculation limit")
    if len(snapshot.assignments) > MAX_ASSIGNMENTS:
        raise ValueError("assignment input exceeds the supported calculation limit")
    if len(snapshot.skills) > MAX_ACTIVE_SKILLS:
        raise ValueError("skill input exceeds the supported calculation limit")
    if len(snapshot.employee_skills) > MAX_EMPLOYEE_SKILL_RECORDS:
        raise ValueError("employee skill input exceeds the supported calculation limit")
    lineage_count = sum(len(record.lineage) for record in _fact_records(snapshot))
    if lineage_count > MAX_IMPORT_LINEAGE_REFERENCES:
        raise ValueError("import lineage input exceeds the supported calculation limit")


def calculate_metrics(snapshot: MetricInputSnapshot) -> dict[str, object]:
    """Calculate versioned capacity, assignment-comparison, overload and skill metrics.

    This function is independent of HTTP and persistence. Input order cannot affect its output.
    """
    if snapshot.week_count < 1 or snapshot.week_count > MAX_METRIC_WEEKS:
        raise ValueError("week_count is outside the supported range")
    if snapshot.start_week_date.isoweekday() != 1:
        raise ValueError("start_week_date must be a Monday")
    _validate_input_limits(snapshot)
    try:
        period_end = snapshot.start_week_date + timedelta(days=7 * snapshot.week_count - 1)
        weeks = tuple(
            snapshot.start_week_date + timedelta(days=7 * index)
            for index in range(snapshot.week_count)
        )
    except OverflowError as exc:
        raise ValueError("metric period is outside the supported calendar range") from exc

    employees_by_id = {employee.id: employee for employee in snapshot.employees}
    if len(employees_by_id) != len(snapshot.employees):
        raise ValueError("metric input contains duplicate employee identities")
    active_employees = sorted(
        (employee for employee in snapshot.employees if employee.employment_status == "active"),
        key=lambda employee: (employee.employee_code.casefold(), employee.id),
    )
    if len(active_employees) > MAX_ACTIVE_EMPLOYEES:
        raise ValueError("active employee input exceeds the supported calculation limit")
    active_employee_ids = {employee.id for employee in active_employees}

    capacities_by_employee_week: dict[tuple[str, date], list[CapacityInput]] = defaultdict(list)
    for capacity in snapshot.capacities:
        if capacity.employee_id in active_employee_ids and capacity.week_start_date in weeks:
            capacities_by_employee_week[(capacity.employee_id, capacity.week_start_date)].append(
                capacity
            )

    assignments_by_week: dict[date, list[AssignmentInput]] = {week: [] for week in weeks}
    assignment_conflict_reasons: list[str] = []
    assignment_ids: set[str] = set()
    for assignment in snapshot.assignments:
        if assignment.id in assignment_ids:
            raise ValueError("metric input contains duplicate assignment identities")
        assignment_ids.add(assignment.id)
        if assignment.status not in {"committed", "proposed"}:
            if assignment.status not in {"completed", "cancelled"}:
                assignment_conflict_reasons.append("invalid_assignment_status")
            continue
        if assignment.end_date < assignment.start_date:
            assignment_conflict_reasons.append("invalid_assignment_period")
            continue
        if not Decimal("0") < assignment.planned_hours_per_week <= Decimal("168"):
            assignment_conflict_reasons.append("invalid_assignment_hours")
            continue
        employee = employees_by_id.get(assignment.employee_id)
        if employee is None:
            assignment_conflict_reasons.append("assignment_employee_not_in_snapshot")
        if assignment.project_status in {"completed", "cancelled"}:
            assignment_conflict_reasons.append("open_assignment_on_terminal_project")
        for week in weeks:
            week_end = week + timedelta(days=6)
            if assignment.start_date <= week_end and assignment.end_date >= week:
                assignments_by_week[week].append(assignment)

    weekly_results: list[dict[str, object]] = []
    confirmed_overloads: list[dict[str, object]] = []
    estimated_overloads: list[dict[str, object]] = []
    unresolved_employee_comparisons: dict[str, dict[str, object]] = {}
    inactive_employee_commitments: dict[tuple[str, date], list[AssignmentInput]] = defaultdict(list)
    capacity_reasons_all: list[str] = []
    any_known_capacity = False
    all_capacity_confirmed = bool(active_employees)
    any_committed_assignment = False

    for week in weeks:
        counts = {"confirmed": 0, "estimated": 0, "unknown": 0, "missing": 0, "invalid": 0}
        available_by_employee: dict[str, tuple[str, Decimal | None, CapacityInput | None]] = {}
        confirmed_values: list[Decimal] = []
        estimated_values: list[Decimal] = []
        week_capacity_reasons: list[str] = []

        for employee in active_employees:
            candidates = capacities_by_employee_week.get((employee.id, week), [])
            if not candidates:
                counts["missing"] += 1
                available_by_employee[employee.id] = ("missing", None, None)
                continue
            latest_version = max(record.version for record in candidates)
            latest_records = [record for record in candidates if record.version == latest_version]
            if len(latest_records) != 1:
                counts["invalid"] += 1
                available_by_employee[employee.id] = ("invalid", None, None)
                continue
            record = latest_records[0]
            status, available = _capacity_values(record)
            counts[status] += 1
            available_by_employee[employee.id] = (status, available, record)
            if status == "confirmed" and available is not None:
                confirmed_values.append(available)
                any_known_capacity = True
            elif status == "estimated" and available is not None:
                estimated_values.append(available)
                any_known_capacity = True

        if not active_employees:
            week_capacity_reasons.append("no_active_employees")
        if counts["missing"]:
            week_capacity_reasons.append("weekly_capacity_missing_for_active_employees")
        if counts["unknown"]:
            week_capacity_reasons.append("weekly_capacity_explicitly_unknown_for_active_employees")
        if counts["estimated"]:
            week_capacity_reasons.append("weekly_capacity_contains_estimates")
        if counts["invalid"]:
            week_capacity_reasons.append("weekly_capacity_record_invalid_or_conflicting")
        capacity_reasons_all.extend(week_capacity_reasons)

        known_count = counts["confirmed"] + counts["estimated"]
        complete_capacity = bool(active_employees) and known_count == len(active_employees)
        if not active_employees or known_count == 0:
            availability_status = "unavailable"
            week_capacity_state = "unavailable"
        elif not complete_capacity:
            availability_status = "partial"
            week_capacity_state = "partial"
        elif counts["estimated"] and counts["confirmed"]:
            availability_status = "mixed"
            week_capacity_state = "partial"
        elif counts["estimated"]:
            availability_status = "estimated"
            week_capacity_state = "partial"
        else:
            availability_status = "confirmed"
            week_capacity_state = "complete"

        complete_available_hours: Decimal | None = None
        complete_capacity_basis: str | None = None
        if complete_capacity:
            complete_available_hours = _sum_hours(confirmed_values + estimated_values)
            complete_capacity_basis = "estimated" if counts["estimated"] else "confirmed"
        if known_count < len(active_employees):
            all_capacity_confirmed = False
        if counts["estimated"] or counts["unknown"] or counts["missing"] or counts["invalid"]:
            all_capacity_confirmed = False

        capacity_result: dict[str, object] = {
            "reliability_state": week_capacity_state,
            "availability_status": availability_status,
            "active_employee_count": len(active_employees),
            "confirmed_employee_count": counts["confirmed"],
            "estimated_employee_count": counts["estimated"],
            "unknown_employee_count": counts["unknown"],
            "missing_employee_count": counts["missing"],
            "invalid_employee_count": counts["invalid"],
            "confirmed_available_hours_subtotal": (
                decimal_text(_sum_hours(confirmed_values)) if confirmed_values else None
            ),
            "estimated_available_hours_subtotal": (
                decimal_text(_sum_hours(estimated_values)) if estimated_values else None
            ),
            "complete_available_hours": (
                decimal_text(complete_available_hours)
                if complete_available_hours is not None
                else None
            ),
            "complete_capacity_basis": complete_capacity_basis,
            "reasons": _unique_reasons(week_capacity_reasons),
        }

        week_assignments = assignments_by_week[week]
        committed = [item for item in week_assignments if item.status == "committed"]
        proposed = [item for item in week_assignments if item.status == "proposed"]
        active_committed = [
            item
            for item in committed
            if employees_by_id.get(item.employee_id) is not None
            and employees_by_id[item.employee_id].employment_status == "active"
        ]
        inactive_committed = [item for item in committed if item not in active_committed]
        any_committed_assignment = any_committed_assignment or bool(committed)

        for item in inactive_committed:
            inactive_employee_commitments[(item.employee_id, week)].append(item)

        active_committed_hours = (
            _sum_hours([item.planned_hours_per_week for item in active_committed])
            if active_committed
            else None
        )
        inactive_committed_hours = (
            _sum_hours([item.planned_hours_per_week for item in inactive_committed])
            if inactive_committed
            else None
        )
        committed_hours = (
            _sum_hours([item.planned_hours_per_week for item in committed]) if committed else None
        )
        proposed_hours = (
            _sum_hours([item.planned_hours_per_week for item in proposed]) if proposed else None
        )
        assignment_result: dict[str, object] = {
            "committed_state": "recorded" if committed else "not_recorded",
            "committed_assignment_count": len(committed),
            "committed_hours_per_week": (
                decimal_text(committed_hours) if committed_hours is not None else None
            ),
            "active_employee_committed_assignment_count": len(active_committed),
            "active_employee_committed_hours_per_week": (
                decimal_text(active_committed_hours) if active_committed_hours is not None else None
            ),
            "inactive_employee_committed_assignment_count": len(inactive_committed),
            "inactive_employee_committed_hours_per_week": (
                decimal_text(inactive_committed_hours)
                if inactive_committed_hours is not None
                else None
            ),
            "proposed_state": "recorded" if proposed else "not_recorded",
            "proposed_assignment_count": len(proposed),
            "proposed_hours_per_week": decimal_text(proposed_hours)
            if proposed_hours is not None
            else None,
            "assignment_completeness": "not_attested",
        }

        comparable_count = 0
        non_overloaded_count = 0
        confirmed_overload_count = 0
        estimated_overload_count = 0
        unresolved_count = 0
        comparison_reasons: list[str] = []

        by_employee: dict[str, list[AssignmentInput]] = defaultdict(list)
        for item in active_committed:
            by_employee[item.employee_id].append(item)
        for employee_id, employee_assignments in sorted(by_employee.items()):
            employee = employees_by_id[employee_id]
            capacity_state, available, capacity_record = available_by_employee.get(
                employee_id, ("missing", None, None)
            )
            assignment_total = _sum_hours(
                [item.planned_hours_per_week for item in employee_assignments]
            )
            ordered_assignments = sorted(employee_assignments, key=lambda item: item.id)
            assignment_refs = [_assignment_source(item) for item in ordered_assignments]
            if available is None or capacity_state not in {"confirmed", "estimated"}:
                unresolved_count += 1
                reason = {
                    "missing": "weekly_capacity_missing_for_active_employee",
                    "unknown": "weekly_capacity_explicitly_unknown_for_active_employee",
                    "invalid": "weekly_capacity_record_invalid_or_conflicting",
                }.get(capacity_state, "weekly_capacity_unavailable")
                employee_result = unresolved_employee_comparisons.setdefault(
                    employee_id,
                    {
                        "employee_id": employee.id,
                        "employee_code": employee.employee_code,
                        "weeks": [],
                    },
                )
                week_items = cast(list[dict[str, object]], employee_result["weeks"])
                week_items.append(
                    {
                        "week_start_date": week.isoformat(),
                        "capacity_state": capacity_state,
                        "committed_hours_per_week": decimal_text(assignment_total),
                        "assignment_sources": assignment_refs,
                        "reasons": [reason],
                    }
                )
                comparison_reasons.append(reason)
                continue

            comparable_count += 1
            difference = available - assignment_total
            if difference >= 0:
                non_overloaded_count += 1
                continue
            overload: dict[str, object] = {
                "employee_id": employee.id,
                "employee_code": employee.employee_code,
                "week_start_date": week.isoformat(),
                "capacity_record_id": capacity_record.id if capacity_record else None,
                "capacity_version": capacity_record.version if capacity_record else None,
                "availability_status": capacity_state,
                "available_hours": decimal_text(available),
                "committed_hours_per_week": decimal_text(assignment_total),
                "overload_hours": decimal_text(-difference),
                "assignment_sources": assignment_refs,
                "reliability_state": (
                    "confirmed_capacity_overload"
                    if capacity_state == "confirmed"
                    else "estimated_capacity_overload"
                ),
            }
            if capacity_state == "confirmed":
                confirmed_overloads.append(overload)
                confirmed_overload_count += 1
            else:
                estimated_overloads.append(overload)
                estimated_overload_count += 1

        company_difference: Decimal | None = None
        if complete_available_hours is not None and active_committed_hours is not None:
            company_difference = complete_available_hours - active_committed_hours
        if not active_committed:
            comparison_state = "not_recorded"
            comparison_reasons.append("no_committed_assignments_recorded_for_active_employees")
        elif company_difference is not None:
            comparison_state = (
                "calculated_confirmed_capacity"
                if complete_capacity_basis == "confirmed"
                else "calculated_estimated_capacity"
            )
            comparison_reasons.append("assignment_completeness_not_attested")
        elif comparable_count:
            comparison_state = "partial"
            comparison_reasons.append("company_capacity_incomplete_for_requested_week")
        else:
            comparison_state = "unavailable"
            comparison_reasons.extend(week_capacity_reasons)
        if inactive_committed:
            comparison_reasons.append(
                "inactive_employee_commitments_excluded_from_capacity_comparison"
            )
        comparison_result: dict[str, object] = {
            "state": comparison_state,
            "capacity_minus_recorded_commitments_hours": (
                decimal_text(company_difference) if company_difference is not None else None
            ),
            "capacity_basis": complete_capacity_basis,
            "employee_comparisons_performed": comparable_count,
            "employee_comparisons_not_exceeded_by_recorded_commitments": non_overloaded_count,
            "confirmed_overload_count": confirmed_overload_count,
            "estimated_overload_count": estimated_overload_count,
            "unresolved_employee_comparison_count": unresolved_count,
            "reasons": _unique_reasons(comparison_reasons),
        }
        if assignment_conflict_reasons:
            comparison_result["reasons"] = _unique_reasons(
                cast(list[str], comparison_result["reasons"]), assignment_conflict_reasons
            )

        weekly_results.append(
            {
                "week_start_date": week.isoformat(),
                "week_end_date": (week + timedelta(days=6)).isoformat(),
                "capacity": capacity_result,
                "assignments": assignment_result,
                "comparison": comparison_result,
            }
        )

    confirmed_overloads.sort(
        key=lambda item: (
            str(item["week_start_date"]),
            str(item["employee_code"]).casefold(),
            str(item["employee_id"]),
        )
    )
    estimated_overloads.sort(
        key=lambda item: (
            str(item["week_start_date"]),
            str(item["employee_code"]).casefold(),
            str(item["employee_id"]),
        )
    )
    unresolved_comparisons = [
        unresolved_employee_comparisons[employee_id]
        for employee_id in sorted(
            unresolved_employee_comparisons,
            key=lambda item: (
                str(unresolved_employee_comparisons[item]["employee_code"]).casefold(),
                item,
            ),
        )
    ]
    for unresolved in unresolved_comparisons:
        cast(list[dict[str, object]], unresolved["weeks"]).sort(
            key=lambda item: str(item["week_start_date"])
        )

    inactive_commitment_results = [
        {
            "employee_id": employee_id,
            "employee_code": employees_by_id[employee_id].employee_code
            if employee_id in employees_by_id
            else None,
            "week_start_date": week.isoformat(),
            "committed_hours_per_week": decimal_text(
                _sum_hours([item.planned_hours_per_week for item in assignments])
            ),
            "assignment_ids": sorted(item.id for item in assignments),
        }
        for (employee_id, week), assignments in sorted(
            inactive_employee_commitments.items(), key=lambda item: (item[0][1], item[0][0])
        )
    ]
    if inactive_employee_commitments:
        assignment_conflict_reasons.append("committed_assignments_for_inactive_employees")

    active_skills = sorted(
        (skill for skill in snapshot.skills if skill.status == "active"),
        key=lambda skill: (skill.name.casefold(), skill.id),
    )
    if len(active_skills) > MAX_ACTIVE_SKILLS:
        raise ValueError("active skill input exceeds the supported calculation limit")
    active_skill_ids = {skill.id for skill in active_skills}
    skill_rows_by_id: dict[str, list[EmployeeSkillInput]] = defaultdict(list)
    employee_skill_reasons: list[str] = []
    for employee_skill in snapshot.employee_skills:
        if (
            employee_skill.status == "active"
            and employee_skill.employee_id in active_employee_ids
            and employee_skill.skill_id in active_skill_ids
        ):
            if not 1 <= employee_skill.proficiency <= 5:
                employee_skill_reasons.append("employee_skill_proficiency_invalid")
                continue
            skill_rows_by_id[employee_skill.skill_id].append(employee_skill)
    total_active_skill_links = sum(len(rows) for rows in skill_rows_by_id.values())
    employees_with_skills = {row.employee_id for rows in skill_rows_by_id.values() for row in rows}
    employees_without_skill_records = max(0, len(active_employees) - len(employees_with_skills))
    skill_summaries: list[dict[str, object]] = []
    for skill in active_skills:
        rows = skill_rows_by_id.get(skill.id, [])
        proficiency_counts = {
            str(level): sum(row.proficiency == level for row in rows) for level in range(1, 6)
        }
        skill_summaries.append(
            {
                "skill_id": skill.id,
                "skill_name": skill.name,
                "inventory_state": "recorded" if rows else "not_recorded",
                "mapped_active_employee_count": len({row.employee_id for row in rows}),
                "proficiency_counts": proficiency_counts,
            }
        )

    skill_reasons: list[str] = []
    if not active_employees:
        skill_reasons.append("no_active_employees")
        skill_state = "unavailable"
    elif not active_skills:
        skill_reasons.append("no_active_skills_recorded")
        skill_state = "unavailable"
    elif not total_active_skill_links:
        skill_reasons.append("no_active_employee_skill_records")
        skill_state = "not_recorded"
    else:
        skill_state = "partial" if employees_without_skill_records else "recorded"
        if employees_without_skill_records:
            skill_reasons.append("active_employees_missing_active_skill_records")
    skill_reasons.extend(employee_skill_reasons)

    fact_records = _fact_records(snapshot)
    facts_with_import_lineage = sum(bool(record.lineage) for record in fact_records)
    import_rows = {
        (reference.import_id, reference.source_row_id)
        for record in fact_records
        for reference in record.lineage
    }
    if active_employees and all_capacity_confirmed:
        reliability_status = "complete"
    elif any_known_capacity:
        reliability_status = "partial"
    else:
        reliability_status = "unavailable"
    capacity_reasons = _unique_reasons(capacity_reasons_all)
    if reliability_status == "complete":
        capacity_reasons = []

    assignment_reasons = ["assignment_completeness_not_attested"]
    if not any_committed_assignment:
        assignment_reasons.insert(0, "no_committed_assignments_recorded")
    assignment_reasons.extend(assignment_conflict_reasons)
    assignment_status = "recorded" if any_committed_assignment else "not_recorded"

    return {
        "metric_catalog_version": METRIC_CATALOG_VERSION,
        "metric_catalog_status": METRIC_CATALOG_STATUS,
        "decision_use": METRIC_DECISION_USE,
        "calculation_version": CALCULATION_VERSION,
        "metric_versions": {
            definition.metric_id: definition.version for definition in METRIC_DEFINITIONS
        },
        "metric_definition_snapshot": metric_definition_payloads(),
        "reliability_status": reliability_status,
        "reliability_scope": (
            "Run-level reliability describes active-employee weekly-capacity coverage only. "
            "See per-metric states for assignment and skill-inventory reliability."
        ),
        "period": {
            "start_week_date": snapshot.start_week_date.isoformat(),
            "end_week_date": period_end.isoformat(),
            "week_count": snapshot.week_count,
            "week_convention": "Monday-Sunday",
            "company_timezone": snapshot.company_timezone,
        },
        "reliability": {
            "capacity": {"state": reliability_status, "reasons": capacity_reasons},
            "committed_assignments": {
                "state": assignment_status,
                "reasons": _unique_reasons(assignment_reasons),
            },
            "skill_inventory": {"state": skill_state, "reasons": _unique_reasons(skill_reasons)},
        },
        "weekly": weekly_results,
        "confirmed_overloads": confirmed_overloads,
        "estimated_overloads": estimated_overloads,
        "unresolved_employee_comparisons": unresolved_comparisons,
        "inactive_employee_commitments": inactive_commitment_results,
        "skill_inventory": {
            "state": skill_state,
            "active_employee_count": len(active_employees),
            "active_skill_catalog_count": len(active_skills),
            "active_employee_skill_record_count": total_active_skill_links,
            "active_employees_with_skill_records": len(employees_with_skills),
            "active_employees_without_skill_records": employees_without_skill_records,
            "skills": skill_summaries,
            "reasons": _unique_reasons(skill_reasons),
        },
        "provenance": {
            "model": "canonical M3 facts plus retained M4 source-row links",
            "canonical_input_fact_count": len(fact_records),
            "facts_with_import_lineage": facts_with_import_lineage,
            "facts_without_recorded_import_lineage": len(fact_records) - facts_with_import_lineage,
            "distinct_import_source_row_count": len(import_rows),
            "import_fingerprint_scope": (
                "canonical_fingerprint_at_import records the fact fingerprint when the M4 import "
                "was applied; it is not a current-value verification."
            ),
        },
        "limitations": [
            "Capacity uses the latest version per active employee and week; "
            "missing and unknown values remain null.",
            "The employee standard weekly capacity field is not used as a fallback "
            "or as available capacity.",
            "Only current active employee status is available; it is applied across "
            "the full requested horizon.",
            "A committed assignment contributes its full planned weekly hours to every "
            "Monday-Sunday week overlapping its inclusive dates; no daily proration is performed.",
            "Assignment completeness is not attested. Capacity minus recorded commitments "
            "is not proof of free capacity; no recorded assignment remains unknown, not zero.",
            "Assignments are plans, not actual work, timesheets, utilization, or elapsed effort; "
            "completed and cancelled rows are not included as current commitments.",
            "Project skill requirements are unsupported. Skill counts describe active recorded "
            "inventory, not project matching, verified competency, or delivery capability.",
            "M4 import links identify original source files/rows; their import-time fingerprint "
            "does not assert that mutable canonical values remain unchanged.",
        ],
        "input_limits": {
            "max_weeks": MAX_METRIC_WEEKS,
            "max_active_employees": MAX_ACTIVE_EMPLOYEES,
            "max_overlapping_assignments": MAX_ASSIGNMENTS,
            "max_active_skills": MAX_ACTIVE_SKILLS,
            "max_active_employee_skill_records": MAX_EMPLOYEE_SKILL_RECORDS,
            "max_import_lineage_references": MAX_IMPORT_LINEAGE_REFERENCES,
        },
    }
