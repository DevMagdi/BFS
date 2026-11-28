from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import cast
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest

from businessflow.forecasting.contracts import ForecastDatasetCreate
from businessflow.forecasting.weekly_work_requests import (
    WeeklyWorkRequestCoverage,
    WeeklyWorkRequestSnapshot,
    WorkRequestReceiptVersion,
    build_weekly_work_request_dataset,
)


def _uuid(name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"https://synthetic.invalid/{name}")


def _hash(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _receipt(
    name: str,
    request_name: str,
    first_received_at: datetime | None,
    recorded_at: datetime,
    *,
    workflow_key: str | None = "ops.intake",
    version: int = 1,
    previous_event_version_id: UUID | None = None,
    receipt_state: str = "received",
    correction_reason: str | None = None,
) -> WorkRequestReceiptVersion:
    payload = {
        "event_id": _uuid(f"event/{name}"),
        "event_version_id": _uuid(f"event-version/{name}/{version}"),
        "source_event_key": f"synthetic-event:{name}",
        "request_key_hash": _hash(f"request/{request_name}"),
        "version": version,
        "previous_event_version_id": previous_event_version_id,
        "event_hash": _hash(f"event-content/{name}/{version}/{first_received_at}"),
        "receipt_state": receipt_state,
        "first_received_at": first_received_at,
        "workflow_key": workflow_key,
        "recorded_at": recorded_at,
        "correction_reason": correction_reason,
    }
    return WorkRequestReceiptVersion.model_validate(payload)


def _coverage(week_start: date) -> WeeklyWorkRequestCoverage:
    close = datetime.combine(week_start + timedelta(days=7), time.min, tzinfo=UTC)
    return WeeklyWorkRequestCoverage.model_validate(
        {
            "week_start": week_start,
            "status": "complete",
            "covered_through": close,
            "known_at": close,
            "evidence_hash": _hash(f"coverage/{week_start}"),
        }
    )


def _base_snapshot(
    *,
    company_id: UUID | None = None,
    timezone_name: str = "Etc/UTC",
    period_start: date = date(2026, 1, 5),
    period_end: date = date(2026, 1, 19),
    snapshot_as_of: datetime = _timestamp("2026-01-27T12:00:00+00:00"),
    receipt_versions: list[WorkRequestReceiptVersion] | None = None,
    period_coverage: list[WeeklyWorkRequestCoverage] | None = None,
) -> WeeklyWorkRequestSnapshot:
    periods = []
    week = period_start
    while week <= period_end:
        periods.append(week)
        week += timedelta(days=7)
    return WeeklyWorkRequestSnapshot.model_validate(
        {
            "data_origin": "synthetic_fixture",
            "company_id": company_id or _uuid("company/one"),
            "source_id": "synthetic.operations-intake",
            "source_profile_version_id": _uuid("source-profile/v1"),
            "source_snapshot_id": _uuid("source-snapshot/one"),
            "workflow_key": "ops.intake",
            "workflow_label": "Operations intake",
            "workflow_support_evidence_hash": _hash("workflow-support/ops.intake"),
            "calendar_timezone": timezone_name,
            "period_start": period_start,
            "period_end": period_end,
            "snapshot_as_of": snapshot_as_of,
            "first_received_semantics": "explicit_synthetic_fixture_timestamp",
            "receipt_versions": receipt_versions or [],
            "period_coverage": period_coverage or [_coverage(item) for item in periods],
        }
    )


def _complete_fixture() -> WeeklyWorkRequestSnapshot:
    jan_5 = date(2026, 1, 5)
    jan_12 = date(2026, 1, 12)
    jan_19 = date(2026, 1, 19)
    request_one = _receipt(
        "req-one-primary",
        "req-one",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:01:00+00:00"),
    )
    request_one_replayed_stream = _receipt(
        "req-one-duplicate-stream",
        "req-one",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:02:00+00:00"),
    )
    request_two = _receipt(
        "req-two",
        "req-two",
        _timestamp("2026-01-06T11:00:00+00:00"),
        _timestamp("2026-01-06T11:01:00+00:00"),
    )
    request_three_v1 = _receipt(
        "req-three",
        "req-three",
        _timestamp("2026-01-12T12:00:00+00:00"),
        _timestamp("2026-01-12T12:01:00+00:00"),
    )
    request_three_v2 = _receipt(
        "req-three",
        "req-three",
        _timestamp("2026-01-12T12:30:00+00:00"),
        _timestamp("2026-01-18T12:00:00+00:00"),
        version=2,
        previous_event_version_id=request_three_v1.event_version_id,
        correction_reason="Corrected the synthetic source timestamp.",
    )
    snapshot = _base_snapshot(
        receipt_versions=[
            request_one,
            request_one_replayed_stream,
            request_two,
            request_two,
            request_three_v1,
            request_three_v2,
        ],
        period_coverage=[_coverage(item) for item in (jan_5, jan_12, jan_19)],
    )
    return snapshot


def test_builds_deterministic_weekly_distinct_request_counts_with_lineage() -> None:
    snapshot = _complete_fixture()

    result = build_weekly_work_request_dataset(snapshot)
    repeated = build_weekly_work_request_dataset(
        snapshot.model_copy(
            update={
                "receipt_versions": list(reversed(snapshot.receipt_versions)),
                "period_coverage": list(reversed(snapshot.period_coverage)),
            }
        )
    )

    assert result == repeated
    assert WeeklyWorkRequestSnapshot.model_validate(snapshot.model_dump(mode="json")) == snapshot
    assert result.readiness.status == "ready_for_synthetic_evaluation"
    assert result.readiness.eligibility_for_rolling_origin == "candidate"
    assert result.readiness.source_authority == "not_established"
    assert result.readiness.period_count == 3
    assert result.readiness.complete_period_count == 3
    assert result.readiness.total_distinct_request_count == 3
    assert result.readiness.duplicate_request_stream_count == 1
    assert result.readiness.replayed_event_version_count == 1
    assert result.readiness.corrected_request_count == 1
    assert result.readiness.periods[0].replayed_event_version_count == 1
    assert result.readiness.periods[1].replayed_event_version_count == 0
    assert result.readiness.warning_codes == [
        "duplicate_request_streams_deduplicated",
        "exact_event_version_replays_deduplicated",
        "receipt_corrections_resolved_as_of_snapshot",
    ]

    dataset = result.dataset
    assert dataset is not None
    assert ForecastDatasetCreate.model_validate(dataset.model_dump(mode="json")) == dataset
    assert dataset.target_key == "operations.weekly_work_requests"
    assert dataset.target_label == "Distinct work requests first received"
    assert dataset.unit == "requests/week"
    assert dataset.frequency == "weekly"
    assert dataset.data_origin == "synthetic_fixture"
    assert [item.value for item in dataset.observations] == ["2", "1", "0"]
    assert all(item.period_coverage == "complete" for item in dataset.observations)
    assert all(item.coverage_evidence_hash is not None for item in dataset.observations)
    assert all(item.dimension_values == {"workflow": "ops.intake"} for item in dataset.observations)
    assert all(
        item.source_reference.source_hash == dataset.source_lineage.source_snapshot_hash
        for item in dataset.observations
    )
    assert dataset.source_lineage is not None
    assert dataset.source_lineage.company_id == snapshot.company_id
    assert dataset.source_lineage.calendar_timezone == "Etc/UTC"
    assert dataset.source_lineage.readiness_status == "ready_for_synthetic_evaluation"
    assert dataset.source_lineage.point_in_time_status == "eligible"
    assert "synthetic_only" in dataset.source_lineage.source_authority


def test_unknown_week_is_missing_not_zero_and_blocks_evaluation_readiness() -> None:
    snapshot = _complete_fixture()
    result = build_weekly_work_request_dataset(
        snapshot.model_copy(
            update={
                "period_coverage": [
                    item
                    for item in snapshot.period_coverage
                    if item.week_start != date(2026, 1, 12)
                ]
            }
        )
    )

    assert result.readiness.status == "blocked"
    assert result.readiness.eligibility_for_rolling_origin == "blocked"
    assert result.readiness.total_distinct_request_count is None
    assert "period_coverage_evidence_missing" in result.readiness.reason_codes
    dataset = result.dataset
    assert dataset is not None
    missing = dataset.observations[1]
    assert missing.period_coverage == "unknown"
    assert missing.value is None
    assert missing.missing_reason == "period_coverage_evidence_missing"
    assert dataset.source_lineage is not None
    assert dataset.source_lineage.readiness_status == "blocked"


def test_late_correction_blocks_point_in_time_evaluation_instead_of_leaking() -> None:
    snapshot = _complete_fixture()
    versions = list(snapshot.receipt_versions)
    original = next(
        item for item in versions if item.source_event_key == "synthetic-event:req-three"
    )
    corrected = _receipt(
        "req-three",
        "req-three",
        _timestamp("2026-01-05T12:30:00+00:00"),
        _timestamp("2026-01-20T12:00:00+00:00"),
        version=2,
        previous_event_version_id=original.event_version_id,
        correction_reason="Late synthetic timestamp correction.",
    )
    versions[-1] = corrected
    result = build_weekly_work_request_dataset(
        snapshot.model_copy(update={"receipt_versions": versions})
    )

    assert result.readiness.status == "blocked"
    assert "receipt_correction_after_origin" in result.readiness.reason_codes
    assert result.readiness.eligibility_for_rolling_origin == "blocked"
    assert result.dataset is not None
    assert result.dataset.source_lineage is not None
    assert result.dataset.source_lineage.point_in_time_status == "blocked"
    assert (
        "receipt_correction_after_origin"
        in result.dataset.source_lineage.point_in_time_reason_codes
    )


def test_late_initial_receipt_blocks_only_the_affected_week() -> None:
    late_receipt = _receipt(
        "late-initial-receipt",
        "late-initial-receipt",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-13T10:00:00+00:00"),
    )

    result = build_weekly_work_request_dataset(_base_snapshot(receipt_versions=[late_receipt]))

    assert result.readiness.status == "blocked"
    assert "receipt_available_after_origin" in result.readiness.reason_codes
    assert result.dataset is not None
    assert result.dataset.source_lineage is not None
    assert result.dataset.source_lineage.point_in_time_status == "blocked"
    assert "receipt_available_after_origin" in result.readiness.periods[0].reason_codes
    assert "receipt_available_after_origin" not in result.readiness.periods[1].reason_codes


def test_late_invalidation_correction_is_attributed_to_the_prior_week() -> None:
    first = _receipt(
        "invalidated-request",
        "invalidated-request",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:01:00+00:00"),
    )
    invalidated = _receipt(
        "invalidated-request",
        "invalidated-request",
        None,
        _timestamp("2026-01-20T10:00:00+00:00"),
        workflow_key=None,
        version=2,
        previous_event_version_id=first.event_version_id,
        receipt_state="invalidated",
        correction_reason="Invalidated an erroneous synthetic receipt.",
    )
    week = date(2026, 1, 5)
    result = build_weekly_work_request_dataset(
        _base_snapshot(
            period_start=week,
            period_end=week,
            receipt_versions=[first, invalidated],
            period_coverage=[_coverage(week)],
        )
    )

    assert result.readiness.status == "blocked"
    assert result.readiness.correction_version_count == 1
    assert result.readiness.periods[0].correction_version_count == 1
    assert result.readiness.periods[0].distinct_request_count == 0
    assert "receipt_correction_after_origin" in result.readiness.periods[0].reason_codes
    assert result.dataset is not None
    assert result.dataset.source_lineage is not None
    assert result.dataset.source_lineage.point_in_time_status == "blocked"


def test_coverage_known_after_week_close_blocks_point_in_time_readiness() -> None:
    week = date(2026, 1, 5)
    late_coverage = _coverage(week).model_copy(
        update={"known_at": _timestamp("2026-01-13T00:00:00+00:00")}
    )
    result = build_weekly_work_request_dataset(
        _base_snapshot(
            period_start=week,
            period_end=week,
            period_coverage=[late_coverage],
        )
    )

    assert result.readiness.status == "blocked"
    assert "period_coverage_available_after_origin" in result.readiness.reason_codes
    assert result.readiness.periods[0].status == "blocked"
    assert result.dataset is not None
    assert result.dataset.source_lineage is not None
    assert result.dataset.source_lineage.point_in_time_status == "blocked"


def test_complete_label_with_short_coverage_is_missing_not_zero() -> None:
    week = date(2026, 1, 5)
    partial = WeeklyWorkRequestCoverage.model_validate(
        {
            "week_start": week,
            "status": "complete",
            "covered_through": _timestamp("2026-01-10T00:00:00+00:00"),
            "known_at": _timestamp("2026-01-10T00:00:00+00:00"),
            "evidence_hash": _hash("partial-week-coverage"),
        }
    )
    result = build_weekly_work_request_dataset(
        _base_snapshot(
            period_start=week,
            period_end=week,
            period_coverage=[partial],
        )
    )

    assert result.readiness.status == "blocked"
    assert result.readiness.total_distinct_request_count is None
    assert result.readiness.periods[0].coverage_status == "incomplete"
    assert result.dataset is not None
    observation = result.dataset.observations[0]
    assert observation.value is None
    assert observation.missing_reason == "period_coverage_ends_before_week_close"


def test_conflicting_event_version_replay_abstains_without_a_dataset() -> None:
    first = _receipt(
        "replayed-version",
        "replayed-request",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:01:00+00:00"),
    )
    conflicting_replay = first.model_copy(update={"event_hash": _hash("different-content")})
    result = build_weekly_work_request_dataset(
        _base_snapshot(receipt_versions=[first, conflicting_replay])
    )

    assert result.dataset is None
    assert result.readiness.status == "blocked"
    assert result.readiness.reason_codes == ["conflicting_event_version_replay"]


def test_conflicting_duplicate_request_identity_abstains_without_a_dataset() -> None:
    first = _receipt(
        "conflict-one",
        "same-request",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:01:00+00:00"),
    )
    conflicting = _receipt(
        "conflict-two",
        "same-request",
        _timestamp("2026-01-12T10:00:00+00:00"),
        _timestamp("2026-01-12T10:01:00+00:00"),
    )
    result = build_weekly_work_request_dataset(
        _base_snapshot(receipt_versions=[first, conflicting])
    )

    assert result.dataset is None
    assert result.readiness.status == "blocked"
    assert result.readiness.reason_codes == ["conflicting_duplicate_request_identity"]
    assert all(item.distinct_request_count is None for item in result.readiness.periods)


def test_timezone_conversion_uses_company_calendar_before_week_assignment() -> None:
    first_received = _timestamp("2026-01-05T04:30:00+00:00")  # Sunday 23:30 in New York.
    receipt = _receipt(
        "timezone-boundary",
        "timezone-boundary",
        first_received,
        _timestamp("2026-01-05T04:45:00+00:00"),
    )
    close = _timestamp("2026-01-05T05:00:00+00:00")  # Monday 00:00 in New York.
    coverage = WeeklyWorkRequestCoverage.model_validate(
        {
            "week_start": date(2025, 12, 29),
            "status": "complete",
            "covered_through": close,
            "known_at": close,
            "evidence_hash": _hash("timezone-complete-week"),
        }
    )
    snapshot = _base_snapshot(
        timezone_name="America/New_York",
        period_start=date(2025, 12, 29),
        period_end=date(2025, 12, 29),
        snapshot_as_of=_timestamp("2026-01-06T12:00:00+00:00"),
        receipt_versions=[receipt],
        period_coverage=[coverage],
    )

    result = build_weekly_work_request_dataset(snapshot)

    assert result.readiness.status == "ready_for_synthetic_evaluation"
    assert result.dataset is not None
    assert result.dataset.observations[0].period_start == date(2025, 12, 29)
    assert result.dataset.observations[0].value == "1"
    assert result.dataset.source_lineage is not None
    assert result.dataset.source_lineage.calendar_timezone == "America/New_York"


def test_first_received_timestamp_is_required_and_generic_events_are_not_coerced() -> None:
    with pytest.raises(ValueError, match="received requests require first_received_at"):
        _receipt(
            "missing-first-received",
            "request-without-first-received",
            None,
            _timestamp("2026-01-05T10:00:00+00:00"),
            workflow_key=None,
        )

    generic_operations_event = {
        "event_id": _uuid("generic-event"),
        "event_version_id": _uuid("generic-event-version"),
        "source_event_key": "generic-event:1",
        "event_hash": _hash("generic-event"),
        "occurred_at": _timestamp("2026-01-05T10:00:00+00:00"),
        "event_type": "opened",
        "source_subject_reference": "work-item-1",
    }
    with pytest.raises(ValueError):
        WorkRequestReceiptVersion.model_validate(generic_operations_event)


def test_non_synthetic_source_is_rejected_and_target_counts_are_integer_nonnegative() -> None:
    snapshot = _complete_fixture()
    payload = snapshot.model_dump(mode="json")
    payload["data_origin"] = "caller_assertion"
    with pytest.raises(ValueError):
        WeeklyWorkRequestSnapshot.model_validate(payload)

    built = build_weekly_work_request_dataset(snapshot).dataset
    assert built is not None
    for invalid_count in ("-1", "1.5"):
        corrupted = built.model_dump(mode="json")
        observations = cast(list[dict[str, object]], corrupted["observations"])
        observations[0]["value"] = invalid_count
        with pytest.raises(ValueError, match="non-negative integers"):
            ForecastDatasetCreate.model_validate_json(json.dumps(corrupted))

    corrupted_zero = built.model_dump(mode="json")
    observations = cast(list[dict[str, object]], corrupted_zero["observations"])
    observations[-1]["period_coverage"] = "unknown"
    observations[-1]["coverage_evidence_hash"] = None
    with pytest.raises(ValueError, match="cannot be assigned a count"):
        ForecastDatasetCreate.model_validate_json(json.dumps(corrupted_zero))


def test_invalid_revision_chain_is_blocked_without_persistable_target() -> None:
    first = _receipt(
        "bad-chain",
        "bad-chain-request",
        _timestamp("2026-01-05T10:00:00+00:00"),
        _timestamp("2026-01-05T10:01:00+00:00"),
    )
    wrong_predecessor = _receipt(
        "bad-chain",
        "bad-chain-request",
        _timestamp("2026-01-05T10:30:00+00:00"),
        _timestamp("2026-01-06T10:00:00+00:00"),
        version=2,
        previous_event_version_id=_uuid("not-the-predecessor"),
        correction_reason="Bad synthetic predecessor reference.",
    )
    result = build_weekly_work_request_dataset(
        _base_snapshot(receipt_versions=[first, wrong_predecessor])
    )

    assert result.dataset is None
    assert result.readiness.status == "blocked"
    assert result.readiness.reason_codes == ["receipt_version_predecessor_mismatch"]
