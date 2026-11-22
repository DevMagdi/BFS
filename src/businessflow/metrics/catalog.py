from __future__ import annotations

from dataclasses import asdict, dataclass

METRIC_CATALOG_VERSION = "businessflow.metrics.v1"
METRIC_CATALOG_STATUS = "implementation_draft"
METRIC_DECISION_USE = "observational_only_no_recommendation"
CALCULATION_VERSION = "businessflow.capacity-weekly.v1"
SNAPSHOT_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class MetricDefinition:
    metric_id: str
    version: str
    name: str
    unit: str
    formula: str
    description: str
    limitations: tuple[str, ...]


METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        metric_id="available_capacity_hours",
        version="1.0.0",
        name="Recorded available capacity",
        unit="hours per employee-week",
        formula="scheduled_hours - non_project_hours - unavailable_hours",
        description=(
            "Uses the latest weekly-capacity version for active employees. Confirmed and estimated "
            "values are reported separately; an all-employee total is exposed only when every "
            "active employee has a known record for that week."
        ),
        limitations=(
            "Unknown and missing capacity remain unknown; "
            "standard weekly capacity is not a fallback.",
            "Current active employee status is applied to every requested week; "
            "no status history exists.",
        ),
    ),
    MetricDefinition(
        metric_id="recorded_committed_hours",
        version="1.0.0",
        name="Recorded committed assignment hours",
        unit="planned hours per week",
        formula=(
            "sum(planned_hours_per_week) for current committed assignments overlapping the week"
        ),
        description=(
            "Includes only committed assignments. Proposed assignments are reported separately. "
            "An assignment contributes its full weekly value to every intersecting Monday-Sunday "
            "week across its inclusive start and end dates."
        ),
        limitations=(
            "Assignment completeness is not attested; no assignment rows means "
            "not recorded, not zero.",
            "Planned assignment hours are not timesheets, actual work, or utilization.",
        ),
    ),
    MetricDefinition(
        metric_id="capacity_minus_recorded_commitments_hours",
        version="1.0.0",
        name="Capacity minus recorded commitments",
        unit="hours per week",
        formula=(
            "complete available capacity - active-employee recorded committed assignment hours"
        ),
        description=(
            "An arithmetic comparison of recorded company capacity and recorded active-employee "
            "commitments. The value is null unless capacity is known for every active employee and "
            "at least one committed assignment for an active employee is recorded."
        ),
        limitations=(
            "This difference is not proof of free capacity because assignment completeness "
            "is unknown.",
            "It does not measure actual utilization or work performed.",
        ),
    ),
    MetricDefinition(
        metric_id="recorded_assignment_overload",
        version="1.0.0",
        name="Recorded assignment overload observation",
        unit="hours per employee-week",
        formula="max(0, recorded committed assignment hours - known available capacity)",
        description=(
            "Reports confirmed-capacity overloads separately from overloads against estimated "
            "capacity. Missing or explicitly unknown capacity produces an unresolved comparison."
        ),
        limitations=(
            "Only recorded committed assignments are compared; unrecorded commitments may exist.",
            "An estimated-capacity overload is a potential overload, not a confirmed one.",
        ),
    ),
    MetricDefinition(
        metric_id="recorded_skill_inventory_coverage",
        version="1.0.0",
        name="Recorded active skill inventory coverage",
        unit="employees and proficiency records",
        formula=("count active employee-skill links by active skill and proficiency level (1-5)"),
        description=(
            "Counts current active employee-skill records for active employees and active catalog "
            "skills. It reports recorded inventory only."
        ),
        limitations=(
            "No project-required-skill model exists, so project skill matching is unsupported.",
            "A recorded proficiency level does not prove practical competency "
            "or delivery capability.",
        ),
    ),
)


def metric_definition_payloads() -> list[dict[str, object]]:
    return [
        {
            **asdict(definition),
            "limitations": list(definition.limitations),
        }
        for definition in METRIC_DEFINITIONS
    ]


def metric_catalog_payload() -> dict[str, object]:
    return {
        "catalog_version": METRIC_CATALOG_VERSION,
        "catalog_status": METRIC_CATALOG_STATUS,
        "decision_use": METRIC_DECISION_USE,
        "calculation_version": CALCULATION_VERSION,
        "week_convention": "Monday through Sunday; weeks are keyed by their Monday start date.",
        "metrics": metric_definition_payloads(),
    }
