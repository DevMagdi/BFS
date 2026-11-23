from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from businessflow.metrics.catalog import metric_definition_payloads
from businessflow.metrics.insight_service import (
    _metric_input_facts,
    _period_completion_payload,
)
from businessflow.metrics.insights import (
    COMPARISON_DEFINITION_VERSION,
    INTERPRETATION_UNAVAILABLE_REASON,
    build_comparison_result,
    extract_metric_value,
    metric_insight_catalog_payload,
)
from businessflow.metrics.snapshot import content_hash

PERIOD_COMPLETION = {
    "status": "completed",
    "checked_at": "2026-12-01T00:00:00.000000+00:00",
    "timezone": "UTC",
    "as_of_local_date": "2026-12-01",
    "completion_rule": (
        "period_end_date_before_company_local_date_and_m5_run_created_at_at_or_after_"
        "local_midnight_after_period_end"
    ),
    "periods": {
        "baseline": {
            "period_end_date": "2026-11-22",
            "period_close_at_local": "2026-11-23T00:00:00+00:00",
            "metric_run_created_at": "2026-11-23T00:00:00.000000+00:00",
            "period_ended_before_local_date": True,
            "metric_run_created_after_close": True,
            "status": "completed",
        },
        "comparison": {
            "period_end_date": "2026-11-29",
            "period_close_at_local": "2026-11-30T00:00:00+00:00",
            "metric_run_created_at": "2026-11-30T00:00:00.000000+00:00",
            "period_ended_before_local_date": True,
            "metric_run_created_after_close": True,
            "status": "completed",
        },
    },
}


def _week(
    metric_id: str,
    value: str | None,
    *,
    basis: str = "confirmed",
    availability: str = "confirmed",
    state: str = "calculated_confirmed_capacity",
    reasons: list[str] | None = None,
    week_start: str = "2026-11-16",
) -> dict[str, object]:
    capacity: dict[str, object] = {
        "availability_status": availability,
        "complete_capacity_basis": basis,
        "complete_available_hours": value,
        "reasons": reasons or [],
    }
    assignments: dict[str, object] = {
        "committed_state": "recorded" if value is not None else "not_recorded",
        "active_employee_committed_assignment_count": 1 if value is not None else 0,
        "active_employee_committed_hours_per_week": value,
    }
    comparison: dict[str, object] = {
        "state": state,
        "capacity_basis": basis,
        "capacity_minus_recorded_commitments_hours": value,
        "reasons": reasons or [],
    }
    return {
        "week_start_date": week_start,
        "capacity": capacity,
        "assignments": assignments,
        "comparison": comparison,
        "_metric_id": metric_id,
    }


def _result(week: dict[str, object], *, metric_id: str) -> dict[str, object]:
    return {
        "metric_catalog_version": "businessflow.metrics.v1",
        "metric_catalog_status": "implementation_draft",
        "metric_definition_snapshot": metric_definition_payloads(),
        "weekly": [week],
    }


def _build(
    metric_id: str,
    baseline_week: dict[str, object],
    comparison_week: dict[str, object],
    *,
    evidence: dict[str, object] | None = None,
    abstentions: list[dict[str, str]] | None = None,
    period_completion: dict[str, object] | None = None,
) -> dict[str, object]:
    return build_comparison_result(
        metric_id=metric_id,
        baseline_result=_result(baseline_week, metric_id=metric_id),
        comparison_result=_result(comparison_week, metric_id=metric_id),
        baseline_period={"start_week_date": "2026-11-16", "week_count": 1},
        comparison_period={"start_week_date": "2026-11-23", "week_count": 1},
        period_completion=period_completion or PERIOD_COMPLETION,
        data_evidence=evidence or {"status": "verified", "source_imports": []},
        evidence_abstentions=abstentions or [],
    )


def test_catalog_reuses_m5_definitions_and_marks_unapproved_interpretation_unavailable():
    catalog = metric_insight_catalog_payload()
    assert catalog["catalog_version"] == "businessflow.metric-insight-catalog.v2"
    assert catalog["comparison_definition_version"] == COMPARISON_DEFINITION_VERSION
    assert catalog["source_metric_catalog_version"] == "businessflow.metrics.v1"
    assert catalog["framework_status"] == "descriptive_comparison_only"
    assert catalog["owner_approved_question"] == {
        "status": "unavailable",
        "reason": INTERPRETATION_UNAVAILABLE_REASON,
    }
    assert catalog["interpretation_status"] == "unavailable"
    supported = {item["metric_id"]: item for item in catalog["supported_comparisons"]}
    assert set(supported) == {
        "available_capacity_hours",
        "recorded_committed_hours",
        "capacity_minus_recorded_commitments_hours",
    }
    unavailable = {item["metric_id"] for item in catalog["unavailable_comparisons"]}
    assert unavailable == {
        "recorded_assignment_overload",
        "recorded_skill_inventory_coverage",
    }
    assert all(len(item["definition_hash"]) == 64 for item in supported.values())
    capacity_contracts = {
        item["dataset_type"]: item
        for item in supported["available_capacity_hours"]["required_contracts"]
    }
    assert capacity_contracts["employees"]["contract_key"] == "businessflow.m4.employees"
    assert capacity_contracts["weekly_capacity"]["version"] == "1.0.0"
    percent_contract = supported["available_capacity_hours"]["percentage_change_contract"]
    assert percent_contract["formula"] == (
        "((comparison_value - baseline_value) / baseline_value) * 100"
    )
    assert "zero_baseline_percent_change_undefined" in percent_contract["zero_baseline"]
    assert "ROUND_HALF_EVEN" in percent_contract["rounding"]
    assert "not value judgments" in catalog["direction_semantics"]
    assert "local midnight following its period end" in catalog["period_convention"]


def test_comparison_builder_fails_closed_for_incomplete_periods():
    baseline = _week("available_capacity_hours", "30.00")
    comparison = _week("available_capacity_hours", "35.00", week_start="2026-11-23")

    with pytest.raises(ValueError, match="periods to be completed"):
        _build(
            "available_capacity_hours",
            baseline,
            comparison,
            period_completion={"status": "incomplete"},
        )


def test_confirmed_capacity_delta_is_exact_versioned_and_observational():
    baseline = _week("available_capacity_hours", "100.25")
    comparison = _week("available_capacity_hours", "99.75", week_start="2026-11-23")
    result = _build("available_capacity_hours", baseline, comparison)
    point = result["comparisons"][0]
    assert point["baseline_value"] == "100.25"
    assert point["comparison_value"] == "99.75"
    assert point["delta"] == "-0.50"
    assert point["direction"] == "decreased"
    assert point["value_status"] == "computed"
    assert point["percentage_change"] == "-0.50"
    assert point["percentage_change_status"] == "computed"
    assert point["percentage_change_reason"] is None
    assert result["period_alignment"]["completion_check"] == PERIOD_COMPLETION
    assert result["availability_status"] == "available"
    assert result["insights"]["status"] == "unavailable"
    assert result["percentage_change_contract"]["formula"] == (
        "((comparison_value - baseline_value) / baseline_value) * 100"
    )
    assert result["insights"]["items"] == []
    assert result["causality"]["status"] == "not_evaluated"
    assert "recommendation" in " ".join(result["limitations"])
    assert content_hash(result) == content_hash(
        _build("available_capacity_hours", baseline, comparison)
    )


def test_estimated_values_remain_estimated_and_decimal_delta_is_not_rounded_away():
    baseline = _week(
        "available_capacity_hours",
        "30.01",
        basis="estimated",
        availability="estimated",
    )
    comparison = _week(
        "available_capacity_hours",
        "30.00",
        basis="estimated",
        availability="estimated",
        week_start="2026-11-23",
    )
    result = _build("available_capacity_hours", baseline, comparison)
    point = result["comparisons"][0]
    assert point["value_status"] == "estimated"
    assert point["baseline_basis"] == point["comparison_basis"] == "estimated"
    assert point["delta"] == "-0.01"
    assert point["direction"] == "decreased"
    assert point["percentage_change"] == "-0.03"
    assert point["percentage_change_status"] == "computed"


def test_zero_baseline_has_valid_delta_but_explicitly_undefined_percentage_change():
    baseline = _week("available_capacity_hours", "0.00")
    comparison = _week("available_capacity_hours", "5.00", week_start="2026-11-23")

    point = _build("available_capacity_hours", baseline, comparison)["comparisons"][0]

    assert point["delta"] == "5.00"
    assert point["direction"] == "increased"
    assert point["percentage_change"] is None
    assert point["percentage_change_status"] == "unavailable"
    assert point["percentage_change_reason"] == "zero_baseline_percent_change_undefined"


def test_percentage_change_uses_baseline_denominator_and_two_decimal_rounding():
    baseline = _week("available_capacity_hours", "3.00")
    comparison = _week("available_capacity_hours", "4.00", week_start="2026-11-23")

    point = _build("available_capacity_hours", baseline, comparison)["comparisons"][0]

    assert point["delta"] == "1.00"
    assert point["percentage_change"] == "33.33"
    assert point["percentage_change_status"] == "computed"


def test_percentage_change_rounding_uses_decimal_half_even_for_ties():
    baseline = _week("available_capacity_hours", "200.00")
    comparison = _week("available_capacity_hours", "202.01", week_start="2026-11-23")

    point = _build("available_capacity_hours", baseline, comparison)["comparisons"][0]

    assert point["percentage_change"] == "1.00"

    round_up_to_even = _week("available_capacity_hours", "202.03", week_start="2026-11-23")
    point = _build("available_capacity_hours", baseline, round_up_to_even)["comparisons"][0]
    assert point["percentage_change"] == "1.02"


def test_negative_baseline_uses_signed_denominator_without_value_judgment():
    baseline = _week("capacity_minus_recorded_commitments_hours", "-10.00")
    comparison = _week(
        "capacity_minus_recorded_commitments_hours", "-5.00", week_start="2026-11-23"
    )

    result = _build("capacity_minus_recorded_commitments_hours", baseline, comparison)
    point = result["comparisons"][0]

    assert point["delta"] == "5.00"
    assert point["direction"] == "increased"
    assert point["percentage_change"] == "-50.00"
    assert point["percentage_change_status"] == "computed"
    assert result["insights"]["status"] == "unavailable"
    assert result["causality"]["status"] == "not_evaluated"


def test_mixed_capacity_basis_is_visible_but_not_differenced():
    baseline = _week("available_capacity_hours", "50.00", basis="mixed", availability="mixed")
    comparison = _week(
        "available_capacity_hours",
        "55.00",
        basis="estimated",
        availability="estimated",
        week_start="2026-11-23",
    )
    result = _build("available_capacity_hours", baseline, comparison)
    point = result["comparisons"][0]
    assert point["value_status"] == "estimated"
    assert point["baseline_value"] == "50.00"
    assert point["comparison_value"] == "55.00"
    assert point["delta"] is None
    assert point["percentage_change"] is None
    assert point["percentage_change_reason"] == "mixed_capacity_basis_not_comparable"
    assert result["availability_status"] == "abstained"
    assert result["abstentions"] == [
        {"target": "week_1", "reason": "mixed_capacity_basis_not_comparable"}
    ]


def test_missing_source_evidence_abstains_without_emitting_values():
    baseline = _week("available_capacity_hours", "30.00")
    comparison = _week("available_capacity_hours", "35.00", week_start="2026-11-23")
    result = _build(
        "available_capacity_hours",
        baseline,
        comparison,
        evidence={"status": "incomplete_or_untrusted"},
        abstentions=[{"target": "capacity-import", "reason": "source_freshness_stale"}],
    )
    assert result["availability_status"] == "abstained"
    assert result["comparisons"] == []
    assert result["abstentions"] == [
        {"target": "capacity-import", "reason": "source_freshness_stale"}
    ]
    assert result["data_evidence"]["status"] == "incomplete_or_untrusted"


def test_unverified_evidence_status_abstains_even_without_reason_details():
    baseline = _week("available_capacity_hours", "30.00")
    comparison = _week("available_capacity_hours", "35.00", week_start="2026-11-23")

    result = _build(
        "available_capacity_hours",
        baseline,
        comparison,
        evidence={"status": "incomplete_or_untrusted"},
    )

    assert result["availability_status"] == "abstained"
    assert result["comparisons"] == []
    assert result["abstentions"] == [
        {"target": "data_evidence", "reason": "source_evidence_not_verified"}
    ]


def test_m5_conflicting_state_is_visible_and_never_differenced():
    baseline = _week("recorded_committed_hours", "20.00", reasons=["invalid_assignment_hours"])
    comparison = _week("recorded_committed_hours", "18.00", week_start="2026-11-23")
    result = _build("recorded_committed_hours", baseline, comparison)
    assert result["availability_status"] == "abstained"
    assert result["comparisons"][0]["value_status"] == "conflicting"
    assert result["comparisons"][0]["delta"] is None
    assert result["comparisons"][0]["percentage_change"] is None
    assert result["comparisons"][0]["percentage_change_reason"] == "invalid_assignment_hours"
    assert {item["reason"] for item in result["abstentions"]} == {"invalid_assignment_hours"}


def test_top_level_m5_assignment_conflict_is_not_lost_by_weekly_projection():
    baseline_week = _week("recorded_committed_hours", "20.00")
    comparison_week = _week("recorded_committed_hours", "18.00", week_start="2026-11-23")
    baseline_result = _result(baseline_week, metric_id="recorded_committed_hours")
    comparison_result = _result(comparison_week, metric_id="recorded_committed_hours")
    baseline_result["reliability"] = {
        "committed_assignments": {"reasons": ["committed_assignments_for_inactive_employees"]}
    }
    result = build_comparison_result(
        metric_id="recorded_committed_hours",
        baseline_result=baseline_result,
        comparison_result=comparison_result,
        baseline_period={"start_week_date": "2026-11-16", "week_count": 1},
        comparison_period={"start_week_date": "2026-11-23", "week_count": 1},
        period_completion=PERIOD_COMPLETION,
        data_evidence={"status": "verified"},
        evidence_abstentions=[],
    )
    assert result["availability_status"] == "abstained"
    assert result["comparisons"][0]["value_status"] == "conflicting"
    assert result["comparisons"][0]["delta"] is None
    assert any(
        item["reason"] == "committed_assignments_for_inactive_employees"
        for item in result["abstentions"]
    )


def test_missing_metric_values_stay_unknown_not_zero():
    baseline_week = _week("recorded_committed_hours", None)
    comparison_week = _week("recorded_committed_hours", "8.00", week_start="2026-11-23")
    baseline = extract_metric_value("recorded_committed_hours", baseline_week)
    assert baseline.value is None
    assert baseline.status == "unavailable"
    result = _build("recorded_committed_hours", baseline_week, comparison_week)
    assert result["comparisons"][0]["baseline_value"] is None
    assert result["comparisons"][0]["delta"] is None
    assert result["comparisons"][0]["percentage_change"] is None
    assert result["comparisons"][0]["percentage_change_reason"] == (
        "committed_assignments_not_recorded_for_active_employees"
    )
    assert result["availability_status"] == "abstained"


def test_basis_change_is_visible_and_does_not_create_a_delta():
    baseline = _week("available_capacity_hours", "40.00", basis="confirmed")
    comparison = _week(
        "available_capacity_hours",
        "42.00",
        basis="estimated",
        availability="estimated",
        week_start="2026-11-23",
    )
    result = _build("available_capacity_hours", baseline, comparison)
    assert result["comparisons"][0]["delta"] is None
    assert result["comparisons"][0]["percentage_change"] is None
    assert result["comparisons"][0]["percentage_change_reason"] == "metric_basis_changed"
    assert {item["reason"] for item in result["abstentions"]} == {"metric_basis_changed"}
    assert result["availability_status"] == "abstained"


def test_metric_definition_mismatch_fails_closed():
    baseline_result = _result(
        _week("available_capacity_hours", "10.00"), metric_id="available_capacity_hours"
    )
    comparison_result = _result(
        _week("available_capacity_hours", "11.00"), metric_id="available_capacity_hours"
    )
    comparison_result["metric_definition_snapshot"] = [
        {**item, "version": "2.0.0"} if item["metric_id"] == "available_capacity_hours" else item
        for item in comparison_result["metric_definition_snapshot"]
    ]
    with pytest.raises(ValueError, match="definitions differ"):
        build_comparison_result(
            metric_id="available_capacity_hours",
            baseline_result=baseline_result,
            comparison_result=comparison_result,
            baseline_period={"start_week_date": "2026-11-16", "week_count": 1},
            comparison_period={"start_week_date": "2026-11-23", "week_count": 1},
            period_completion=PERIOD_COMPLETION,
            data_evidence={"status": "verified"},
            evidence_abstentions=[],
        )


def test_committed_assignment_lineage_includes_inactive_employee_conflicts():
    active_employee = {
        "id": "employee-active",
        "employment_status": "active",
        "lineage": [
            {
                "import_id": "employees-active-import",
                "source_row_id": "employees-active-row",
                "dataset_type": "employees",
                "import_status": "applied",
                "mapping_version": 1,
                "file_hash": "a" * 64,
                "worksheet_name": "CSV",
                "row_number": 2,
                "row_hash": "b" * 64,
                "canonical_fingerprint_at_import": "c" * 64,
            }
        ],
    }
    inactive_employee = {
        "id": "employee-inactive",
        "employment_status": "inactive",
        "lineage": [
            {
                "import_id": "employees-inactive-import",
                "source_row_id": "employees-inactive-row",
                "dataset_type": "employees",
                "import_status": "applied",
                "mapping_version": 1,
                "file_hash": "d" * 64,
                "worksheet_name": "CSV",
                "row_number": 3,
                "row_hash": "e" * 64,
                "canonical_fingerprint_at_import": "f" * 64,
            }
        ],
    }
    assignment = {
        "id": "assignment-inactive",
        "employee_id": inactive_employee["id"],
        "status": "committed",
        "start_date": "2026-11-16",
        "end_date": "2026-11-22",
        "lineage": [
            {
                "import_id": "assignments-import",
                "source_row_id": "assignments-row",
                "dataset_type": "resource_assignments",
                "import_status": "applied",
                "mapping_version": 1,
                "file_hash": "1" * 64,
                "worksheet_name": "CSV",
                "row_number": 2,
                "row_hash": "2" * 64,
                "canonical_fingerprint_at_import": "3" * 64,
            }
        ],
    }
    run = SimpleNamespace(
        input_snapshot={
            "employees": [active_employee, inactive_employee],
            "resource_assignments": [assignment],
        },
        start_week_date=date(2026, 11, 16),
        end_week_date=date(2026, 11, 22),
    )

    lineage, missing = _metric_input_facts(run, "recorded_committed_hours", role="baseline")

    assert missing == []
    assert len(lineage) == 3
    assert {item["source_row_id"] for item in lineage} == {
        "employees-active-row",
        "employees-inactive-row",
        "assignments-row",
    }


def test_period_completion_requires_elapsed_week_and_post_close_m5_snapshot():
    baseline_end = date(2026, 11, 22)
    comparison_end = date(2026, 11, 29)
    baseline = SimpleNamespace(
        end_week_date=baseline_end,
        created_at=datetime(2026, 11, 23, 0, 0, tzinfo=UTC),
    )
    comparison = SimpleNamespace(
        end_week_date=comparison_end,
        created_at=datetime(2026, 11, 30, 0, 0, tzinfo=UTC),
    )
    completion = _period_completion_payload(
        baseline,
        comparison,
        timezone_name="UTC",
        checked_at=datetime(2026, 12, 1, 12, 0, tzinfo=UTC),
    )
    assert completion["status"] == "completed"
    assert completion["periods"]["baseline"]["period_close_at_local"] == (
        "2026-11-23T00:00:00+00:00"
    )

    early_baseline = SimpleNamespace(
        end_week_date=baseline_end,
        created_at=datetime(2026, 11, 22, 23, 59, tzinfo=UTC),
    )
    incomplete = _period_completion_payload(
        early_baseline,
        comparison,
        timezone_name="UTC",
        checked_at=datetime(2026, 12, 1, 12, 0, tzinfo=UTC),
    )
    assert incomplete["status"] == "incomplete"
    assert incomplete["periods"]["baseline"]["metric_run_created_after_close"] is False

    still_open = _period_completion_payload(
        baseline,
        comparison,
        timezone_name="UTC",
        checked_at=datetime(2026, 11, 29, 23, 59, tzinfo=UTC),
    )
    assert still_open["status"] == "incomplete"
    assert still_open["periods"]["comparison"]["period_ended_before_local_date"] is False


def test_period_completion_uses_the_metric_runs_company_timezone():
    cairo = ZoneInfo("Africa/Cairo")
    baseline = SimpleNamespace(
        end_week_date=date(2025, 11, 9),
        created_at=datetime(2025, 11, 10, 0, 0, tzinfo=cairo),
    )
    comparison = SimpleNamespace(
        end_week_date=date(2025, 11, 16),
        created_at=datetime(2025, 11, 17, 0, 0, tzinfo=cairo),
    )

    completion = _period_completion_payload(
        baseline,
        comparison,
        timezone_name="Africa/Cairo",
        checked_at=datetime(2025, 11, 18, 1, 0, tzinfo=cairo),
    )

    assert completion["status"] == "completed"
    assert completion["as_of_local_date"] == "2025-11-18"
    assert completion["periods"]["baseline"]["period_close_at_local"] == (
        "2025-11-10T00:00:00+02:00"
    )
