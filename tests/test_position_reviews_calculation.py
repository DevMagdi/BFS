from __future__ import annotations

from datetime import date, timedelta

import pytest

from businessflow.metrics.catalog import CALCULATION_VERSION, METRIC_CATALOG_VERSION
from businessflow.position_reviews.calculation import (
    POSITION_REVIEW_CALCULATION_VERSION,
    build_position_review_payload,
    position_review_hashes,
)
from businessflow.position_reviews.demo import (
    DEMO_SYNTHETIC_LABEL,
    OPERATIONAL_DATASETS,
    current_template_csv,
    current_template_hashes,
    demo_manifest_hash,
    next_monday,
    prior_pack_csvs,
)
from businessflow.position_reviews.schemas import PositionReviewDispositionRequest
from businessflow.position_reviews.service import _valid_currency, _validate_financial_rows


def _metric_run(start: date, hours_per_week: str) -> dict[str, object]:
    end = start + timedelta(days=27)
    import_id = "11111111-1111-4111-8111-111111111111"
    employee_id = "22222222-2222-4222-8222-222222222222"
    project_id = "33333333-3333-4333-8333-333333333333"
    lineage = [
        {
            "import_id": import_id,
            "import_status": "applied",
            "dataset_type": "resource_assignments",
            "mapping_version": 1,
            "file_hash": "a" * 64,
            "source_row_id": "44444444-4444-4444-8444-444444444444",
            "worksheet_name": "CSV",
            "row_number": 2,
            "row_hash": "b" * 64,
            "canonical_fingerprint_at_import": "c" * 64,
        }
    ]
    return {
        "id": f"run-{start.isoformat()}",
        "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "company_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "start_week_date": start.isoformat(),
        "end_week_date": end.isoformat(),
        "week_count": 4,
        "metric_catalog_version": METRIC_CATALOG_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "input_hash": "d" * 64,
        "result_hash": "e" * 64,
        "input_snapshot": {
            "scope": {
                "tenant_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                "company_id": "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
            },
            "employees": [
                {
                    "id": employee_id,
                    "employee_code": "SYN-EMP-001",
                    "employment_status": "active",
                    "lineage": [],
                }
            ],
            "weekly_capacity": [],
            "resource_assignments": [
                {
                    "id": "55555555-5555-4555-8555-555555555555",
                    "employee_id": employee_id,
                    "project_id": project_id,
                    "project_code": "SYN-PRJ-001",
                    "start_date": start.isoformat(),
                    "end_date": end.isoformat(),
                    "status": "committed",
                    "planned_hours_per_week": hours_per_week,
                    "lineage": lineage,
                }
            ],
        },
        "result_snapshot": {
            "metric_catalog_status": "implementation_draft",
            "decision_use": "observational_only_no_recommendation",
            "limitations": ["Assignment completeness is unattested."],
            "weekly": [
                {
                    "week_start_date": (start + timedelta(days=7 * index)).isoformat(),
                    "assignments": {
                        "committed_state": "recorded",
                        "active_employee_committed_assignment_count": 1,
                        "active_employee_committed_hours_per_week": hours_per_week,
                        "inactive_employee_committed_assignment_count": 0,
                        "assignment_completeness": "not_attested",
                    },
                }
                for index in range(4)
            ],
            "reliability": {"capacity": {"state": "complete"}},
        },
    }


def _source_catalog() -> list[dict[str, object]]:
    return [
        {
            "import_id": "11111111-1111-4111-8111-111111111111",
            "file_name": "synthetic_demo_resource_assignments.csv",
            "dataset_type": "resource_assignments",
            "file_hash": "a" * 64,
            "validation_hash": "f" * 64,
            "mapping_version": 1,
            "status": "applied",
        }
    ]


def _payload(attestations: dict[str, object]):
    baseline_start = date(2026, 10, 26)
    current_start = baseline_start + timedelta(days=28)
    return build_position_review_payload(
        _metric_run(baseline_start, "20.00"),
        _metric_run(current_start, "30.00"),
        {
            "id": "seed-1",
            "seed_version": "businessflow.m10-demo.v1",
            "manifest_hash": "9" * 64,
            "current_template_hashes": {
                **{dataset: "1" * 64 for dataset in OPERATIONAL_DATASETS},
                "financial_export": "2" * 64,
            },
            "synthetic_label": DEMO_SYNTHETIC_LABEL,
            "baseline_metric_run_id": "baseline-run",
            "baseline_start_week_date": baseline_start.isoformat(),
            "baseline_end_week_date": (baseline_start + timedelta(days=27)).isoformat(),
            "current_start_week_date": current_start.isoformat(),
        },
        [
            {
                "dataset_type": dataset,
                "import_id": f"{index + 1:08d}-1111-4111-8111-111111111111",
                "file_name": f"synthetic_demo_{dataset}.csv",
                "file_hash": "1" * 64,
                "validation_hash": "2" * 64,
                "mapping_version": 1,
                "status": "applied",
            }
            for index, dataset in enumerate(OPERATIONAL_DATASETS)
        ],
        None,
        attestations,
        _source_catalog(),
    )


def test_synthetic_templates_and_manifest_are_deterministic_and_period_bound() -> None:
    monday = date(2026, 11, 23)
    assert next_monday(date(2026, 11, 21)) == monday
    assert next_monday(monday) == monday

    prior = prior_pack_csvs(monday)
    assert tuple(sorted(prior)) == tuple(sorted(OPERATIONAL_DATASETS))
    assert b"SYN-EMP-001" in prior["employees"]
    assert b"SYN-PRJ-001" in prior["projects"]
    assert prior == prior_pack_csvs(monday)
    assert current_template_csv("employees", monday) == prior["employees"]
    hashes = current_template_hashes(monday)
    assert set(hashes) == {*OPERATIONAL_DATASETS, "financial_export"}
    assert all(len(value) == 64 for value in hashes.values())
    assert demo_manifest_hash(monday) == demo_manifest_hash(monday)
    assert demo_manifest_hash(monday) != demo_manifest_hash(monday + timedelta(days=7))
    assert current_template_csv("financial_export", monday) == current_template_csv(
        "financial_export", monday
    )


def test_financial_csv_validation_preserves_source_text_and_never_calculates() -> None:
    current_start = date(2026, 11, 23)
    content = current_template_csv("financial_export", current_start)
    worksheet, observations, report, validation_hash = _validate_financial_rows(
        "synthetic_demo_financial_export.csv", content, 100
    )

    assert worksheet == "CSV"
    assert report["total_rows"] == 4
    assert report["invalid_rows"] == 0
    assert report["financial_calculations_performed"] is False
    assert report["currency_conversion_performed"] is False
    assert observations[0]["source_value_text"] == "125000.00"
    assert observations[0]["currency_code"] == "USD"
    assert len(validation_hash) == 64
    assert _valid_currency("USD")
    assert _valid_currency("EGP")
    assert not _valid_currency("XXX")
    assert not _valid_currency("usd")
    assert not _valid_currency("ABC")


def test_invalid_financial_source_rows_are_explicitly_invalid_not_reinterpreted() -> None:
    content = current_template_csv("financial_export", date(2026, 11, 23))
    invalid_content = content.replace(b",125000.00,", b",,")
    _worksheet, observations, report, _validation_hash = _validate_financial_rows(
        "synthetic_demo_financial_export.csv", invalid_content, 100
    )

    assert report["invalid_rows"] == 1
    assert observations[0]["source_value_text"] is None
    assert observations[0]["issues"][0]["code"] == "required_value_missing"
    assert report["financial_calculations_performed"] is False


def test_position_review_change_is_deterministic_and_keeps_planned_units_and_lineage() -> None:
    attestations = {
        "m5_definition_acknowledged": True,
        "active_cohort_comparability_attested": True,
        "committed_assignment_completeness_attested": True,
        "baseline_capacity_currentness_attested": True,
        "current_capacity_currentness_attested": True,
        "actor_user_id": "user-1",
        "actor_display_name": "Synthetic Reviewer",
        "attested_at": "2026-11-21T12:00:00+00:00",
    }
    input_snapshot, result_snapshot = _payload(attestations)
    replay_input, replay_result = _payload(attestations)

    comparison = result_snapshot["operational"]["comparison"]
    assert comparison["state"] == "available"
    assert comparison["baseline_recorded_planned_hours"] == "80.00"
    assert comparison["current_recorded_planned_hours"] == "120.00"
    assert comparison["change_in_recorded_planned_hours"] == "40.00"
    assert comparison["unit"] == "planned hours per week, summed across four weekly buckets"
    assert comparison["current_assignment_evidence"][0]["source_rows"][0]["row_hash"] == "b" * 64
    assert result_snapshot["financial"]["comparison_state"] == "abstained"
    assert result_snapshot["external_action_taken"] is False
    assert result_snapshot["synthetic_label"] == DEMO_SYNTHETIC_LABEL
    assert input_snapshot["demo_seed"]["current_template_hashes"]["financial_export"] == "2" * 64
    assert POSITION_REVIEW_CALCULATION_VERSION == "businessflow.position-review.v1"
    assert position_review_hashes(input_snapshot, result_snapshot) == position_review_hashes(
        replay_input, replay_result
    )
    assert result_snapshot["operational"]["comparison"]["state"] == "available"


def test_missing_attestations_force_an_operational_abstention_without_a_change_value() -> None:
    attestations = {
        "m5_definition_acknowledged": True,
        "active_cohort_comparability_attested": True,
        "committed_assignment_completeness_attested": False,
    }
    _input, result = _payload(attestations)
    comparison = result["operational"]["comparison"]

    assert comparison["state"] == "abstained"
    assert "committed_assignment_completeness_not_attested" in comparison["reason_codes"]
    assert "change_in_recorded_planned_hours" not in comparison
    assert any(item["area"] == "four_week_operational_change" for item in result["abstentions"])


def test_disposition_rationale_must_contain_non_whitespace_text() -> None:
    with pytest.raises(ValueError):
        PositionReviewDispositionRequest(
            review_hash="a" * 64,
            disposition="noted",
            rationale="   ",
        )

    request = PositionReviewDispositionRequest(
        review_hash="a" * 64,
        disposition="noted",
        rationale="  I reviewed the source evidence.  ",
    )
    assert request.rationale == "I reviewed the source evidence."
