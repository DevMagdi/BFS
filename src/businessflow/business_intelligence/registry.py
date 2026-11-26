from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from businessflow.core_financials.contracts import FINANCIAL_MEASURES
from businessflow.metrics.catalog import (
    METRIC_CATALOG_VERSION,
    METRIC_DEFINITIONS,
)
from businessflow.metrics.snapshot import content_hash
from businessflow.universal_data.catalog import CANONICAL_ENTITIES

METRIC_REGISTRY_VERSION = "businessflow.metric-registry.v1"
CALCULATION_CATALOG_VERSION = "businessflow.deterministic-calculations.v1"
METRIC_AUTHORITY_BOUNDARY = "descriptive_evidence_only_no_recommendation"

MetricDomain = Literal["finance", "sales", "customers", "procurement", "operations", "people"]
MetricDefinitionType = Literal["system", "company", "custom"]
MetricStatus = Literal["draft", "review", "published", "deprecated"]

UNIVERSAL_ENTITY_IDS = frozenset(CANONICAL_ENTITIES)
M5_METRIC_IDS = frozenset(item.metric_id for item in METRIC_DEFINITIONS)
FINANCIAL_MEASURE_IDS = frozenset(FINANCIAL_MEASURES)

CALCULATION_CAPABILITIES: tuple[dict[str, object], ...] = (
    {
        "calculation_key": "universal_record_count",
        "label": "Count source records",
        "source_kind": "universal_data",
        "description": "Counts accepted source-scoped canonical records for one entity type.",
        "limitations": [
            "This is a one-source record count, not a cross-source party count.",
            "The count is usable only after source, mapping, freshness, quality, and "
            "readiness checks.",
        ],
    },
    {
        "calculation_key": "universal_source_amount_sum",
        "label": "Sum source amounts",
        "source_kind": "universal_data",
        "description": "Sums exact source amount strings for one entity and currency.",
        "limitations": [
            "Amounts remain source observations; no FX conversion or accounting "
            "recognition is applied.",
            "A source amount is not automatically revenue, expense, profit, or cash.",
        ],
    },
    {
        "calculation_key": "universal_distinct_source_values",
        "label": "Count distinct source values",
        "source_kind": "universal_data",
        "description": "Counts distinct non-empty values in a declared non-identity text field.",
        "limitations": [
            "Values remain source-specific and are never merged across sources.",
            "Names, email addresses, phone numbers, and tax identifiers are not eligible fields.",
        ],
    },
    {
        "calculation_key": "m5_metric",
        "label": "Reuse an existing M5 operational metric",
        "source_kind": "company_context",
        "description": "References a supported deterministic M5 metric and its saved run.",
        "limitations": [
            "M5 values remain implementation-draft and observational.",
            "M5 completeness, assignment, and employee-status limitations remain in force.",
        ],
    },
    {
        "calculation_key": "core_financial_measure",
        "label": "Reuse an existing Core Financials measure",
        "source_kind": "core_financials",
        "description": "References a saved measure from an existing Core Financials run.",
        "limitations": [
            "Core Financials remains default-disabled and production-blocked; its "
            "policies are illustrative.",
            "Cash Flow is net observed bank movement, not a cash-flow statement or balance.",
        ],
    },
    {
        "calculation_key": "custom_formula",
        "label": "Constrained custom expression",
        "source_kind": "published_metric_dependencies",
        "description": (
            "Uses a bounded arithmetic expression tree with typed constants and metric references."
        ),
        "limitations": [
            "Only the enumerated expression operators are accepted; no user code or "
            "query text runs.",
            "Each reference must be a complete saved analysis with the exact pinned "
            "definition and Company configuration.",
            "Formula arithmetic does not create independent source evidence or make an "
            "incomplete dependency complete.",
        ],
    },
)

CAPABILITY_BY_KEY = {str(item["calculation_key"]): item for item in CALCULATION_CAPABILITIES}


def metric_domain_for_calculation(calculation_key: str) -> frozenset[str]:
    return {
        "universal_record_count": frozenset({"sales", "customers", "procurement", "operations"}),
        "universal_source_amount_sum": frozenset({"sales", "procurement"}),
        "universal_distinct_source_values": frozenset(
            {"sales", "customers", "procurement", "operations"}
        ),
        "m5_metric": frozenset({"operations", "people"}),
        "core_financial_measure": frozenset({"finance"}),
        "custom_formula": frozenset(
            {"finance", "sales", "customers", "procurement", "operations", "people"}
        ),
    }.get(calculation_key, frozenset())


def metric_authority(calculation_key: str) -> str:
    if calculation_key == "core_financial_measure":
        return "existing_core_financials_illustrative_not_authoritative"
    if calculation_key == "m5_metric":
        return "m5_implementation_draft_observational"
    if calculation_key == "custom_formula":
        return "deterministic_expression_from_saved_analyses_not_independent_evidence"
    return "source_observation_only"


@dataclass(frozen=True, slots=True)
class SystemMetric:
    metric_key: str
    name: str
    description: str
    domain: str
    category: str
    unit: str
    calculation: dict[str, object]
    limitations: tuple[str, ...]
    version: str


SYSTEM_METRICS: tuple[SystemMetric, ...] = tuple(
    SystemMetric(
        metric_key=f"m5.{item.metric_id}",
        name=item.name,
        description=item.description,
        domain="operations",
        category="workforce_operations",
        unit=item.unit,
        calculation={"kind": "m5_metric", "metric_id": item.metric_id},
        limitations=item.limitations,
        version=f"{METRIC_CATALOG_VERSION}:{item.version}",
    )
    for item in METRIC_DEFINITIONS
) + tuple(
    SystemMetric(
        metric_key=f"core_financials.{measure}",
        name=measure.replace("_", " ").title(),
        description=(
            "References a saved Core Financials result; the source run and its exact policy "
            "remain authoritative for this bounded calculation."
        ),
        domain="finance",
        category="financial_performance",
        unit="source currency amount",
        calculation={"kind": "core_financial_measure", "measure": measure},
        limitations=(
            "Core Financials is default-disabled and production-blocked; policies are "
            "illustrative.",
            "Cash Flow is net observed bank movement, not a cash-flow statement or balance.",
        ),
        version="core_financials.catalog.v1",
    )
    for measure in FINANCIAL_MEASURES
)

SYSTEM_METRIC_BY_KEY = {item.metric_key: item for item in SYSTEM_METRICS}


def system_metric_definition_hash(metric: SystemMetric) -> str:
    calculation_kind = str(metric.calculation.get("kind", ""))
    return content_hash(
        {
            "registry_version": METRIC_REGISTRY_VERSION,
            "system_metric": asdict(metric),
            "authority_boundary": metric_authority(calculation_kind),
        }
    )


def system_metric_payloads() -> list[dict[str, object]]:
    return [
        {
            **asdict(item),
            "definition_type": "system",
            "status": "published",
            "calculation": item.calculation,
            "limitations": list(item.limitations),
            "authority_boundary": metric_authority(str(item.calculation["kind"])),
            "definition_hash": system_metric_definition_hash(item),
        }
        for item in SYSTEM_METRICS
    ]


def calculation_catalog_payload() -> dict[str, object]:
    return {
        "registry_version": METRIC_REGISTRY_VERSION,
        "calculation_catalog_version": CALCULATION_CATALOG_VERSION,
        "authority_boundary": METRIC_AUTHORITY_BOUNDARY,
        "system_metric_count": len(SYSTEM_METRICS),
        "calculations": [{**item, "supported": True} for item in CALCULATION_CAPABILITIES],
        "expression_operators": [
            "constant",
            "metric_ref",
            "add",
            "subtract",
            "multiply",
            "divide",
            "negate",
        ],
        "unsupported": [
            {
                "calculation_key": "arbitrary_sql_or_user_code",
                "supported": False,
                "reason": (
                    "Metric definitions accept only the versioned, allow-listed calculation schema."
                ),
            },
            {
                "calculation_key": "automatic_cross_source_party_merge",
                "supported": False,
                "reason": "Source-scoped references are not automatically merged or deduplicated.",
            },
            {
                "calculation_key": "ai_generated_arithmetic",
                "supported": False,
                "reason": (
                    "Metric definitions and calculations are validated deterministic "
                    "specifications."
                ),
            },
        ],
    }
