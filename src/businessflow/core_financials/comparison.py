from __future__ import annotations

from dataclasses import dataclass
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from typing import Literal, cast
from uuid import UUID

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    fixed_scale_text,
    parse_output_decimal_text,
    quantize_decimal,
)
from businessflow.core_financials.contracts import (
    FINANCIAL_CATEGORIES,
    FINANCIAL_MEASURES,
    CoreFinancialComparisonContributor,
    CoreFinancialComparisonFinding,
    CoreFinancialComparisonResponse,
    CoreFinancialComparisonRunReference,
    CoreFinancialPeriodSelection,
    CoreFinancialPolicyCreateRequest,
    FinancialCategory,
    FinancialMeasure,
    ProfitAndLossMeasure,
    period_alignment,
)

_PERCENT_SCALE = 6
_PROFITABILITY_MEASURES: frozenset[FinancialMeasure] = frozenset(
    {"gross_profit", "operating_profit", "net_profit"}
)
_CONTRIBUTOR_MEASURES: frozenset[FinancialMeasure] = frozenset(
    {"revenue", "expenses", "gross_profit", "operating_profit", "net_profit"}
)
_EXPECTED_SOURCE_TYPE = {
    "revenue": "general_ledger",
    "expenses": "general_ledger",
    "gross_profit": "general_ledger",
    "operating_profit": "general_ledger",
    "net_profit": "general_ledger",
    "cash_flow": "bank_activity",
}
_REASON_TEXT: dict[str, str] = {
    "period_length_mismatch": (
        "The selected windows do not have matching inclusive day counts or matching whole-"
        "calendar-month counts."
    ),
    "prior_run_unavailable": "No persisted run exists for this measure and prior period.",
    "current_run_unavailable": "No persisted run exists for this measure and current period.",
    "currency_mismatch_no_fx": (
        "The saved runs use different currencies; no FX conversion is configured."
    ),
    "reporting_basis_mismatch": "The saved runs use different reporting bases.",
    "policy_version_mismatch": "The saved runs use different immutable policy versions.",
    "calculation_engine_version_mismatch": (
        "The saved runs use different calculation-engine versions."
    ),
    "source_type_mismatch": (
        "The saved runs do not use the source adapter required for this measure."
    ),
    "prior_measure_incomplete": "The prior-period measure is incomplete or has no exact value.",
    "current_measure_incomplete": "The current-period measure is incomplete or has no exact value.",
    "prior_run_state_incomplete": "The prior-period persisted run is incomplete.",
    "current_run_state_incomplete": "The current-period persisted run is incomplete.",
    "prior_source_period_completeness_not_asserted": (
        "The prior source import does not assert complete period coverage."
    ),
    "current_source_period_completeness_not_asserted": (
        "The current source import does not assert complete period coverage."
    ),
    "prior_source_quality_incomplete": (
        "The prior source import contains rejected or duplicate rows."
    ),
    "current_source_quality_incomplete": (
        "The current source import contains rejected or duplicate rows."
    ),
    "prior_source_period_does_not_cover_run": (
        "The prior source import's declared period does not cover the saved run window."
    ),
    "current_source_period_does_not_cover_run": (
        "The current source import's declared period does not cover the saved run window."
    ),
    "zero_prior_value_percentage_unavailable": (
        "Percentage variance is undefined because the prior exact value is zero."
    ),
    "percentage_variance_exceeds_supported_output": (
        "Percentage variance exceeds the supported exact output range."
    ),
    "comparison_not_comparable": (
        "The measure comparison is abstained; percentage variance is unavailable."
    ),
    "profit_measure_not_comparable": "The profit comparison is abstained; margins are unavailable.",
    "prior_revenue_run_unavailable_for_margin": (
        "No compatible prior-period Revenue run is available to calculate the margin."
    ),
    "current_revenue_run_unavailable_for_margin": (
        "No compatible current-period Revenue run is available to calculate the margin."
    ),
    "prior_zero_revenue_margin_denominator": (
        "Prior-period margin is undefined because exact Revenue is zero."
    ),
    "current_zero_revenue_margin_denominator": (
        "Current-period margin is undefined because exact Revenue is zero."
    ),
    "prior_margin_context_mismatch": (
        "Prior-period Revenue and profit runs do not share a compatible saved context."
    ),
    "current_margin_context_mismatch": (
        "Current-period Revenue and profit runs do not share a compatible saved context."
    ),
    "margin_revenue_period_comparison_incompatible": (
        "The prior and current Revenue runs are not comparable for a margin calculation."
    ),
    "negative_revenue_prior_denominator": (
        "Prior-period margin uses negative Revenue as its denominator; interpret cautiously."
    ),
    "negative_revenue_current_denominator": (
        "Current-period margin uses negative Revenue as its denominator; interpret cautiously."
    ),
    "margin_exceeds_supported_output": "Margin exceeds the supported exact output range.",
    "category_level_evidence_unavailable": (
        "Category contributors are withheld because category-level source evidence is unavailable."
    ),
    "category_coverage_not_complete": (
        "Category contributors are withheld because policy coverage is not complete."
    ),
    "category_evidence_totals_mismatch": (
        "Category contributors are withheld because persisted line evidence does not reconcile "
        "to the saved category total."
    ),
    "category_totals_formula_mismatch": (
        "Category contributors are withheld because formula-signed category totals "
        "do not reconcile to the exact saved measure value."
    ),
    "cash_flow_category_contributors_not_supported": (
        "Cash Flow is net observed bank movement, not a formal cash flow statement; no "
        "category-level expense or profitability contributors are inferred."
    ),
}


@dataclass(frozen=True, slots=True)
class ComparisonRunData:
    reference: CoreFinancialComparisonRunReference
    policy: CoreFinancialPolicyCreateRequest
    category_contributions: dict[FinancialCategory, Decimal]
    category_evidence_counts: dict[FinancialCategory, int]


def _unique(values: list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _direction(
    prior_value: Decimal, current_value: Decimal
) -> Literal["increase", "decrease", "unchanged"]:
    if current_value > prior_value:
        return "increase"
    if current_value < prior_value:
        return "decrease"
    return "unchanged"


def _scaled_ratio(numerator: Decimal, denominator: Decimal) -> Decimal:
    """Return numerator / denominator * 100 at six places using isolated half-even rounding."""
    if not numerator.is_finite() or not denominator.is_finite() or denominator.is_zero():
        raise ValueError("a finite non-zero denominator is required")
    try:
        with localcontext(
            Context(
                prec=256,
                rounding=ROUND_HALF_EVEN,
                Emin=-999_999,
                Emax=999_999,
                traps=[InvalidOperation, DivisionByZero, Overflow],
            )
        ):
            percent = numerator.scaleb(2) / denominator
        return quantize_decimal(percent, scale=_PERCENT_SCALE, mode="half_even")
    except (InvalidOperation, DivisionByZero, Overflow, ValueError) as exc:
        raise ValueError("percentage result exceeds the supported precision") from exc


def _as_decimal(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return parse_output_decimal_text(value)
    except ValueError:
        return None


def _side_readiness_reasons(
    side: Literal["prior", "current"], run: ComparisonRunData | None
) -> list[str]:
    if run is None:
        return [f"{side}_run_unavailable"]
    reference = run.reference
    reasons: list[str] = []
    if reference.metric_status != "calculated" or reference.exact_value is None:
        reasons.append(f"{side}_measure_incomplete")
    if reference.run_state != "calculated":
        reasons.append(f"{side}_run_state_incomplete")
    source = reference.source_import
    if source.period_completeness != "complete":
        reasons.append(f"{side}_source_period_completeness_not_asserted")
    if source.quality_status != "passed":
        reasons.append(f"{side}_source_quality_incomplete")
    if source.period_start > reference.period_start or source.period_end < reference.period_end:
        reasons.append(f"{side}_source_period_does_not_cover_run")
    return reasons


def _pair_reasons(
    prior: ComparisonRunData | None,
    current: ComparisonRunData | None,
    alignment: str | None,
) -> list[str]:
    reasons: list[str] = []
    if alignment is None:
        reasons.append("period_length_mismatch")
    reasons.extend(_side_readiness_reasons("prior", prior))
    reasons.extend(_side_readiness_reasons("current", current))
    if prior is None or current is None:
        return list(_unique(reasons))

    prior_reference = prior.reference
    current_reference = current.reference
    if prior_reference.currency_code != current_reference.currency_code:
        reasons.append("currency_mismatch_no_fx")
    if prior_reference.basis != current_reference.basis:
        reasons.append("reporting_basis_mismatch")
    if (
        prior_reference.policy_id != current_reference.policy_id
        or prior_reference.policy_version != current_reference.policy_version
        or prior_reference.policy_hash != current_reference.policy_hash
    ):
        reasons.append("policy_version_mismatch")
    if prior_reference.engine_version != current_reference.engine_version:
        reasons.append("calculation_engine_version_mismatch")
    expected_source_type = _EXPECTED_SOURCE_TYPE[prior_reference.measure]
    if (
        prior_reference.source_import.source_type != expected_source_type
        or current_reference.source_import.source_type != expected_source_type
    ):
        reasons.append("source_type_mismatch")
    return list(_unique(reasons))


def _reason_text(code: str) -> str:
    if code in _REASON_TEXT:
        return _REASON_TEXT[code]
    if code.startswith("prior_metric_reason:"):
        return f"Prior-period saved run reason: {code.partition(':')[2].replace('_', ' ')}."
    if code.startswith("current_metric_reason:"):
        return f"Current-period saved run reason: {code.partition(':')[2].replace('_', ' ')}."
    if code.startswith("category_coverage_not_complete:"):
        return f"Policy coverage for {code.partition(':')[2].replace('_', ' ')} is not complete."
    if code.startswith("category_evidence_totals_mismatch:"):
        category = code.partition(":")[2].replace("_", " ")
        return f"Persisted line evidence does not reconcile for {category}."
    if code.startswith("category_level_evidence_missing:"):
        category = code.partition(":")[2].replace("_", " ")
        return f"No persisted category-level evidence is available for {category}."
    return code.replace("_", " ").capitalize() + "."


def _comparison_margin_reasons(
    measure_run: ComparisonRunData | None,
    revenue_run: ComparisonRunData | None,
    side: Literal["prior", "current"],
) -> list[str]:
    if measure_run is None or revenue_run is None:
        return [f"{side}_revenue_run_unavailable_for_margin"]
    reasons = _side_readiness_reasons(side, revenue_run)
    measure_reference = measure_run.reference
    revenue_reference = revenue_run.reference
    if (
        measure_reference.period_start != revenue_reference.period_start
        or measure_reference.period_end != revenue_reference.period_end
        or measure_reference.currency_code != revenue_reference.currency_code
        or measure_reference.basis != revenue_reference.basis
        or measure_reference.engine_version != revenue_reference.engine_version
        or measure_reference.policy_id != revenue_reference.policy_id
        or measure_reference.policy_version != revenue_reference.policy_version
        or measure_reference.policy_hash != revenue_reference.policy_hash
        or measure_reference.source_import.id != revenue_reference.source_import.id
    ):
        reasons.append(f"{side}_margin_context_mismatch")
    return reasons


def _margin_values(
    measure: FinancialMeasure,
    prior: ComparisonRunData | None,
    current: ComparisonRunData | None,
    revenue_prior: ComparisonRunData | None,
    revenue_current: ComparisonRunData | None,
    alignment: str | None,
    comparison_reasons: tuple[str, ...],
) -> dict[str, object]:
    if measure not in _PROFITABILITY_MEASURES:
        return {
            "margin_status": "not_applicable",
            "margin_reason_codes": (),
            "margin_caution_codes": (),
        }
    if comparison_reasons:
        return {
            "margin_status": "abstained",
            "margin_reason_codes": _unique(["profit_measure_not_comparable", *comparison_reasons]),
            "margin_caution_codes": (),
        }

    reasons = _comparison_margin_reasons(prior, revenue_prior, "prior")
    reasons.extend(_comparison_margin_reasons(current, revenue_current, "current"))
    revenue_pair_incompatible = (
        revenue_prior is None
        or revenue_current is None
        or _pair_reasons(revenue_prior, revenue_current, alignment)
    )
    if revenue_pair_incompatible:
        reasons.append("margin_revenue_period_comparison_incompatible")
    if reasons:
        return {
            "prior_margin": None,
            "current_margin": None,
            "margin_variance_percentage_points": None,
            "margin_status": "abstained",
            "margin_reason_codes": _unique(reasons),
            "margin_caution_codes": (),
        }

    prior_profit = _as_decimal(prior.reference.exact_value) if prior is not None else None
    current_profit = _as_decimal(current.reference.exact_value) if current is not None else None
    prior_revenue = (
        _as_decimal(revenue_prior.reference.exact_value) if revenue_prior is not None else None
    )
    current_revenue = (
        _as_decimal(revenue_current.reference.exact_value) if revenue_current is not None else None
    )
    prior_margin: Decimal | None = None
    current_margin: Decimal | None = None
    if prior_revenue is not None and prior_revenue.is_zero():
        reasons.append("prior_zero_revenue_margin_denominator")
    elif prior_profit is not None and prior_revenue is not None:
        try:
            prior_margin = _scaled_ratio(prior_profit, prior_revenue)
        except ValueError:
            reasons.append("margin_exceeds_supported_output")
    if current_revenue is not None and current_revenue.is_zero():
        reasons.append("current_zero_revenue_margin_denominator")
    elif current_profit is not None and current_revenue is not None:
        try:
            current_margin = _scaled_ratio(current_profit, current_revenue)
        except ValueError:
            reasons.append("margin_exceeds_supported_output")

    if prior_revenue is not None and prior_revenue < 0:
        prior_caution = "negative_revenue_prior_denominator"
    else:
        prior_caution = None
    if current_revenue is not None and current_revenue < 0:
        current_caution = "negative_revenue_current_denominator"
    else:
        current_caution = None
    caution_codes = _unique([code for code in (prior_caution, current_caution) if code is not None])

    variance_text: str | None = None
    if prior_margin is not None and current_margin is not None:
        try:
            variance = exact_decimal_sum([current_margin, prior_margin.copy_negate()])
            variance_text = fixed_scale_text(variance, scale=_PERCENT_SCALE)
        except ValueError:
            reasons.append("margin_exceeds_supported_output")

    return {
        "prior_margin": (
            fixed_scale_text(prior_margin, scale=_PERCENT_SCALE)
            if prior_margin is not None
            else None
        ),
        "current_margin": (
            fixed_scale_text(current_margin, scale=_PERCENT_SCALE)
            if current_margin is not None
            else None
        ),
        "margin_variance_percentage_points": variance_text,
        "margin_status": "abstained" if reasons else "calculated",
        "margin_reason_codes": _unique(reasons),
        "margin_caution_codes": caution_codes,
    }


def _contributor_results(
    measure: FinancialMeasure,
    prior: ComparisonRunData | None,
    current: ComparisonRunData | None,
    comparison_reasons: tuple[str, ...],
) -> tuple[
    Literal["not_applicable", "calculated", "abstained"],
    tuple[str, ...],
    list[CoreFinancialComparisonContributor],
]:
    if measure not in _CONTRIBUTOR_MEASURES:
        if measure == "cash_flow":
            return (
                "not_applicable",
                ("cash_flow_category_contributors_not_supported",),
                [],
            )
        return "not_applicable", (), []
    if comparison_reasons:
        return "abstained", comparison_reasons, []
    if prior is None or current is None:
        return "abstained", ("category_level_evidence_unavailable",), []

    prior_formula = prior.policy.metric_formulas.get(cast(ProfitAndLossMeasure, measure))
    current_formula = current.policy.metric_formulas.get(cast(ProfitAndLossMeasure, measure))
    if prior_formula is None or current_formula is None or prior_formula != current_formula:
        return "abstained", ("category_level_evidence_unavailable",), []

    reasons: list[str] = []
    contributors: list[CoreFinancialComparisonContributor] = []
    prior_formula_values: list[Decimal] = []
    current_formula_values: list[Decimal] = []
    prior_totals = prior.reference.category_totals
    current_totals = current.reference.category_totals
    category_order = {category: index for index, category in enumerate(FINANCIAL_CATEGORIES)}
    terms = sorted(prior_formula.terms, key=lambda term: category_order[term.category])

    for term in terms:
        category = term.category
        coverage = prior.policy.category_coverage.get(category)
        if coverage == "unknown" or coverage is None:
            reasons.append(f"category_coverage_not_complete:{category}")
            continue
        prior_count = prior.category_evidence_counts.get(category, 0)
        current_count = current.category_evidence_counts.get(category, 0)
        prior_evidence_value = prior.category_contributions.get(category, Decimal(0))
        current_evidence_value = current.category_contributions.get(category, Decimal(0))
        if coverage == "not_applicable":
            prior_raw = _as_decimal(prior_totals.get(category))
            current_raw = _as_decimal(current_totals.get(category))
            if (
                prior_raw is None
                or current_raw is None
                or not prior_raw.is_zero()
                or not current_raw.is_zero()
                or prior_count
                or current_count
            ):
                reasons.append(f"category_evidence_totals_mismatch:{category}")
            prior_formula_values.append(Decimal(0))
            current_formula_values.append(Decimal(0))
            continue

        prior_raw = _as_decimal(prior_totals.get(category))
        current_raw = _as_decimal(current_totals.get(category))
        if prior_raw is None or current_raw is None:
            reasons.append(f"category_level_evidence_missing:{category}")
            continue
        prior_expected = prior_raw if term.coefficient == 1 else prior_raw.copy_negate()
        current_expected = current_raw if term.coefficient == 1 else current_raw.copy_negate()
        if prior_expected != prior_evidence_value or current_expected != current_evidence_value:
            reasons.append(f"category_evidence_totals_mismatch:{category}")
            continue
        prior_formula_values.append(prior_evidence_value)
        current_formula_values.append(current_evidence_value)
        variance = exact_decimal_sum([current_evidence_value, prior_evidence_value.copy_negate()])
        if variance.is_zero():
            continue
        if prior_count + current_count == 0:
            reasons.append(f"category_level_evidence_missing:{category}")
            continue
        try:
            prior_text = decimal_text(prior_evidence_value)
            current_text = decimal_text(current_evidence_value)
            variance_text = decimal_text(variance)
        except ValueError:
            reasons.append(f"category_evidence_totals_mismatch:{category}")
            continue
        contributors.append(
            CoreFinancialComparisonContributor(
                category=category,
                prior_contribution=prior_text,
                current_contribution=current_text,
                absolute_variance=variance_text,
                direction=_direction(prior_evidence_value, current_evidence_value),
                prior_evidence_count=prior_count,
                current_evidence_count=current_count,
                prior_evidence_url=(f"{prior.reference.evidence_url}&category={category}"),
                current_evidence_url=(f"{current.reference.evidence_url}&category={category}"),
            )
        )
    if not reasons:
        prior_exact = _as_decimal(prior.reference.exact_value)
        current_exact = _as_decimal(current.reference.exact_value)
        try:
            prior_formula_total = exact_decimal_sum(prior_formula_values)
            current_formula_total = exact_decimal_sum(current_formula_values)
        except ValueError:
            reasons.append("category_totals_formula_mismatch")
        else:
            if prior_formula_total != prior_exact or current_formula_total != current_exact:
                reasons.append("category_totals_formula_mismatch")
    if reasons:
        return "abstained", _unique(reasons), []
    return "calculated", (), contributors


def _build_finding(
    measure: FinancialMeasure,
    prior: ComparisonRunData | None,
    current: ComparisonRunData | None,
    revenue_prior: ComparisonRunData | None,
    revenue_current: ComparisonRunData | None,
    alignment: str | None,
) -> CoreFinancialComparisonFinding:
    reasons = _unique(_pair_reasons(prior, current, alignment))
    absolute_variance: str | None = None
    percentage_variance: str | None = None
    direction: Literal["increase", "decrease", "unchanged"] | None = None
    percentage_status: Literal["calculated", "abstained"] = "abstained"
    percentage_reasons: tuple[str, ...] = ("comparison_not_comparable",)

    if not reasons and prior is not None and current is not None:
        prior_value = _as_decimal(prior.reference.exact_value)
        current_value = _as_decimal(current.reference.exact_value)
        if prior_value is None or current_value is None:
            reasons = (
                ("prior_measure_incomplete",)
                if prior_value is None
                else ("current_measure_incomplete",)
            )
        else:
            try:
                variance = exact_decimal_sum([current_value, prior_value.copy_negate()])
                absolute_variance = decimal_text(variance)
                direction = _direction(prior_value, current_value)
                if prior_value.is_zero():
                    percentage_reasons = ("zero_prior_value_percentage_unavailable",)
                else:
                    try:
                        percentage = _scaled_ratio(variance, prior_value.copy_abs())
                        percentage_variance = fixed_scale_text(percentage, scale=_PERCENT_SCALE)
                        percentage_status = "calculated"
                        percentage_reasons = ()
                    except ValueError:
                        percentage_reasons = ("percentage_variance_exceeds_supported_output",)
            except ValueError:
                reasons = ("absolute_variance_exceeds_supported_output",)
        if reasons:
            absolute_variance = None
            percentage_variance = None
            direction = None
            percentage_reasons = ("comparison_not_comparable", *reasons)

    margin = _margin_values(
        measure,
        prior,
        current,
        revenue_prior,
        revenue_current,
        alignment,
        reasons,
    )
    contributors_status, contributor_reasons, contributors = _contributor_results(
        measure, prior, current, reasons
    )
    return CoreFinancialComparisonFinding(
        measure=measure,
        disposition="abstained" if reasons else "calculated",
        reason_codes=reasons,
        reasons=tuple(_reason_text(code) for code in reasons),
        prior_run=prior.reference if prior is not None else None,
        current_run=current.reference if current is not None else None,
        absolute_variance=absolute_variance,
        percentage_variance=percentage_variance,
        percentage_variance_status=percentage_status,
        percentage_variance_reason_codes=percentage_reasons,
        direction=direction,
        margin_status=cast(
            Literal["not_applicable", "calculated", "abstained"], margin["margin_status"]
        ),
        margin_reason_codes=cast(tuple[str, ...], margin["margin_reason_codes"]),
        margin_caution_codes=cast(tuple[str, ...], margin["margin_caution_codes"]),
        prior_margin=cast(str | None, margin.get("prior_margin")),
        current_margin=cast(str | None, margin.get("current_margin")),
        margin_variance_percentage_points=cast(
            str | None, margin.get("margin_variance_percentage_points")
        ),
        contributors_status=contributors_status,
        contributors_reason_codes=contributor_reasons,
        contributors=contributors,
    )


def build_period_comparison(
    *,
    tenant_id: UUID,
    company_id: UUID,
    prior_period: CoreFinancialPeriodSelection,
    current_period: CoreFinancialPeriodSelection,
    runs: dict[tuple[Literal["prior", "current"], FinancialMeasure], ComparisonRunData | None],
) -> CoreFinancialComparisonResponse:
    """Compare exact persisted runs without recalculation, imputation, or causal attribution."""
    alignment = period_alignment(
        prior_period.period_start,
        prior_period.period_end,
        current_period.period_start,
        current_period.period_end,
    )
    period_reasons = () if alignment is not None else ("period_length_mismatch",)
    findings = tuple(
        _build_finding(
            measure,
            runs.get(("prior", measure)),
            runs.get(("current", measure)),
            runs.get(("prior", "revenue")),
            runs.get(("current", "revenue")),
            alignment,
        )
        for measure in FINANCIAL_MEASURES
    )
    return CoreFinancialComparisonResponse(
        tenant_id=tenant_id,
        company_id=company_id,
        selection_rule="latest_persisted_run_per_measure_exact_period",
        prior_period=prior_period,
        current_period=current_period,
        period_alignment=alignment,
        period_reason_codes=period_reasons,
        findings=findings,
        limitations=(
            "Results are deterministic comparisons of persisted illustrative management-reporting "
            "runs, not accounting conclusions, audit opinions, or independent source validation.",
            "Source completeness and source identity are caller-asserted; accepted complete status "
            "is not independently verified against a source system.",
            "No missing value is imputed; no foreign-exchange conversion, fiscal calendar, or "
            "accounting policy is inferred.",
            "Cash Flow is net observed bank movement under the saved bank policy, not a formal "
            "cash flow statement; it has no inferred profitability or expense contributors.",
            "Category movements are evidence-supported changes in saved formula contributions, "
            "not causal explanations.",
        ),
        assumptions=(
            "For each selected exact period and measure, the latest saved run by creation time and "
            "run ID is selected; the returned run ID makes that selection auditable.",
            "Whole-calendar-month windows are comparable only when they contain the same number "
            "of months; other windows are comparable only when inclusive day counts match. The "
            "prior window must precede and not overlap the current window.",
            "Absolute variance is current exact value minus prior exact value. Direction is a "
            "numeric increase/decrease only and does not mean favorable or unfavorable.",
            "Percentage variance is (current minus prior) divided by the absolute prior exact "
            "value, multiplied by 100, rounded to six decimal places using half-even. A zero prior "
            "has no percentage variance; negative baselines use their absolute magnitude.",
            "Profit margins are exact profit value divided by exact Revenue times 100, rounded to "
            "six decimal places using half-even, only when compatible saved Revenue and profit "
            "runs share the same period source import and non-zero Revenue denominator.",
            "Category contributors use persisted line-level evidence after the saved formula "
            "coefficient is applied; no category movement is described as causal.",
        ),
    )
