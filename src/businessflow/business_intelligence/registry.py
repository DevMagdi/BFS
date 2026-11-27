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

METRIC_REGISTRY_VERSION = "businessflow.metric-registry.v2"
CALCULATION_CATALOG_VERSION = "businessflow.deterministic-calculations.v2"
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
        "calculation_key": "manual_customer_commitment_count",
        "label": "Count accepted manual customer commitments",
        "source_kind": "manual_customer_commitments",
        "description": (
            "Counts only accepted manual customer commitments from the pinned Company source."
        ),
        "limitations": [
            "This is a count of recorded human-reviewed commitments, not proof of delivery, "
            "revenue, backlog completeness, or customer identity.",
            "Customer commitment read permission is required; customer details are not copied "
            "into the analytical snapshot.",
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
        "manual_customer_commitment_count": frozenset({"operations", "sales"}),
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


SYSTEM_METRICS: tuple[SystemMetric, ...] = (
    tuple(
        SystemMetric(
            metric_key=f"m5.{item.metric_id}",
            name=item.name,
            description=item.description,
            domain="people",
            category="workforce_operations",
            unit=item.unit,
            calculation={"kind": "m5_metric", "metric_id": item.metric_id},
            limitations=item.limitations,
            version=f"{METRIC_CATALOG_VERSION}:{item.version}:bi.v2",
        )
        for item in METRIC_DEFINITIONS
    )
    + tuple(
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
            version="core_financials.catalog.v1:bi.v2",
        )
        for measure in FINANCIAL_MEASURES
    )
    + (
        SystemMetric(
            metric_key="sales.source_product_catalog_record_count",
            name="Source product and service record count",
            description=(
                "Counts accepted source-scoped product or service catalog records; "
                "a source record is not a cross-source product identity."
            ),
            domain="sales",
            category="sales_catalog_observation",
            unit="records",
            calculation={"kind": "universal_record_count", "entity_type": "product"},
            limitations=(
                "Counts use one selected source and do not merge product references across "
                "sources.",
                "Catalog records do not establish product pricing, margin, demand, or sales "
                "attribution.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="sales.source_transaction_record_count",
            name="Sales source transaction record count",
            description=(
                "Counts accepted source-scoped sales transaction records for the selected period; "
                "record volume is not revenue or customer count."
            ),
            domain="sales",
            category="sales_activity",
            unit="records",
            calculation={"kind": "universal_record_count", "entity_type": "sales_transaction"},
            limitations=(
                "Counts source-scoped records from one selected source; it does not merge or "
                "deduplicate records across sources.",
                "Record volume is not revenue, recognized sales, or transaction completeness.",
                "Refund, reversal, adjustment, and status policies are not implemented.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="sales.source_amount_observed",
            name="Observed sales transaction source amounts",
            description=(
                "Sums exact source amount strings by the source-provided currency for accepted "
                "sales transaction records."
            ),
            domain="sales",
            category="sales_activity",
            unit="source currency amount",
            calculation={
                "kind": "universal_source_amount_sum",
                "entity_type": "sales_transaction",
                "field": "amount",
                "currency_field": "currency_code",
            },
            limitations=(
                "A source amount is not approved revenue, recognized sales, or an accounting fact.",
                "Currencies remain separate; no FX conversion or accounting-period policy is "
                "applied.",
                "Refund, reversal, adjustment, completeness, and independent-control policies "
                "remain unresolved or unsupported.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="sales.source_transaction_record_count_by_product",
            name="Sales transaction source record count by source product code",
            description=(
                "Counts accepted source-scoped sales transaction records by the optional "
                "product_code supplied on each transaction."
            ),
            domain="sales",
            category="sales_activity",
            unit="records",
            calculation={
                "kind": "universal_record_count",
                "entity_type": "sales_transaction",
                "grouping_dimension": "product",
            },
            limitations=(
                "Product codes remain source-scoped; records are not merged across sources.",
                "A product grouping is not demand, units sold, or proof of product identity.",
                "Transactions without product_code cannot be assigned to a product group.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.1.0",
        ),
        SystemMetric(
            metric_key="sales.source_amount_observed_by_product",
            name="Observed sales transaction source amounts by source product code",
            description=(
                "Sums exact source amount strings by source currency and the optional "
                "product_code supplied on each transaction."
            ),
            domain="sales",
            category="sales_activity",
            unit="source currency amount",
            calculation={
                "kind": "universal_source_amount_sum",
                "entity_type": "sales_transaction",
                "field": "amount",
                "currency_field": "currency_code",
                "grouping_dimension": "product",
            },
            limitations=(
                "This is a source amount observation, not recognized revenue, margin, or an "
                "accounting fact.",
                "Product codes remain source-scoped; records are not merged across sources.",
                "Currencies remain separate; no FX, refund, reversal, or recognition policy "
                "is applied.",
                "Transactions without product_code cannot be assigned to a product group.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.1.0",
        ),
        SystemMetric(
            metric_key="customers.source_customer_record_count",
            name="Customer source record count",
            description=(
                "Counts accepted source-scoped customer records without resolving or matching "
                "customer identities across sources."
            ),
            domain="customers",
            category="customer_observation",
            unit="records",
            calculation={"kind": "universal_record_count", "entity_type": "customer"},
            limitations=(
                "Customer identity and deduplication are unsupported; this is a record count, "
                "not a unique-company count or retention measure.",
                "Analysis is blocked until customer-data processing authorization is established.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="operations.accepted_customer_commitment_count",
            name="Accepted manual customer commitment count",
            description=(
                "Counts accepted manually captured customer commitments, optionally grouped "
                "by their requested delivery date."
            ),
            domain="operations",
            category="delivery_commitment_observation",
            unit="records",
            calculation={
                "kind": "manual_customer_commitment_count",
                "date_field": "requested_delivery_date",
            },
            limitations=(
                "Counts recorded accepted commitments only; it does not establish delivery, "
                "completion, backlog completeness, or customer identity.",
                "Customer details are not copied into the analytical snapshot; "
                "customer_commitment:read is required.",
                "Missing requested delivery dates cannot be assigned to a requested period.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="operations.source_business_activity_record_count",
            name="Business activity source record count",
            description=(
                "Counts accepted source-scoped business activity records for the selected period."
            ),
            domain="operations",
            category="operational_observation",
            unit="records",
            calculation={"kind": "universal_record_count", "entity_type": "business_activity"},
            limitations=(
                "A source activity record is not proof of completed work, service quality, "
                "delivery, productivity, or causality.",
                "Counts use one selected source and do not establish activity completeness.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
        SystemMetric(
            metric_key="operations.source_operational_record_count",
            name="Operational source record count",
            description=(
                "Counts accepted source-scoped operational records for the selected period."
            ),
            domain="operations",
            category="operational_observation",
            unit="records",
            calculation={"kind": "universal_record_count", "entity_type": "operational_record"},
            limitations=(
                "A source record has no additional business interpretation by default.",
                "Counts use one selected source and do not establish completeness, delivery, "
                "productivity, or causal impact.",
            ),
            version=f"{METRIC_REGISTRY_VERSION}:1.0.0",
        ),
    )
)

SYSTEM_METRIC_BY_KEY = {item.metric_key: item for item in SYSTEM_METRICS}


def system_metric_dimensions(metric: SystemMetric) -> tuple[str, ...]:
    calculation = metric.calculation
    kind = calculation.get("kind")
    if kind == "m5_metric":
        return (
            () if calculation.get("metric_id") == "recorded_skill_inventory_coverage" else ("week",)
        )
    if kind == "manual_customer_commitment_count":
        return ("day", "week", "month", "quarter", "year")
    if kind not in {"universal_record_count", "universal_source_amount_sum"}:
        return ()
    if calculation.get("grouping_dimension") == "product":
        return (
            "product",
            "currency_code",
            "status",
            "day",
            "week",
            "month",
            "quarter",
            "year",
        )
    return {
        "customer": ("country", "customer_segment"),
        "product": ("category",),
        "sales_transaction": (
            "customer",
            "currency_code",
            "status",
            "day",
            "week",
            "month",
            "quarter",
            "year",
        ),
        "business_activity": (
            "activity_type",
            "status",
            "day",
            "week",
            "month",
            "quarter",
            "year",
        ),
        "operational_record": (
            "record_type",
            "status",
            "day",
            "week",
            "month",
            "quarter",
            "year",
        ),
    }.get(str(calculation.get("entity_type")), ())


def system_metric_time_granularity(metric: SystemMetric) -> str:
    if "week" in system_metric_dimensions(metric) and metric.calculation.get("kind") == "m5_metric":
        return "week"
    return "none"


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
