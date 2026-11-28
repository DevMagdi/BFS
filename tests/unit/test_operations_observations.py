from __future__ import annotations

from datetime import datetime

import pytest

from businessflow.operations_intelligence.engine import (
    CALCULATION_VERSION,
    MAX_ANALYSIS_EVENTS,
    SNAPSHOT_SCHEMA_VERSION,
    SnapshotValidationError,
    calculate_operations_observations,
)
from businessflow.operations_intelligence.schemas import (
    OperationsAnalysisCreateRequest,
    OperationsEventCorrectionRequest,
    OperationsEventCreateRequest,
)


def _snapshot(events: list[dict[str, object]]) -> dict[str, object]:
    return {
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "calculation_version": CALCULATION_VERSION,
        "scope": {"tenant_id": "tenant", "company_id": "company", "source_id": "source"},
        "period": {
            "start_at": "2026-11-20T00:00:00.000000+00:00",
            "end_at": "2026-11-21T00:00:00.000000+00:00",
            "interval": "[start_at,end_at)",
        },
        "events": events,
    }


def _event(
    version_id: str,
    *,
    event_type: str,
    state_before: str | None = None,
    state_after: str | None = None,
) -> dict[str, object]:
    return {
        "event_id": f"event-{version_id}",
        "event_version_id": version_id,
        "source_event_key": f"source-key-{version_id}",
        "occurred_at": "2026-11-20T12:00:00.000000+00:00",
        "event_type": event_type,
        "state_before": state_before,
        "state_after": state_after,
    }


def test_observation_counts_are_deterministic_and_keep_exact_labels():
    snapshot = _snapshot(
        [
            _event("v2", event_type="Completed", state_before="Open", state_after="Closed"),
            _event("v1", event_type="completed", state_before="open", state_after="closed"),
            _event("v3", event_type="Completed", state_after="Closed"),
        ]
    )

    first = calculate_operations_observations(snapshot)
    second = calculate_operations_observations(snapshot)

    assert first == second
    assert first["status"] == "observations_available"
    assert first["observed_event_count"] == 3
    assert first["event_type_counts"] == [
        {"label": "Completed", "count": 2},
        {"label": "completed", "count": 1},
    ]
    assert first["state_before_counts"] == [
        {"label": "Open", "count": 1},
        {"label": "open", "count": 1},
    ]
    assert first["state_after_counts"] == [
        {"label": "Closed", "count": 2},
        {"label": "closed", "count": 1},
    ]
    assert first["source_authority"] == "not_assessed"
    assert first["source_completeness"] == "not_assessed"
    assert first["source_freshness"] == "not_assessed"
    assert first["source_accuracy"] == "not_independently_verified"
    assert first["hypothesis"] == "not_produced"
    assert first["forecast"] == "not_produced"
    assert first["recommendation"] == "not_produced"
    assert first["human_decision"] == "not_produced"
    assert all(item["count"] > 0 for item in first["event_type_counts"])


def test_empty_snapshot_reports_no_matching_evidence_without_zero_groups():
    result = calculate_operations_observations(_snapshot([]))

    assert result["status"] == "no_matching_evidence"
    assert result["observed_event_count"] == 0
    assert result["event_type_counts"] == []
    assert result["state_before_counts"] == []
    assert result["state_after_counts"] == []


def test_snapshot_validation_rejects_wrong_version_duplicate_and_oversized_input():
    invalid_version = _snapshot([])
    invalid_version["calculation_version"] = "unreviewed"
    with pytest.raises(SnapshotValidationError):
        calculate_operations_observations(invalid_version)

    duplicate = _event("same-version", event_type="opened")
    with pytest.raises(SnapshotValidationError, match="duplicate event version"):
        calculate_operations_observations(_snapshot([duplicate, duplicate]))

    too_many = [
        _event(f"v{index}", event_type="recorded") for index in range(MAX_ANALYSIS_EVENTS + 1)
    ]
    with pytest.raises(SnapshotValidationError, match="supported event count"):
        calculate_operations_observations(_snapshot(too_many))


def test_request_schemas_require_explicit_timezone_and_consistent_evidence_reference():
    base = {
        "source_id": "a627c4d0-e6e3-4d21-8992-d23d912135c1",
        "source_event_key": "source-key-0001",
        "occurred_at": "2026-11-20T12:00:00Z",
        "event_type": "recorded",
        "source_subject_reference": "work-item-1",
        "evidence_kind": "manual_assertion",
    }
    valid = OperationsEventCreateRequest.model_validate(base)
    assert valid.occurred_at.tzinfo is not None

    with pytest.raises(ValueError):
        OperationsEventCreateRequest.model_validate({**base, "occurred_at": "2026-11-20T12:00:00"})

    with pytest.raises(ValueError):
        OperationsEventCreateRequest.model_validate(
            {
                **base,
                "canonical_record_id": "d11fd5f8-66de-4450-9c59-fc9628f3f11b",
            }
        )

    with pytest.raises(ValueError):
        OperationsAnalysisCreateRequest.model_validate(
            {
                "source_id": "a627c4d0-e6e3-4d21-8992-d23d912135c1",
                "start_at": "2026-11-20T00:00:00",
                "end_at": "2026-11-21T00:00:00Z",
            }
        )

    for field in ("source_event_key", "event_type", "source_subject_reference"):
        with pytest.raises(ValueError):
            OperationsEventCreateRequest.model_validate({**base, field: "   "})

    correction = {
        key: value for key, value in base.items() if key not in {"source_id", "source_event_key"}
    }
    with pytest.raises(ValueError):
        OperationsEventCorrectionRequest.model_validate(
            {
                **correction,
                "expected_version": 1,
                "correction_reason": "          ",
            }
        )


def test_datetime_inputs_are_values_not_business_interpretations():
    value = datetime.fromisoformat("2026-11-20T12:00:00+00:00")
    assert value.utcoffset() is not None
