from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from typing import cast

from businessflow.financial_assessments.catalog import FINANCIAL_POLICY_CATALOG_STATUS
from businessflow.metrics.catalog import CALCULATION_VERSION, METRIC_CATALOG_VERSION
from businessflow.metrics.snapshot import content_hash, decimal_text
from businessflow.position_reviews.demo import DEMO_SYNTHETIC_LABEL

POSITION_REVIEW_CALCULATION_VERSION = "businessflow.position-review.v1"
POSITION_REVIEW_SCHEMA_VERSION = 1
MAX_REVIEW_ASSIGNMENT_EVIDENCE = 2_000


def _object(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: object) -> list[Mapping[str, object]]:
    if not isinstance(value, list):
        return []
    return [_object(item) for item in cast(list[object], value) if isinstance(item, Mapping)]


def _text(value: object) -> str | None:
    return value if isinstance(value, str) else None


def _integer(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _decimal(value: object) -> Decimal | None:
    if not isinstance(value, str):
        return None
    try:
        result = Decimal(value)
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def _run_reference(run: Mapping[str, object]) -> dict[str, object]:
    return {
        "id": run.get("id"),
        "tenant_id": run.get("tenant_id"),
        "company_id": run.get("company_id"),
        "start_week_date": run.get("start_week_date"),
        "end_week_date": run.get("end_week_date"),
        "week_count": run.get("week_count"),
        "metric_catalog_version": run.get("metric_catalog_version"),
        "calculation_version": run.get("calculation_version"),
        "input_hash": run.get("input_hash"),
        "result_hash": run.get("result_hash"),
    }


def _source_catalog_map(source_catalog: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    return {
        str(item["import_id"]): item
        for item in source_catalog
        if isinstance(item.get("import_id"), str)
    }


def _source_rows(
    fact: Mapping[str, object], source_catalog: Mapping[str, dict[str, object]]
) -> list[dict[str, object]]:
    result: list[dict[str, object]] = []
    for reference in _objects(fact.get("lineage")):
        import_id = _text(reference.get("import_id"))
        metadata = source_catalog.get(import_id or "", {})
        result.append(
            {
                "import_id": import_id,
                "file_name": metadata.get("file_name"),
                "dataset_type": reference.get("dataset_type"),
                "mapping_version": reference.get("mapping_version"),
                "file_hash": reference.get("file_hash"),
                "source_row_id": reference.get("source_row_id"),
                "worksheet_name": reference.get("worksheet_name"),
                "row_number": reference.get("row_number"),
                "row_hash": reference.get("row_hash"),
                "canonical_fingerprint_at_import": reference.get("canonical_fingerprint_at_import"),
            }
        )
    return sorted(
        result,
        key=lambda item: (
            str(item.get("import_id")),
            str(item.get("worksheet_name")),
            _integer(item.get("row_number")) or 0,
        ),
    )


def _active_employee_ids(input_snapshot: Mapping[str, object]) -> tuple[str, ...]:
    return tuple(
        sorted(
            employee_id
            for employee in _objects(input_snapshot.get("employees"))
            if employee.get("employment_status") == "active"
            and (employee_id := _text(employee.get("id"))) is not None
        )
    )


def _period(run: Mapping[str, object]) -> dict[str, object]:
    return {
        "start_week_date": run.get("start_week_date"),
        "end_week_date": run.get("end_week_date"),
        "week_count": run.get("week_count"),
        "week_convention": "Monday-Sunday",
    }


def _assignment_evidence(
    run: Mapping[str, object], source_catalog: Mapping[str, dict[str, object]]
) -> list[dict[str, object]]:
    input_snapshot = _object(run.get("input_snapshot"))
    period = _object(run.get("period")) or run
    try:
        first = date.fromisoformat(str(period["start_week_date"]))
        last = date.fromisoformat(str(period["end_week_date"]))
    except (KeyError, TypeError, ValueError):
        return []
    employees = {employee_id for employee_id in _active_employee_ids(input_snapshot)}
    assignments: list[dict[str, object]] = []
    for record in _objects(input_snapshot.get("resource_assignments")):
        if record.get("status") != "committed" or record.get("employee_id") not in employees:
            continue
        try:
            start_date = date.fromisoformat(str(record["start_date"]))
            end_date = date.fromisoformat(str(record["end_date"]))
        except (KeyError, TypeError, ValueError):
            continue
        if end_date < first or start_date > last:
            continue
        assignments.append(
            {
                "record_id": record.get("id"),
                "employee_id": record.get("employee_id"),
                "project_id": record.get("project_id"),
                "project_code": record.get("project_code"),
                "start_date": record.get("start_date"),
                "end_date": record.get("end_date"),
                "status": record.get("status"),
                "planned_hours_per_week": record.get("planned_hours_per_week"),
                "source_rows": _source_rows(record, source_catalog),
            }
        )
    return sorted(
        assignments,
        key=lambda item: (
            str(item.get("project_code")).casefold(),
            str(item.get("employee_id")),
            str(item.get("record_id")),
        ),
    )


def _capacity_fact(
    input_snapshot: Mapping[str, object],
    employee_id: object,
    week_start_date: object,
    capacity_record_id: object,
    capacity_version: object,
) -> Mapping[str, object] | None:
    for capacity in _objects(input_snapshot.get("weekly_capacity")):
        if (
            capacity.get("id") == capacity_record_id
            and capacity.get("employee_id") == employee_id
            and capacity.get("week_start_date") == week_start_date
            and capacity.get("version") == capacity_version
        ):
            return capacity
    return None


def _assignment_fact(
    input_snapshot: Mapping[str, object], assignment_id: object
) -> Mapping[str, object] | None:
    for assignment in _objects(input_snapshot.get("resource_assignments")):
        if assignment.get("id") == assignment_id:
            return assignment
    return None


def _capacity_evidence(
    capacity: Mapping[str, object] | None,
    source_catalog: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    if capacity is None:
        return {"state": "input_snapshot_reference_missing", "source_rows": []}
    return {
        "state": "source_fact_resolved",
        "record_id": capacity.get("id"),
        "employee_id": capacity.get("employee_id"),
        "week_start_date": capacity.get("week_start_date"),
        "version": capacity.get("version"),
        "availability_status": capacity.get("availability_status"),
        "scheduled_hours": capacity.get("scheduled_hours"),
        "non_project_hours": capacity.get("non_project_hours"),
        "unavailable_hours": capacity.get("unavailable_hours"),
        "source_rows": _source_rows(capacity, source_catalog),
    }


def _overload_findings(
    current_run: Mapping[str, object],
    source_catalog: Mapping[str, dict[str, object]],
    capacity_attested: bool,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    input_snapshot = _object(current_run.get("input_snapshot"))
    result_snapshot = _object(current_run.get("result_snapshot"))
    findings: list[dict[str, object]] = []
    missing_evidence: list[dict[str, object]] = []
    categories = (
        ("confirmed_overloads", "confirmed_capacity_observation"),
        ("estimated_overloads", "estimated_capacity_observation"),
    )
    for result_key, observation_state in categories:
        for overload in _objects(result_snapshot.get(result_key)):
            capacity = _capacity_fact(
                input_snapshot,
                overload.get("employee_id"),
                overload.get("week_start_date"),
                overload.get("capacity_record_id"),
                overload.get("capacity_version"),
            )
            assignment_evidence: list[dict[str, object]] = []
            unresolved_ids: list[str] = []
            for assignment_source in _objects(overload.get("assignment_sources")):
                assignment_id = assignment_source.get("assignment_id")
                assignment = _assignment_fact(input_snapshot, assignment_id)
                if assignment is None:
                    unresolved_ids.append(str(assignment_id))
                    continue
                assignment_evidence.append(
                    {
                        "record_id": assignment.get("id"),
                        "employee_id": assignment.get("employee_id"),
                        "project_id": assignment.get("project_id"),
                        "project_code": assignment.get("project_code"),
                        "start_date": assignment.get("start_date"),
                        "end_date": assignment.get("end_date"),
                        "status": assignment.get("status"),
                        "planned_hours_per_week": assignment.get("planned_hours_per_week"),
                        "source_rows": _source_rows(assignment, source_catalog),
                    }
                )
            if capacity is None or unresolved_ids:
                missing_evidence.append(
                    {
                        "week_start_date": overload.get("week_start_date"),
                        "employee_id": overload.get("employee_id"),
                        "unresolved_assignment_ids": sorted(unresolved_ids),
                        "capacity_record_id": overload.get("capacity_record_id"),
                    }
                )
                continue
            findings.append(
                {
                    "finding_id": (
                        f"{observation_state}:{overload.get('week_start_date')}"
                        f":{overload.get('employee_id')}"
                    ),
                    "state": observation_state,
                    "employee_id": overload.get("employee_id"),
                    "employee_code": overload.get("employee_code"),
                    "week_start_date": overload.get("week_start_date"),
                    "unit": "planned hours per week",
                    "available_hours": overload.get("available_hours"),
                    "committed_hours_per_week": overload.get("committed_hours_per_week"),
                    "recorded_excess_hours": overload.get("overload_hours"),
                    "capacity_evidence": _capacity_evidence(capacity, source_catalog),
                    "assignment_evidence": sorted(
                        assignment_evidence,
                        key=lambda item: (
                            str(item.get("project_code")).casefold(),
                            str(item.get("record_id")),
                        ),
                    ),
                    "capacity_currentness_attestation": (
                        "attested" if capacity_attested else "not_attested"
                    ),
                    "interpretation_limit": (
                        "Recorded committed assignment hours exceed the recorded capacity value. "
                        "This is not proof that every commitment is recorded or that the employee "
                        "performed this amount of work."
                    ),
                    "limitations": [
                        (
                            "M5 capacity status is confirmed; this does not attest that the "
                            "source is current."
                        ),
                        (
                            "Only recorded committed assignments are included; completeness "
                            "is not guaranteed."
                        ),
                        "Planned assignment hours are not actual work, timesheets, or utilization.",
                    ],
                }
            )
    findings.sort(
        key=lambda item: (
            str(item.get("week_start_date")),
            str(item.get("employee_code")).casefold(),
            str(item.get("state")),
        )
    )
    return findings, missing_evidence


def _unresolved_findings(
    current_run: Mapping[str, object], source_catalog: Mapping[str, dict[str, object]]
) -> list[dict[str, object]]:
    input_snapshot = _object(current_run.get("input_snapshot"))
    result_snapshot = _object(current_run.get("result_snapshot"))
    result: list[dict[str, object]] = []
    for employee in _objects(result_snapshot.get("unresolved_employee_comparisons")):
        employee_weeks: list[dict[str, object]] = []
        for week in _objects(employee.get("weeks")):
            source_rows: list[dict[str, object]] = []
            for source in _objects(week.get("assignment_sources")):
                fact = _assignment_fact(input_snapshot, source.get("assignment_id"))
                if fact is not None:
                    source_rows.append(
                        {
                            "record_id": fact.get("id"),
                            "project_code": fact.get("project_code"),
                            "planned_hours_per_week": fact.get("planned_hours_per_week"),
                            "source_rows": _source_rows(fact, source_catalog),
                        }
                    )
            employee_weeks.append(
                {
                    "week_start_date": week.get("week_start_date"),
                    "capacity_state": week.get("capacity_state"),
                    "recorded_committed_hours_per_week": week.get("committed_hours_per_week"),
                    "reasons": week.get("reasons", []),
                    "assignment_evidence": source_rows,
                }
            )
        result.append(
            {
                "employee_id": employee.get("employee_id"),
                "employee_code": employee.get("employee_code"),
                "weeks": employee_weeks,
                "state": "capacity_comparison_unresolved",
                "interpretation_limit": (
                    "The M5 input lacks one confirmed or estimated capacity value required for "
                    "this employee-week comparison. Missing capacity is not zero."
                ),
            }
        )
    return sorted(result, key=lambda item: str(item.get("employee_code")).casefold())


def _comparison_reasons(
    baseline_run: Mapping[str, object],
    current_run: Mapping[str, object],
    attestations: Mapping[str, object],
) -> list[str]:
    reasons: list[str] = []
    if baseline_run.get("company_id") != current_run.get("company_id"):
        reasons.append("metric_run_company_scope_mismatch")
    if (
        baseline_run.get("metric_catalog_version") != METRIC_CATALOG_VERSION
        or current_run.get("metric_catalog_version") != METRIC_CATALOG_VERSION
        or baseline_run.get("metric_catalog_version") != current_run.get("metric_catalog_version")
        or baseline_run.get("calculation_version") != CALCULATION_VERSION
        or current_run.get("calculation_version") != CALCULATION_VERSION
        or baseline_run.get("calculation_version") != current_run.get("calculation_version")
    ):
        reasons.append("metric_definition_or_calculation_version_incompatible")
    if (
        _integer(baseline_run.get("week_count")) != 4
        or _integer(current_run.get("week_count")) != 4
    ):
        reasons.append("four_week_metric_windows_required")
    try:
        baseline_start = date.fromisoformat(str(baseline_run.get("start_week_date")))
        baseline_end = date.fromisoformat(str(baseline_run.get("end_week_date")))
        current_start = date.fromisoformat(str(current_run.get("start_week_date")))
        current_end = date.fromisoformat(str(current_run.get("end_week_date")))
        if (
            baseline_start.isoweekday() != 1
            or current_start.isoweekday() != 1
            or baseline_end - baseline_start != timedelta(days=27)
            or current_end - current_start != timedelta(days=27)
            or current_start != baseline_end + timedelta(days=1)
        ):
            reasons.append("metric_windows_not_adjacent_four_week_periods")
    except (TypeError, ValueError):
        reasons.append("metric_period_invalid")

    baseline_input = _object(baseline_run.get("input_snapshot"))
    current_input = _object(current_run.get("input_snapshot"))
    baseline_cohort = _active_employee_ids(baseline_input)
    current_cohort = _active_employee_ids(current_input)
    if not baseline_cohort or not current_cohort or baseline_cohort != current_cohort:
        reasons.append("active_employee_cohort_changed_or_unavailable")
    if attestations.get("m5_definition_acknowledged") is not True:
        reasons.append("m5_draft_definition_not_acknowledged")
    if attestations.get("active_cohort_comparability_attested") is not True:
        reasons.append("active_cohort_comparability_not_attested")
    if attestations.get("committed_assignment_completeness_attested") is not True:
        reasons.append("committed_assignment_completeness_not_attested")

    for name, run in (("baseline", baseline_run), ("current", current_run)):
        result_snapshot = _object(run.get("result_snapshot"))
        weekly = _objects(result_snapshot.get("weekly"))
        if len(weekly) != 4:
            reasons.append(f"{name}_weekly_results_incomplete")
            continue
        for item in weekly:
            assignments = _object(item.get("assignments"))
            if (
                assignments.get("committed_state") != "recorded"
                or _integer(assignments.get("active_employee_committed_assignment_count"))
                in {None, 0}
                or _decimal(assignments.get("active_employee_committed_hours_per_week")) is None
            ):
                reasons.append(f"{name}_committed_assignments_not_recorded_for_every_week")
                break
            if _integer(assignments.get("inactive_employee_committed_assignment_count")) != 0:
                reasons.append(f"{name}_inactive_employee_commitments_present")
                break
    return sorted(set(reasons))


def _operational_comparison(
    baseline_run: Mapping[str, object],
    current_run: Mapping[str, object],
    attestations: Mapping[str, object],
    source_catalog: Mapping[str, dict[str, object]],
) -> dict[str, object]:
    reasons = _comparison_reasons(baseline_run, current_run, attestations)
    baseline_evidence = _assignment_evidence(baseline_run, source_catalog)
    current_evidence = _assignment_evidence(current_run, source_catalog)
    if (
        len(baseline_evidence) > MAX_REVIEW_ASSIGNMENT_EVIDENCE
        or len(current_evidence) > MAX_REVIEW_ASSIGNMENT_EVIDENCE
    ):
        reasons = sorted(set([*reasons, "assignment_evidence_limit_exceeded"]))

    baseline_totals: list[Decimal] = []
    current_totals: list[Decimal] = []
    for _name, run, totals in (
        ("baseline", baseline_run, baseline_totals),
        ("current", current_run, current_totals),
    ):
        for item in _objects(_object(run.get("result_snapshot")).get("weekly")):
            assignments = _object(item.get("assignments"))
            value = _decimal(assignments.get("active_employee_committed_hours_per_week"))
            if value is not None:
                totals.append(value)
    if len(baseline_totals) != 4:
        reasons = sorted(set([*reasons, "baseline_weekly_assignment_totals_incomplete"]))
    if len(current_totals) != 4:
        reasons = sorted(set([*reasons, "current_weekly_assignment_totals_incomplete"]))

    state = "available" if not reasons else "abstained"
    comparison: dict[str, object] = {
        "state": state,
        "metric_name": "Recorded committed assignment hours",
        "unit": "planned hours per week, summed across four weekly buckets",
        "baseline_period": _period(baseline_run),
        "current_period": _period(current_run),
        "baseline_metric_run": _run_reference(baseline_run),
        "current_metric_run": _run_reference(current_run),
        "reason_codes": reasons,
        "weekly_values": {
            "baseline": [
                {
                    "week_start_date": item.get("week_start_date"),
                    "committed_state": _object(item.get("assignments")).get("committed_state"),
                    "active_employee_committed_hours_per_week": _object(
                        item.get("assignments")
                    ).get("active_employee_committed_hours_per_week"),
                    "assignment_completeness": _object(item.get("assignments")).get(
                        "assignment_completeness"
                    ),
                }
                for item in _objects(_object(baseline_run.get("result_snapshot")).get("weekly"))
            ],
            "current": [
                {
                    "week_start_date": item.get("week_start_date"),
                    "committed_state": _object(item.get("assignments")).get("committed_state"),
                    "active_employee_committed_hours_per_week": _object(
                        item.get("assignments")
                    ).get("active_employee_committed_hours_per_week"),
                    "assignment_completeness": _object(item.get("assignments")).get(
                        "assignment_completeness"
                    ),
                }
                for item in _objects(_object(current_run.get("result_snapshot")).get("weekly"))
            ],
        },
        "baseline_assignment_evidence": baseline_evidence[:MAX_REVIEW_ASSIGNMENT_EVIDENCE],
        "current_assignment_evidence": current_evidence[:MAX_REVIEW_ASSIGNMENT_EVIDENCE],
        "interpretation_limit": (
            "This compares recorded planned weekly assignment values across four Monday-Sunday "
            "buckets. It is not actual work, timesheets, utilization, or a business-cause finding."
        ),
    }
    if state == "available":
        baseline_total = sum(baseline_totals, Decimal("0"))
        current_total = sum(current_totals, Decimal("0"))
        comparison["baseline_recorded_planned_hours"] = decimal_text(baseline_total)
        comparison["current_recorded_planned_hours"] = decimal_text(current_total)
        comparison["change_in_recorded_planned_hours"] = decimal_text(
            current_total - baseline_total
        )
        comparison["change_statement"] = (
            "The current four-week input records a different sum of planned assignment hours "
            "across the four weekly buckets. The evidence identifies source rows, not why the "
            "plan changed."
        )
    else:
        comparison["abstention"] = (
            "No four-week planned-assignment change is calculated because one or more period, "
            "version, cohort, source-completeness, or human-attestation requirements are not met."
        )
    return comparison


def _financial_observations(
    financial_import: dict[str, object] | None,
) -> dict[str, object]:
    if financial_import is None:
        return {
            "state": "not_provided",
            "comparison_state": "abstained",
            "observations": [],
            "abstention_reasons": ["financial_source_not_provided"],
            "limitations": [
                "No financial observation file was provided.",
                (
                    "No company-level finance formula, classification, comparison, or "
                    "conclusion is implemented."
                ),
            ],
        }
    observations: list[dict[str, object]] = []
    for row in _objects(financial_import.get("observations")):
        if _objects(row.get("issues")):
            continue
        observations.append(
            {
                "source_label": row.get("source_label"),
                "source_value_text": row.get("source_value_text"),
                "currency_code": row.get("currency_code"),
                "period_start": row.get("period_start"),
                "period_end": row.get("period_end"),
                "source_report": row.get("source_report"),
                "exported_at": row.get("exported_at"),
                "source_record_id": row.get("source_record_id"),
                "source_import_id": financial_import.get("id"),
                "source_file_name": financial_import.get("file_name"),
                "source_file_hash": financial_import.get("file_hash"),
                "worksheet_name": row.get("worksheet_name"),
                "row_number": row.get("row_number"),
                "row_hash": row.get("row_hash"),
                "mapping_schema_version": financial_import.get("schema_version"),
            }
        )
    observations.sort(
        key=lambda item: (
            str(item.get("source_label")).casefold(),
            str(item.get("period_start")),
            str(item.get("period_end")),
            _integer(item.get("row_number")) or 0,
        )
    )
    return {
        "state": "source_native_observations" if observations else "no_valid_observations",
        "comparison_state": "abstained",
        "source_import": {
            "id": financial_import.get("id"),
            "file_name": financial_import.get("file_name"),
            "file_hash": financial_import.get("file_hash"),
            "validation_hash": financial_import.get("validation_hash"),
            "schema_version": financial_import.get("schema_version"),
            "source_report": financial_import.get("source_report"),
            "exported_at": financial_import.get("exported_at"),
        },
        "observations": observations,
        "abstention_reasons": [
            "no_approved_financial_policy",
            "no_financial_comparison_or_variance_calculated",
        ],
        "limitations": [
            (
                f"Financial policy catalog status is {FINANCIAL_POLICY_CATALOG_STATUS}; "
                "no approved company-level policy exists."
            ),
            "Source values, labels, currencies, periods, and row references remain verbatim.",
            (
                "No financial aggregation, variance, conversion, classification, verdict, "
                "or recommendation is calculated."
            ),
            (
                "Period comparability is not approved or inferred; observations are not "
                "compared side by side."
            ),
        ],
    }


def build_position_review_payload(
    baseline_run: dict[str, object],
    current_run: dict[str, object],
    demo_seed: dict[str, object],
    operational_imports: list[dict[str, object]],
    financial_import: dict[str, object] | None,
    attestations: dict[str, object],
    source_catalog: list[dict[str, object]],
) -> tuple[dict[str, object], dict[str, object]]:
    """Build deterministic, source-linked, explicitly limited M10 snapshots."""
    source_by_id = _source_catalog_map(source_catalog)
    comparison = _operational_comparison(baseline_run, current_run, attestations, source_by_id)
    confirmed_and_estimated, unresolved_evidence = _overload_findings(
        current_run,
        source_by_id,
        attestations.get("current_capacity_currentness_attested") is True,
    )
    unresolved = _unresolved_findings(current_run, source_by_id)
    if unresolved_evidence:
        unresolved.append(
            {
                "state": "input_snapshot_evidence_unresolved",
                "items": unresolved_evidence,
                "interpretation_limit": (
                    "An M5 finding was withheld because its exact input record "
                    "could not be resolved."
                ),
            }
        )
    result_financial = _financial_observations(financial_import)

    run_company_id = current_run.get("company_id")
    run_tenant_id = current_run.get("tenant_id")
    seed_reference = {
        "id": demo_seed.get("id"),
        "seed_version": demo_seed.get("seed_version"),
        "manifest_hash": demo_seed.get("manifest_hash"),
        "current_template_hashes": demo_seed.get("current_template_hashes", {}),
        "synthetic_label": demo_seed.get("synthetic_label", DEMO_SYNTHETIC_LABEL),
        "baseline_metric_run_id": demo_seed.get("baseline_metric_run_id"),
        "baseline_start_week_date": demo_seed.get("baseline_start_week_date"),
        "baseline_end_week_date": demo_seed.get("baseline_end_week_date"),
        "current_start_week_date": demo_seed.get("current_start_week_date"),
    }
    attestation_snapshot = dict(attestations)
    input_snapshot: dict[str, object] = {
        "snapshot_schema_version": POSITION_REVIEW_SCHEMA_VERSION,
        "scope": {"tenant_id": run_tenant_id, "company_id": run_company_id},
        "synthetic_label": DEMO_SYNTHETIC_LABEL,
        "calculation_version": POSITION_REVIEW_CALCULATION_VERSION,
        "demo_seed": seed_reference,
        "baseline_metric_run": _run_reference(baseline_run),
        "current_metric_run": _run_reference(current_run),
        "operational_imports": sorted(
            operational_imports, key=lambda item: str(item.get("dataset_type"))
        ),
        "source_import_catalog": sorted(
            source_catalog, key=lambda item: str(item.get("import_id"))
        ),
        "financial_import": financial_import,
        "attestations": attestation_snapshot,
    }

    limitations = [
        "The M5 catalog is an implementation draft with observational-only, no-recommendation use.",
        (
            "Recorded available capacity and planned committed assignment hours are "
            "operational facts, not financial performance."
        ),
        (
            "Assignment completeness is not attested by M5; human attestation is stored "
            "separately and does not prove source completeness."
        ),
        (
            "Current employee status is applied across the requested horizon because "
            "employment-status history is unavailable."
        ),
        (
            "A full weekly planned assignment value contributes to every overlapping "
            "Monday-Sunday week; no daily proration occurs."
        ),
        "Planned assignment hours are not actual work, timesheets, utilization, or elapsed effort.",
        (
            "No financial formula, variance, conversion, classification, verdict, causal "
            "explanation, or recommendation is produced."
        ),
        (
            "The demonstration is synthetic and does not establish customer-data readiness "
            "or production suitability."
        ),
    ]
    for run in (baseline_run, current_run):
        for limitation in _objects(_object(run.get("result_snapshot")).get("limitations")):
            value = limitation.get("value")
            if isinstance(value, str) and value not in limitations:
                limitations.append(value)
        raw_limitations = _object(run.get("result_snapshot")).get("limitations")
        if isinstance(raw_limitations, list):
            for value in cast(list[object], raw_limitations):
                if isinstance(value, str) and value not in limitations:
                    limitations.append(value)

    abstentions: list[dict[str, object]] = []
    if comparison.get("state") == "abstained":
        abstentions.append(
            {
                "area": "four_week_operational_change",
                "reason_codes": comparison.get("reason_codes", []),
                "message": comparison.get("abstention"),
            }
        )
    if result_financial.get("state") != "source_native_observations":
        abstentions.append(
            {
                "area": "financial_observations",
                "reason_codes": result_financial.get("abstention_reasons", []),
                "message": "No source-native financial observations are available in this brief.",
            }
        )
    abstentions.append(
        {
            "area": "financial_comparison",
            "reason_codes": result_financial.get("abstention_reasons", []),
            "message": (
                "Financial periods are not compared; no approved comparability or "
                "calculation policy is configured."
            ),
        }
    )

    result_snapshot: dict[str, object] = {
        "result_schema_version": POSITION_REVIEW_SCHEMA_VERSION,
        "calculation_version": POSITION_REVIEW_CALCULATION_VERSION,
        "synthetic_label": DEMO_SYNTHETIC_LABEL,
        "state": "review_ready_with_limitations",
        "scope": {"tenant_id": run_tenant_id, "company_id": run_company_id},
        "operational": {
            "metric_catalog_version": current_run.get("metric_catalog_version"),
            "metric_calculation_version": current_run.get("calculation_version"),
            "metric_catalog_status": _object(current_run.get("result_snapshot")).get(
                "metric_catalog_status"
            ),
            "decision_use": _object(current_run.get("result_snapshot")).get("decision_use"),
            "comparison": comparison,
            "current_capacity_reliability": _object(
                _object(current_run.get("result_snapshot")).get("reliability")
            ).get("capacity"),
            "current_recorded_capacity_findings": confirmed_and_estimated,
            "current_unresolved_capacity_comparisons": unresolved,
            "evidence_state": (
                "complete_for_reported_findings" if not unresolved_evidence else "partial"
            ),
        },
        "financial": result_financial,
        "abstentions": abstentions,
        "limitations": limitations,
        "human_attestations": attestation_snapshot,
        "external_action_taken": False,
    }
    return input_snapshot, result_snapshot


def position_review_hashes(
    input_snapshot: dict[str, object], result_snapshot: dict[str, object]
) -> tuple[str, str]:
    return content_hash(input_snapshot), content_hash(result_snapshot)
