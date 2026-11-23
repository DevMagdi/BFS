from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation, localcontext
from typing import Literal, cast

from businessflow.metrics.catalog import (
    METRIC_CATALOG_STATUS,
    METRIC_CATALOG_VERSION,
    METRIC_DECISION_USE,
    METRIC_DEFINITIONS,
    metric_definition_payloads,
)
from businessflow.metrics.snapshot import content_hash, decimal_text

METRIC_INSIGHT_ENGINE_VERSION = "businessflow.metric-insight.v2"
COMPARISON_DEFINITION_VERSION = "businessflow.metric-comparison.completed-adjacent-weekly.v2"
INSIGHT_CATALOG_VERSION = "businessflow.metric-insight-catalog.v2"
INSIGHT_SNAPSHOT_SCHEMA_VERSION = 2
INSIGHT_EVIDENCE_SCHEMA_VERSION = 2
PERCENTAGE_CHANGE_QUANTUM = Decimal("0.01")

INTERPRETATION_UNAVAILABLE_REASON = "owner_approved_business_question_not_configured"
PERCENTAGE_CHANGE_CONTRACT: dict[str, str] = {
    "formula": "((comparison_value - baseline_value) / baseline_value) * 100",
    "availability_rule": (
        "Computed only when both numeric values are present and non-conflicting, their metric "
        "bases match, and the baseline is nonzero; estimated values remain labeled estimated."
    ),
    "zero_baseline": "Null with reason zero_baseline_percent_change_undefined.",
    "negative_baseline": (
        "A nonzero negative baseline uses the signed denominator in the formula; the resulting "
        "percentage is arithmetic only and has no evaluative interpretation."
    ),
    "missing_or_incompatible_values": (
        "Null with an explicit reason; existing metric-value abstentions remain in force."
    ),
    "rounding": "Two decimal places, Decimal ROUND_HALF_EVEN; unit is percent.",
}


@dataclass(frozen=True, slots=True)
class ComparisonSpec:
    """A mechanical comparison over an existing M5 definition, not a new metric."""

    metric_id: str
    required_input_groups: tuple[str, ...]
    required_dataset_types: tuple[str, ...]
    required_contracts: tuple[tuple[str, str, str], ...]
    additional_limitations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MetricValue:
    value: Decimal | None
    basis: str | None
    status: Literal["available", "estimated", "unavailable", "conflicting"]
    reasons: tuple[str, ...] = ()


COMPARISON_SPECS: tuple[ComparisonSpec, ...] = (
    ComparisonSpec(
        metric_id="available_capacity_hours",
        required_input_groups=("employees", "weekly_capacity"),
        required_dataset_types=("employees", "weekly_capacity"),
        required_contracts=(
            ("employees", "businessflow.m4.employees", "1.0.0"),
            ("weekly_capacity", "businessflow.m4.weekly_capacity", "1.0.0"),
        ),
        additional_limitations=(
            "Only complete weekly capacity totals are compared.",
            "Estimated or mixed capacity remains visibly estimated; it is never "
            "promoted to confirmed.",
            "Mixed confirmed/estimated employee bases are shown but are not differenced.",
        ),
    ),
    ComparisonSpec(
        metric_id="recorded_committed_hours",
        required_input_groups=("employees", "resource_assignments"),
        required_dataset_types=("employees", "resource_assignments"),
        required_contracts=(
            ("employees", "businessflow.m4.employees", "1.0.0"),
            ("resource_assignments", "businessflow.m4.resource_assignments", "1.0.0"),
        ),
        additional_limitations=(
            "Only recorded committed assignment hours are compared; completeness is not attested.",
            "Planned assignments are not actual work, timesheets, or utilization.",
        ),
    ),
    ComparisonSpec(
        metric_id="capacity_minus_recorded_commitments_hours",
        required_input_groups=("employees", "weekly_capacity", "resource_assignments"),
        required_dataset_types=("employees", "weekly_capacity", "resource_assignments"),
        required_contracts=(
            ("employees", "businessflow.m4.employees", "1.0.0"),
            ("weekly_capacity", "businessflow.m4.weekly_capacity", "1.0.0"),
            ("resource_assignments", "businessflow.m4.resource_assignments", "1.0.0"),
        ),
        additional_limitations=(
            "This comparison is not proof of free capacity because assignment completeness "
            "is unknown.",
            "Confirmed, estimated, and mixed capacity bases are never silently conflated.",
        ),
    ),
)

COMPARISON_SPEC_BY_ID: dict[str, ComparisonSpec] = {
    spec.metric_id: spec for spec in COMPARISON_SPECS
}

UNAVAILABLE_COMPARISONS: tuple[dict[str, str], ...] = (
    {
        "metric_id": "recorded_assignment_overload",
        "reason": (
            "M5 exposes employee-level overload observations separated by confirmed/estimated "
            "capacity; M13 has no owner-approved aggregation or comparison question."
        ),
    },
    {
        "metric_id": "recorded_skill_inventory_coverage",
        "reason": (
            "M5 defines this as a current skill-inventory snapshot, not a weekly time series; "
            "M13 does not invent a period or change rule."
        ),
    },
)

_CONFLICT_REASONS = frozenset(
    {
        "invalid_assignment_status",
        "invalid_assignment_period",
        "invalid_assignment_hours",
        "assignment_employee_not_in_snapshot",
        "open_assignment_on_terminal_project",
        "committed_assignments_for_inactive_employees",
    }
)


def metric_insight_catalog_payload() -> dict[str, object]:
    definitions = {item.metric_id: item for item in METRIC_DEFINITIONS}
    definition_payloads = {
        cast(str, item["metric_id"]): item for item in metric_definition_payloads()
    }
    supported: list[dict[str, object]] = []
    for spec in COMPARISON_SPECS:
        definition = definitions[spec.metric_id]
        definition_payload = definition_payloads[spec.metric_id]
        supported.append(
            {
                "metric_id": definition.metric_id,
                "metric_version": definition.version,
                "name": definition.name,
                "unit": definition.unit,
                "formula": definition.formula,
                "definition_hash": content_hash(definition_payload),
                "required_input_groups": list(spec.required_input_groups),
                "required_dataset_types": list(spec.required_dataset_types),
                "required_contracts": [
                    {
                        "dataset_type": dataset_type,
                        "contract_key": contract_key,
                        "version": version,
                    }
                    for dataset_type, contract_key, version in spec.required_contracts
                ],
                "comparison_definition_version": COMPARISON_DEFINITION_VERSION,
                "comparison_behavior": (
                    "Pair equal-length completed Monday-Sunday periods by week index; "
                    "delta equals comparison-period M5 value minus baseline-period M5 value. "
                    "Increased, decreased, and unchanged are neutral arithmetic labels, not "
                    "positive or negative evaluations. No sum, average, target, threshold, "
                    "ranking, or causal claim is added."
                ),
                "percentage_change_contract": dict(PERCENTAGE_CHANGE_CONTRACT),
                "limitations": [*definition.limitations, *spec.additional_limitations],
            }
        )
    return {
        "catalog_version": INSIGHT_CATALOG_VERSION,
        "engine_version": METRIC_INSIGHT_ENGINE_VERSION,
        "comparison_definition_version": COMPARISON_DEFINITION_VERSION,
        "source_metric_catalog_version": METRIC_CATALOG_VERSION,
        "source_metric_catalog_status": METRIC_CATALOG_STATUS,
        "decision_use": METRIC_DECISION_USE,
        "framework_status": "descriptive_comparison_only",
        "supported_comparisons": supported,
        "unavailable_comparisons": list(UNAVAILABLE_COMPARISONS),
        "owner_approved_question": {
            "status": "unavailable",
            "reason": INTERPRETATION_UNAVAILABLE_REASON,
        },
        "interpretation_status": "unavailable",
        "causal_analysis_status": "not_available",
        "direction_semantics": (
            "Increased, decreased, and unchanged are neutral arithmetic labels, not value "
            "judgments."
        ),
        "period_convention": (
            "Inclusive Monday-Sunday weeks; equal week counts; baseline immediately precedes "
            "comparison, which starts the next Monday. Each M5 run must be created at or after "
            "local midnight following its period end, and both period ends must precede the "
            "Company-local date when the comparison is created."
        ),
    }


def metric_definition_snapshot(
    result_snapshot: Mapping[str, object], metric_id: str
) -> dict[str, object]:
    raw_definitions = result_snapshot.get("metric_definition_snapshot")
    if not isinstance(raw_definitions, list):
        raise ValueError("M5 metric definition snapshot is missing")
    matches = [
        item
        for item in raw_definitions
        if isinstance(item, Mapping) and item.get("metric_id") == metric_id
    ]
    if len(matches) != 1:
        raise ValueError("M5 metric definition snapshot is ambiguous")
    return {str(key): value for key, value in matches[0].items()}


def extract_metric_value(metric_id: str, week: Mapping[str, object]) -> MetricValue:
    """Read one existing M5 weekly metric without re-calculating or imputing values."""
    if metric_id not in COMPARISON_SPEC_BY_ID:
        return MetricValue(None, None, "unavailable", ("metric_comparison_unavailable",))

    if metric_id == "available_capacity_hours":
        capacity = _mapping(week.get("capacity"))
        availability_status = capacity.get("availability_status")
        value = capacity.get("complete_available_hours")
        if value is None:
            return MetricValue(
                None,
                cast(str | None, capacity.get("complete_capacity_basis"))
                if isinstance(capacity.get("complete_capacity_basis"), str)
                else None,
                "unavailable",
                _state_reasons(capacity, "complete_capacity_unavailable"),
            )
        parsed = _decimal_value(value)
        if parsed is None:
            return MetricValue(None, None, "conflicting", ("metric_value_invalid",))
        if availability_status == "confirmed":
            return MetricValue(parsed, "confirmed", "available")
        if availability_status == "estimated":
            return MetricValue(parsed, "estimated", "estimated")
        if availability_status == "mixed":
            return MetricValue(parsed, "mixed", "estimated", ("mixed_capacity_basis",))
        return MetricValue(
            None,
            cast(str | None, availability_status) if isinstance(availability_status, str) else None,
            "unavailable",
            _state_reasons(capacity, "complete_capacity_state_unavailable"),
        )

    if metric_id == "recorded_committed_hours":
        assignments = _mapping(week.get("assignments"))
        value = assignments.get("active_employee_committed_hours_per_week")
        count = assignments.get("active_employee_committed_assignment_count")
        if assignments.get("committed_state") != "recorded" or value is None or count == 0:
            return MetricValue(
                None,
                "recorded",
                "unavailable",
                _state_reasons(
                    assignments,
                    "committed_assignments_not_recorded_for_active_employees",
                ),
            )
        parsed = _decimal_value(value)
        if parsed is None:
            return MetricValue(None, None, "conflicting", ("metric_value_invalid",))
        reasons = _week_conflict_reasons(week)
        if reasons:
            return MetricValue(None, "recorded", "conflicting", reasons)
        return MetricValue(parsed, "recorded", "available")

    comparison = _mapping(week.get("comparison"))
    state = comparison.get("state")
    value = comparison.get("capacity_minus_recorded_commitments_hours")
    basis = comparison.get("capacity_basis")
    conflict_reasons = _week_conflict_reasons(week)
    if conflict_reasons:
        return MetricValue(None, cast(str | None, basis), "conflicting", conflict_reasons)
    if (
        state not in {"calculated_confirmed_capacity", "calculated_estimated_capacity"}
        or value is None
    ):
        return MetricValue(
            None,
            cast(str | None, basis) if isinstance(basis, str) else None,
            "unavailable",
            _state_reasons(comparison, "capacity_commitment_comparison_unavailable"),
        )
    parsed = _decimal_value(value)
    if parsed is None or basis not in {"confirmed", "estimated"}:
        return MetricValue(None, None, "conflicting", ("metric_value_invalid",))
    capacity = _mapping(week.get("capacity"))
    if capacity.get("availability_status") == "mixed":
        return MetricValue(parsed, "mixed", "estimated", ("mixed_capacity_basis",))
    return MetricValue(parsed, basis, "estimated" if basis == "estimated" else "available")


def build_comparison_result(
    *,
    metric_id: str,
    baseline_result: Mapping[str, object],
    comparison_result: Mapping[str, object],
    baseline_period: Mapping[str, object],
    comparison_period: Mapping[str, object],
    period_completion: Mapping[str, object],
    data_evidence: dict[str, object],
    evidence_abstentions: list[dict[str, str]],
) -> dict[str, object]:
    """Build a traceable descriptive comparison, not an unapproved business interpretation."""
    if metric_id not in COMPARISON_SPEC_BY_ID:
        raise ValueError("metric comparison is unavailable")
    if period_completion.get("status") != "completed":
        raise ValueError("M13 comparisons require both metric periods to be completed")
    spec = COMPARISON_SPEC_BY_ID[metric_id]
    definition = metric_definition_snapshot(baseline_result, metric_id)
    comparison_definition = metric_definition_snapshot(comparison_result, metric_id)
    if definition != comparison_definition:
        raise ValueError("M5 metric definitions differ between the selected runs")

    points: list[dict[str, object]] = []
    abstentions = list(evidence_abstentions)
    evidence_unverified = data_evidence.get("status") != "verified"
    if evidence_unverified and not abstentions:
        abstentions.append({"target": "data_evidence", "reason": "source_evidence_not_verified"})
    globally_blocked = evidence_unverified or bool(evidence_abstentions)
    baseline_weeks = _week_list(baseline_result)
    comparison_weeks = _week_list(comparison_result)
    if len(baseline_weeks) != len(comparison_weeks):
        raise ValueError("M5 weekly output lengths differ")
    baseline_conflicts = _result_conflict_reasons(metric_id, baseline_result)
    comparison_conflicts = _result_conflict_reasons(metric_id, comparison_result)

    for index, (baseline_week, comparison_week) in enumerate(
        zip(baseline_weeks, comparison_weeks, strict=True), start=1
    ):
        if globally_blocked:
            continue
        baseline_value = extract_metric_value(metric_id, baseline_week)
        comparison_value = extract_metric_value(metric_id, comparison_week)
        if baseline_conflicts:
            baseline_value = MetricValue(
                None, baseline_value.basis, "conflicting", baseline_conflicts
            )
        if comparison_conflicts:
            comparison_value = MetricValue(
                None, comparison_value.basis, "conflicting", comparison_conflicts
            )
        point_reasons = list(baseline_value.reasons) + list(comparison_value.reasons)
        point: dict[str, object] = {
            "week_index": index,
            "baseline_week_start_date": _week_start(baseline_week),
            "comparison_week_start_date": _week_start(comparison_week),
            "classification": "computed_m5_metric_comparison",
            "baseline_value": decimal_text(baseline_value.value)
            if baseline_value.value is not None
            else None,
            "comparison_value": decimal_text(comparison_value.value)
            if comparison_value.value is not None
            else None,
            "baseline_basis": baseline_value.basis,
            "comparison_basis": comparison_value.basis,
            "value_status": _pair_value_status(baseline_value, comparison_value),
            "delta": None,
            "direction": None,
            "percentage_change": None,
            "percentage_change_status": "unavailable",
            "percentage_change_reason": "metric_value_unavailable",
            "abstentions": [],
        }
        if baseline_value.status == "conflicting" or comparison_value.status == "conflicting":
            reasons = point_reasons or ["conflicting_metric_state"]
            point["value_status"] = "conflicting"
            point["percentage_change_reason"] = reasons[0]
            point["abstentions"] = _reason_records(reasons)
            abstentions.extend(_reason_records(reasons, target=f"week_{index}"))
        elif baseline_value.value is None or comparison_value.value is None:
            reasons = point_reasons or ["metric_value_unavailable"]
            point["percentage_change_reason"] = reasons[0]
            point["abstentions"] = _reason_records(reasons)
            abstentions.extend(_reason_records(reasons, target=f"week_{index}"))
        elif baseline_value.basis == "mixed" or comparison_value.basis == "mixed":
            reason = "mixed_capacity_basis_not_comparable"
            point["percentage_change_reason"] = reason
            point["abstentions"] = _reason_records([reason])
            abstentions.append({"target": f"week_{index}", "reason": reason})
        elif baseline_value.basis != comparison_value.basis:
            reason = "metric_basis_changed"
            point["percentage_change_reason"] = reason
            point["abstentions"] = _reason_records([reason])
            abstentions.append({"target": f"week_{index}", "reason": reason})
        else:
            with localcontext() as context:
                context.prec = 256
                delta = comparison_value.value - baseline_value.value
                point["delta"] = decimal_text(delta)
                point["direction"] = (
                    "increased" if delta > 0 else "decreased" if delta < 0 else "unchanged"
                )
                if baseline_value.value == 0:
                    point["percentage_change_reason"] = "zero_baseline_percent_change_undefined"
                else:
                    percentage_change = (delta / baseline_value.value) * Decimal("100")
                    rounded_percentage = percentage_change.quantize(
                        PERCENTAGE_CHANGE_QUANTUM,
                        rounding=ROUND_HALF_EVEN,
                    )
                    if rounded_percentage == 0:
                        rounded_percentage = Decimal("0.00")
                    point["percentage_change"] = format(rounded_percentage, "f")
                    point["percentage_change_status"] = "computed"
                    point["percentage_change_reason"] = None
        points.append(point)

    if globally_blocked or not points:
        availability_status = "abstained"
    else:
        comparable_points = sum(point["delta"] is not None for point in points)
        availability_status = (
            "available"
            if comparable_points == len(points)
            else "partial"
            if comparable_points > 0
            else "abstained"
        )
    raw_limitations = definition.get("limitations")
    if not isinstance(raw_limitations, list) or any(
        not isinstance(item, str) for item in raw_limitations
    ):
        raise ValueError("M5 metric limitations are invalid")
    limitations = list(cast(list[str], raw_limitations))
    limitations.extend(spec.additional_limitations)
    limitations.extend(
        [
            "Comparison deltas and percentage changes are descriptive arithmetic over immutable "
            "M5 outputs, not a new metric definition.",
            "Increased, decreased, and unchanged are neutral labels; none is inherently positive "
            "or negative.",
            "Percentage change uses the signed baseline denominator, is rounded to two decimal "
            "places with Decimal ROUND_HALF_EVEN, and is unavailable for a zero baseline or "
            "unavailable/incompatible values.",
            "No owner-approved business question or interpretation is configured; "
            "interpretation is unavailable.",
            "No target, threshold, ranking, causal claim, forecast, or recommendation is produced.",
            "M12 source_as_of_at is user-reported evidence and is not independently authenticated.",
        ]
    )
    return {
        "snapshot_schema_version": INSIGHT_SNAPSHOT_SCHEMA_VERSION,
        "engine_version": METRIC_INSIGHT_ENGINE_VERSION,
        "comparison_definition_version": COMPARISON_DEFINITION_VERSION,
        "source_metric_catalog_version": baseline_result.get("metric_catalog_version"),
        "source_metric_catalog_status": baseline_result.get("metric_catalog_status"),
        "decision_use": METRIC_DECISION_USE,
        "availability_status": availability_status,
        "observation_type": "adjacent_period_m5_metric_comparison",
        "percentage_change_contract": dict(PERCENTAGE_CHANGE_CONTRACT),
        "metric": {
            "metric_id": metric_id,
            "version": definition.get("version"),
            "name": definition.get("name"),
            "unit": definition.get("unit"),
            "formula": definition.get("formula"),
            "definition_hash": content_hash(definition),
            "definition": definition,
        },
        "period_alignment": {
            "behavior": "equal_length_completed_adjacent_weeks_by_index",
            "baseline": dict(baseline_period),
            "comparison": dict(comparison_period),
            "completion_check": dict(period_completion),
        },
        "data_evidence": data_evidence,
        "comparisons": points,
        "abstentions": abstentions,
        "insights": {
            "status": "unavailable",
            "reason": INTERPRETATION_UNAVAILABLE_REASON,
            "items": [],
        },
        "causality": {"status": "not_evaluated", "reason": "causal_inference_out_of_scope"},
        "limitations": limitations,
    }


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _week_list(result: Mapping[str, object]) -> list[Mapping[str, object]]:
    raw = result.get("weekly")
    if not isinstance(raw, list) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError("M5 weekly result snapshot is invalid")
    return cast(list[Mapping[str, object]], raw)


def _week_start(week: Mapping[str, object]) -> str:
    value = week.get("week_start_date")
    if not isinstance(value, str):
        raise ValueError("M5 weekly result is missing its start date")
    return value


def _decimal_value(value: object) -> Decimal | None:
    if not isinstance(value, str) or not value or len(value) > 64:
        return None
    try:
        parsed = Decimal(value)
    except InvalidOperation:
        return None
    if not parsed.is_finite():
        return None
    return parsed


def _week_conflict_reasons(week: Mapping[str, object]) -> tuple[str, ...]:
    comparison = _mapping(week.get("comparison"))
    raw = comparison.get("reasons")
    if not isinstance(raw, list):
        return ()
    return tuple(
        reason for reason in raw if isinstance(reason, str) and reason in _CONFLICT_REASONS
    )


def _result_conflict_reasons(metric_id: str, result: Mapping[str, object]) -> tuple[str, ...]:
    if metric_id not in {
        "recorded_committed_hours",
        "capacity_minus_recorded_commitments_hours",
    }:
        return ()
    reliability = _mapping(result.get("reliability"))
    assignments = _mapping(reliability.get("committed_assignments"))
    raw = assignments.get("reasons")
    if not isinstance(raw, list):
        return ()
    return tuple(
        reason for reason in raw if isinstance(reason, str) and reason in _CONFLICT_REASONS
    )


def _state_reasons(value: Mapping[str, object], fallback: str) -> tuple[str, ...]:
    raw = value.get("reasons")
    if isinstance(raw, list):
        reasons = tuple(item for item in raw if isinstance(item, str))
        if reasons:
            return reasons
    return (fallback,)


def _pair_value_status(baseline: MetricValue, comparison: MetricValue) -> str:
    if baseline.status == "conflicting" or comparison.status == "conflicting":
        return "conflicting"
    if baseline.status == "unavailable" or comparison.status == "unavailable":
        return "unavailable"
    if baseline.status == "estimated" or comparison.status == "estimated":
        return "estimated"
    return "computed"


def _reason_records(
    reasons: list[str] | tuple[str, ...], *, target: str = "metric_value"
) -> list[dict[str, str]]:
    return [{"target": target, "reason": reason} for reason in dict.fromkeys(reasons)]
