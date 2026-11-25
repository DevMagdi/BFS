from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from decimal import (
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from itertools import pairwise
from typing import Literal
from uuid import UUID

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    fixed_scale_text,
    parse_decimal_text,
    parse_output_decimal_text,
    quantize_decimal,
)
from businessflow.core_financials.contracts import (
    FINANCIAL_MEASURES,
    CoreFinancialComparisonContributor,
    CoreFinancialComparisonFinding,
    CoreFinancialComparisonResponse,
    CoreFinancialComparisonRunReference,
    CoreFinancialInsight,
    CoreFinancialInsightAssessment,
    CoreFinancialInsightEvidenceReference,
    CoreFinancialInsightRequest,
    CoreFinancialInsightsResponse,
    CoreFinancialInsightStatement,
    CoreFinancialPeriodSelection,
    FinancialCategory,
    FinancialMeasure,
)

_PERCENT_SCALE = 6
_MAX_ATTENTION_INSIGHTS = 2
_MAX_OUTLIER_INSIGHTS = 1
_PROFITABILITY_MEASURES: tuple[FinancialMeasure, ...] = (
    "gross_profit",
    "operating_profit",
    "net_profit",
)
_DRIVER_KINDS: tuple[tuple[Literal["revenue_driver", "expense_driver"], FinancialMeasure], ...] = (
    ("revenue_driver", "revenue"),
    ("expense_driver", "expenses"),
)
_MEASURE_LABELS: dict[FinancialMeasure, str] = {
    "revenue": "Revenue",
    "expenses": "Expenses",
    "gross_profit": "Gross Profit",
    "operating_profit": "Operating Profit",
    "net_profit": "Net Profit",
    "cash_flow": "Cash Flow — net observed bank movement",
}
_EXPECTED_IMPACT_REASON = "historical_variance_is_not_a_defensible_impact_estimate"


def _unique(values: tuple[str, ...] | list[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _parse_output(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        return parse_output_decimal_text(value)
    except ValueError:
        return None


def _measure_label(measure: FinancialMeasure) -> str:
    return _MEASURE_LABELS[measure]


def _percentage_ratio(numerator: Decimal, denominator: Decimal) -> tuple[Decimal, str]:
    """Return an unrounded percentage for deterministic thresholding and its six-place display."""
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
            percentage = numerator.copy_abs().scaleb(2) / denominator.copy_abs()
        rounded = quantize_decimal(percentage, scale=_PERCENT_SCALE, mode="half_even")
        return percentage, fixed_scale_text(rounded, scale=_PERCENT_SCALE)
    except (DecimalException, ValueError) as exc:
        raise ValueError("percentage result exceeds the supported exact output range") from exc


def _evidence_reference(
    run: CoreFinancialComparisonRunReference,
    *,
    category: FinancialCategory | None = None,
    evidence_url: str | None = None,
    evidence_count: int | None = None,
) -> CoreFinancialInsightEvidenceReference:
    source = run.source_import
    return CoreFinancialInsightEvidenceReference(
        measure=run.measure,
        period_start=run.period_start,
        period_end=run.period_end,
        currency_code=run.currency_code,
        run_id=run.run_id,
        run_url=run.run_url,
        run_input_hash=run.input_hash,
        run_result_hash=run.result_hash,
        run_trace_hash=run.trace_hash,
        evidence_url=evidence_url or run.evidence_url,
        source_import_id=source.id,
        source_file_hash=source.file_hash,
        evidence_count=run.evidence_count if evidence_count is None else evidence_count,
        category=category,
    )


def _evidence_for_runs(
    runs: tuple[CoreFinancialComparisonRunReference | None, ...],
) -> tuple[CoreFinancialInsightEvidenceReference, ...]:
    result: list[CoreFinancialInsightEvidenceReference] = []
    seen: set[tuple[UUID, str | None, str]] = set()
    for run in runs:
        if run is None:
            continue
        reference = _evidence_reference(run)
        key = (reference.run_id, reference.category, reference.evidence_url)
        if key not in seen:
            result.append(reference)
            seen.add(key)
    return tuple(result)


def _evidence_for_finding(
    finding: CoreFinancialComparisonFinding,
) -> tuple[CoreFinancialInsightEvidenceReference, ...]:
    return _evidence_for_runs((finding.prior_run, finding.current_run))


def _evidence_for_comparisons(
    comparisons: tuple[CoreFinancialComparisonResponse, ...],
    measure: FinancialMeasure,
) -> tuple[CoreFinancialInsightEvidenceReference, ...]:
    runs: list[CoreFinancialComparisonRunReference | None] = []
    for comparison in comparisons:
        finding = _finding(comparison, measure)
        runs.extend((finding.prior_run, finding.current_run))
    return _evidence_for_runs(tuple(runs))


def _evidence_for_contributor(
    finding: CoreFinancialComparisonFinding,
    contributor: CoreFinancialComparisonContributor,
) -> tuple[CoreFinancialInsightEvidenceReference, ...]:
    references: list[CoreFinancialInsightEvidenceReference] = []
    if finding.prior_run is not None:
        references.append(
            _evidence_reference(
                finding.prior_run,
                category=contributor.category,
                evidence_url=contributor.prior_evidence_url,
                evidence_count=contributor.prior_evidence_count,
            )
        )
    if finding.current_run is not None:
        references.append(
            _evidence_reference(
                finding.current_run,
                category=contributor.category,
                evidence_url=contributor.current_evidence_url,
                evidence_count=contributor.current_evidence_count,
            )
        )
    return tuple(references)


def _unique_evidence(
    references: tuple[CoreFinancialInsightEvidenceReference, ...],
) -> tuple[CoreFinancialInsightEvidenceReference, ...]:
    output: list[CoreFinancialInsightEvidenceReference] = []
    seen: set[tuple[UUID, str | None, str]] = set()
    for reference in references:
        key = (reference.run_id, reference.category, reference.evidence_url)
        if key not in seen:
            output.append(reference)
            seen.add(key)
    return tuple(output)


def _statement(
    text: str,
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...],
) -> CoreFinancialInsightStatement:
    if not evidence:
        raise ValueError("an insight statement must cite persisted financial evidence")
    return CoreFinancialInsightStatement(text=text, evidence=_unique_evidence(evidence))


def _make_insight(
    *,
    kind: Literal[
        "profitability_analysis",
        "revenue_driver",
        "expense_driver",
        "attention_movement",
        "potential_outlier",
    ],
    measure: FinancialMeasure,
    title: str,
    observed_facts: tuple[CoreFinancialInsightStatement, ...],
    calculations: tuple[CoreFinancialInsightStatement, ...],
    recommendations: tuple[CoreFinancialInsightStatement, ...],
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...],
) -> CoreFinancialInsight:
    return CoreFinancialInsight(
        kind=kind,
        measure=measure,
        title=title,
        observed_facts=observed_facts,
        calculations=calculations,
        # Persisted transaction categories do not establish operational causes. No causal
        # hypotheses are generated; recommendations are limited to evidence-review actions.
        hypotheses=(),
        recommendations=recommendations,
        expected_impact=None,
        expected_impact_status="not_estimated",
        expected_impact_reason_codes=(_EXPECTED_IMPACT_REASON,),
        evidence=_unique_evidence(evidence),
    )


def _finding(
    comparison: CoreFinancialComparisonResponse, measure: FinancialMeasure
) -> CoreFinancialComparisonFinding:
    for finding in comparison.findings:
        if finding.measure == measure:
            return finding
    raise ValueError(f"comparison is missing required {measure} finding")


def _comparison_reasons(finding: CoreFinancialComparisonFinding) -> tuple[str, ...]:
    if finding.reason_codes:
        return finding.reason_codes
    if finding.disposition != "calculated":
        return ("comparison_not_comparable",)
    return ()


def _assessment(
    *,
    kind: Literal[
        "profitability_analysis",
        "revenue_driver",
        "expense_driver",
        "attention_movement",
        "potential_outlier",
    ],
    measure: FinancialMeasure,
    status: Literal["insight_generated", "not_triggered", "abstained", "not_selected"],
    period: CoreFinancialPeriodSelection,
    reason_codes: tuple[str, ...] = (),
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...] = (),
) -> CoreFinancialInsightAssessment:
    return CoreFinancialInsightAssessment(
        kind=kind,
        measure=measure,
        status=status,
        period_start=period.period_start,
        period_end=period.period_end,
        reason_codes=reason_codes,
        evidence=_unique_evidence(evidence),
    )


def _build_profitability_insight(
    measure: FinancialMeasure,
    finding: CoreFinancialComparisonFinding,
    comparison: CoreFinancialComparisonResponse,
) -> CoreFinancialInsight | None:
    prior = finding.prior_run
    current = finding.current_run
    if (
        finding.disposition != "calculated"
        or prior is None
        or current is None
        or prior.exact_value is None
        or current.exact_value is None
        or finding.absolute_variance is None
    ):
        return None
    evidence = _evidence_for_finding(finding)
    if not evidence:
        return None
    currency = current.currency_code
    observed = _statement(
        f"Persisted {_measure_label(measure)} changed from {prior.exact_value} to "
        f"{current.exact_value} {currency} between "
        f"{prior.period_start} to {prior.period_end} and "
        f"{current.period_start} to {current.period_end}. The exact change was "
        f"{finding.absolute_variance} {currency} ({finding.direction}); direction is numeric, "
        "not a favorable or unfavorable assessment.",
        evidence,
    )
    calculations: list[CoreFinancialInsightStatement] = []
    if (
        finding.percentage_variance_status == "calculated"
        and finding.percentage_variance is not None
    ):
        calculations.append(
            _statement(
                f"Percentage variance was {finding.percentage_variance}% using the saved "
                "comparison convention: (current exact value minus prior exact value) divided by "
                "the absolute prior exact value, then multiplied by 100 and rounded to six "
                "decimal places using half-even.",
                evidence,
            )
        )
    elif finding.percentage_variance_reason_codes:
        calculations.append(
            _statement(
                "Percentage variance was withheld: "
                + ", ".join(finding.percentage_variance_reason_codes)
                + ".",
                evidence,
            )
        )

    if finding.margin_status == "calculated":
        revenue_finding = _finding(comparison, "revenue")
        margin_evidence = _unique_evidence((*evidence, *_evidence_for_finding(revenue_finding)))
        if margin_evidence:
            calculations.append(
                _statement(
                    f"Saved {measure.replace('_', ' ')} margin changed from "
                    f"{finding.prior_margin}% to {finding.current_margin}% "
                    f"({finding.margin_variance_percentage_points} percentage points). "
                    "M14 calculates each margin from the exact saved profit and compatible "
                    "Revenue value using six-place half-even rounding.",
                    margin_evidence,
                )
            )
    elif finding.margin_reason_codes:
        calculations.append(
            _statement(
                "Margin was withheld by the persisted comparison: "
                + ", ".join(finding.margin_reason_codes)
                + ".",
                evidence,
            )
        )

    recommendation = _statement(
        f"Review the linked {_measure_label(measure)} source rows and saved formula for both "
        "periods; confirm source completeness, timing, and account classification with the "
        "finance owner before interpreting the movement. No business cause is inferred.",
        evidence,
    )
    all_evidence = _unique_evidence(
        tuple(
            ref
            for statement in (observed, *calculations, recommendation)
            for ref in statement.evidence
        )
    )
    return _make_insight(
        kind="profitability_analysis",
        measure=measure,
        title=f"{_measure_label(measure)} period movement",
        observed_facts=(observed,),
        calculations=tuple(calculations),
        recommendations=(recommendation,),
        evidence=all_evidence,
    )


def _material_contributors(
    finding: CoreFinancialComparisonFinding,
    threshold: Decimal,
) -> tuple[list[tuple[Decimal, str, CoreFinancialComparisonContributor]], str | None]:
    total_variance = _parse_output(finding.absolute_variance)
    if total_variance is None:
        return [], "category_total_variance_unavailable"
    if total_variance.is_zero():
        return [], None
    candidates: list[tuple[Decimal, str, CoreFinancialComparisonContributor]] = []
    for contributor in finding.contributors:
        amount = _parse_output(contributor.absolute_variance)
        if amount is None:
            return [], "category_variance_unavailable"
        if amount.is_zero():
            continue
        try:
            share, share_text = _percentage_ratio(amount, total_variance)
        except ValueError:
            return [], "category_materiality_exceeds_supported_precision"
        if share >= threshold:
            candidates.append((share, share_text, contributor))
    candidates.sort(key=lambda item: (-item[0], item[2].category))
    return candidates, None


def _build_driver_insight(
    *,
    kind: Literal["revenue_driver", "expense_driver"],
    measure: FinancialMeasure,
    finding: CoreFinancialComparisonFinding,
    contributor: CoreFinancialComparisonContributor,
    share_text: str,
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...],
) -> CoreFinancialInsight:
    prior = finding.prior_run
    current = finding.current_run
    if prior is None or current is None or not evidence:
        raise ValueError("a material category driver requires both persisted run references")
    category_label = contributor.category.replace("_", " ")
    currency = current.currency_code
    observed = _statement(
        f"The saved {_measure_label(measure)} formula contribution from {category_label} "
        f"changed from {contributor.prior_contribution} to {contributor.current_contribution} "
        f"{currency} between {prior.period_start} to {prior.period_end} and "
        f"{current.period_start} to {current.period_end}; the signed contribution change was "
        f"{contributor.absolute_variance} {currency}. This is a formula contribution, not a "
        "causal explanation.",
        evidence,
    )
    calculation = _statement(
        f"The absolute category contribution change was {share_text}% of the absolute net "
        f"{_measure_label(measure)} movement. This ratio can exceed 100% when category changes "
        "offset one another.",
        evidence,
    )
    recommendation = _statement(
        f"Inspect the linked {category_label} source rows in both periods. Verify transaction "
        "dates, refunds or reversals, source completeness, and the saved account mapping with the "
        "finance owner before attributing the change to an operational cause or changing policy.",
        evidence,
    )
    return _make_insight(
        kind=kind,
        measure=measure,
        title=f"{_measure_label(measure)} contribution movement: {category_label}",
        observed_facts=(observed,),
        calculations=(calculation,),
        recommendations=(recommendation,),
        evidence=evidence,
    )


def _build_attention_insight(
    measure: FinancialMeasure,
    finding: CoreFinancialComparisonFinding,
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...],
    threshold_text: str,
) -> CoreFinancialInsight:
    prior = finding.prior_run
    current = finding.current_run
    percentage = finding.percentage_variance
    if (
        prior is None
        or current is None
        or percentage is None
        or finding.absolute_variance is None
        or not evidence
    ):
        raise ValueError("a material movement requires compatible saved values and evidence")
    currency = current.currency_code
    observed = _statement(
        f"Persisted {_measure_label(measure)} changed from {prior.exact_value} to "
        f"{current.exact_value} {currency}; the exact variance was "
        f"{finding.absolute_variance} {currency} ({finding.direction}) and the M14 percentage "
        f"variance was {percentage}%.",
        evidence,
    )
    calculation = _statement(
        f"The absolute six-decimal M14 percentage variance meets the configured "
        f"{threshold_text}% attention threshold. The threshold identifies a movement for review; "
        "it does not classify the movement as favorable or unfavorable.",
        evidence,
    )
    recommendation = _statement(
        "Review the linked persisted source rows and reconcile the movement to known timing, "
        "classification, refund or reversal, and completeness conditions. Confirm any explanation "
        "with the finance owner; no cause or expected savings is inferred.",
        evidence,
    )
    return _make_insight(
        kind="attention_movement",
        measure=measure,
        title=f"Movement to review: {_measure_label(measure)}",
        observed_facts=(observed,),
        calculations=(calculation,),
        recommendations=(recommendation,),
        evidence=evidence,
    )


@dataclass(frozen=True, slots=True)
class _OutlierCandidate:
    measure: FinancialMeasure
    finding: CoreFinancialComparisonFinding
    baseline_maximum: Decimal
    latest_movement: Decimal
    ratio_percent: Decimal
    ratio_text: str
    evidence: tuple[CoreFinancialInsightEvidenceReference, ...]


def _periods_are_contiguous(periods: tuple[CoreFinancialPeriodSelection, ...]) -> bool:
    return all(
        previous.period_end + timedelta(days=1) == current.period_start
        for previous, current in pairwise(periods)
    )


def _assess_outlier(
    *,
    measure: FinancialMeasure,
    periods: tuple[CoreFinancialPeriodSelection, ...],
    comparisons: tuple[CoreFinancialComparisonResponse, ...],
) -> tuple[CoreFinancialInsightAssessment | None, _OutlierCandidate | None]:
    latest_period = periods[-1]
    evidence = _evidence_for_comparisons(comparisons, measure)
    if len(periods) < 4:
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=("insufficient_history",),
                evidence=evidence,
            ),
            None,
        )
    if not _periods_are_contiguous(periods):
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=("non_contiguous_history",),
                evidence=evidence,
            ),
            None,
        )

    findings = tuple(_finding(comparison, measure) for comparison in comparisons)
    unavailable = [finding for finding in findings if finding.disposition != "calculated"]
    if unavailable:
        reasons = _unique(
            [
                reason
                for finding in unavailable
                for reason in (_comparison_reasons(finding) or ("insufficient_comparable_history",))
            ]
        )
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=reasons,
                evidence=evidence,
            ),
            None,
        )

    movements: list[Decimal] = []
    for finding in findings:
        amount = _parse_output(finding.absolute_variance)
        if amount is None:
            return (
                _assessment(
                    kind="potential_outlier",
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=("historical_variance_unavailable",),
                    evidence=evidence,
                ),
                None,
            )
        movements.append(amount.copy_abs())

    baseline_maximum = max(movements[:-1])
    latest_movement = movements[-1]
    if baseline_maximum.is_zero():
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=("zero_historical_movement_baseline",),
                evidence=evidence,
            ),
            None,
        )
    try:
        three_times_baseline = exact_decimal_sum(
            (baseline_maximum, baseline_maximum, baseline_maximum)
        )
    except ValueError:
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=("outlier_threshold_exceeds_supported_precision",),
                evidence=evidence,
            ),
            None,
        )
    if latest_movement <= three_times_baseline:
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="not_triggered",
                period=latest_period,
                reason_codes=("within_three_times_historical_maximum",),
                evidence=evidence,
            ),
            None,
        )
    try:
        ratio, ratio_text = _percentage_ratio(latest_movement, baseline_maximum)
    except ValueError:
        return (
            _assessment(
                kind="potential_outlier",
                measure=measure,
                status="abstained",
                period=latest_period,
                reason_codes=("outlier_ratio_exceeds_supported_precision",),
                evidence=evidence,
            ),
            None,
        )
    return (
        None,
        _OutlierCandidate(
            measure=measure,
            finding=findings[-1],
            baseline_maximum=baseline_maximum,
            latest_movement=latest_movement,
            ratio_percent=ratio,
            ratio_text=ratio_text,
            evidence=evidence,
        ),
    )


def _build_outlier_insight(
    candidate: _OutlierCandidate, prior_periods: int
) -> CoreFinancialInsight:
    finding = candidate.finding
    prior = finding.prior_run
    current = finding.current_run
    if prior is None or current is None or not candidate.evidence:
        raise ValueError("a potential outlier requires persisted references for the latest period")
    baseline_text = decimal_text(candidate.baseline_maximum)
    latest_text = decimal_text(candidate.latest_movement)
    multiplier_threshold = decimal_text(
        exact_decimal_sum(
            (candidate.baseline_maximum, candidate.baseline_maximum, candidate.baseline_maximum)
        )
    )
    currency = current.currency_code
    observed = _statement(
        f"The latest {_measure_label(candidate.measure)} movement from "
        f"{prior.period_start} through {prior.period_end} to "
        f"{current.period_start} through {current.period_end} was {latest_text} {currency} "
        "in absolute terms. This is above three times the largest absolute "
        f"movement ({baseline_text} {currency}) across {prior_periods} earlier "
        "contiguous selected comparisons.",
        candidate.evidence,
    )
    calculation = _statement(
        f"Deterministic potential-outlier rule: latest absolute M14 variance {latest_text} "
        f"{currency} > three times prior maximum {baseline_text} {currency} = "
        f"{multiplier_threshold} {currency} (latest/prior maximum: "
        f"{candidate.ratio_text}%). This statistical flag does not identify a cause.",
        candidate.evidence,
    )
    recommendation = _statement(
        "Compare the latest linked source rows with the earlier selected periods and verify dates, "
        "account mapping, source completeness, refunds, and reversals. Treat the flag as a review "
        "prompt only; it is not proof of an anomaly or its cause.",
        candidate.evidence,
    )
    return _make_insight(
        kind="potential_outlier",
        measure=candidate.measure,
        title=f"Potential unusual movement: {_measure_label(candidate.measure)}",
        observed_facts=(observed,),
        calculations=(calculation,),
        recommendations=(recommendation,),
        evidence=candidate.evidence,
    )


def build_core_financial_insights(
    *,
    tenant_id: UUID,
    company_id: UUID,
    request: CoreFinancialInsightRequest,
    comparisons: tuple[CoreFinancialComparisonResponse, ...],
) -> CoreFinancialInsightsResponse:
    """Derive bounded, evidence-linked observations from the existing M14 comparisons only."""
    threshold = parse_decimal_text(request.attention_threshold_percent)
    periods = request.periods
    latest_comparison = comparisons[-1]
    latest_period = periods[-1]
    latest_findings = {finding.measure: finding for finding in latest_comparison.findings}
    insights: list[CoreFinancialInsight] = []
    assessments: list[CoreFinancialInsightAssessment] = []

    for measure in _PROFITABILITY_MEASURES:
        finding = latest_findings[measure]
        insight = _build_profitability_insight(measure, finding, latest_comparison)
        evidence = _evidence_for_finding(finding)
        if insight is None:
            reasons = _comparison_reasons(finding) or ("profitability_evidence_unavailable",)
            assessments.append(
                _assessment(
                    kind="profitability_analysis",
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=reasons,
                    evidence=evidence,
                )
            )
        else:
            insights.append(insight)
            assessments.append(
                _assessment(
                    kind="profitability_analysis",
                    measure=measure,
                    status="insight_generated",
                    period=latest_period,
                    evidence=insight.evidence,
                )
            )

    for kind, measure in _DRIVER_KINDS:
        finding = latest_findings[measure]
        evidence = _evidence_for_finding(finding)
        if finding.disposition != "calculated":
            assessments.append(
                _assessment(
                    kind=kind,
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=_comparison_reasons(finding) or ("comparison_not_comparable",),
                    evidence=evidence,
                )
            )
            continue
        if finding.contributors_status != "calculated":
            assessments.append(
                _assessment(
                    kind=kind,
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=finding.contributors_reason_codes
                    or ("category_contributors_unavailable",),
                    evidence=evidence,
                )
            )
            continue
        candidates, error = _material_contributors(finding, threshold)
        if error is not None:
            assessments.append(
                _assessment(
                    kind=kind,
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=(error,),
                    evidence=evidence,
                )
            )
            continue
        if not candidates:
            net_movement = _parse_output(finding.absolute_variance)
            reason = (
                "no_net_measure_movement"
                if net_movement is not None and net_movement.is_zero()
                else "no_category_contribution_meets_attention_threshold"
            )
            assessments.append(
                _assessment(
                    kind=kind,
                    measure=measure,
                    status="not_triggered",
                    period=latest_period,
                    reason_codes=(reason,),
                    evidence=evidence,
                )
            )
            continue
        _share, share_text, contributor = candidates[0]
        driver_evidence = _evidence_for_contributor(finding, contributor)
        if not driver_evidence:
            assessments.append(
                _assessment(
                    kind=kind,
                    measure=measure,
                    status="abstained",
                    period=latest_period,
                    reason_codes=("category_evidence_reference_unavailable",),
                    evidence=evidence,
                )
            )
            continue
        insight = _build_driver_insight(
            kind=kind,
            measure=measure,
            finding=finding,
            contributor=contributor,
            share_text=share_text,
            evidence=driver_evidence,
        )
        insights.append(insight)
        assessments.append(
            _assessment(
                kind=kind,
                measure=measure,
                status="insight_generated",
                period=latest_period,
                evidence=insight.evidence,
            )
        )

    threshold_text = request.attention_threshold_percent
    attention_candidates: list[
        tuple[Decimal, FinancialMeasure, CoreFinancialComparisonFinding]
    ] = []
    attention_outcomes: dict[
        FinancialMeasure,
        tuple[
            Literal["abstained", "not_triggered"],
            tuple[str, ...],
            tuple[CoreFinancialInsightEvidenceReference, ...],
        ],
    ] = {}
    for measure in FINANCIAL_MEASURES:
        finding = latest_findings[measure]
        evidence = _evidence_for_finding(finding)
        if finding.disposition != "calculated":
            attention_outcomes[measure] = (
                "abstained",
                _comparison_reasons(finding) or ("comparison_not_comparable",),
                evidence,
            )
            continue
        if (
            finding.percentage_variance_status != "calculated"
            or finding.percentage_variance is None
        ):
            attention_outcomes[measure] = (
                "abstained",
                finding.percentage_variance_reason_codes or ("percentage_variance_unavailable",),
                evidence,
            )
            continue
        amount = _parse_output(finding.absolute_variance)
        percentage = _parse_output(finding.percentage_variance)
        if amount is None or percentage is None:
            attention_outcomes[measure] = (
                "abstained",
                ("comparison_variance_unavailable",),
                evidence,
            )
            continue
        if amount.is_zero():
            attention_outcomes[measure] = ("not_triggered", ("no_absolute_movement",), evidence)
            continue
        magnitude = percentage.copy_abs()
        if magnitude < threshold:
            attention_outcomes[measure] = (
                "not_triggered",
                ("below_attention_threshold",),
                evidence,
            )
            continue
        if finding.prior_run is None or finding.current_run is None or not evidence:
            attention_outcomes[measure] = (
                "abstained",
                ("comparison_evidence_reference_unavailable",),
                evidence,
            )
            continue
        attention_candidates.append((magnitude, measure, finding))

    attention_candidates.sort(key=lambda item: (-item[0], FINANCIAL_MEASURES.index(item[1])))
    selected_attention = {item[1] for item in attention_candidates[:_MAX_ATTENTION_INSIGHTS]}
    for measure in FINANCIAL_MEASURES:
        attention_candidate = next(
            (item for item in attention_candidates if item[1] == measure), None
        )
        if attention_candidate is None:
            status, reasons, evidence = attention_outcomes[measure]
            assessments.append(
                _assessment(
                    kind="attention_movement",
                    measure=measure,
                    status=status,
                    period=latest_period,
                    reason_codes=reasons,
                    evidence=evidence,
                )
            )
        elif measure not in selected_attention:
            assessments.append(
                _assessment(
                    kind="attention_movement",
                    measure=measure,
                    status="not_selected",
                    period=latest_period,
                    reason_codes=("priority_limit_reached",),
                    evidence=_evidence_for_finding(attention_candidate[2]),
                )
            )
    for _magnitude, measure, finding in attention_candidates[:_MAX_ATTENTION_INSIGHTS]:
        evidence = _evidence_for_finding(finding)
        insight = _build_attention_insight(measure, finding, evidence, threshold_text)
        insights.append(insight)
        assessments.append(
            _assessment(
                kind="attention_movement",
                measure=measure,
                status="insight_generated",
                period=latest_period,
                evidence=insight.evidence,
            )
        )

    outlier_candidates: list[_OutlierCandidate] = []
    outlier_outcomes: dict[FinancialMeasure, CoreFinancialInsightAssessment] = {}
    for measure in FINANCIAL_MEASURES:
        outcome, outlier_candidate = _assess_outlier(
            measure=measure,
            periods=periods,
            comparisons=comparisons,
        )
        if outlier_candidate is not None:
            outlier_candidates.append(outlier_candidate)
        elif outcome is not None:
            outlier_outcomes[measure] = outcome

    outlier_candidates.sort(
        key=lambda item: (-item.ratio_percent, FINANCIAL_MEASURES.index(item.measure))
    )
    selected_outliers = {item.measure for item in outlier_candidates[:_MAX_OUTLIER_INSIGHTS]}
    for measure in FINANCIAL_MEASURES:
        selected_outlier_candidate = next(
            (item for item in outlier_candidates if item.measure == measure), None
        )
        if selected_outlier_candidate is None:
            assessments.append(outlier_outcomes[measure])
        elif measure not in selected_outliers:
            assessments.append(
                _assessment(
                    kind="potential_outlier",
                    measure=measure,
                    status="not_selected",
                    period=latest_period,
                    reason_codes=("priority_limit_reached",),
                    evidence=selected_outlier_candidate.evidence,
                )
            )
    for selected_outlier_candidate in outlier_candidates[:_MAX_OUTLIER_INSIGHTS]:
        insights.append(
            _build_outlier_insight(selected_outlier_candidate, prior_periods=len(comparisons) - 1)
        )
        assessments.append(
            _assessment(
                kind="potential_outlier",
                measure=selected_outlier_candidate.measure,
                status="insight_generated",
                period=latest_period,
                evidence=selected_outlier_candidate.evidence,
            )
        )

    limitations = (
        "Insights are deterministic summaries of the selected persisted M14 comparisons; no "
        "financial values are recalculated, imputed, or sourced from a separate engine.",
        "Persisted category contributions explain the saved formula arithmetic only; source rows "
        "do not establish an operational root cause, and no causal hypothesis is asserted.",
        "Source identity and completeness are caller-asserted and are not independently verified "
        "against an originating system.",
        "Expected impact is not estimated because historical variance alone does not support a "
        "defensible savings, recovery, or future-impact amount. No forecast or budget is produced.",
        "A potential outlier requires at least four contiguous selected periods, complete "
        "comparable findings for every adjacent pair, and a latest absolute movement greater than "
        "three times the largest earlier selected adjacent movement. It is a review flag, not a "
        "proof of anomaly or cause.",
        "Cash Flow remains net observed bank movement under the saved policy, not a formal cash "
        "flow statement or a P&L driver.",
    )
    assumptions = (
        "For every selected period and measure, the latest exact-period persisted run by creation "
        "time and run ID is used by M14; each adjacent comparison retains its own run and source "
        "lineage.",
        "The attention threshold applies to the absolute six-decimal M14 percentage variance; "
        "zero prior values retain M14's explicit percentage abstention.",
        "Category materiality is the absolute category formula-contribution change divided by "
        "the absolute net measure movement, multiplied by 100. The ratio may exceed 100% when "
        "signed category movements offset.",
        "Potential-outlier detection compares the latest absolute M14 variance with three times "
        "the maximum absolute variance in earlier contiguous selected comparisons; it does not "
        "infer seasonality, a forecast, or a cause.",
        "A numeric increase or decrease is not labeled favorable or unfavorable without business "
        "context.",
    )
    return CoreFinancialInsightsResponse(
        tenant_id=tenant_id,
        company_id=company_id,
        selection_rule="latest_persisted_run_per_measure_exact_selected_period",
        periods=periods,
        attention_threshold_percent=request.attention_threshold_percent,
        comparisons=comparisons,
        insights=tuple(insights),
        assessments=tuple(assessments),
        limitations=limitations,
        assumptions=assumptions,
    )
