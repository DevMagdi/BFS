from __future__ import annotations

from decimal import Decimal
from typing import Literal, cast

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    parse_output_decimal_text,
)
from businessflow.core_financials.contracts import (
    FINANCIAL_CATEGORIES,
    CategoryCoverage,
    CoreFinancialOverviewCheck,
    CoreFinancialOverviewMeasure,
    CoreFinancialOverviewRunReference,
    FinancialCategory,
    FinancialMeasure,
    FormulaTerm,
    ProfitAndLossMeasure,
)

_CHECK_LIMITATIONS = (
    "These are deterministic consistency checks against saved illustrative formulas, not "
    "accounting validation, an independent audit, or approval of the source data.",
    "Source identity and period completeness are caller-asserted and are not independently "
    "verified.",
)
_CHECK_ASSUMPTIONS = (
    "A numeric comparison is made only for runs with the same saved source import, reporting "
    "period, currency, basis, policy version, and calculation-engine version.",
    "No foreign-exchange conversion, missing-value imputation, rounding tolerance, or "
    "authoritative derived measure is applied.",
)

_CHECKS: tuple[
    tuple[
        Literal[
            "gross_profit_bridge",
            "revenue_expenses_gross_profit",
            "operating_profit_bridge",
            "net_profit_bridge",
        ],
        str,
        str,
        tuple[FinancialMeasure, ...],
        Literal["bridge", "expenses_identity"],
    ],
    ...,
] = (
    (
        "gross_profit_bridge",
        "Gross Profit policy bridge",
        "Gross Profit = Revenue plus the saved policy's category adjustments",
        ("revenue", "gross_profit"),
        "bridge",
    ),
    (
        "revenue_expenses_gross_profit",
        "Revenue, Expenses, and Gross Profit",
        "Gross Profit = Revenue - Expenses",
        ("revenue", "expenses", "gross_profit"),
        "expenses_identity",
    ),
    (
        "operating_profit_bridge",
        "Operating Profit policy bridge",
        "Operating Profit = Gross Profit plus the saved policy's category adjustments",
        ("gross_profit", "operating_profit"),
        "bridge",
    ),
    (
        "net_profit_bridge",
        "Net Profit policy bridge",
        "Net Profit = Operating Profit plus the saved policy's category adjustments",
        ("operating_profit", "net_profit"),
        "bridge",
    ),
)


def _formula_vector(
    formulas: dict[ProfitAndLossMeasure, tuple[FormulaTerm, ...]], measure: ProfitAndLossMeasure
) -> dict[str, int] | None:
    terms = formulas.get(measure)
    if terms is None:
        return None
    return {term.category: term.coefficient for term in terms}


def _combine_vectors(*terms: tuple[dict[str, int], int]) -> dict[str, int]:
    combined: dict[str, int] = {}
    for vector, multiplier in terms:
        for category, coefficient in vector.items():
            combined[category] = combined.get(category, 0) + multiplier * coefficient
    return {category: coefficient for category, coefficient in combined.items() if coefficient}


def _ordered_terms(vector: dict[str, int]) -> dict[str, int]:
    category_order = {category: index for index, category in enumerate(FINANCIAL_CATEGORIES)}
    return dict(
        sorted(
            vector.items(),
            key=lambda item: (category_order.get(cast(FinancialCategory, item[0]), 999), item[0]),
        )
    )


def _formula_definitions(
    measures: tuple[CoreFinancialOverviewMeasure, ...],
) -> tuple[
    dict[ProfitAndLossMeasure, tuple[FormulaTerm, ...]] | None,
    dict[FinancialCategory, CategoryCoverage],
]:
    source = next((item for item in measures if item.policy_formulas), None)
    if source is None:
        return None, {}
    return source.policy_formulas, source.category_coverage


def _to_run_reference(
    measure: CoreFinancialOverviewMeasure,
) -> CoreFinancialOverviewRunReference | None:
    if (
        measure.run_id is None
        or measure.run_url is None
        or measure.evidence_url is None
        or measure.metric_status is None
        or measure.period_start is None
        or measure.period_end is None
        or measure.currency_code is None
        or measure.basis is None
        or measure.engine_version is None
        or measure.policy_id is None
        or measure.policy_version is None
        or measure.policy_hash is None
        or measure.policy_url is None
        or measure.reconciliation is None
        or measure.status == "missing"
    ):
        return None
    if measure.evidence_status == "not_available":
        return None
    return CoreFinancialOverviewRunReference(
        measure=measure.measure,
        run_id=measure.run_id,
        run_url=measure.run_url,
        evidence_url=measure.evidence_url,
        reported_value=measure.reported_value,
        exact_value=measure.exact_value,
        status=measure.status,
        metric_status=measure.metric_status,
        period_start=measure.period_start,
        period_end=measure.period_end,
        currency_code=measure.currency_code,
        basis=measure.basis,
        engine_version=measure.engine_version,
        policy_id=measure.policy_id,
        policy_version=measure.policy_version,
        policy_hash=measure.policy_hash,
        policy_url=measure.policy_url,
        source_imports=measure.source_imports,
        evidence_status=measure.evidence_status,
        evidence_count=measure.evidence_count,
        reconciliation=measure.reconciliation,
        limitations=measure.limitations,
        assumptions=measure.assumptions,
    )


def _policy_signatures(
    measures: tuple[CoreFinancialOverviewMeasure, ...],
) -> set[tuple[object, ...]]:
    return {
        (measure.policy_id, measure.policy_version, measure.policy_hash)
        for measure in measures
        if measure.run_id is not None
    }


def _coverage_reasons(
    categories: set[str], coverage: dict[FinancialCategory, CategoryCoverage]
) -> tuple[str, ...]:
    reasons: list[str] = []
    for category in _ordered_terms({item: 1 for item in categories}):
        category_key = cast(FinancialCategory, category)
        if category_key not in coverage:
            reasons.append(f"policy_category_coverage_missing:{category}")
        elif coverage[category_key] == "unknown":
            reasons.append(f"policy_category_coverage_unknown:{category}")
    return tuple(reasons)


def _context_mismatch_reasons(
    measures: tuple[CoreFinancialOverviewMeasure, ...],
) -> tuple[str, ...]:
    reasons: list[str] = []
    if len({(item.period_start, item.period_end) for item in measures}) > 1:
        reasons.append("reporting_period_mismatch")
    if len({item.currency_code for item in measures}) > 1:
        reasons.append("currency_mismatch_no_fx")
    if len({item.basis for item in measures}) > 1:
        reasons.append("reporting_basis_mismatch")
    if len({item.engine_version for item in measures}) > 1:
        reasons.append("calculation_engine_version_mismatch")
    if len(_policy_signatures(measures)) > 1:
        reasons.append("policy_version_mismatch")
    source_ids: list[str] = []
    source_types: list[str] = []
    for item in measures:
        if len(item.source_imports) != 1:
            reasons.append("source_import_reference_missing_or_ambiguous")
            continue
        source_ids.append(str(item.source_imports[0].id))
        source_types.append(item.source_imports[0].source_type)
    if len(set(source_ids)) > 1:
        reasons.append("source_import_mismatch")
    if source_types and any(source_type != "general_ledger" for source_type in source_types):
        reasons.append("p_and_l_source_type_mismatch")
    return tuple(dict.fromkeys(reasons))


def _policy_difference(
    formulas: dict[ProfitAndLossMeasure, tuple[FormulaTerm, ...]],
    key: str,
    operation: Literal["bridge", "expenses_identity"],
) -> tuple[dict[str, int] | None, dict[str, int] | None]:
    if operation == "bridge":
        base, target = {
            "gross_profit_bridge": ("revenue", "gross_profit"),
            "operating_profit_bridge": ("gross_profit", "operating_profit"),
            "net_profit_bridge": ("operating_profit", "net_profit"),
        }[key]
        base_vector = _formula_vector(formulas, cast(ProfitAndLossMeasure, base))
        target_vector = _formula_vector(formulas, cast(ProfitAndLossMeasure, target))
        if base_vector is None or target_vector is None:
            return None, None
        delta = _combine_vectors((target_vector, 1), (base_vector, -1))
        return delta, {}

    revenue = _formula_vector(formulas, "revenue")
    expenses = _formula_vector(formulas, "expenses")
    gross_profit = _formula_vector(formulas, "gross_profit")
    if revenue is None or expenses is None or gross_profit is None:
        return None, None
    # An empty residual means the saved policy supports Revenue less Expenses equaling Gross Profit.
    residual = _combine_vectors((revenue, 1), (expenses, -1), (gross_profit, -1))
    return {}, residual


def _equation(
    key: str,
    formula_terms: dict[str, int],
    operation: Literal["bridge", "expenses_identity"],
) -> str:
    if operation == "expenses_identity":
        return "Gross Profit = Revenue - Expenses"
    base, target = {
        "gross_profit_bridge": ("Revenue", "Gross Profit"),
        "operating_profit_bridge": ("Gross Profit", "Operating Profit"),
        "net_profit_bridge": ("Operating Profit", "Net Profit"),
    }[key]
    pieces = [target, " = ", base]
    ordered = _ordered_terms(formula_terms)
    for category, coefficient in ordered.items():
        magnitude = abs(coefficient)
        label = category.replace("_", " ")
        term = f"{magnitude} x {label}" if magnitude != 1 else label
        pieces.append(" + " if coefficient > 0 else " - ")
        pieces.append(term)
    return "".join(pieces)


def _value(measure: CoreFinancialOverviewMeasure) -> Decimal | None:
    if measure.exact_value is None:
        return None
    try:
        return parse_output_decimal_text(measure.exact_value)
    except ValueError:
        return None


def _signed(value: Decimal, coefficient: int) -> Decimal:
    if coefficient < 0:
        value = value.copy_negate()
        coefficient = -coefficient
    return exact_decimal_sum([value] * coefficient)


def _bridge_variance(
    base: CoreFinancialOverviewMeasure,
    target: CoreFinancialOverviewMeasure,
    formula_terms: dict[str, int],
) -> tuple[Decimal | None, bool]:
    base_value = _value(base)
    target_value = _value(target)
    if base_value is None or target_value is None:
        return None, False

    adjustment_values: dict[str, Decimal] = {}
    for category in formula_terms:
        candidates = [
            item.category_totals[category]
            for item in (base, target)
            if category in item.category_totals
        ]
        if not candidates:
            return None, False
        try:
            parsed = [parse_output_decimal_text(candidate) for candidate in candidates]
        except ValueError:
            return None, False
        if any(value != parsed[0] for value in parsed[1:]):
            return None, True
        adjustment_values[category] = parsed[0]

    adjustments = exact_decimal_sum(
        _signed(adjustment_values[category], coefficient)
        for category, coefficient in formula_terms.items()
    )
    return exact_decimal_sum([base_value, adjustments, target_value.copy_negate()]), False


def _expense_identity_variance(
    by_measure: dict[FinancialMeasure, CoreFinancialOverviewMeasure],
) -> Decimal | None:
    revenue = _value(by_measure["revenue"])
    expenses = _value(by_measure["expenses"])
    gross_profit = _value(by_measure["gross_profit"])
    if revenue is None or expenses is None or gross_profit is None:
        return None
    return exact_decimal_sum([revenue, expenses.copy_negate(), gross_profit.copy_negate()])


def _context_fields(
    measures: tuple[CoreFinancialOverviewMeasure, ...],
) -> dict[str, object]:
    if not measures or any(
        item.period_start is None
        or item.period_end is None
        or item.currency_code is None
        or item.basis is None
        for item in measures
    ):
        return {}
    if _context_mismatch_reasons(measures):
        return {}
    first = measures[0]
    return {
        "period_start": first.period_start,
        "period_end": first.period_end,
        "currency_code": first.currency_code,
        "basis": first.basis,
    }


def build_overview_checks(
    measures: tuple[CoreFinancialOverviewMeasure, ...],
) -> tuple[CoreFinancialOverviewCheck, ...]:
    """Compare only persisted P&L results whose saved formulas and source scopes support it."""
    by_measure = {item.measure: item for item in measures}
    checks: list[CoreFinancialOverviewCheck] = []

    for key, label, fallback_equation, required, operation in _CHECKS:
        participants = tuple(by_measure[item] for item in required)
        run_references = tuple(
            reference for item in participants if (reference := _to_run_reference(item)) is not None
        )
        definitions, coverage = _formula_definitions(participants)
        reasons: list[str] = []
        policy_relation_supported: bool | None = None
        formula_terms: dict[str, int] = {}

        if not any(item.run_id is not None for item in participants):
            reasons.append("missing_policy_definition")
        elif len(_policy_signatures(participants)) > 1:
            reasons.append("policy_version_mismatch")
        elif definitions is None:
            reasons.append("missing_policy_formula_definition")
        else:
            formula_terms_result, residual = _policy_difference(definitions, key, operation)
            if formula_terms_result is None or residual is None:
                reasons.append("missing_policy_formula_definition")
            else:
                formula_terms = formula_terms_result if operation == "bridge" else residual
                formula_categories = {
                    str(term.category)
                    for measure_key in required
                    for term in definitions.get(cast(ProfitAndLossMeasure, measure_key), ())
                }
                coverage_reasons = _coverage_reasons(formula_categories, coverage)
                if coverage_reasons:
                    reasons.extend(coverage_reasons)
                    policy_relation_supported = False
                elif operation == "expenses_identity" and residual:
                    policy_relation_supported = False
                    reasons.append("policy_does_not_define_expenses_as_gross_profit_cost_base")
                else:
                    policy_relation_supported = True

        equation = (
            _equation(key, formula_terms, operation)
            if definitions is not None and policy_relation_supported is not None
            else fallback_equation
        )

        missing_runs = [item.measure for item in participants if item.run_id is None]
        if missing_runs:
            reasons.append("required_measure_run_missing")
        incomplete = [
            item for item in participants if item.run_id is not None and item.status != "available"
        ]
        for item in incomplete:
            reasons.append(
                "measure_incomplete" if item.status == "incomplete" else "measure_value_withheld"
            )
            reasons.extend(f"{item.measure}:{reason}" for reason in item.reason_codes)
        if any(_value(item) is None for item in participants if item.run_id is not None):
            reasons.append("exact_value_unavailable")

        context_reasons: tuple[str, ...] = ()
        if not missing_runs:
            context_reasons = _context_mismatch_reasons(participants)
            reasons.extend(context_reasons)

        if "policy_version_mismatch" in reasons or context_reasons:
            status: Literal["consistent", "inconsistent", "unverifiable", "non_comparable"] = (
                "non_comparable"
            )
            variance = None
        elif reasons:
            status = "unverifiable"
            variance = None
        else:
            if operation == "bridge":
                base_key, target_key = {
                    "gross_profit_bridge": ("revenue", "gross_profit"),
                    "operating_profit_bridge": ("gross_profit", "operating_profit"),
                    "net_profit_bridge": ("operating_profit", "net_profit"),
                }[key]
                difference, category_total_mismatch = _bridge_variance(
                    by_measure[cast(FinancialMeasure, base_key)],
                    by_measure[cast(FinancialMeasure, target_key)],
                    formula_terms,
                )
                if category_total_mismatch:
                    status = "inconsistent"
                    variance = None
                    reasons.append("category_total_mismatch_across_runs")
                elif difference is None:
                    status = "unverifiable"
                    variance = None
                    reasons.append("policy_category_total_unavailable")
                else:
                    variance = decimal_text(difference)
                    status = "consistent" if difference.is_zero() else "inconsistent"
            else:
                difference = _expense_identity_variance(by_measure)
                if difference is None:
                    status = "unverifiable"
                    variance = None
                    reasons.append("exact_value_unavailable")
                else:
                    variance = decimal_text(difference)
                    status = "consistent" if difference.is_zero() else "inconsistent"

        fields = _context_fields(participants) if status in ("consistent", "inconsistent") else {}
        assumptions = list(_CHECK_ASSUMPTIONS)
        if operation == "expenses_identity":
            assumptions.append(
                "Expenses is used as a gross-profit cost base only when the saved formula vectors "
                "prove that identity; Expenses is never assumed to mean COGS."
            )
        else:
            assumptions.append(
                "Category adjustments are read from the policy version saved with the runs; they "
                "are not separately reported or presented as authoritative measures."
            )
        checks.append(
            CoreFinancialOverviewCheck(
                key=key,
                label=label,
                equation=equation,
                status=status,
                policy_relation_supported=policy_relation_supported,
                policy_difference_terms=_ordered_terms(formula_terms),
                required_measures=required,
                run_references=run_references,
                period_start=fields.get("period_start"),
                period_end=fields.get("period_end"),
                currency_code=fields.get("currency_code"),
                basis=fields.get("basis"),
                variance=variance,
                reason_codes=tuple(dict.fromkeys(reasons)),
                limitations=_CHECK_LIMITATIONS,
                assumptions=tuple(assumptions),
            )
        )
    return tuple(checks)
