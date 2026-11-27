from __future__ import annotations

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from businessflow.business_intelligence.registry import (
    FINANCIAL_MEASURE_IDS,
    M5_METRIC_IDS,
    SYSTEM_METRIC_BY_KEY,
    UNIVERSAL_ENTITY_IDS,
    MetricDomain,
)
from businessflow.business_semantic.catalog import SEMANTIC_RELATIONSHIPS
from businessflow.calculation_core.decimal import parse_decimal_text
from businessflow.schemas import StrictModel
from businessflow.universal_data.catalog import CANONICAL_ENTITIES

UniversalEntity = Literal[
    "customer",
    "supplier",
    "product",
    "sales_transaction",
    "expense",
    "business_activity",
    "operational_record",
]
UniversalValueField = Literal[
    "customer_code",
    "supplier_code",
    "product_code",
    "transaction_reference",
    "expense_reference",
    "activity_reference",
    "record_reference",
    "customer_segment",
    "country",
    "category",
    "status",
    "activity_type",
    "record_type",
    "currency_code",
]
M5MetricId = Literal[
    "available_capacity_hours",
    "recorded_committed_hours",
    "capacity_minus_recorded_commitments_hours",
    "recorded_assignment_overload",
    "recorded_skill_inventory_coverage",
]
NON_DISTINCT_SOURCE_FIELDS = frozenset(
    {
        "customer_code",
        "supplier_code",
        "product_code",
        "transaction_reference",
        "expense_reference",
        "activity_reference",
        "record_reference",
        "name",
        "email",
        "phone",
        "tax_identifier",
    }
)
FinancialMeasure = Literal[
    "revenue", "expenses", "gross_profit", "operating_profit", "net_profit", "cash_flow"
]
MetricDefinitionType = Literal["system", "company", "custom"]
MetricLifecycleStatus = Literal["draft", "review", "published", "deprecated"]
MetricDimension = Literal[
    "day",
    "week",
    "month",
    "quarter",
    "year",
    "customer",
    "supplier",
    "product",
    "category",
    "business_unit",
    "department",
    "branch",
    "project",
    "employee",
    "country",
    "customer_segment",
    "activity_type",
    "record_type",
    "currency_code",
    "status",
]
MetricDatasetType = Literal[
    "universal_records", "customer_commitments", "company_context", "m5", "core_financials"
]
TimeGranularity = Literal["none", "day", "week", "month", "quarter", "year"]
PeriodBoundary = Literal[
    "calendar_period",
    "company_fiscal_period",
    "monday_week",
    "source_period",
    "transaction_date",
]
TimezoneRule = Literal["company", "utc", "source_local"]
RoundingMode = Literal["half_even", "half_up", "down", "up", "none"]
DecimalText = Annotated[
    str,
    StringConstraints(
        strict=True,
        min_length=1,
        max_length=32,
        pattern=r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$",
    ),
]


class CalculationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UniversalRecordCountCalculation(CalculationModel):
    kind: Literal["universal_record_count"]
    entity_type: UniversalEntity


class UniversalSourceAmountSumCalculation(CalculationModel):
    kind: Literal["universal_source_amount_sum"]
    entity_type: Literal["sales_transaction", "expense"]
    field: Literal["amount"] = "amount"
    currency_field: Literal["currency_code"] = "currency_code"
    aggregation: Literal["sum", "arithmetic_mean"] = "sum"


class UniversalDistinctSourceValuesCalculation(CalculationModel):
    kind: Literal["universal_distinct_source_values"]
    entity_type: UniversalEntity
    field: UniversalValueField


class ManualCustomerCommitmentCountCalculation(CalculationModel):
    kind: Literal["manual_customer_commitment_count"]
    date_field: Literal["order_date", "requested_delivery_date"] = "requested_delivery_date"


class M5MetricCalculation(CalculationModel):
    kind: Literal["m5_metric"]
    metric_id: M5MetricId


class CoreFinancialMeasureCalculation(CalculationModel):
    kind: Literal["core_financial_measure"]
    measure: FinancialMeasure


class CustomFormulaCalculation(CalculationModel):
    kind: Literal["custom_formula"]


MetricCalculation = Annotated[
    UniversalRecordCountCalculation
    | UniversalSourceAmountSumCalculation
    | UniversalDistinctSourceValuesCalculation
    | ManualCustomerCommitmentCountCalculation
    | M5MetricCalculation
    | CoreFinancialMeasureCalculation
    | CustomFormulaCalculation,
    Field(discriminator="kind"),
]


class FormulaExpression(StrictModel):
    """Bounded arithmetic AST. It never accepts source code, SQL, or dynamic field access."""

    op: Literal["constant", "metric_ref", "add", "subtract", "multiply", "divide", "negate"]
    value: DecimalText | None = None
    metric_key: Annotated[
        str | None,
        StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$"),
    ] = None
    operands: list[FormulaExpression] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def validate_node_shape_and_bounds(self) -> FormulaExpression:
        if self.op == "constant":
            if self.value is None or self.metric_key is not None or self.operands:
                raise ValueError("constant nodes require only a numeric value")
            try:
                decimal_value = Decimal(self.value)
            except InvalidOperation:
                raise ValueError("formula constants must be decimal text") from None
            digits = len(decimal_value.as_tuple().digits)
            exponent = decimal_value.as_tuple().exponent
            if (
                not decimal_value.is_finite()
                or not isinstance(exponent, int)
                or digits > 24
                or abs(exponent) > 12
            ):
                raise ValueError("formula constants exceed the supported precision")
        elif self.op == "metric_ref":
            if not self.metric_key or self.value is not None or self.operands:
                raise ValueError("metric_ref nodes require only a metric_key")
        elif self.op == "negate":
            if len(self.operands) != 1 or self.value is not None or self.metric_key is not None:
                raise ValueError("negate nodes require exactly one operand")
        elif len(self.operands) != 2 or self.value is not None or self.metric_key is not None:
            raise ValueError("binary formula operators require exactly two operands")
        node_count, depth = self._measure_tree()
        if node_count > 64 or depth > 8:
            raise ValueError("formula expressions are limited to 64 nodes and 8 levels")
        return self

    def _measure_tree(self) -> tuple[int, int]:
        if not self.operands:
            return 1, 1
        descendants = [operand._measure_tree() for operand in self.operands]
        return 1 + sum(count for count, _ in descendants), 1 + max(
            height for _, height in descendants
        )


def formula_metric_references(expression: FormulaExpression | None) -> set[str]:
    if expression is None:
        return set()
    result = (
        {expression.metric_key}
        if expression.op == "metric_ref" and expression.metric_key
        else set()
    )
    for operand in expression.operands:
        result.update(formula_metric_references(operand))
    return result


class MetricSourceRequirement(StrictModel):
    dataset_type: MetricDatasetType
    required: bool = True


class MetricFieldRequirement(StrictModel):
    entity_type: UniversalEntity
    field: Annotated[str, StringConstraints(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def validate_canonical_field(self) -> MetricFieldRequirement:
        field = CANONICAL_ENTITIES[self.entity_type].field_by_name.get(self.field)
        if field is None:
            raise ValueError("required_fields must name a field in the selected canonical entity")
        return self


class MetricFilter(StrictModel):
    entity_type: UniversalEntity
    field: Annotated[str, StringConstraints(min_length=1, max_length=64)]
    operation: Literal["equals", "not_equals", "greater_than", "less_than", "is_not_null"]
    value: Annotated[str, StringConstraints(max_length=256)] | None = None

    @model_validator(mode="after")
    def validate_filter(self) -> MetricFilter:
        field = CANONICAL_ENTITIES[self.entity_type].field_by_name.get(self.field)
        if field is None:
            raise ValueError("filters must use a field in the selected canonical entity")
        if self.operation == "is_not_null" and self.value is not None:
            raise ValueError("is_not_null filters cannot include a comparison value")
        if self.operation != "is_not_null" and self.value is None:
            raise ValueError("comparison filters require a value")
        if self.operation in {"greater_than", "less_than"} and field.field_type == "text":
            raise ValueError("ordered filters require a date or decimal field")
        if self.operation != "is_not_null" and self.value is not None:
            try:
                if field.field_type == "date":
                    parsed_date = date.fromisoformat(self.value)
                    if parsed_date.isoformat() != self.value:
                        raise ValueError("date comparison values must use canonical YYYY-MM-DD")
                elif field.field_type == "decimal":
                    parse_decimal_text(self.value)
            except ValueError as exc:
                raise ValueError(
                    "filter values must match the canonical date or decimal field type"
                ) from exc
        return self


class MetricPolicy(StrictModel):
    currency_behavior: Literal["not_applicable", "single_currency_only", "group_by_currency"] = (
        "not_applicable"
    )
    refund_behavior: Literal["include_signed_source_value", "exclude", "unsupported"] = (
        "unsupported"
    )
    reversal_behavior: Literal["include_signed_source_value", "exclude", "unsupported"] = (
        "unsupported"
    )
    adjustment_behavior: Literal["include_signed_source_value", "exclude", "unsupported"] = (
        "unsupported"
    )
    source_authority_requirement: Literal["no_authority_claim", "owner_asserted_primary"] = (
        "no_authority_claim"
    )
    reconciliation_required: bool = False


class MetricDefinitionCreateRequest(StrictModel):
    expected_version: int = Field(ge=0)
    definition_type: Literal["company", "custom"] = "custom"
    metric_key: Annotated[
        str,
        StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$"),
    ]
    name: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    domain: MetricDomain
    category: Annotated[
        str,
        StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$"),
    ]
    business_meaning: Annotated[str, StringConstraints(min_length=1, max_length=2000)]
    calculation: MetricCalculation
    formula: FormulaExpression | None = None
    numerator: FormulaExpression | None = None
    denominator: FormulaExpression | None = None
    aggregation_method: Literal[
        "count", "sum", "average", "distinct_count", "ratio", "source_defined"
    ]
    dimensions: list[MetricDimension] = Field(default_factory=list, max_length=20)
    required_source_datasets: list[MetricSourceRequirement] = Field(
        default_factory=list, max_length=12
    )
    required_fields: list[MetricFieldRequirement] = Field(default_factory=list, max_length=50)
    required_relationships: list[Annotated[str, StringConstraints(min_length=1, max_length=64)]] = (
        Field(default_factory=list, max_length=20)
    )
    filters: list[MetricFilter] = Field(default_factory=list, max_length=50)
    exclusions: list[MetricFilter] = Field(default_factory=list, max_length=50)
    unit: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    currency_code: Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")] | None = None
    time_granularity: TimeGranularity = "none"
    period_boundary: PeriodBoundary = "calendar_period"
    timezone_rule: TimezoneRule = "company"
    policy: MetricPolicy = Field(default_factory=MetricPolicy)
    completeness_requirements: list[
        Annotated[str, StringConstraints(min_length=1, max_length=500)]
    ] = Field(default_factory=list, max_length=20)
    precision_scale: int = Field(default=2, ge=0, le=12)
    rounding_mode: RoundingMode = "half_even"
    metric_owner_user_id: UUID | None = None
    effective_from: date | None = None
    effective_to: date | None = None
    limitations: list[Annotated[str, StringConstraints(min_length=1, max_length=500)]] = Field(
        min_length=1, max_length=20
    )

    @field_validator("name", "description", "category", "business_meaning", "unit", mode="before")
    @classmethod
    def normalize_definition_text(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("completeness_requirements")
    @classmethod
    def normalize_completeness_requirements(cls, values: list[str]) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized) or len(normalized) != len(set(normalized)):
            raise ValueError("completeness_requirements must be non-empty and unique")
        return normalized

    @model_validator(mode="after")
    def validate_definition(self) -> MetricDefinitionCreateRequest:
        calculation_key = self.calculation.kind
        if calculation_key != "custom_formula" and any(
            expression is not None
            for expression in (self.formula, self.numerator, self.denominator)
        ):
            raise ValueError("formula expressions are supported only by custom_formula metrics")
        if self.domain not in _allowed_domains(calculation_key):
            raise ValueError("the metric domain is not supported by this calculation")

        required_dataset = {
            "universal_record_count": "universal_records",
            "universal_source_amount_sum": "universal_records",
            "universal_distinct_source_values": "universal_records",
            "manual_customer_commitment_count": "customer_commitments",
            "m5_metric": "m5",
            "core_financial_measure": "core_financials",
        }.get(calculation_key)
        if required_dataset is None:
            if self.required_source_datasets:
                raise ValueError("custom formulas use saved analyses, not direct source datasets")
        else:
            if any(item.dataset_type != required_dataset for item in self.required_source_datasets):
                raise ValueError(
                    "source requirements cannot name datasets this calculation does not use"
                )
            if not any(
                item.dataset_type == required_dataset and item.required
                for item in self.required_source_datasets
            ):
                raise ValueError(f"this calculation requires the {required_dataset} source dataset")

        universal_calculation = isinstance(
            self.calculation,
            (
                UniversalRecordCountCalculation,
                UniversalSourceAmountSumCalculation,
                UniversalDistinctSourceValuesCalculation,
            ),
        )
        entity_type: str | None = None
        if isinstance(
            self.calculation,
            (
                UniversalRecordCountCalculation,
                UniversalSourceAmountSumCalculation,
                UniversalDistinctSourceValuesCalculation,
            ),
        ):
            entity_type = self.calculation.entity_type
        if entity_type is not None:
            expected_domain = {
                "customer": "customers",
                "supplier": "procurement",
                "product": "sales",
                "sales_transaction": "sales",
                "expense": "procurement",
                "business_activity": "operations",
                "operational_record": "operations",
            }[entity_type]
            if self.domain != expected_domain:
                raise ValueError("the metric domain must match the selected Universal Data entity")
            allowed_dimensions = {
                "customer": {"customer", "country", "customer_segment"},
                "supplier": {"supplier", "country"},
                "product": {"product", "category"},
                "sales_transaction": {"customer", "product", "currency_code", "status"},
                "expense": {"supplier", "category", "currency_code"},
                "business_activity": {"activity_type", "status"},
                "operational_record": {"record_type", "status"},
            }[entity_type]
            date_entities = {
                "sales_transaction",
                "expense",
                "business_activity",
                "operational_record",
            }
            if entity_type in date_entities:
                allowed_dimensions |= {"day", "week", "month", "quarter", "year"}
            if isinstance(self.calculation, UniversalDistinctSourceValuesCalculation):
                allowed_dimensions |= {self.calculation.field}
            if not set(self.dimensions) <= allowed_dimensions:
                raise ValueError("dimensions must be supported by the selected canonical entity")
            if any(item.entity_type != entity_type for item in self.required_fields):
                raise ValueError("required_fields must use the metric's canonical entity")
            if any(item.entity_type != entity_type for item in self.filters + self.exclusions):
                raise ValueError("filters and exclusions must use the metric's canonical entity")
            if self.time_granularity != "none" and entity_type not in date_entities:
                raise ValueError("this canonical entity does not have a supported date field")
            if self.policy.source_authority_requirement not in {
                "no_authority_claim",
                "owner_asserted_primary",
            }:
                raise ValueError("source authority requirement is unsupported")
            if self.timezone_rule != "company":
                raise ValueError(
                    "Universal Data date fields use the Company calendar in this version"
                )
            if self.period_boundary == "source_period":
                raise ValueError(
                    "source-period boundaries are not supported for Universal Data dates"
                )
        elif self.required_fields or self.filters or self.exclusions or self.required_relationships:
            raise ValueError(
                "source fields, filters, and semantic relationships require a Universal Data metric"
            )

        if isinstance(self.calculation, ManualCustomerCommitmentCountCalculation):
            allowed_time_dimensions = {"day", "week", "month", "quarter", "year"}
            if not set(self.dimensions) <= allowed_time_dimensions:
                raise ValueError(
                    "manual customer commitment metrics support only requested-date dimensions"
                )
            if (
                self.time_granularity != "none"
                and self.time_granularity not in allowed_time_dimensions
            ):
                raise ValueError("manual commitment time granularity must be calendar-based")
            if self.unit != "records":
                raise ValueError("manual customer commitment counts must use records")
            if self.policy.currency_behavior != "not_applicable" or self.currency_code is not None:
                raise ValueError("manual customer commitment counts do not have currency behavior")

        if not universal_calculation:
            if self.completeness_requirements:
                raise ValueError(
                    "free-text completeness requirements are only retained for "
                    "Universal Data metrics"
                )
            if self.policy.reconciliation_required:
                raise ValueError("reconciliation is governed by the exact saved source analysis")
            if self.policy.source_authority_requirement != "no_authority_claim":
                raise ValueError(
                    "source authority requirements apply only to Universal Data metrics"
                )
            if self.timezone_rule != "company" or self.period_boundary != "calendar_period":
                raise ValueError(
                    "this calculation does not implement alternate time-zone or period rules"
                )

        if self.time_granularity != "none" and self.time_granularity not in self.dimensions:
            raise ValueError("the declared time granularity must be included in metric dimensions")
        if self.period_boundary == "monday_week" and self.time_granularity not in {"none", "week"}:
            raise ValueError("monday_week boundaries apply only to weekly metrics")
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions must be unique")
        if len(self.required_relationships) != len(set(self.required_relationships)):
            raise ValueError("required_relationships must be unique")
        unknown_relationships = set(self.required_relationships) - set(SEMANTIC_RELATIONSHIPS)
        if unknown_relationships:
            raise ValueError("required_relationships must use a supported verified relationship")
        if len(self.required_source_datasets) != len(
            {item.dataset_type for item in self.required_source_datasets}
        ):
            raise ValueError("required_source_datasets must not repeat a dataset type")
        if self.effective_from and self.effective_to and self.effective_to < self.effective_from:
            raise ValueError("effective_to must be on or after effective_from")
        self._validate_unique_fields(self.required_fields, "required_fields")
        self._validate_unique_fields(self.filters, "filters")
        self._validate_unique_fields(self.exclusions, "exclusions")

        expected_aggregation = {
            "universal_record_count": "count",
            "universal_source_amount_sum": "sum",
            "universal_distinct_source_values": "distinct_count",
            "manual_customer_commitment_count": "count",
            "m5_metric": "source_defined",
            "core_financial_measure": "source_defined",
        }.get(calculation_key)
        if isinstance(self.calculation, UniversalSourceAmountSumCalculation):
            expected_aggregation = (
                "average" if self.calculation.aggregation == "arithmetic_mean" else "sum"
            )
        if calculation_key == "custom_formula":
            is_ratio = self.numerator is not None and self.denominator is not None
            expected_aggregation = "ratio" if is_ratio else "source_defined"
            expressions = [self.formula, self.numerator, self.denominator]
            references = set().union(
                *(formula_metric_references(expression) for expression in expressions)
            )
            if not references:
                raise ValueError("custom formulas must reference at least one published metric")
            if len(references) > 20:
                raise ValueError("custom formulas are limited to 20 distinct metric dependencies")
            if self.policy.currency_behavior != "not_applicable":
                raise ValueError(
                    "custom formula currency behavior is derived from its exact dependencies"
                )
        if expected_aggregation is not None and self.aggregation_method != expected_aggregation:
            raise ValueError(f"this calculation requires aggregation_method={expected_aggregation}")

        if isinstance(self.calculation, UniversalSourceAmountSumCalculation):
            field = CANONICAL_ENTITIES[self.calculation.entity_type].field_by_name.get("amount")
            if field is None or field.field_type != "decimal":
                raise ValueError("source amount metrics require a canonical decimal amount field")
            average_basis = (
                "transaction"
                if self.calculation.entity_type == "sales_transaction"
                else "expense record"
            )
            expected_unit = (
                f"source currency amount per {average_basis}"
                if self.calculation.aggregation == "arithmetic_mean"
                else "source currency amount"
            )
            if self.unit != expected_unit:
                raise ValueError("source amount metric units must match its declared aggregation")
            if self.policy.currency_behavior not in {"single_currency_only", "group_by_currency"}:
                raise ValueError("source amount metrics must declare a supported currency behavior")
            if (
                self.policy.currency_behavior == "group_by_currency"
                and self.currency_code is not None
            ):
                raise ValueError("group_by_currency metrics cannot pin one currency_code")
        elif isinstance(self.calculation, CoreFinancialMeasureCalculation):
            if self.calculation.measure not in FINANCIAL_MEASURE_IDS:
                raise ValueError("the requested Core Financials measure is unsupported")
            core_metric = SYSTEM_METRIC_BY_KEY.get(f"core_financials.{self.calculation.measure}")
            if core_metric is None or self.unit != core_metric.unit:
                raise ValueError(
                    "Core Financials units must match the saved measure's source-currency unit"
                )
            if self.policy.currency_behavior != "single_currency_only":
                raise ValueError("Core Financials measures are bound to one saved-run currency")
            if self.currency_code is not None:
                raise ValueError("Core Financials currency comes from the exact saved run")
            if self.dimensions or self.time_granularity != "none":
                raise ValueError("Core Financials measures cannot be regrouped by dimensions")
        elif isinstance(self.calculation, UniversalRecordCountCalculation):
            if self.calculation.entity_type not in UNIVERSAL_ENTITY_IDS:
                raise ValueError("choose a supported Universal Data entity type")
            if self.unit != "records":
                raise ValueError("record-count metric units must be records")
            if self.policy.currency_behavior != "not_applicable":
                raise ValueError("record counts do not have currency behavior")
            if self.currency_code is not None:
                raise ValueError("record counts cannot declare a currency")
        elif isinstance(self.calculation, UniversalDistinctSourceValuesCalculation):
            if self.unit != "source values":
                raise ValueError("distinct-value metric units must be source values")
            entity = CANONICAL_ENTITIES[self.calculation.entity_type]
            field = entity.field_by_name.get(self.calculation.field)
            if field is None or field.field_type != "text":
                raise ValueError("distinct-value metrics require a supported text field")
            if self.calculation.field in NON_DISTINCT_SOURCE_FIELDS:
                raise ValueError(
                    "identity or personal fields cannot be used for distinct-value metrics"
                )
            if self.policy.currency_behavior != "not_applicable":
                raise ValueError("distinct-value metrics do not have currency behavior")
            if self.currency_code is not None:
                raise ValueError("distinct-value metrics cannot declare a currency")
        elif isinstance(self.calculation, M5MetricCalculation):
            if self.calculation.metric_id not in M5_METRIC_IDS:
                raise ValueError("the requested M5 metric is unsupported")
            m5_metric = SYSTEM_METRIC_BY_KEY.get(f"m5.{self.calculation.metric_id}")
            if m5_metric is None or self.unit != m5_metric.unit:
                raise ValueError("M5 metric units must match the saved metric definition")
            if self.policy.currency_behavior != "not_applicable":
                raise ValueError("M5 metric currency behavior is not applicable")
            if self.currency_code is not None:
                raise ValueError("M5 metrics cannot declare a currency")
            if self.calculation.metric_id == "recorded_skill_inventory_coverage":
                if self.time_granularity != "none" or self.dimensions:
                    raise ValueError("skill inventory is a non-periodic structured observation")
            elif self.time_granularity != "week" or set(self.dimensions) != {"week"}:
                raise ValueError(
                    "existing M5 metrics are supported only at their saved weekly grain"
                )
        elif isinstance(self.calculation, CustomFormulaCalculation):
            has_ratio = self.numerator is not None and self.denominator is not None
            if (self.numerator is None) != (self.denominator is None):
                raise ValueError("ratio metrics require both numerator and denominator expressions")
            if (self.formula is None) == (not has_ratio):
                raise ValueError(
                    "custom formulas require either a formula or a numerator/denominator pair"
                )
            if has_ratio and self.formula is not None:
                raise ValueError("use either a formula or a numerator/denominator pair, not both")

        event_policies = (
            self.policy.refund_behavior,
            self.policy.reversal_behavior,
            self.policy.adjustment_behavior,
        )
        if any(value != "unsupported" for value in event_policies):
            raise ValueError("refund, reversal, and adjustment policies are not implemented")

        limitations = [value.strip() for value in self.limitations]
        if any(not value for value in limitations) or len(limitations) != len(set(limitations)):
            raise ValueError("limitations must be non-empty and unique")
        object.__setattr__(self, "limitations", limitations)
        return self

    @staticmethod
    def _validate_unique_fields(
        items: Sequence[MetricFieldRequirement | MetricFilter], field_name: str
    ) -> None:
        keys = []
        for item in items:
            keys.append((item.entity_type, item.field))
        if len(keys) != len(set(keys)):
            raise ValueError(f"{field_name} cannot repeat an entity field")


class MetricDefinitionLifecycleRequest(StrictModel):
    expected_version: int = Field(ge=1)
    target_status: Literal["draft", "review", "published", "deprecated"]
    note: Annotated[str, StringConstraints(min_length=1, max_length=500)]

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("note must contain non-whitespace text")
        return normalized


class MetricDefinitionResponse(StrictModel):
    id: UUID
    metric_key: str
    version: int
    definition_type: MetricDefinitionType
    status: MetricLifecycleStatus
    name: str
    description: str
    domain: MetricDomain
    category: str
    business_meaning: str
    calculation: dict[str, object]
    calculation_key: str
    formula: dict[str, object] | None
    numerator: dict[str, object] | None
    denominator: dict[str, object] | None
    dependencies: list[dict[str, object]]
    aggregation_method: str
    dimensions: list[str]
    required_source_datasets: list[dict[str, object]]
    required_fields: list[dict[str, object]]
    required_relationships: list[str]
    filters: list[dict[str, object]]
    exclusions: list[dict[str, object]]
    unit: str
    currency_code: str | None
    time_granularity: TimeGranularity
    period_boundary: PeriodBoundary
    timezone_rule: TimezoneRule
    policy: dict[str, object]
    completeness_requirements: list[str]
    precision_scale: int
    rounding_mode: RoundingMode
    metric_owner_user_id: UUID
    effective_from: date | None
    effective_to: date | None
    definition_hash: str
    supersedes_metric_definition_version_id: UUID | None
    author_user_id: UUID
    created_by_user_id: UUID
    reviewed_by_user_id: UUID | None
    review_note: str | None
    limitations: list[str]
    created_at: datetime


class MetricDefinitionPage(StrictModel):
    items: list[MetricDefinitionResponse]
    total: int
    limit: int
    offset: int


class MetricDependency(StrictModel):
    metric_key: str
    version: int | None
    version_id: UUID | None
    system_version: str | None


class MetricRegistryCatalog(StrictModel):
    registry_version: str
    calculation_catalog_version: str
    authority_boundary: str
    system_metrics: list[dict[str, object]]
    calculations: list[dict[str, object]]
    expression_operators: list[str]
    unsupported: list[dict[str, object]]
    governance_blockers: list[str]


def metric_calculation_dict(calculation: MetricCalculation) -> dict[str, object]:
    return calculation.model_dump(mode="json", exclude_none=True)


def metric_definition_payload(body: MetricDefinitionCreateRequest) -> dict[str, object]:
    return body.model_dump(mode="json", exclude={"expected_version", "metric_owner_user_id"})


def _allowed_domains(calculation_key: str) -> frozenset[str]:
    from businessflow.business_intelligence.registry import metric_domain_for_calculation

    return metric_domain_for_calculation(calculation_key)


class SourcePrecedenceInput(StrictModel):
    dataset_type: Literal["universal_records", "customer_commitments"] = "universal_records"
    source_id: UUID


class CompanySemanticConfigurationCreateRequest(StrictModel):
    expected_version: int = Field(ge=0)
    fiscal_year_start_month: int = Field(default=1, ge=1, le=12)
    fiscal_year_label: Literal["start_year", "end_year"] = "end_year"
    week_start_day: int = Field(default=1, ge=1, le=7)
    fallback_policy: Literal["strict_primary", "next_ready_source"] = "strict_primary"
    source_precedence: list[SourcePrecedenceInput] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def validate_precedence(self) -> CompanySemanticConfigurationCreateRequest:
        source_keys = [(item.dataset_type, item.source_id) for item in self.source_precedence]
        if len(source_keys) != len(set(source_keys)):
            raise ValueError("source_precedence cannot repeat a source for one dataset")
        return self


class SourcePrecedenceResponse(StrictModel):
    dataset_type: str
    source_id: UUID
    source_name: str
    source_profile_version_id: UUID
    source_profile_version: int
    priority: int
    source_status: str
    authority_classification: str
    freshness_max_age_hours: int | None


class CompanySemanticConfigurationResponse(StrictModel):
    id: UUID
    version: int
    status: Literal["published"]
    base_currency: str
    timezone_name: str
    fiscal_year_start_month: int
    fiscal_year_label: Literal["start_year", "end_year"]
    week_start_day: int
    fallback_policy: Literal["strict_primary", "next_ready_source"]
    semantic_policy: dict[str, object]
    source_precedence: list[SourcePrecedenceResponse]
    configuration_hash: str
    created_by_user_id: UUID
    created_at: datetime


class CompanySemanticConfigurationPage(StrictModel):
    items: list[CompanySemanticConfigurationResponse]
    total: int
    limit: int
    offset: int


class MetricReadinessCheck(StrictModel):
    check_key: str
    status: Literal["ready", "warning", "blocked", "unknown"]
    message: str
    evidence: dict[str, object] = Field(default_factory=dict)


class MetricReadinessResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    domain: MetricDomain
    metric_key: str
    metric_version: int | None
    definition_hash: str
    configuration_version: int | None
    analysis_id: UUID | None
    period_start: date | None
    period_end: date | None
    dimensions: list[str]
    readiness_status: Literal["ready", "conditional", "blocked", "unknown"]
    calculation_supported: bool
    checks: list[MetricReadinessCheck]
    blockers: list[str]
    warnings: list[str]
    limitations: list[str]
    governance_blockers: list[str]
    retention_status: Literal["unresolved"] = "unresolved"


class MetricDependencyAnalysisInput(StrictModel):
    metric_key: Annotated[
        str,
        StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$"),
    ]
    analysis_id: UUID


class MetricAnalysisCreateRequest(StrictModel):
    metric_key: Annotated[
        str,
        StringConstraints(min_length=2, max_length=64, pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$"),
    ]
    metric_version: int | None = Field(default=None, ge=1)
    configuration_version: int = Field(ge=1)
    period_start: date | None = None
    period_end: date | None = None
    dimensions: list[MetricDimension] = Field(default_factory=list, max_length=2)
    filters: list[MetricFilter] = Field(default_factory=list, max_length=20)
    source_run_id: UUID | None = None
    core_financial_run_id: UUID | None = None
    dependency_analyses: list[MetricDependencyAnalysisInput] = Field(
        default_factory=list, max_length=20
    )

    @model_validator(mode="after")
    def validate_analysis_request(self) -> MetricAnalysisCreateRequest:
        if (self.period_start is None) != (self.period_end is None):
            raise ValueError("period_start and period_end must be supplied together")
        if self.period_start and self.period_end and self.period_end < self.period_start:
            raise ValueError("period_end must be on or after period_start")
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions must be unique")
        filter_keys = [(item.entity_type, item.field) for item in self.filters]
        if len(filter_keys) != len(set(filter_keys)):
            raise ValueError("filters cannot repeat an entity field")
        dependency_keys = [item.metric_key for item in self.dependency_analyses]
        if len(dependency_keys) != len(set(dependency_keys)):
            raise ValueError("dependency_analyses cannot repeat a metric key")
        if self.source_run_id is not None and self.core_financial_run_id is not None:
            raise ValueError("select at most one direct saved source run")
        return self


class MetricEvidenceReference(StrictModel):
    evidence_index: int = Field(ge=0)
    evidence_kind: Literal[
        "universal_record",
        "manual_customer_commitment",
        "semantic_relationship",
        "m5_metric_run",
        "core_financial_run",
        "dependency_analysis",
    ]
    source_id: UUID | None = None
    record_id: UUID | None = None
    revision: int | None = Field(default=None, ge=1)
    ingestion_id: UUID | None = None
    source_snapshot_id: UUID | None = None
    source_row_id: UUID | None = None
    mapping_profile_version_id: UUID | None = None
    source_profile_version_id: UUID | None = None
    row_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    attributes_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    commitment_id: UUID | None = None
    commitment_status: Literal["accepted"] | None = None
    commitment_event_id: UUID | None = None
    commitment_event_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = (
        None
    )
    commitment_snapshot_hash: (
        Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None
    ) = None
    analysis_date: date | None = None
    analysis_date_field: Literal["order_date", "requested_delivery_date"] | None = None
    relationship_version_id: UUID | None = None
    relationship_type: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = None
    relationship_version: int | None = Field(default=None, ge=1)
    relationship_status: Literal["active", "retracted"] | None = None
    relationship_evidence_kind: Literal["source_verified"] | None = None
    relationship_subject_entity_id: UUID | None = None
    relationship_object_entity_id: UUID | None = None
    subject_mapping_version_id: UUID | None = None
    object_mapping_version_id: UUID | None = None
    run_id: UUID | None = None
    analysis_id: UUID | None = None
    metric_key: str | None = None
    metric_version: int | None = Field(default=None, ge=1)
    system_metric_version: str | None = None
    input_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    result_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    definition_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    metric_catalog_version: str | None = None
    calculation_version: str | None = None
    trace_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None
    policy_id: UUID | None = None
    policy_version: int | None = Field(default=None, ge=1)
    policy_hash: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")] | None = None

    @model_validator(mode="after")
    def validate_evidence_reference(self) -> MetricEvidenceReference:
        required: tuple[object | None, ...]
        if self.evidence_kind == "universal_record":
            required = (
                self.source_id,
                self.record_id,
                self.revision,
                self.ingestion_id,
                self.source_snapshot_id,
                self.source_row_id,
                self.mapping_profile_version_id,
                self.source_profile_version_id,
                self.row_hash,
                self.attributes_hash,
            )
        elif self.evidence_kind == "manual_customer_commitment":
            required = (
                self.source_id,
                self.source_profile_version_id,
                self.commitment_id,
                self.revision,
                self.commitment_status,
                self.commitment_event_id,
                self.commitment_event_hash,
                self.commitment_snapshot_hash,
                self.analysis_date_field,
            )
        elif self.evidence_kind == "semantic_relationship":
            required = (
                self.relationship_version_id,
                self.relationship_type,
                self.relationship_version,
                self.relationship_status,
                self.relationship_evidence_kind,
                self.relationship_subject_entity_id,
                self.relationship_object_entity_id,
                self.subject_mapping_version_id,
                self.object_mapping_version_id,
            )
        elif self.evidence_kind == "m5_metric_run":
            required = (
                self.run_id,
                self.metric_key,
                self.system_metric_version,
                self.input_hash,
                self.result_hash,
                self.metric_catalog_version,
                self.calculation_version,
            )
        elif self.evidence_kind == "core_financial_run":
            required = (
                self.run_id,
                self.metric_key,
                self.system_metric_version,
                self.input_hash,
                self.result_hash,
                self.trace_hash,
                self.policy_id,
                self.policy_version,
                self.policy_hash,
            )
        else:
            required = (
                self.analysis_id,
                self.metric_key,
                self.input_hash,
                self.result_hash,
                self.definition_hash,
            )
        if any(value is None for value in required):
            raise ValueError(
                "evidence references must include fields required by their evidence_kind"
            )
        return self


class MetricEvidencePage(StrictModel):
    tenant_id: UUID
    company_id: UUID
    analysis_id: UUID
    metric_key: str
    domain: MetricDomain
    period_start: date | None
    period_end: date | None
    items: list[MetricEvidenceReference]
    total: int
    limit: int
    offset: int
    governance_blockers: list[str]
    retention_status: Literal["unresolved"] = "unresolved"


class MetricAnalysisResponse(StrictModel):
    id: UUID
    tenant_id: UUID
    company_id: UUID
    domain: MetricDomain
    metric_key: str
    metric_version: int | None
    system_metric_version: str | None
    definition_hash: str
    configuration_version: int
    configuration_hash: str
    period_start: date | None
    period_end: date | None
    status: Literal["complete", "partial", "abstained"]
    calculation_version: str
    input_hash: str
    result_hash: str
    result_snapshot: dict[str, object]
    evidence_count: int
    governance_blockers: list[str]
    retention_status: Literal["unresolved"] = "unresolved"
    created_at: datetime


class MetricAnalysisPage(StrictModel):
    items: list[MetricAnalysisResponse]
    total: int
    limit: int
    offset: int


class MetricAnalysisReplayResponse(StrictModel):
    tenant_id: UUID
    company_id: UUID
    domain: MetricDomain
    analysis_id: UUID
    replay_status: Literal["verified"]
    input_hash: str
    stored_result_hash: str
    replayed_result_hash: str
    result_snapshot: dict[str, object]
    governance_blockers: list[str]
    retention_status: Literal["unresolved"] = "unresolved"
