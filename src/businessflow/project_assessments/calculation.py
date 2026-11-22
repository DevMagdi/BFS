from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal
from typing import cast

from businessflow.metrics.catalog import CALCULATION_VERSION, METRIC_CATALOG_VERSION
from businessflow.metrics.snapshot import decimal_text

ASSESSMENT_CALCULATION_VERSION = "businessflow.project-capacity-assessment.v1"


def _mapping(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _decimal(value: object) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except (ArithmeticError, ValueError):
        return None


def _week_dates(start_week_date: date, week_count: int) -> tuple[date, ...]:
    return tuple(start_week_date + timedelta(days=7 * index) for index in range(week_count))


def _demand_map(value: object) -> dict[str, Decimal]:
    rows: dict[str, Decimal] = {}
    for item in _list(value):
        row = _mapping(item)
        week = _text(row.get("week_start_date"))
        hours = _decimal(row.get("hours"))
        if week is not None and hours is not None:
            rows[week] = hours
    return rows


def _append_once(target: list[dict[str, str]], code: str, message: str) -> None:
    if not any(item["code"] == code for item in target):
        target.append({"code": code, "message": message})


def _overlaps_week(assignment: Mapping[str, object], week_start: date) -> bool:
    try:
        assignment_start = date.fromisoformat(str(assignment["start_date"]))
        assignment_end = date.fromisoformat(str(assignment["end_date"]))
    except (KeyError, TypeError, ValueError):
        return False
    week_end = week_start + timedelta(days=6)
    return assignment_start <= week_end and assignment_end >= week_start


def calculate_capacity_assessment(
    *,
    tenant_id: str,
    company_id: str,
    intake_id: str,
    intake_version: int,
    intake_version_id: str,
    intake_version_hash: str,
    intake_snapshot: dict[str, object],
    metric_run_id: str,
    metric_catalog_version: str,
    metric_calculation_version: str,
    metric_input_hash: str,
    metric_result_hash: str,
    metric_input_snapshot: dict[str, object],
    metric_result_snapshot: dict[str, object],
    assumptions: dict[str, object],
    scenario: dict[str, object] | None,
) -> tuple[dict[str, object], dict[str, object]]:
    """Compare explicit weekly project demand with M5 outputs; never recalculate M5 capacity."""
    intake_data = _mapping(intake_snapshot.get("data"))
    period_start_raw = _text(intake_data.get("start_week_date"))
    week_count_raw = intake_data.get("week_count")
    if period_start_raw is None or not isinstance(week_count_raw, int):
        raise ValueError("stored project intake period is invalid")
    start_week_date = date.fromisoformat(period_start_raw)
    week_count = week_count_raw
    weeks = _week_dates(start_week_date, week_count)
    end_week_date = weeks[-1] + timedelta(days=6)

    demand_source = (
        scenario.get("weekly_demand") if scenario is not None else intake_data.get("weekly_demand")
    )
    demand_by_week = _demand_map(demand_source)
    expected_week_keys = {week.isoformat() for week in weeks}
    scenario_is_hypothetical = scenario is not None
    distribution_confirmed = (
        scenario_is_hypothetical or intake_data.get("demand_distribution_confirmed") is True
    )
    if set(demand_by_week) - expected_week_keys:
        raise ValueError("stored weekly demand falls outside the intake period")
    if scenario_is_hypothetical and set(demand_by_week) != expected_week_keys:
        raise ValueError("a scenario must provide demand for every week in the intake period")

    blockers: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    if not distribution_confirmed:
        _append_once(
            blockers,
            "demand_distribution_unconfirmed",
            "A confirmed weekly distribution is required; total hours are not spread across weeks.",
        )
    if any(week.isoformat() not in demand_by_week for week in weeks):
        _append_once(
            blockers,
            "weekly_demand_missing",
            "Missing weekly demand stays unknown; no week is treated as zero.",
        )

    capacity_current = assumptions.get("capacity_data_current_confirmed") is True
    commitments_complete = assumptions.get("recorded_commitments_complete_confirmed") is True
    if not capacity_current:
        _append_once(
            blockers,
            "capacity_currentness_unconfirmed",
            "An authorized human has not confirmed that capacity evidence is current.",
        )
    if not commitments_complete:
        _append_once(
            blockers,
            "commitment_completeness_unconfirmed",
            "The requester has not confirmed that assignments include all existing committed work.",
        )

    metric_period = _mapping(metric_result_snapshot.get("period"))
    if (
        _text(metric_period.get("start_week_date")) != start_week_date.isoformat()
        or metric_period.get("week_count") != week_count
        or metric_catalog_version != METRIC_CATALOG_VERSION
        or metric_calculation_version != CALCULATION_VERSION
    ):
        _append_once(
            blockers,
            "unsupported_metric_evidence",
            "The metric run does not match the supported M5 version and intake period.",
        )

    metric_weekly = {
        _text(_mapping(item).get("week_start_date")): _mapping(item)
        for item in _list(metric_result_snapshot.get("weekly"))
        if _text(_mapping(item).get("week_start_date")) is not None
    }
    metric_input_scope = _mapping(metric_input_snapshot.get("scope"))
    if (
        _text(metric_input_scope.get("tenant_id")) != tenant_id
        or _text(metric_input_scope.get("company_id")) != company_id
    ):
        _append_once(
            blockers,
            "metric_scope_mismatch",
            "The referenced metric input snapshot is outside the authorized tenant/company scope.",
        )

    metric_input_period = _mapping(metric_input_snapshot.get("period"))
    if (
        _text(metric_input_period.get("start_week_date")) != start_week_date.isoformat()
        or metric_input_period.get("week_count") != week_count
    ):
        _append_once(
            blockers,
            "metric_input_period_mismatch",
            "The immutable M5 input snapshot does not match the project intake period.",
        )
    employees = {
        _text(_mapping(item).get("id")): _mapping(item)
        for item in _list(metric_input_snapshot.get("employees"))
        if _text(_mapping(item).get("id")) is not None
    }
    capacity_inputs = [
        _mapping(item) for item in _list(metric_input_snapshot.get("weekly_capacity"))
    ]
    assignment_inputs = [
        _mapping(item) for item in _list(metric_input_snapshot.get("resource_assignments"))
    ]

    estimated_capacity_weeks = 0
    shortfall_weeks: list[str] = []
    confirmed_capacity_shortfall = False
    unsupported_estimated_shortfall = False
    has_other_proposed_assignments = False
    weekly_results: list[dict[str, object]] = []

    for week in weeks:
        week_key = week.isoformat()
        metric_week = metric_weekly.get(week_key, {})
        capacity = _mapping(metric_week.get("capacity"))
        assignments = _mapping(metric_week.get("assignments"))
        comparison = _mapping(metric_week.get("comparison"))
        available = _decimal(capacity.get("complete_available_hours"))
        capacity_basis = _text(capacity.get("complete_capacity_basis"))
        active_employee_count = capacity.get("active_employee_count")
        if not isinstance(active_employee_count, int) or active_employee_count < 1:
            available = None
        if available is None or capacity_basis not in {"confirmed", "estimated"}:
            _append_once(
                blockers,
                "capacity_incomplete",
                "M5 capacity is incomplete for one or more active employees.",
            )
        elif capacity_basis == "estimated":
            estimated_capacity_weeks += 1

        active_committed = _decimal(assignments.get("active_employee_committed_hours_per_week"))
        active_commitment_count = assignments.get("active_employee_committed_assignment_count")
        if not isinstance(active_commitment_count, int):
            active_commitment_count = 0
        inactive_commitment_count = assignments.get("inactive_employee_committed_assignment_count")
        if not isinstance(inactive_commitment_count, int):
            inactive_commitment_count = 0
        inactive_committed = _decimal(assignments.get("inactive_employee_committed_hours_per_week"))
        if inactive_commitment_count > 0:
            _append_once(
                blockers,
                "inactive_employee_commitments",
                "M5 found committed work for an inactive employee; comparison is unresolved.",
            )

        remaining = _decimal(comparison.get("capacity_minus_recorded_commitments_hours"))
        remaining_basis: str | None = (
            "m5_capacity_minus_recorded_commitments" if remaining is not None else None
        )
        if (
            remaining is None
            and available is not None
            and active_committed is None
            and active_commitment_count == 0
            and inactive_commitment_count == 0
            and commitments_complete
        ):
            # M5 intentionally keeps no assignment records as unknown. Only the explicit human
            # completeness attestation permits this assessment to treat the recorded zero as zero.
            active_committed = Decimal("0.00")
            remaining = available
            remaining_basis = "m5_capacity_with_human_attested_zero_commitments"
            _append_once(
                warnings,
                "zero_commitments_human_attested",
                "Zero is used only because the requester attested complete commitment records.",
            )

        if active_committed is None and active_commitment_count > 0:
            _append_once(
                blockers,
                "commitment_comparison_unavailable",
                "M5 cannot compare recorded commitments with capacity for one or more weeks.",
            )
        if active_committed is None and active_commitment_count == 0 and not commitments_complete:
            # Keep the value null: a missing assignment record is not interpreted as zero.
            pass

        if not commitments_complete:
            existing_commitments_state = (
                "recorded_but_completeness_unconfirmed"
                if active_committed is not None
                else "not_recorded_not_zero"
            )
        elif active_committed is None:
            existing_commitments_state = "unresolved"
        elif active_commitment_count == 0:
            existing_commitments_state = "human_attested_zero"
        else:
            existing_commitments_state = "recorded_complete_by_human_attestation"

        demand = demand_by_week.get(week_key)
        demand_known = demand is not None
        demand_usable = demand_known and distribution_confirmed
        if not demand_known:
            demand_state = "missing"
            demand_hours: str | None = None
        elif not distribution_confirmed:
            demand_state = "unconfirmed"
            demand_hours = decimal_text(cast(Decimal, demand))
        else:
            demand_state = "confirmed_hypothetical" if scenario_is_hypothetical else "confirmed"
            demand_hours = decimal_text(cast(Decimal, demand))

        projected_remaining: Decimal | None = None
        if remaining is not None and demand_usable:
            projected_remaining = remaining - cast(Decimal, demand)
        deficit = (
            max(Decimal("0.00"), -projected_remaining) if projected_remaining is not None else None
        )
        surplus = (
            max(Decimal("0.00"), projected_remaining) if projected_remaining is not None else None
        )
        if deficit is not None and deficit > 0:
            shortfall_weeks.append(week_key)
            if capacity_basis == "confirmed":
                confirmed_capacity_shortfall = True
            elif capacity_basis == "estimated":
                unsupported_estimated_shortfall = True

        week_capacity_rows = [
            row for row in capacity_inputs if _text(row.get("week_start_date")) == week_key
        ]
        active_week_assignments: list[dict[str, object]] = []
        inactive_week_assignments: list[dict[str, object]] = []
        overlapping_assignments: list[dict[str, object]] = []
        for assignment in assignment_inputs:
            if not _overlaps_week(assignment, week):
                continue
            overlapping_assignments.append(assignment)
            employee = employees.get(_text(assignment.get("employee_id")))
            if employee is not None and employee.get("employment_status") == "active":
                active_week_assignments.append(assignment)
            else:
                inactive_week_assignments.append(assignment)
        committed_ids = sorted(
            str(item["id"])
            for item in active_week_assignments
            if item.get("status") == "committed" and isinstance(item.get("id"), str)
        )
        inactive_committed_ids = sorted(
            str(item["id"])
            for item in inactive_week_assignments
            if item.get("status") == "committed" and isinstance(item.get("id"), str)
        )
        proposed_ids = sorted(
            str(item["id"])
            for item in overlapping_assignments
            if item.get("status") == "proposed" and isinstance(item.get("id"), str)
        )
        has_other_proposed_assignments = has_other_proposed_assignments or bool(proposed_ids)
        if proposed_ids:
            _append_once(
                warnings,
                "other_proposed_assignments_not_subtracted",
                "M5 proposals are reported separately and are not subtracted as committed work.",
            )

        metric_week_capacity = _mapping(metric_week.get("capacity"))
        metric_week_assignments = _mapping(metric_week.get("assignments"))
        metric_week_comparison = _mapping(metric_week.get("comparison"))
        weekly_results.append(
            {
                "week_start_date": week_key,
                "week_end_date": (week + timedelta(days=6)).isoformat(),
                "demand": {
                    "state": demand_state,
                    "hours": demand_hours,
                    "unit": "team_hours_per_week",
                    "source": "hypothetical_scenario"
                    if scenario_is_hypothetical
                    else "project_intake_version",
                    "intake_distribution_confirmed": distribution_confirmed,
                },
                "available_capacity_hours": decimal_text(available)
                if available is not None
                else None,
                "capacity_basis": capacity_basis,
                "existing_committed_hours": (
                    decimal_text(active_committed) if active_committed is not None else None
                ),
                "existing_commitments_state": existing_commitments_state,
                "inactive_employee_committed_hours": (
                    decimal_text(inactive_committed) if inactive_committed is not None else None
                ),
                "remaining_capacity_before_project_hours": (
                    decimal_text(remaining) if remaining is not None else None
                ),
                "remaining_capacity_basis": remaining_basis,
                "projected_remaining_capacity_hours": (
                    decimal_text(projected_remaining) if projected_remaining is not None else None
                ),
                "deficit_hours": decimal_text(deficit) if deficit is not None else None,
                "surplus_hours": decimal_text(surplus) if surplus is not None else None,
                "m5_comparison_state": _text(comparison.get("state")),
                "m5_weekly_evidence": {
                    "capacity": metric_week_capacity,
                    "assignments": metric_week_assignments,
                    "comparison": metric_week_comparison,
                },
                "source_evidence": {
                    "metric_run_id": metric_run_id,
                    "metric_input_hash": metric_input_hash,
                    "metric_result_hash": metric_result_hash,
                    "capacity_source_record_ids": sorted(
                        str(row["id"])
                        for row in week_capacity_rows
                        if isinstance(row.get("id"), str)
                    ),
                    "active_committed_assignment_ids": committed_ids,
                    "inactive_employee_committed_assignment_ids": inactive_committed_ids,
                    "proposed_assignment_ids": proposed_ids,
                },
            }
        )

    if scenario_is_hypothetical:
        _append_once(
            warnings,
            "hypothetical_scenario",
            "Scenario is hypothetical; canonical data and operations are unchanged.",
        )
    lineage_present = any(
        bool(_list(_mapping(row).get("lineage")))
        for group in ("weekly_capacity", "resource_assignments")
        for row in _list(metric_input_snapshot.get(group))
    )
    if lineage_present:
        _append_once(
            warnings,
            "import_fingerprint_not_currentness_check",
            "Import fingerprints are not current-value verification.",
        )

    if unsupported_estimated_shortfall:
        _append_once(
            blockers,
            "estimated_capacity_shortfall_requires_confirmation",
            "Estimated-capacity shortfall needs confirmation.",
        )
    if shortfall_weeks and not blockers and confirmed_capacity_shortfall:
        _append_once(
            warnings,
            "weekly_capacity_shortfall",
            "At least one week is short after commitments; weekly deficits are not averaged away.",
        )

    if blockers:
        capacity_status = "incomplete"
    elif confirmed_capacity_shortfall:
        capacity_status = "not_feasible"
    elif estimated_capacity_weeks or has_other_proposed_assignments or scenario_is_hypothetical:
        capacity_status = "feasible_with_conditions"
        if estimated_capacity_weeks:
            _append_once(
                warnings,
                "estimated_capacity_condition",
                "Estimated capacity in at least one week requires human verification.",
            )
    else:
        capacity_status = "feasible"

    if commitments_complete:
        _append_once(
            warnings,
            "commitment_completeness_human_attestation",
            "Commitment completeness is a requester attestation, not independently verified by M5.",
        )
    if capacity_current:
        _append_once(
            warnings,
            "capacity_currentness_human_attestation",
            "Capacity currentness is a requester attestation; no freshness threshold is inferred.",
        )

    metric_reference = {
        "id": metric_run_id,
        "catalog_version": metric_catalog_version,
        "calculation_version": metric_calculation_version,
        "input_hash": metric_input_hash,
        "result_hash": metric_result_hash,
    }
    evidence_input: dict[str, object] = {
        "snapshot_schema_version": 1,
        "scope": {"tenant_id": tenant_id, "company_id": company_id},
        "intake": {
            "id": intake_id,
            "version": intake_version,
            "version_id": intake_version_id,
            "version_hash": intake_version_hash,
            "snapshot": intake_snapshot,
        },
        "period": {
            "start_week_date": start_week_date.isoformat(),
            "end_week_date": end_week_date.isoformat(),
            "week_count": week_count,
            "week_convention": "Monday-Sunday",
        },
        "metric_run": metric_reference,
        "metric_run_input_snapshot_reference": {
            "metric_run_id": metric_run_id,
            "input_hash": metric_input_hash,
            "snapshot_type": "immutable_M5_metric_run_input_snapshot",
        },
        "assumptions": assumptions,
        "scenario": scenario,
        "demand_source": "hypothetical_scenario"
        if scenario_is_hypothetical
        else "project_intake_version",
    }
    result: dict[str, object] = {
        "assessment_calculation_version": ASSESSMENT_CALCULATION_VERSION,
        "capacity_status": capacity_status,
        "business_feasibility_status": "incomplete",
        "business_feasibility_reason": (
            "Finance inputs/rules are not implemented; missing financial data is not zero."
        ),
        "decision_use": "human_capacity_observation_only_no_project_acceptance_or_rejection",
        "capacity_scope": "company_aggregate",
        "weekly": weekly_results,
        "warnings": warnings,
        "blockers": blockers,
        "assumptions": assumptions,
        "metric_run": metric_reference,
        "hypothetical_scenario": scenario_is_hypothetical,
        "canonical_capacity_changed": False,
        "project_or_employee_assignments_created": False,
        "operational_action_taken": False,
        "limitations": [
            "Capacity and commitments are linked to the immutable M5 run/version.",
            "M5 uses scheduled minus non-project minus unavailable; no standard-capacity fallback.",
            "M5 residual is reused; existing commitments are not subtracted twice.",
            "No recorded commitments mean unknown unless the requester attests completeness.",
            "Freshness and completeness are human attestations, not independently verified.",
            "No employee/skill/department matching is claimed; employees are not assigned.",
            "Capacity status is not project acceptance, rejection, approval, or action.",
            "Business feasibility stays incomplete; no approved finance inputs/rules exist.",
        ],
    }
    return evidence_input, result
