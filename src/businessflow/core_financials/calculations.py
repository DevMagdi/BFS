from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Literal
from uuid import UUID

from businessflow.calculation_core.decimal import (
    decimal_text,
    exact_decimal_sum,
    fixed_scale_text,
    parse_output_decimal_text,
    quantize_decimal,
    round_currency_amount,
)
from businessflow.calculation_core.verification import reconcile_values
from businessflow.core_financials.contracts import (
    FINANCIAL_MEASURES,
    CoreFinancialAnalysisStatement,
    CoreFinancialMetricResult,
    CoreFinancialPolicyCreateRequest,
    FinancialCategory,
    FinancialMeasure,
    MetricControl,
    MetricReconciliation,
    MonetaryRoundingPolicy,
    SourceType,
)


@dataclass(frozen=True, slots=True)
class SourceImportState:
    id: UUID
    source_key: str
    source_type: SourceType
    file_hash: str
    mapping_hash: str
    mapping_version: int
    policy_hash: str
    period_start: date
    period_end: date
    period_completeness: str
    quality_status: str
    accepted_rows: int
    rejected_rows: int
    duplicate_rows: int


@dataclass(frozen=True, slots=True)
class CanonicalFinancialFact:
    id: UUID
    import_id: UUID
    source_record_id: str
    transaction_date: date
    currency_code: str
    account_code: str | None
    category: FinancialCategory | None
    event_type: str
    signed_amount: str
    source_row_number: int
    source_row_hash: str
    source_key: str
    source_type: SourceType
    source_file_hash: str


@dataclass(frozen=True, slots=True)
class FinancialRunLine:
    metric: FinancialMeasure
    fact: CanonicalFinancialFact
    coefficient: Literal[-1, 1]
    contribution_amount: str


def _apply_formula_coefficient(value: Decimal, coefficient: Literal[-1, 1]) -> Decimal:
    """Apply a formula sign without context-rounded Decimal multiplication."""
    return value if coefficient == 1 else value.copy_negate()


def calculate_financial_metrics(
    *,
    policy: CoreFinancialPolicyCreateRequest,
    policy_version: int,
    request_period_start: date,
    request_period_end: date,
    currency_code: str,
    ledger_import: SourceImportState | None,
    bank_import: SourceImportState | None,
    facts: tuple[CanonicalFinancialFact, ...],
    controls: tuple[MetricControl, ...],
    requested_metrics: tuple[FinancialMeasure, ...] | None = None,
) -> tuple[
    tuple[CoreFinancialMetricResult, ...],
    tuple[CoreFinancialAnalysisStatement, ...],
    tuple[FinancialRunLine, ...],
    Literal["calculated", "incomplete"],
]:
    """Calculate selected versioned measures; unsupported policies/data abstain per measure."""
    selected_metrics = FINANCIAL_MEASURES if requested_metrics is None else requested_metrics
    if (
        not selected_metrics
        or len(selected_metrics) != len(set(selected_metrics))
        or any(metric not in FINANCIAL_MEASURES for metric in selected_metrics)
    ):
        raise ValueError("requested_metrics must be a non-empty set of supported measures")
    controls_by_metric = {control.metric: control for control in controls}
    facts_by_import: dict[UUID, list[CanonicalFinancialFact]] = {}
    for fact in facts:
        facts_by_import.setdefault(fact.import_id, []).append(fact)

    results: list[CoreFinancialMetricResult] = []
    lines: list[FinancialRunLine] = []
    analysis: list[CoreFinancialAnalysisStatement] = [
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="illustrative_management_policy",
            text=(
                f"Policy version {policy_version} is illustrative management reporting only; "
                "accounting approval and source authority are not established."
            ),
        ),
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="source_completeness_caller_asserted",
            text=(
                "Source identity and period completeness are caller assertions and are not "
                "independently authenticated or validated against a source system."
            ),
        ),
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="no_fx_conversion",
            text=(
                f"Results are calculated only in {currency_code}; "
                "no FX conversion or consolidation is performed."
            ),
        ),
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="explicit_period_window",
            text=(
                f"The requested transaction-date window is {request_period_start.isoformat()} "
                f"through {request_period_end.isoformat()}, inclusive; "
                "no fiscal calendar is inferred."
            ),
        ),
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="cash_flow_scope",
            text=(
                "Cash Flow is the net movement of accepted bank-activity rows only; it is not a "
                "cash-flow statement and has no inferred opening or closing balance."
            ),
            metric="cash_flow",
        ),
        CoreFinancialAnalysisStatement(
            kind="limitation",
            code="no_ai_conclusion",
            text=(
                "No AI-generated explanation, causal claim, forecast, or recommendation "
                "is produced."
            ),
        ),
    ]
    if "cash_flow" not in selected_metrics:
        analysis = [item for item in analysis if item.code != "cash_flow_scope"]

    for metric in selected_metrics:
        if metric == "cash_flow":
            source_import = bank_import
            metric_basis: Literal["accrual", "cash", "cash_movement"] = "cash_movement"
            required_categories: tuple[FinancialCategory, ...] = ()
            formula_terms: tuple[tuple[FinancialCategory, Literal[-1, 1]], ...] = ()
        else:
            source_import = ledger_import
            metric_basis = policy.ledger_basis
            formula = policy.metric_formulas[metric]
            formula_terms = tuple((term.category, term.coefficient) for term in formula.terms)
            required_categories = tuple(term.category for term in formula.terms)

        if source_import is None:
            result = _unavailable_metric(
                metric=metric,
                basis=metric_basis,
                currency_code=currency_code,
                required_categories=required_categories,
                status="missing_source",
                reason="required_source_missing",
                control=controls_by_metric.get(metric),
            )
            results.append(result)
            analysis.append(
                CoreFinancialAnalysisStatement(
                    kind="limitation",
                    code="required_source_missing",
                    text=(
                        f"{_metric_label(metric)} was not calculated because its required "
                        "source is absent."
                    ),
                    metric=metric,
                )
            )
            continue

        quality_reasons = _import_readiness_reasons(
            source_import, request_period_start, request_period_end
        )
        if quality_reasons:
            result = _unavailable_metric(
                metric=metric,
                basis=metric_basis,
                currency_code=currency_code,
                required_categories=required_categories,
                status="incomplete",
                reason=quality_reasons,
                control=controls_by_metric.get(metric),
                source_import_id=source_import.id,
            )
            results.append(result)
            analysis.append(
                CoreFinancialAnalysisStatement(
                    kind="limitation",
                    code="source_import_incomplete",
                    text=(
                        f"{_metric_label(metric)} was withheld: the required source is not both "
                        "caller-asserted complete and fully accepted."
                    ),
                    metric=metric,
                )
            )
            continue

        import_facts = facts_by_import.get(source_import.id, [])
        in_window = [
            fact
            for fact in import_facts
            if request_period_start <= fact.transaction_date <= request_period_end
        ]
        excluded_outside_period_count = len(import_facts) - len(in_window)
        selected = [fact for fact in in_window if fact.currency_code == currency_code]
        excluded_currency_count = len(in_window) - len(selected)
        reasons: list[str] = []
        category_totals: dict[str, str] = {}
        required_coverage: list[FinancialCategory] = []
        if metric != "cash_flow":
            for category, _coefficient in formula_terms:
                if category not in required_coverage:
                    required_coverage.append(category)
                if policy.category_coverage[category] == "unknown":
                    reasons.append(f"category_coverage_unknown:{category}")
            for category in required_coverage:
                category_rows = [row for row in selected if row.category == category]
                coverage = policy.category_coverage[category]
                if coverage == "not_applicable":
                    if category_rows:
                        reasons.append(f"records_in_category_declared_not_applicable:{category}")
                    category_total = Decimal(0)
                else:
                    category_total = (
                        exact_decimal_sum(
                            parse_output_decimal_text(row.signed_amount) for row in category_rows
                        )
                        if category_rows
                        else Decimal(0)
                    )
                category_totals[category] = decimal_text(category_total)
            if reasons:
                result = _unavailable_metric(
                    metric=metric,
                    basis=metric_basis,
                    currency_code=currency_code,
                    required_categories=tuple(required_coverage),
                    status="incomplete",
                    reason=tuple(sorted(set(reasons))),
                    control=controls_by_metric.get(metric),
                    source_import_id=source_import.id,
                    excluded_other_currency_count=excluded_currency_count,
                    excluded_outside_period_count=excluded_outside_period_count,
                    category_totals=category_totals,
                )
                results.append(result)
                analysis.append(
                    CoreFinancialAnalysisStatement(
                        kind="limitation",
                        code="category_coverage_insufficient",
                        text=(
                            f"{_metric_label(metric)} was withheld because required category "
                            "coverage is unknown or inconsistent with accepted source rows."
                        ),
                        metric=metric,
                    )
                )
                continue

            exact_value = exact_decimal_sum(
                _apply_formula_coefficient(
                    parse_output_decimal_text(category_totals[category]), coefficient
                )
                for category, coefficient in formula_terms
            )
            for category, coefficient in formula_terms:
                if policy.category_coverage[category] == "not_applicable":
                    continue
                for fact in selected:
                    if fact.category == category:
                        contribution = _apply_formula_coefficient(
                            parse_output_decimal_text(fact.signed_amount), coefficient
                        )
                        lines.append(
                            FinancialRunLine(
                                metric=metric,
                                fact=fact,
                                coefficient=coefficient,
                                contribution_amount=decimal_text(contribution),
                            )
                        )
            included_count = sum(1 for fact in selected if fact.category in set(required_coverage))
        else:
            exact_value = (
                exact_decimal_sum(
                    parse_output_decimal_text(fact.signed_amount) for fact in selected
                )
                if selected
                else Decimal(0)
            )
            included_count = len(selected)
            for fact in selected:
                lines.append(
                    FinancialRunLine(
                        metric="cash_flow",
                        fact=fact,
                        coefficient=1,
                        contribution_amount=fact.signed_amount,
                    )
                )

        exact_text = decimal_text(exact_value)
        reported_text = _apply_rounding(exact_value, currency_code, policy.rounding)
        reconciliation = _reconcile_metric(
            actual_value=exact_text,
            currency_code=currency_code,
            control=controls_by_metric.get(metric),
            imports=(source_import,),
            facts=tuple(selected),
        )
        result = CoreFinancialMetricResult(
            metric=metric,
            label=_metric_label(metric),
            status="calculated",
            basis=metric_basis,
            currency_code=currency_code,
            exact_value=exact_text,
            reported_value=reported_text,
            rounding_target=policy.rounding.target,
            rounding_mode=policy.rounding.mode,
            rounding_scale=_rounding_scale(policy.rounding, currency_code),
            included_record_count=included_count,
            excluded_other_currency_count=excluded_currency_count,
            excluded_outside_period_count=excluded_outside_period_count,
            required_categories=required_categories,
            category_totals=category_totals,
            reason_codes=(),
            reconciliation=reconciliation,
            source_import_ids=(source_import.id,),
        )
        results.append(result)
        analysis.append(
            CoreFinancialAnalysisStatement(
                kind="deterministic_calculation",
                code=f"{metric}_calculated",
                text=(
                    f"{_metric_label(metric)} is {reported_text} {currency_code} for the inclusive "
                    f"period {request_period_start.isoformat()} to "
                    f"{request_period_end.isoformat()} "
                    f"under {metric_basis} basis using policy version {policy_version}."
                ),
                metric=metric,
            )
        )
        if reconciliation.state == "failed":
            analysis.append(
                CoreFinancialAnalysisStatement(
                    kind="deterministic_observation",
                    code=f"{metric}_reconciliation_difference",
                    text=(
                        f"The caller-provided control differs from the calculated exact value by "
                        f"{reconciliation.variance} {currency_code}; control independence and "
                        "source authority are caller-asserted, not independently verified."
                    ),
                    metric=metric,
                )
            )

    overall_state: Literal["calculated", "incomplete"] = (
        "calculated" if all(item.status == "calculated" for item in results) else "incomplete"
    )
    return tuple(results), tuple(analysis), tuple(lines), overall_state


def _import_readiness_reasons(
    source_import: SourceImportState,
    request_period_start: date,
    request_period_end: date,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if source_import.period_completeness != "complete":
        reasons.append("source_period_completeness_not_asserted")
    if source_import.quality_status != "passed":
        reasons.append("source_contains_rejected_or_duplicate_rows")
    if (
        source_import.period_start > request_period_start
        or source_import.period_end < request_period_end
    ):
        reasons.append("source_period_does_not_cover_requested_period")
    return tuple(reasons)


def _unavailable_metric(
    *,
    metric: FinancialMeasure,
    basis: Literal["accrual", "cash", "cash_movement"],
    currency_code: str,
    required_categories: tuple[FinancialCategory, ...],
    status: Literal["incomplete", "missing_source"],
    reason: str | tuple[str, ...],
    control: MetricControl | None,
    source_import_id: UUID | None = None,
    excluded_other_currency_count: int = 0,
    excluded_outside_period_count: int = 0,
    category_totals: dict[str, str] | None = None,
) -> CoreFinancialMetricResult:
    reasons = (reason,) if isinstance(reason, str) else reason
    reconciliation = MetricReconciliation(
        state="not_verifiable",
        expected_value=control.expected_value if control else None,
        actual_value=None,
        variance=None,
        effective_tolerance=None,
        control_source_hash=control.source.source_hash if control else None,
        reason_codes=(*reasons, "metric_value_unavailable"),
    )
    return CoreFinancialMetricResult(
        metric=metric,
        label=_metric_label(metric),
        status=status,
        basis=basis,
        currency_code=currency_code,
        exact_value=None,
        reported_value=None,
        rounding_target=None,
        rounding_mode=None,
        rounding_scale=None,
        included_record_count=0,
        excluded_other_currency_count=excluded_other_currency_count,
        excluded_outside_period_count=excluded_outside_period_count,
        required_categories=required_categories,
        category_totals=category_totals or {},
        reason_codes=reasons,
        reconciliation=reconciliation,
        source_import_ids=(source_import_id,) if source_import_id else (),
    )


def _reconcile_metric(
    *,
    actual_value: str,
    currency_code: str,
    control: MetricControl | None,
    imports: tuple[SourceImportState | None, ...],
    facts: tuple[CanonicalFinancialFact, ...],
) -> MetricReconciliation:
    if control is None:
        return MetricReconciliation(
            state="not_provided",
            expected_value=None,
            actual_value=actual_value,
            variance=None,
            effective_tolerance=None,
            control_source_hash=None,
            reason_codes=("independent_control_missing",),
        )
    input_file_hashes = {item.file_hash for item in imports if item is not None}
    input_row_hashes = {fact.source_row_hash for fact in facts}
    overlapping = (
        control.source.source_hash in input_file_hashes
        or (
            control.source.record_hash is not None
            and control.source.record_hash in input_row_hashes
        )
        or any(
            control.source.source_id == item.source_key
            and control.source.record_locator
            in {f"csv:row:{fact.source_row_number}" for fact in facts if fact.import_id == item.id}
            for item in imports
            if item is not None
        )
    )
    if overlapping:
        return MetricReconciliation(
            state="not_verifiable",
            expected_value=control.expected_value,
            actual_value=actual_value,
            variance=None,
            effective_tolerance=None,
            control_source_hash=control.source.source_hash,
            reason_codes=("control_source_overlaps_calculation_evidence",),
        )
    report = reconcile_values(
        expected_value=control.expected_value,
        actual_value=actual_value,
        absolute_tolerance=control.absolute_tolerance,
        relative_tolerance=control.relative_tolerance,
        currency_code=currency_code,
    )
    return MetricReconciliation(
        state=report.state,
        expected_value=report.expected_value,
        actual_value=report.actual_value,
        variance=report.variance,
        effective_tolerance=report.effective_tolerance,
        control_source_hash=control.source.source_hash,
        reason_codes=report.reason_codes,
    )


def _apply_rounding(value: Decimal, currency_code: str, policy: MonetaryRoundingPolicy) -> str:
    if policy.target == "none":
        return decimal_text(value)
    if policy.target == "currency_minor_unit":
        return round_currency_amount(value, currency_code, mode=policy.mode)
    if policy.scale is None:
        raise ValueError("fixed-scale policy is missing its scale")
    return fixed_scale_text(
        quantize_decimal(value, scale=policy.scale, mode=policy.mode), scale=policy.scale
    )


def _rounding_scale(policy: MonetaryRoundingPolicy, currency_code: str) -> int | None:
    if policy.target == "none":
        return None
    if policy.target == "fixed_scale":
        return policy.scale
    from businessflow.calculation_core.decimal import currency_exponent

    return currency_exponent(currency_code)


def _metric_label(metric: FinancialMeasure) -> str:
    return {
        "revenue": "Revenue",
        "expenses": "Expenses",
        "gross_profit": "Gross Profit",
        "operating_profit": "Operating Profit",
        "net_profit": "Net Profit",
        "cash_flow": "Cash Flow — net observed bank movement",
    }[metric]
