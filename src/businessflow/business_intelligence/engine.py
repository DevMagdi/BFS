from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    localcontext,
)
from typing import Any, cast

from businessflow.business_intelligence.registry import (
    CAPABILITY_BY_KEY,
    METRIC_AUTHORITY_BOUNDARY,
    metric_authority,
)
from businessflow.business_intelligence.schemas import FormulaExpression
from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    fixed_scale_text,
    parse_decimal_text,
    quantize_decimal,
)
from businessflow.metrics.snapshot import content_hash
from businessflow.universal_data.catalog import CANONICAL_ENTITIES

BI_CALCULATION_VERSION = "businessflow.bi-calculation.v2"
BI_INPUT_SCHEMA_VERSION = 1
BI_RESULT_SCHEMA_VERSION = 2
MAX_FORMULA_PRECISION = 128
DIMENSIONLESS_UNITS = frozenset({"dimensionless", "fraction", "percent", "percentage", "ratio"})

_ROUNDING_MAP = {
    "half_even": "half_even",
    "half_up": "half_up",
    "down": "down",
    "up": "up",
}


def _mapping(value: object) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError("saved analysis snapshot object is invalid")
    return cast(dict[str, object], value)


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise ValueError("saved analysis snapshot list is invalid")
    return value


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value]


def _integer_value(value: object, default: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        return default
    return value


def _text(value: object, *, name: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value


def _decimal_result(value: Decimal, definition: dict[str, object]) -> str:
    if not value.is_finite():
        raise ValueError("calculation produced a non-finite Decimal")
    rounding_mode = definition.get("rounding_mode", "half_even")
    scale_value = definition.get("precision_scale", 2)
    if not isinstance(scale_value, int) or isinstance(scale_value, bool):
        raise ValueError("metric precision scale is invalid")
    if rounding_mode == "none":
        return decimal_text(value)
    if rounding_mode not in _ROUNDING_MAP:
        raise ValueError("metric rounding mode is unsupported")
    rounded = quantize_decimal(
        value,
        scale=scale_value,
        mode=cast(Any, _ROUNDING_MAP[rounding_mode]),
    )
    return fixed_scale_text(rounded, scale=scale_value)


def evaluate_formula(expression: FormulaExpression, metric_values: dict[str, Decimal]) -> Decimal:
    """Evaluate a bounded, validated AST using only exact Decimal references and operators."""
    try:
        with localcontext(Context(prec=MAX_FORMULA_PRECISION, rounding=ROUND_HALF_EVEN)):
            return _evaluate_formula_node(expression, metric_values)
    except (DecimalException, DivisionByZero, InvalidOperation) as exc:
        raise ValueError("formula arithmetic is undefined or exceeds supported precision") from exc


def _evaluate_formula_node(
    expression: FormulaExpression, metric_values: dict[str, Decimal]
) -> Decimal:
    if expression.op == "constant":
        if expression.value is None:
            raise ValueError("formula constant is missing")
        return Decimal(expression.value)
    if expression.op == "metric_ref":
        if expression.metric_key is None or expression.metric_key not in metric_values:
            raise ValueError("a formula dependency is missing for this group")
        return metric_values[expression.metric_key]
    operands = [_evaluate_formula_node(item, metric_values) for item in expression.operands]
    if expression.op == "negate":
        return -operands[0]
    if expression.op == "add":
        return operands[0] + operands[1]
    if expression.op == "subtract":
        return operands[0] - operands[1]
    if expression.op == "multiply":
        return operands[0] * operands[1]
    if expression.op == "divide":
        return operands[0] / operands[1]
    raise ValueError("formula operator is unsupported")


def _record_date(record: dict[str, object], entity_type: str) -> date | None:
    attributes = _mapping(record.get("attributes"))
    date_field = {
        "sales_transaction": "transaction_date",
        "expense": "expense_date",
        "business_activity": "activity_date",
        "operational_record": "record_date",
    }.get(entity_type)
    if date_field is None:
        return None
    raw = attributes.get(date_field)
    if not isinstance(raw, str):
        return None
    try:
        return date.fromisoformat(raw)
    except ValueError:
        return None


def _dimension_value(
    dimension: str,
    record: dict[str, object],
    *,
    config: dict[str, object],
    period_boundary: str,
) -> object:
    entity_type = _text(record.get("entity_type"), name="entity_type")
    attributes = _mapping(record.get("attributes"))
    record_date = _record_date(record, entity_type)
    if dimension in {"day", "week", "month", "quarter", "year"}:
        if record_date is None:
            return None
        if dimension == "day":
            return record_date.isoformat()
        if dimension == "week":
            raw_start = config.get("week_start_day", 1)
            start_day = (
                1
                if period_boundary == "monday_week"
                else raw_start
                if isinstance(raw_start, int) and 1 <= raw_start <= 7
                else 1
            )
            week_start = record_date - timedelta(days=(record_date.isoweekday() - start_day) % 7)
            return week_start.isoformat()
        if dimension == "month":
            return f"{record_date.year:04d}-{record_date.month:02d}"
        if dimension == "quarter":
            if period_boundary == "company_fiscal_period":
                raw_month = config.get("fiscal_year_start_month", 1)
                fiscal_start_month = raw_month if isinstance(raw_month, int) else 1
                quarter = ((record_date.month - fiscal_start_month) % 12) // 3 + 1
                fiscal_start_year = (
                    record_date.year
                    if record_date.month >= fiscal_start_month
                    else record_date.year - 1
                )
                fiscal_end_year = fiscal_start_year + (1 if fiscal_start_month != 1 else 0)
                fiscal_label = config.get("fiscal_year_label", "end_year")
                fiscal_year = fiscal_start_year if fiscal_label == "start_year" else fiscal_end_year
                return f"FY{fiscal_year:04d}-Q{quarter}"
            quarter = (record_date.month - 1) // 3 + 1
            return f"{record_date.year:04d}-Q{quarter}"
        if period_boundary == "company_fiscal_period":
            raw_month = config.get("fiscal_year_start_month", 1)
            fiscal_start_month = raw_month if isinstance(raw_month, int) else 1
            fiscal_start_year = (
                record_date.year
                if record_date.month >= fiscal_start_month
                else record_date.year - 1
            )
            fiscal_end_year = fiscal_start_year + (1 if fiscal_start_month != 1 else 0)
            fiscal_label = config.get("fiscal_year_label", "end_year")
            fiscal_year = fiscal_start_year if fiscal_label == "start_year" else fiscal_end_year
            return f"FY{fiscal_year:04d}"
        return f"{record_date.year:04d}"

    field_by_dimension: dict[str, dict[str, str]] = {
        "customer": {
            "customer": "customer_code",
        },
        "supplier": {
            "supplier": "supplier_code",
        },
        "product": {
            "product": "product_code",
            "category": "category",
        },
        "sales_transaction": {
            "customer": "customer_code",
            "currency_code": "currency_code",
            "status": "status",
        },
        "expense": {
            "supplier": "supplier_code",
            "category": "category",
            "currency_code": "currency_code",
        },
        "business_activity": {
            "activity_type": "activity_type",
            "status": "status",
        },
        "operational_record": {
            "record_type": "record_type",
            "status": "status",
        },
    }
    if dimension == "customer_segment" and entity_type == "customer":
        return attributes.get("customer_segment")
    if dimension == "country" and entity_type in {"customer", "supplier"}:
        return attributes.get("country")
    field = field_by_dimension.get(entity_type, {}).get(dimension)
    if field is None:
        raise ValueError(f"dimension {dimension} is unsupported for {entity_type}")
    return attributes.get(field)


def _filter_matches(record: dict[str, object], filter_item: dict[str, object]) -> bool:
    entity_type = _text(record.get("entity_type"), name="entity_type")
    filter_entity = _text(filter_item.get("entity_type"), name="filter entity_type")
    if filter_entity != entity_type:
        raise ValueError("filter entity type does not match the metric's source entity")
    attributes = _mapping(record.get("attributes"))
    field = _text(filter_item.get("field"), name="filter field")
    operation = _text(filter_item.get("operation"), name="filter operation")
    actual = attributes.get(field)
    expected = filter_item.get("value")
    if operation == "is_not_null":
        return actual is not None and actual != ""
    if not isinstance(expected, str):
        raise ValueError("comparison filter value is missing")
    if actual is None:
        return False
    canonical_field = CANONICAL_ENTITIES[entity_type].field_by_name.get(field)
    if canonical_field is None:
        raise ValueError("filter field is not part of the canonical entity")
    if canonical_field.field_type == "date":
        left_value: object = date.fromisoformat(str(actual))
        right_value: object = date.fromisoformat(expected)
    elif canonical_field.field_type == "decimal":
        left_value = parse_decimal_text(str(actual))
        right_value = parse_decimal_text(expected)
    else:
        left_value = str(actual)
        right_value = expected
    if operation == "equals":
        return left_value == right_value
    if operation == "not_equals":
        return left_value != right_value
    if operation in {"greater_than", "less_than"}:
        if canonical_field.field_type == "date":
            left_date = cast(date, left_value)
            right_date = cast(date, right_value)
            return left_date > right_date if operation == "greater_than" else left_date < right_date
        if canonical_field.field_type == "decimal":
            left_decimal = cast(Decimal, left_value)
            right_decimal = cast(Decimal, right_value)
            return (
                left_decimal > right_decimal
                if operation == "greater_than"
                else left_decimal < right_decimal
            )
        raise ValueError("ordered filters require a date or decimal field")
    raise ValueError("comparison filter operation is unsupported")


def _filtered_records(
    definition: dict[str, object],
    input_snapshot: dict[str, object],
) -> list[dict[str, object]]:
    calculation = _mapping(definition.get("calculation"))
    entity_type = _text(calculation.get("entity_type"), name="calculation entity_type")
    inputs = _mapping(input_snapshot.get("calculation_inputs"))
    records = [_mapping(item) for item in _list(inputs.get("records", []))]
    period = _mapping(input_snapshot.get("period", {}))
    start = date.fromisoformat(str(period["start"])) if period.get("start") else None
    end = date.fromisoformat(str(period["end"])) if period.get("end") else None
    filters = [_mapping(item) for item in _list(definition.get("filters", []))] + [
        _mapping(item) for item in _list(input_snapshot.get("analysis_filters", []))
    ]
    exclusions = [_mapping(item) for item in _list(definition.get("exclusions", []))]
    selected: list[dict[str, object]] = []
    for record in records:
        if record.get("entity_type") != entity_type:
            continue
        record_day = _record_date(record, entity_type)
        if start is not None or end is not None:
            if record_day is None:
                continue
            if start is not None and record_day < start:
                continue
            if end is not None and record_day > end:
                continue
        if any(not _filter_matches(record, filter_item) for filter_item in filters):
            continue
        if any(_filter_matches(record, filter_item) for filter_item in exclusions):
            continue
        selected.append(record)
    return sorted(
        selected,
        key=lambda item: (
            str(item.get("source_id", "")),
            str(item.get("record_id", "")),
            _integer_value(item.get("revision", 0)),
        ),
    )


def _group_records(
    records: list[dict[str, object]],
    dimensions: list[str],
    *,
    config: dict[str, object],
    period_boundary: str,
    group_by_currency: bool = False,
) -> dict[tuple[object, ...], list[dict[str, object]]]:
    active_dimensions = list(dimensions)
    if group_by_currency and "currency_code" not in active_dimensions:
        active_dimensions.append("currency_code")
    groups: dict[tuple[object, ...], list[dict[str, object]]] = defaultdict(list)
    for record in records:
        key = tuple(
            _dimension_value(
                dimension,
                record,
                config=config,
                period_boundary=period_boundary,
            )
            for dimension in active_dimensions
        )
        groups[key].append(record)
    if not groups:
        groups[()] = []
    return groups


def _result_group_dimensions(
    key: tuple[object, ...], dimensions: list[str], *, group_by_currency: bool = False
) -> dict[str, object]:
    active_dimensions = list(dimensions)
    if group_by_currency and "currency_code" not in active_dimensions:
        active_dimensions.append("currency_code")
    return {dimension: key[index] for index, dimension in enumerate(active_dimensions)}


def _source_currency(group: list[dict[str, object]]) -> tuple[str | None, str | None]:
    currencies: set[str] = set()
    for record in group:
        attributes = _mapping(record.get("attributes"))
        value = attributes.get("currency_code")
        if (
            not isinstance(value, str)
            or len(value) != 3
            or not value.isascii()
            or not value.isupper()
        ):
            return None, "source_currency_missing_or_invalid"
        currencies.add(value)
    if len(currencies) > 1:
        return None, "multiple_source_currencies"
    return (next(iter(currencies)) if currencies else None), None


def _lineage_refs(input_snapshot: dict[str, object]) -> list[dict[str, object]]:
    inputs = _mapping(input_snapshot.get("calculation_inputs"))
    return [_mapping(item) for item in _list(inputs.get("evidence", []))]


def _common_result(
    definition: dict[str, object],
    input_snapshot: dict[str, object],
    *,
    status: str,
    groups: list[dict[str, object]],
    reasons: list[str],
    limitations: list[str] | None = None,
) -> dict[str, object]:
    calculation = _mapping(definition.get("calculation"))
    calculation_inputs = _mapping(input_snapshot.get("calculation_inputs", {}))
    lineage_refs = _lineage_refs(input_snapshot)
    semantic_relationships = _list(calculation_inputs.get("semantic_relationships", []))
    external_evidence = _list(calculation_inputs.get("external_evidence", []))
    calculation_key = str(definition.get("calculation_key", calculation.get("kind", "")))
    capability = CAPABILITY_BY_KEY.get(calculation_key, {})
    capability_limitations = _string_list(capability.get("limitations", []))
    supplied_limitations = (
        list(limitations)
        if limitations is not None
        else _string_list(definition.get("limitations", []))
    )
    effective_limitations = list(dict.fromkeys([*capability_limitations, *supplied_limitations]))
    return {
        "schema_version": BI_RESULT_SCHEMA_VERSION,
        "metric_key": definition.get("metric_key"),
        "metric_version": definition.get("version"),
        "definition_hash": definition.get("definition_hash"),
        "calculation_version": BI_CALCULATION_VERSION,
        "calculation_key": calculation_key,
        "authority_boundary": METRIC_AUTHORITY_BOUNDARY,
        "calculation_authority": metric_authority(calculation_key),
        "status": status,
        "unit": definition.get("unit"),
        "currency_code": definition.get("currency_code"),
        "period": input_snapshot.get("period"),
        "groups": groups,
        "reasons": sorted(set(reasons)),
        "limitations": effective_limitations,
        "evidence_count": len(lineage_refs) + len(semantic_relationships) + len(external_evidence),
        "evidence_hash": content_hash(
            {
                "universal_records": lineage_refs,
                "semantic_relationships": semantic_relationships,
                "external_evidence": external_evidence,
            }
        ),
        "source_selection": input_snapshot.get("source_selection"),
        "external_evidence": external_evidence,
        "sensitivity_scope": input_snapshot.get("sensitivity_scope", "company_context"),
    }


def _calculate_universal(
    definition: dict[str, object],
    config: dict[str, object],
    input_snapshot: dict[str, object],
) -> dict[str, object]:
    calculation = _mapping(definition.get("calculation"))
    calc_kind = _text(calculation.get("kind"), name="calculation kind")
    inputs = _mapping(input_snapshot.get("calculation_inputs"))
    blockers = [str(item) for item in _list(inputs.get("blockers", []))]
    warnings = [str(item) for item in _list(inputs.get("warnings", []))]
    if blockers:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": sorted(set(blockers)),
                }
            ],
            reasons=blockers,
            limitations=_string_list(definition.get("limitations", [])),
        )

    records = _filtered_records(definition, input_snapshot)
    requested_dimensions = [str(item) for item in _list(input_snapshot.get("dimensions", []))]
    policy = _mapping(definition.get("policy", {}))
    currency_behavior = str(policy.get("currency_behavior", "not_applicable"))
    all_currencies: set[str] = set()
    if calc_kind == "universal_source_amount_sum":
        for record in records:
            attributes = _mapping(record.get("attributes"))
            currency = attributes.get("currency_code")
            if isinstance(currency, str):
                all_currencies.add(currency)
        if currency_behavior == "single_currency_only" and len(all_currencies) > 1:
            return _common_result(
                definition,
                input_snapshot,
                status="abstained",
                groups=[
                    {
                        "dimensions": {},
                        "value": None,
                        "record_count": len(records),
                        "reasons": ["multiple_source_currencies"],
                    }
                ],
                reasons=["multiple_source_currencies"],
            )
        if currency_behavior == "group_by_currency" and not all_currencies:
            return _common_result(
                definition,
                input_snapshot,
                status="abstained",
                groups=[
                    {
                        "dimensions": {},
                        "value": None,
                        "record_count": len(records),
                        "reasons": ["source_currency_missing_or_invalid"],
                    }
                ],
                reasons=["source_currency_missing_or_invalid"],
            )

    if not records:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["no_matching_source_records"],
                }
            ],
            reasons=["no_matching_source_records"],
            limitations=_string_list(definition.get("limitations", [])),
        )

    period_boundary = str(definition.get("period_boundary", "calendar_period"))
    include_currency = (
        calc_kind == "universal_source_amount_sum" and currency_behavior == "group_by_currency"
    )
    groups = _group_records(
        records,
        requested_dimensions,
        config=config,
        period_boundary=period_boundary,
        group_by_currency=include_currency,
    )
    result_groups: list[dict[str, object]] = []
    result_reasons = list(warnings)
    all_values_available = True
    result_status = "complete"
    for key, group in sorted(groups.items(), key=lambda item: str(item[0])):
        dimensions = _result_group_dimensions(
            key, requested_dimensions, group_by_currency=include_currency
        )
        value: str | None
        group_currency_code: str | None = None
        group_reasons: list[str] = []
        if not group:
            value = None
            group_reasons.append("no_matching_source_records")
        elif calc_kind == "universal_record_count":
            value = str(len(group))
        elif calc_kind == "universal_distinct_source_values":
            field = _text(calculation.get("field"), name="distinct field")
            values: set[str] = set()
            missing = False
            for record in group:
                attributes = _mapping(record.get("attributes"))
                raw_value = attributes.get(field)
                if raw_value is None or raw_value == "":
                    missing = True
                elif isinstance(raw_value, str):
                    values.add(raw_value)
                else:
                    group_reasons.append("distinct_source_value_invalid_type")
            if not values or group_reasons:
                value = None
                all_values_available = False
                group_reasons.append("distinct_source_values_unavailable")
            else:
                value = str(len(values))
                if missing:
                    result_status = "partial"
                    group_reasons.append("some_source_values_missing")
        elif calc_kind == "universal_source_amount_sum":
            amounts: list[Decimal] = []
            amount_missing = False
            for record in group:
                attributes = _mapping(record.get("attributes"))
                raw_amount = attributes.get("amount")
                try:
                    amounts.append(parse_decimal_text(raw_amount))
                except ValueError:
                    amount_missing = True
                    break
            currency_code, currency_reason = _source_currency(group)
            group_currency_code = currency_code
            expected_currency = definition.get("currency_code")
            if expected_currency is not None and currency_code != expected_currency:
                currency_reason = "configured_currency_does_not_match_source"
            if amount_missing:
                group_reasons.append("source_amount_missing_or_invalid")
            if currency_reason:
                group_reasons.append(currency_reason)
            if group_reasons:
                value = None
                all_values_available = False
            else:
                try:
                    amount_sum = exact_decimal_sum(amounts)
                    value = _decimal_result(amount_sum, definition)
                except ValueError:
                    value = None
                    all_values_available = False
                    group_reasons.append("decimal_precision_limit_exceeded")
        else:
            raise ValueError("universal calculation key is unsupported")
        if group_reasons:
            result_reasons.extend(group_reasons)
            if value is None:
                result_status = "abstained"
        result_currency: object = definition.get("currency_code")
        if calc_kind == "universal_source_amount_sum":
            result_currency = group_currency_code
        result_groups.append(
            {
                "dimensions": dimensions,
                "value": value,
                "unit": definition.get("unit"),
                "currency_code": result_currency,
                "record_count": len(group),
                "reasons": sorted(set(group_reasons)),
            }
        )

    if not all_values_available:
        result_status = "abstained"
    if result_status == "complete" and warnings:
        result_status = "partial"
    return _common_result(
        definition,
        input_snapshot,
        status=result_status,
        groups=result_groups,
        reasons=result_reasons,
    )


def _external_run_inputs(input_snapshot: dict[str, object], key: str) -> dict[str, object] | None:
    calculation_inputs = _mapping(input_snapshot.get("calculation_inputs"))
    value = calculation_inputs.get(key)
    return _mapping(value) if isinstance(value, dict) else None


def _calculate_m5(
    definition: dict[str, object],
    input_snapshot: dict[str, object],
) -> dict[str, object]:
    calculation = _mapping(definition.get("calculation"))
    metric_id = _text(calculation.get("metric_id"), name="M5 metric id")
    m5_run = _external_run_inputs(input_snapshot, "m5_run")
    if m5_run is None:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["m5_saved_run_required"],
                }
            ],
            reasons=["m5_saved_run_required"],
        )
    result_snapshot = _mapping(m5_run.get("result_snapshot"))
    weekly = [_mapping(item) for item in _list(result_snapshot.get("weekly", []))]
    period = _mapping(input_snapshot.get("period", {}))
    start = period.get("start")
    end = period.get("end")
    selected: list[dict[str, object]] = []
    for item in weekly:
        week_start = str(item.get("week_start_date"))
        week_end = str(item.get("week_end_date"))
        if start is not None and week_start < str(start):
            continue
        if end is not None and week_end > str(end):
            continue
        selected.append(item)
    if not selected:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["m5_run_period_does_not_cover_requested_period"],
                }
            ],
            reasons=["m5_run_period_does_not_cover_requested_period"],
        )

    value_path: tuple[str, ...] | None = {
        "available_capacity_hours": ("capacity", "complete_available_hours"),
        "recorded_committed_hours": ("assignments", "active_employee_committed_hours_per_week"),
        "capacity_minus_recorded_commitments_hours": (
            "comparison",
            "capacity_minus_recorded_commitments_hours",
        ),
    }.get(metric_id)
    groups: list[dict[str, object]] = []
    reasons: list[str] = []
    if value_path is not None:
        for week in selected:
            current: object = week
            for segment in value_path:
                current = _mapping(current).get(segment)
            week_date = str(week.get("week_start_date"))
            group_reasons: list[str] = []
            if current is None:
                group_reasons.append("m5_metric_value_unavailable")
                reasons.extend(group_reasons)
            groups.append(
                {
                    "dimensions": {"week": week_date},
                    "value": str(current) if current is not None else None,
                    "unit": definition.get("unit"),
                    "record_count": 1,
                    "reasons": group_reasons,
                }
            )
    elif metric_id == "recorded_assignment_overload":
        for week in selected:
            comparison = _mapping(week.get("comparison"))
            groups.append(
                {
                    "dimensions": {"week": str(week.get("week_start_date"))},
                    "value": None,
                    "confirmed_overload_count": comparison.get("confirmed_overload_count"),
                    "estimated_overload_count": comparison.get("estimated_overload_count"),
                    "record_count": 1,
                    "reasons": _string_list(comparison.get("reasons", [])),
                }
            )
        reasons.append("m5_overload_is_structured_observation_not_scalar")
    elif metric_id == "recorded_skill_inventory_coverage":
        skill_inventory = _mapping(result_snapshot.get("skill_inventory", {}))
        groups.append(
            {
                "dimensions": {},
                "value": None,
                "structured_value": skill_inventory,
                "record_count": _integer_value(
                    skill_inventory.get("active_employee_skill_record_count", 0)
                ),
                "reasons": _string_list(skill_inventory.get("reasons", [])),
            }
        )
        reasons.extend(str(item) for item in _list(skill_inventory.get("reasons", [])))
        reasons.append("m5_skill_inventory_is_structured_observation_not_scalar")
    else:
        raise ValueError("M5 metric is unsupported")

    value_available = all(group.get("value") is not None for group in groups)
    run_status = str(m5_run.get("reliability_status", "partial"))
    status = "complete" if value_available and run_status == "complete" else "partial"
    if not value_available and metric_id in {
        "available_capacity_hours",
        "recorded_committed_hours",
        "capacity_minus_recorded_commitments_hours",
    }:
        status = "partial"
    if metric_id in {"recorded_assignment_overload", "recorded_skill_inventory_coverage"}:
        status = "complete" if not reasons and run_status == "complete" else "partial"
    return _common_result(
        definition,
        input_snapshot,
        status=status,
        groups=groups,
        reasons=reasons,
        limitations=[
            *_string_list(definition.get("limitations", [])),
            "This analysis references the exact saved M5 run; M5 remains observational "
            "and implementation-draft.",
        ],
    )


def _calculate_core_financial(
    definition: dict[str, object],
    input_snapshot: dict[str, object],
) -> dict[str, object]:
    calculation = _mapping(definition.get("calculation"))
    measure = _text(calculation.get("measure"), name="Core Financials measure")
    financial_run = _external_run_inputs(input_snapshot, "core_financial_run")
    if financial_run is None:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["core_financials_saved_run_required"],
                }
            ],
            reasons=["core_financials_saved_run_required"],
        )
    result_snapshot = _mapping(financial_run.get("result_snapshot"))
    metrics = [_mapping(item) for item in _list(result_snapshot.get("metrics", []))]
    metric_result = next((item for item in metrics if item.get("metric") == measure), None)
    if metric_result is None:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["core_financial_measure_missing"],
                }
            ],
            reasons=["core_financial_measure_missing"],
        )
    exact_value = metric_result.get("exact_value")
    metric_status = metric_result.get("status")
    reason_codes = metric_result.get("reason_codes", [])
    if not isinstance(reason_codes, list):
        reason_codes = []
    value_text: str | None = None
    if isinstance(exact_value, str):
        try:
            value_text = decimal_text(parse_decimal_text(exact_value))
        except ValueError:
            value_text = None
    available = metric_status == "calculated" and value_text is not None
    group = {
        "dimensions": {},
        "value": value_text if available else None,
        "unit": definition.get("unit"),
        "currency_code": financial_run.get("currency_code"),
        "record_count": metric_result.get("included_record_count", 0),
        "metric_status": metric_status,
        "reasons": list(reason_codes),
    }
    reasons = [str(item) for item in reason_codes]
    status = (
        "complete" if available else "partial" if metric_status == "incomplete" else "abstained"
    )
    return _common_result(
        definition,
        input_snapshot,
        status=status,
        groups=[group],
        reasons=reasons,
        limitations=[
            *_string_list(definition.get("limitations", [])),
            "Core Financials remains illustrative and not authoritative; use its own run, "
            "policy, and evidence as the source of truth.",
        ],
    )


def _group_key(value: object) -> str:
    from businessflow.metrics.snapshot import canonical_json_bytes

    return canonical_json_bytes(value).decode("utf-8")


def _unit_is_dimensionless(signature: tuple[str | None, str | None]) -> bool:
    unit, currency_code = signature
    return currency_code is None and (unit is None or unit.casefold() in DIMENSIONLESS_UNITS)


def _formula_unit_signature(
    expression: FormulaExpression,
    metric_signatures: dict[str, tuple[str | None, str | None]],
) -> tuple[str | None, str | None]:
    if expression.op == "constant":
        return None, None
    if expression.op == "metric_ref":
        if expression.metric_key is None or expression.metric_key not in metric_signatures:
            raise ValueError("formula dependency unit is unavailable")
        return metric_signatures[expression.metric_key]
    if expression.op == "negate":
        return _formula_unit_signature(expression.operands[0], metric_signatures)

    left = _formula_unit_signature(expression.operands[0], metric_signatures)
    right = _formula_unit_signature(expression.operands[1], metric_signatures)
    if expression.op in {"add", "subtract"}:
        if left != right:
            raise ValueError("formula unit mismatch in additive operation")
        return left
    if expression.op == "multiply":
        left_dimensionless = _unit_is_dimensionless(left)
        right_dimensionless = _unit_is_dimensionless(right)
        if left_dimensionless and right_dimensionless:
            return None, None
        if left_dimensionless:
            return right
        if right_dimensionless:
            return left
        raise ValueError("formula multiplication of two unit-bearing values is unsupported")
    if expression.op == "divide":
        if _unit_is_dimensionless(right):
            return (None, None) if _unit_is_dimensionless(left) else left
        if left == right:
            return None, None
        raise ValueError("formula division has incompatible units or currencies")
    raise ValueError("formula unit operator is unsupported")


def _calculate_formula(
    definition: dict[str, object],
    input_snapshot: dict[str, object],
) -> dict[str, object]:
    formulas: list[FormulaExpression] = []
    raw_formula = definition.get("formula")
    raw_numerator = definition.get("numerator")
    raw_denominator = definition.get("denominator")
    if isinstance(raw_formula, dict):
        formulas.append(FormulaExpression.model_validate(raw_formula))
    elif isinstance(raw_numerator, dict) and isinstance(raw_denominator, dict):
        formulas.extend(
            [
                FormulaExpression.model_validate(raw_numerator),
                FormulaExpression.model_validate(raw_denominator),
            ]
        )
    else:
        raise ValueError("saved custom formula definition is invalid")
    dependencies = [_mapping(item) for item in _list(input_snapshot.get("dependency_analyses", []))]
    dependency_results: dict[str, list[dict[str, object]]] = {}
    for dependency in dependencies:
        key = _text(dependency.get("metric_key"), name="dependency metric key")
        result_snapshot = _mapping(dependency.get("result_snapshot"))
        groups = [_mapping(item) for item in _list(result_snapshot.get("groups", []))]
        dependency_results[key] = groups
    expected_keys = set().union(*(set(_formula_references(formula)) for formula in formulas))
    if expected_keys != set(dependency_results):
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["formula_dependency_analyses_mismatch"],
                }
            ],
            reasons=["formula_dependency_analyses_mismatch"],
        )
    group_sets: dict[str, dict[str, dict[str, object]]] = {}
    for key, items in dependency_results.items():
        group_sets[key] = {_group_key(item.get("dimensions", {})): item for item in items}
    first_key = sorted(expected_keys)[0] if expected_keys else None
    if first_key is None:
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["formula_requires_metric_dependencies"],
                }
            ],
            reasons=["formula_requires_metric_dependencies"],
        )
    target_groups = group_sets[first_key]
    if any(set(group_sets[key]) != set(target_groups) for key in expected_keys):
        return _common_result(
            definition,
            input_snapshot,
            status="abstained",
            groups=[
                {
                    "dimensions": {},
                    "value": None,
                    "record_count": 0,
                    "reasons": ["formula_dependency_group_mismatch"],
                }
            ],
            reasons=["formula_dependency_group_mismatch"],
        )
    output_groups: list[dict[str, object]] = []
    reasons: list[str] = []
    all_complete = True
    dependency_statuses: list[str] = []
    for dependency in dependencies:
        result_snapshot = _mapping(dependency.get("result_snapshot"))
        dependency_statuses.append(str(result_snapshot.get("status", "partial")))
    is_ratio = raw_numerator is not None and raw_denominator is not None
    for group_key in sorted(target_groups):
        exemplar = target_groups[group_key]
        dimensions = _mapping(exemplar.get("dimensions", {}))
        metric_values: dict[str, Decimal] = {}
        metric_signatures: dict[str, tuple[str | None, str | None]] = {}
        missing_dependency = False
        for dependency_key in sorted(expected_keys):
            group = group_sets[dependency_key][group_key]
            raw_value = group.get("value")
            if not isinstance(raw_value, str):
                missing_dependency = True
                break
            try:
                metric_values[dependency_key] = parse_decimal_text(raw_value)
            except ValueError:
                missing_dependency = True
                break
            raw_unit = group.get("unit")
            raw_currency = group.get("currency_code")
            if not isinstance(raw_unit, str) or not raw_unit.strip():
                missing_dependency = True
                break
            if raw_currency is not None and not isinstance(raw_currency, str):
                missing_dependency = True
                break
            metric_signatures[dependency_key] = (raw_unit, raw_currency)
        group_reasons: list[str] = []
        value_text: str | None = None
        output_currency_code = (
            definition.get("currency_code")
            if isinstance(definition.get("currency_code"), str)
            else None
        )
        if missing_dependency:
            all_complete = False
            group_reasons.append("formula_dependency_value_or_unit_unavailable")
        else:
            try:
                if is_ratio:
                    numerator_signature = _formula_unit_signature(formulas[0], metric_signatures)
                    denominator_signature = _formula_unit_signature(formulas[1], metric_signatures)
                    if denominator_signature == (None, None):
                        result_signature = numerator_signature
                    elif numerator_signature == denominator_signature:
                        result_signature = (None, None)
                    else:
                        raise ValueError("formula ratio has incompatible units or currencies")
                    denominator = evaluate_formula(formulas[1], metric_values)
                    if denominator == 0:
                        raise ValueError("formula denominator is zero")
                    value = evaluate_formula(formulas[0], metric_values) / denominator
                else:
                    result_signature = _formula_unit_signature(formulas[0], metric_signatures)
                    value = evaluate_formula(formulas[0], metric_values)

                result_unit, result_currency = result_signature
                declared_unit = definition.get("unit")
                declared_currency = definition.get("currency_code")
                if _unit_is_dimensionless(result_signature):
                    if (
                        not isinstance(declared_unit, str)
                        or declared_unit.casefold() not in DIMENSIONLESS_UNITS
                    ):
                        raise ValueError("formula output unit must be dimensionless")
                    if declared_currency is not None:
                        raise ValueError("dimensionless formula output cannot declare a currency")
                    output_currency_code = None
                else:
                    if declared_unit != result_unit:
                        raise ValueError("formula output unit does not match its dependencies")
                    if declared_currency is not None and declared_currency != result_currency:
                        raise ValueError("formula output currency does not match its dependencies")
                    output_currency_code = (
                        declared_currency if isinstance(declared_currency, str) else result_currency
                    )
                value_text = _decimal_result(value, definition)
            except ValueError as exc:
                all_complete = False
                message = str(exc)
                if "zero" in message:
                    reason = "formula_division_by_zero"
                elif "currency" in message:
                    reason = "formula_currency_mismatch"
                elif "unit" in message or "units" in message:
                    reason = "formula_unit_mismatch"
                else:
                    reason = "formula_arithmetic_undefined"
                group_reasons.append(reason)
        if any(status != "complete" for status in dependency_statuses):
            all_complete = False
            group_reasons.append("formula_dependency_analysis_not_complete")
        reasons.extend(group_reasons)
        output_groups.append(
            {
                "dimensions": dimensions,
                "value": value_text,
                "unit": definition.get("unit"),
                "currency_code": output_currency_code,
                "dependency_count": len(expected_keys),
                "reasons": sorted(set(group_reasons)),
            }
        )
    status = "complete" if all_complete else "abstained"
    if not output_groups:
        status = "abstained"
        reasons.append("formula_dependency_analyses_empty")
        output_groups.append(
            {"dimensions": {}, "value": None, "record_count": 0, "reasons": reasons}
        )
    return _common_result(
        definition,
        input_snapshot,
        status=status,
        groups=output_groups,
        reasons=reasons,
        limitations=[
            *_string_list(definition.get("limitations", [])),
            "Formula inputs are exact saved analyses pinned to their original definition "
            "and Company configuration versions.",
        ],
    )


def _formula_references(expression: FormulaExpression) -> set[str]:
    result = (
        {expression.metric_key}
        if expression.op == "metric_ref" and expression.metric_key
        else set()
    )
    for operand in expression.operands:
        result.update(_formula_references(operand))
    return result


def calculate_saved_analysis(
    definition: dict[str, object],
    configuration: dict[str, object],
    input_snapshot: dict[str, object],
) -> dict[str, object]:
    """Calculate only from the exact, already-snapshotted evidence recorded with an analysis."""
    calculation = _mapping(definition.get("calculation"))
    calculation_key = definition.get("calculation_key", calculation.get("kind"))
    if calculation_key in {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    }:
        return _calculate_universal(definition, configuration, input_snapshot)
    if calculation_key == "m5_metric":
        return _calculate_m5(definition, input_snapshot)
    if calculation_key == "core_financial_measure":
        return _calculate_core_financial(definition, input_snapshot)
    if calculation_key == "custom_formula":
        return _calculate_formula(definition, input_snapshot)
    raise ValueError("saved metric calculation is unsupported")
