from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import cast

MAX_ANALYSIS_EVENTS = 500
SNAPSHOT_SCHEMA_VERSION = 1
CALCULATION_VERSION = "operations-observations-v1"


class SnapshotValidationError(ValueError):
    """Saved operational evidence cannot be interpreted under its declared contract."""


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SnapshotValidationError(f"snapshot {name} must be an object")
    return cast(Mapping[str, object], value)


def _required_text(mapping: Mapping[str, object], key: str) -> str:
    value = mapping.get(key)
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError(f"snapshot field {key} must be a non-empty string")
    return value


def _optional_text(mapping: Mapping[str, object], key: str) -> str | None:
    value = mapping.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise SnapshotValidationError(f"snapshot field {key} must be text or null")
    return value


def _counts(counter: Counter[str]) -> list[dict[str, object]]:
    return [{"label": label, "count": count} for label, count in sorted(counter.items())]


def calculate_operations_observations(snapshot: object) -> dict[str, object]:
    """Count only exact labels in a bounded, saved source/time evidence snapshot.

    This calculation deliberately makes no claims about completeness, authority, freshness,
    accuracy, process meaning, targets, throughput, productivity, forecasting, or decisions.
    """
    root = _mapping(snapshot, "root")
    if root.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise SnapshotValidationError("snapshot schema version is unsupported")
    if root.get("calculation_version") != CALCULATION_VERSION:
        raise SnapshotValidationError("snapshot calculation version is unsupported")
    _mapping(root.get("scope"), "scope")
    _mapping(root.get("period"), "period")
    events_value = root.get("events")
    if not isinstance(events_value, list):
        raise SnapshotValidationError("snapshot events must be a list")
    if len(events_value) > MAX_ANALYSIS_EVENTS:
        raise SnapshotValidationError("snapshot exceeds the supported event count")

    event_types: Counter[str] = Counter()
    state_before: Counter[str] = Counter()
    state_after: Counter[str] = Counter()
    seen_versions: set[str] = set()
    for raw_event in events_value:
        event = _mapping(raw_event, "event")
        version_id = _required_text(event, "event_version_id")
        if version_id in seen_versions:
            raise SnapshotValidationError("snapshot contains a duplicate event version")
        seen_versions.add(version_id)
        _required_text(event, "event_id")
        _required_text(event, "source_event_key")
        _required_text(event, "occurred_at")
        event_types[_required_text(event, "event_type")] += 1
        before = _optional_text(event, "state_before")
        after = _optional_text(event, "state_after")
        if before is not None:
            state_before[before] += 1
        if after is not None:
            state_after[after] += 1

    evidence_count = len(events_value)
    return {
        "status": "observations_available" if evidence_count else "no_matching_evidence",
        "observed_event_count": evidence_count,
        "event_type_counts": _counts(event_types),
        "state_before_counts": _counts(state_before),
        "state_after_counts": _counts(state_after),
        "interpretation": "recorded_observations_only",
        "source_authority": "not_assessed",
        "source_completeness": "not_assessed",
        "source_freshness": "not_assessed",
        "source_accuracy": "not_independently_verified",
        "hypothesis": "not_produced",
        "forecast": "not_produced",
        "recommendation": "not_produced",
        "human_decision": "not_produced",
    }
