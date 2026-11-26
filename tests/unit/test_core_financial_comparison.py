from __future__ import annotations

from datetime import date
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from businessflow.core_financials.comparison import ComparisonRunData, build_period_comparison
from businessflow.core_financials.contracts import (
    FINANCIAL_CATEGORIES,
    FINANCIAL_MEASURES,
    AccountRule,
    CashFlowPolicyDefinition,
    CoreFinancialComparisonRequest,
    CoreFinancialComparisonRunReference,
    CoreFinancialOverviewSource,
    CoreFinancialPeriodSelection,
    CoreFinancialPolicyCreateRequest,
    FormulaTerm,
    MetricFormula,
    MonetaryRoundingPolicy,
    period_alignment,
)

_POLICY_ID = UUID("11111111-1111-4111-8111-111111111111")
_POLICY_HASH = "a" * 64
_IMPORT_PRIOR = UUID("22222222-2222-4222-8222-222222222222")
_IMPORT_CURRENT = UUID("33333333-3333-4333-8333-333333333333")
_FORMULAS = {
    "revenue": MetricFormula(
        terms=(
            FormulaTerm(category="revenue", coefficient=1),
            FormulaTerm(category="revenue_reduction", coefficient=-1),
        )
    ),
    "expenses": MetricFormula(
        terms=(
            FormulaTerm(category="cost_of_sales", coefficient=1),
            FormulaTerm(category="operating_expense", coefficient=1),
            FormulaTerm(category="non_operating_expense", coefficient=1),
            FormulaTerm(category="tax_expense", coefficient=1),
        )
    ),
    "gross_profit": MetricFormula(
        terms=(
            FormulaTerm(category="revenue", coefficient=1),
            FormulaTerm(category="revenue_reduction", coefficient=-1),
            FormulaTerm(category="cost_of_sales", coefficient=-1),
        )
    ),
    "operating_profit": MetricFormula(
        terms=(
            FormulaTerm(category="revenue", coefficient=1),
            FormulaTerm(category="revenue_reduction", coefficient=-1),
            FormulaTerm(category="cost_of_sales", coefficient=-1),
            FormulaTerm(category="operating_expense", coefficient=-1),
        )
    ),
    "net_profit": MetricFormula(
        terms=(
            FormulaTerm(category="revenue", coefficient=1),
            FormulaTerm(category="revenue_reduction", coefficient=-1),
            FormulaTerm(category="cost_of_sales", coefficient=-1),
            FormulaTerm(category="operating_expense", coefficient=-1),
            FormulaTerm(category="non_operating_income", coefficient=1),
            FormulaTerm(category="non_operating_expense", coefficient=-1),
            FormulaTerm(category="tax_expense", coefficient=-1),
        )
    ),
}
_POLICY = CoreFinancialPolicyCreateRequest(
    policy_key="unit.comparison",
    reporting_framework="management_reporting",
    ledger_basis="accrual",
    account_map={
        f"{index:04d}": AccountRule(category=category, normal_balance="debit")
        for index, category in enumerate(FINANCIAL_CATEGORIES, start=4000)
    },
    category_coverage={category: "complete" for category in FINANCIAL_CATEGORIES},
    metric_formulas=_FORMULAS,
    rounding=MonetaryRoundingPolicy(target="none", mode="half_even"),
    cash_flow_policy=CashFlowPolicyDefinition(
        movement_rule="cash_in_less_cash_out",
        refund_treatment="include_as_signed_bank_movement",
        reversal_treatment="require_linked_exact_opposite_entry",
        adjustment_treatment="include_as_signed_bank_movement",
        transfer_treatment="exclude_marked_transfers",
        opening_balance_treatment="not_reported_no_balance_fields",
        closing_balance_treatment="not_reported_no_balance_fields",
    ),
)


def _source(
    *,
    period_start: date,
    period_end: date,
    import_id: UUID,
    source_type: str = "general_ledger",
    completeness: str = "complete",
    quality: str = "passed",
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    policy_id: UUID = _POLICY_ID,
    policy_version: int = 1,
    policy_hash: str = _POLICY_HASH,
) -> CoreFinancialOverviewSource:
    return CoreFinancialOverviewSource(
        id=import_id,
        source_key="unit-ledger" if source_type == "general_ledger" else "unit-bank",
        source_type=source_type,
        source_identity_status="caller_asserted_not_verified",
        source_authority_status="not_established",
        source_archive_status="retained",
        file_name="unit.csv",
        file_hash="b" * 64,
        input_hash="c" * 64,
        result_hash="d" * 64,
        mapping_version=1,
        mapping_hash="e" * 64,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_hash=policy_hash,
        period_start=source_period_start or period_start,
        period_end=source_period_end or period_end,
        period_completeness=completeness,
        completeness_authority="caller_asserted_not_verified",
        quality_status=quality,
        total_rows=4,
        accepted_rows=4 if quality == "passed" else 3,
        rejected_rows=0 if quality == "passed" else 1,
        duplicate_rows=0,
        import_url=f"/api/v1/tenants/unit/core-financials/imports/{import_id}",
    )


def _run(
    measure: str,
    value: str,
    *,
    side: str,
    period_start: date,
    period_end: date,
    currency: str = "USD",
    basis: str | None = None,
    policy_id: UUID = _POLICY_ID,
    policy_version: int = 1,
    policy_hash: str = _POLICY_HASH,
    engine: str = "businessflow.core-financials.v3-shared-measures",
    status: str = "calculated",
    completeness: str = "complete",
    quality: str = "passed",
    source_type_override: str | None = None,
    source_period_start: date | None = None,
    source_period_end: date | None = None,
    category_totals: dict[str, str] | None = None,
) -> ComparisonRunData:
    import_id = _IMPORT_PRIOR if side == "prior" else _IMPORT_CURRENT
    source_type = source_type_override or (
        "bank_activity" if measure == "cash_flow" else "general_ledger"
    )
    policy = _POLICY.model_copy(update={"ledger_basis": "cash"}) if basis == "cash" else _POLICY
    if policy_id != _POLICY_ID or policy_hash != _POLICY_HASH:
        policy = policy.model_copy(update={"policy_key": "unit.other-policy"})
    run_id = uuid4()
    totals = category_totals or {}
    terms = () if measure == "cash_flow" else policy.metric_formulas[measure].terms
    coefficient_by_category = {term.category: term.coefficient for term in terms}
    category_contributions = {
        category: Decimal(raw) * coefficient_by_category[category]
        for category, raw in totals.items()
        if category in coefficient_by_category
    }
    category_counts = {
        category: 1 for category, contribution in category_contributions.items() if contribution
    }
    reference = CoreFinancialComparisonRunReference(
        run_id=run_id,
        measure=measure,
        run_url=f"/api/v1/tenants/unit/core-financials/runs/{run_id}",
        evidence_url=(
            f"/api/v1/tenants/unit/core-financials/runs/{run_id}/evidence?metric={measure}"
        ),
        metric_status=status,
        exact_value=value if status == "calculated" else None,
        reported_value=value if status == "calculated" else None,
        period_start=period_start,
        period_end=period_end,
        currency_code=currency,
        basis=basis or ("cash_movement" if measure == "cash_flow" else "accrual"),
        run_state="calculated" if status == "calculated" else "incomplete",
        engine_version=engine,
        policy_id=policy_id,
        policy_version=policy_version,
        policy_hash=policy_hash,
        input_hash="1" * 64,
        result_hash="2" * 64,
        trace_hash="3" * 64,
        evidence_count=sum(category_counts.values()),
        source_import=_source(
            period_start=period_start,
            period_end=period_end,
            import_id=import_id,
            source_type=source_type,
            completeness=completeness,
            quality=quality,
            source_period_start=source_period_start,
            source_period_end=source_period_end,
            policy_id=policy_id,
            policy_version=policy_version,
            policy_hash=policy_hash,
        ),
        category_totals=totals,
        formula_terms=terms,
        reason_codes=("category_coverage_unknown:operating_expense",)
        if status != "calculated"
        else (),
    )
    return ComparisonRunData(
        reference=reference,
        policy=policy,
        category_contributions=category_contributions,
        category_evidence_counts=category_counts,
    )


def _periods() -> tuple[CoreFinancialPeriodSelection, CoreFinancialPeriodSelection]:
    return (
        CoreFinancialPeriodSelection(period_start=date(2026, 10, 1), period_end=date(2026, 10, 31)),
        CoreFinancialPeriodSelection(period_start=date(2026, 11, 1), period_end=date(2026, 11, 30)),
    )


def _measure_runs(
    prior_values: dict[str, str],
    current_values: dict[str, str],
    *,
    prior_totals: dict[str, dict[str, str]] | None = None,
    current_totals: dict[str, dict[str, str]] | None = None,
) -> dict[tuple[str, str], ComparisonRunData | None]:
    prior_period, current_period = _periods()
    runs: dict[tuple[str, str], ComparisonRunData | None] = {}
    for measure in FINANCIAL_MEASURES:
        prior_source_type = "bank_activity" if measure == "cash_flow" else "general_ledger"
        current_source_type = prior_source_type
        prior = _run(
            measure,
            prior_values[measure],
            side="prior",
            period_start=prior_period.period_start,
            period_end=prior_period.period_end,
            category_totals=(prior_totals or {}).get(measure),
        )
        current = _run(
            measure,
            current_values[measure],
            side="current",
            period_start=current_period.period_start,
            period_end=current_period.period_end,
            category_totals=(current_totals or {}).get(measure),
        )
        assert prior.reference.source_import.source_type == prior_source_type
        assert current.reference.source_import.source_type == current_source_type
        runs[("prior", measure)] = prior
        runs[("current", measure)] = current
    return runs


def test_period_alignment_uses_equal_whole_month_counts_or_equal_custom_day_counts() -> None:
    assert (
        period_alignment(
            date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 30)
        )
        == "equal_calendar_month_count"
    )
    assert (
        period_alignment(
            date(2026, 10, 5), date(2026, 10, 11), date(2026, 11, 2), date(2026, 11, 8)
        )
        == "equal_day_count"
    )
    assert (
        period_alignment(
            date(2026, 10, 1), date(2026, 10, 31), date(2026, 11, 1), date(2026, 11, 14)
        )
        is None
    )
    assert (
        period_alignment(date(2026, 1, 1), date(2026, 1, 31), date(2026, 2, 1), date(2026, 3, 3))
        is None
    )
    assert (
        period_alignment(date(2024, 1, 1), date(2024, 2, 29), date(2025, 1, 1), date(2025, 2, 28))
        == "equal_calendar_month_count"
    )
    assert (
        period_alignment(date(2024, 2, 28), date(2024, 3, 1), date(2025, 2, 28), date(2025, 3, 2))
        == "equal_day_count"
    )
    assert (
        period_alignment(date(2025, 2, 1), date(2025, 2, 28), date(2025, 3, 2), date(2025, 3, 29))
        is None
    )
    assert (
        period_alignment(date(2024, 1, 1), date(2024, 3, 31), date(2025, 1, 1), date(2025, 2, 28))
        is None
    )


def test_comparison_uses_exact_values_and_evidence_supported_category_contributions() -> None:
    prior_period, current_period = _periods()
    prior_totals = {
        "expenses": {
            "cost_of_sales": "20",
            "operating_expense": "10",
            "non_operating_expense": "2",
            "tax_expense": "3",
        },
        "gross_profit": {"revenue": "100", "revenue_reduction": "0", "cost_of_sales": "20"},
        "operating_profit": {
            "revenue": "100",
            "revenue_reduction": "0",
            "cost_of_sales": "20",
            "operating_expense": "10",
        },
        "net_profit": {
            "revenue": "100",
            "revenue_reduction": "0",
            "cost_of_sales": "20",
            "operating_expense": "10",
            "non_operating_income": "5",
            "non_operating_expense": "2",
            "tax_expense": "3",
        },
    }
    current_totals = {
        "expenses": {
            "cost_of_sales": "25",
            "operating_expense": "10",
            "non_operating_expense": "2",
            "tax_expense": "3",
        },
        "gross_profit": {"revenue": "125", "revenue_reduction": "0", "cost_of_sales": "25"},
        "operating_profit": {
            "revenue": "125",
            "revenue_reduction": "0",
            "cost_of_sales": "25",
            "operating_expense": "10",
        },
        "net_profit": {
            "revenue": "125",
            "revenue_reduction": "0",
            "cost_of_sales": "25",
            "operating_expense": "10",
            "non_operating_income": "5",
            "non_operating_expense": "2",
            "tax_expense": "3",
        },
    }
    runs = _measure_runs(
        {
            "revenue": "100",
            "expenses": "35",
            "gross_profit": "80",
            "operating_profit": "70",
            "net_profit": "70",
            "cash_flow": "650",
        },
        {
            "revenue": "125",
            "expenses": "40",
            "gross_profit": "100",
            "operating_profit": "90",
            "net_profit": "90",
            "cash_flow": "850",
        },
        prior_totals=prior_totals,
        current_totals=current_totals,
    )

    result = build_period_comparison(
        tenant_id=UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"),
        company_id=UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"),
        prior_period=prior_period,
        current_period=current_period,
        runs=runs,
    )
    findings = {finding.measure: finding for finding in result.findings}

    assert result.period_alignment == "equal_calendar_month_count"
    assert findings["revenue"].absolute_variance == "25"
    assert findings["revenue"].percentage_variance == "25.000000"
    assert findings["revenue"].direction == "increase"
    assert findings["expenses"].absolute_variance == "5"
    assert findings["expenses"].percentage_variance == "14.285714"
    assert findings["expenses"].contributors_status == "calculated"
    assert [
        (item.category, item.absolute_variance) for item in findings["expenses"].contributors
    ] == [("cost_of_sales", "5")]
    assert findings["gross_profit"].prior_margin == "80.000000"
    assert findings["gross_profit"].current_margin == "80.000000"
    assert findings["gross_profit"].margin_variance_percentage_points == "0.000000"
    assert [
        (item.category, item.absolute_variance)
        for item in findings["operating_profit"].contributors
    ] == [("revenue", "25"), ("cost_of_sales", "-5")]
    assert findings["cash_flow"].contributors_status == "not_applicable"
    assert (
        "cash_flow_category_contributors_not_supported"
        in findings["cash_flow"].contributors_reason_codes
    )
    assert "formal cash flow statement" in result.limitations[3]
    cost_contributor = findings["expenses"].contributors[0]
    assert cost_contributor.prior_evidence_count == 1
    assert cost_contributor.current_evidence_url.endswith("&category=cost_of_sales")
    assert findings["net_profit"].prior_run.trace_hash == "3" * 64


def test_zero_and_negative_prior_values_have_explicit_percentage_semantics() -> None:
    prior_period, current_period = _periods()
    prior = _run(
        "revenue",
        "0",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    current = _run(
        "revenue",
        "10",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    runs = {("prior", "revenue"): prior, ("current", "revenue"): current}
    zero_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs=runs,
    )
    zero_finding = next(item for item in zero_result.findings if item.measure == "revenue")
    assert zero_finding.disposition == "calculated"
    assert zero_finding.absolute_variance == "10"
    assert zero_finding.percentage_variance is None
    assert zero_finding.percentage_variance_status == "abstained"
    assert (
        "zero_prior_value_percentage_unavailable" in zero_finding.percentage_variance_reason_codes
    )

    negative_prior = _run(
        "revenue",
        "-100",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    negative_current = _run(
        "revenue",
        "-50",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    negative_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={("prior", "revenue"): negative_prior, ("current", "revenue"): negative_current},
    )
    negative_finding = next(item for item in negative_result.findings if item.measure == "revenue")
    assert negative_finding.absolute_variance == "50"
    assert negative_finding.percentage_variance == "50.000000"
    assert negative_finding.direction == "increase"


def test_percentage_variance_uses_half_even_rounding_at_six_decimal_places() -> None:
    prior_period, current_period = _periods()
    cases = (
        ("200000000", "200000001", "0.000000"),
        ("200000000", "200000003", "0.000002"),
    )
    for prior_value, current_value, expected_percentage in cases:
        result = build_period_comparison(
            tenant_id=uuid4(),
            company_id=uuid4(),
            prior_period=prior_period,
            current_period=current_period,
            runs={
                ("prior", "revenue"): _run(
                    "revenue",
                    prior_value,
                    side="prior",
                    period_start=prior_period.period_start,
                    period_end=prior_period.period_end,
                ),
                ("current", "revenue"): _run(
                    "revenue",
                    current_value,
                    side="current",
                    period_start=current_period.period_start,
                    period_end=current_period.period_end,
                ),
            },
        )
        revenue = next(item for item in result.findings if item.measure == "revenue")
        assert revenue.percentage_variance == expected_percentage


def test_zero_revenue_margin_and_category_evidence_mismatch_abstain() -> None:
    prior_period, current_period = _periods()
    prior_profit = _run(
        "gross_profit",
        "50",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    current_profit = _run(
        "gross_profit",
        "60",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    prior_revenue = _run(
        "revenue",
        "0",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    current_revenue = _run(
        "revenue",
        "100",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    margin_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={
            ("prior", "gross_profit"): prior_profit,
            ("current", "gross_profit"): current_profit,
            ("prior", "revenue"): prior_revenue,
            ("current", "revenue"): current_revenue,
        },
    )
    gross_profit = next(item for item in margin_result.findings if item.measure == "gross_profit")
    assert gross_profit.disposition == "calculated"
    assert gross_profit.absolute_variance == "10"
    assert gross_profit.margin_status == "abstained"
    assert gross_profit.prior_margin is None
    assert gross_profit.current_margin == "60.000000"
    assert "prior_zero_revenue_margin_denominator" in gross_profit.margin_reason_codes

    prior_totals = {
        "cost_of_sales": "20",
        "operating_expense": "10",
        "non_operating_expense": "2",
        "tax_expense": "3",
    }
    current_totals = {
        "cost_of_sales": "25",
        "operating_expense": "10",
        "non_operating_expense": "2",
        "tax_expense": "3",
    }
    prior_expenses = _run(
        "expenses",
        "35",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
        category_totals=prior_totals,
    )
    current_expenses = _run(
        "expenses",
        "40",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
        category_totals=current_totals,
    )
    consistent_current_expenses = current_expenses
    current_expenses = ComparisonRunData(
        reference=current_expenses.reference,
        policy=current_expenses.policy,
        category_contributions={
            **current_expenses.category_contributions,
            "cost_of_sales": Decimal("999"),
        },
        category_evidence_counts=current_expenses.category_evidence_counts,
    )
    evidence_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={
            ("prior", "expenses"): prior_expenses,
            ("current", "expenses"): current_expenses,
        },
    )
    expenses = next(item for item in evidence_result.findings if item.measure == "expenses")
    assert expenses.disposition == "calculated"
    assert expenses.absolute_variance == "5"
    assert expenses.contributors_status == "abstained"
    assert "category_evidence_totals_mismatch:cost_of_sales" in expenses.contributors_reason_codes
    assert expenses.contributors == []

    inconsistent_total = ComparisonRunData(
        reference=consistent_current_expenses.reference.model_copy(update={"exact_value": "41"}),
        policy=consistent_current_expenses.policy,
        category_contributions=consistent_current_expenses.category_contributions,
        category_evidence_counts=consistent_current_expenses.category_evidence_counts,
    )
    total_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={
            ("prior", "expenses"): prior_expenses,
            ("current", "expenses"): inconsistent_total,
        },
    )
    total_finding = next(item for item in total_result.findings if item.measure == "expenses")
    assert total_finding.absolute_variance == "6"
    assert total_finding.contributors_status == "abstained"
    assert "category_totals_formula_mismatch" in total_finding.contributors_reason_codes
    assert total_finding.contributors == []


def test_negative_profit_and_revenue_margins_keep_numeric_direction_and_cautions() -> None:
    prior_period, current_period = _periods()
    result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={
            ("prior", "gross_profit"): _run(
                "gross_profit",
                "-20",
                side="prior",
                period_start=prior_period.period_start,
                period_end=prior_period.period_end,
            ),
            ("current", "gross_profit"): _run(
                "gross_profit",
                "-8",
                side="current",
                period_start=current_period.period_start,
                period_end=current_period.period_end,
            ),
            ("prior", "revenue"): _run(
                "revenue",
                "-100",
                side="prior",
                period_start=prior_period.period_start,
                period_end=prior_period.period_end,
            ),
            ("current", "revenue"): _run(
                "revenue",
                "-80",
                side="current",
                period_start=current_period.period_start,
                period_end=current_period.period_end,
            ),
        },
    )
    gross_profit = next(item for item in result.findings if item.measure == "gross_profit")
    assert gross_profit.disposition == "calculated"
    assert gross_profit.absolute_variance == "12"
    assert gross_profit.percentage_variance == "60.000000"
    assert gross_profit.direction == "increase"
    assert gross_profit.prior_margin == "20.000000"
    assert gross_profit.current_margin == "10.000000"
    assert gross_profit.margin_variance_percentage_points == "-10.000000"
    assert gross_profit.margin_status == "calculated"
    assert gross_profit.margin_caution_codes == (
        "negative_revenue_prior_denominator",
        "negative_revenue_current_denominator",
    )


def test_profit_margin_abstains_when_revenue_and_profit_use_different_period_imports() -> None:
    prior_period, current_period = _periods()
    prior_profit = _run(
        "gross_profit",
        "80",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    current_profit = _run(
        "gross_profit",
        "90",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    prior_revenue = _run(
        "revenue",
        "100",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    prior_source = prior_revenue.reference.source_import.model_copy(update={"id": uuid4()})
    prior_revenue = ComparisonRunData(
        reference=prior_revenue.reference.model_copy(update={"source_import": prior_source}),
        policy=prior_revenue.policy,
        category_contributions=prior_revenue.category_contributions,
        category_evidence_counts=prior_revenue.category_evidence_counts,
    )
    current_revenue = _run(
        "revenue",
        "120",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={
            ("prior", "gross_profit"): prior_profit,
            ("current", "gross_profit"): current_profit,
            ("prior", "revenue"): prior_revenue,
            ("current", "revenue"): current_revenue,
        },
    )
    gross_profit = next(item for item in result.findings if item.measure == "gross_profit")
    assert gross_profit.disposition == "calculated"
    assert gross_profit.absolute_variance == "10"
    assert gross_profit.margin_status == "abstained"
    assert "prior_margin_context_mismatch" in gross_profit.margin_reason_codes
    assert gross_profit.prior_margin is None
    assert gross_profit.current_margin is None
    assert gross_profit.margin_variance_percentage_points is None


def test_missing_periods_and_incompatible_contexts_abstain_without_variance() -> None:
    prior_period, current_period = _periods()
    prior = _run(
        "revenue",
        "100",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    missing = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={("prior", "revenue"): prior},
    )
    missing_revenue = next(item for item in missing.findings if item.measure == "revenue")
    assert missing_revenue.disposition == "abstained"
    assert "current_run_unavailable" in missing_revenue.reason_codes
    assert missing_revenue.absolute_variance is None

    cases = (
        ("currency", {"currency": "EUR"}, "currency_mismatch_no_fx"),
        ("basis", {"basis": "cash"}, "reporting_basis_mismatch"),
        (
            "policy identity",
            {"policy_id": UUID("44444444-4444-4444-8444-444444444444"), "policy_hash": "f" * 64},
            "policy_version_mismatch",
        ),
        ("policy version", {"policy_version": 2}, "policy_version_mismatch"),
        (
            "engine",
            {"engine": "businessflow.core-financials.v9"},
            "calculation_engine_version_mismatch",
        ),
        (
            "source adapter",
            {"source_type_override": "bank_activity"},
            "source_type_mismatch",
        ),
        (
            "source completeness",
            {"completeness": "partial"},
            "current_source_period_completeness_not_asserted",
        ),
        (
            "source period coverage",
            {"source_period_start": date(2026, 11, 2)},
            "current_source_period_does_not_cover_run",
        ),
        ("source quality", {"quality": "incomplete"}, "current_source_quality_incomplete"),
    )
    for _label, overrides, expected_reason in cases:
        current = _run(
            "revenue",
            "125",
            side="current",
            period_start=current_period.period_start,
            period_end=current_period.period_end,
            **overrides,
        )
        result = build_period_comparison(
            tenant_id=uuid4(),
            company_id=uuid4(),
            prior_period=prior_period,
            current_period=current_period,
            runs={("prior", "revenue"): prior, ("current", "revenue"): current},
        )
        finding = next(item for item in result.findings if item.measure == "revenue")
        assert finding.disposition == "abstained", _label
        assert expected_reason in finding.reason_codes, _label
        assert finding.absolute_variance is None, _label


def test_incompatible_period_shape_and_incomplete_metric_are_explicit_abstentions() -> None:
    prior_period = CoreFinancialPeriodSelection(
        period_start=date(2026, 10, 1), period_end=date(2026, 10, 31)
    )
    current_period = CoreFinancialPeriodSelection(
        period_start=date(2026, 11, 1), period_end=date(2026, 11, 14)
    )
    prior = _run(
        "revenue",
        "100",
        side="prior",
        period_start=prior_period.period_start,
        period_end=prior_period.period_end,
    )
    current = _run(
        "revenue",
        "125",
        side="current",
        period_start=current_period.period_start,
        period_end=current_period.period_end,
    )
    period_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=prior_period,
        current_period=current_period,
        runs={("prior", "revenue"): prior, ("current", "revenue"): current},
    )
    assert period_result.period_alignment is None
    assert all(item.disposition == "abstained" for item in period_result.findings)
    assert all("period_length_mismatch" in item.reason_codes for item in period_result.findings)

    p_month, c_month = _periods()
    incomplete = _run(
        "revenue",
        "125",
        side="current",
        period_start=c_month.period_start,
        period_end=c_month.period_end,
        status="incomplete",
    )
    incomplete_result = build_period_comparison(
        tenant_id=uuid4(),
        company_id=uuid4(),
        prior_period=p_month,
        current_period=c_month,
        runs={
            ("prior", "revenue"): _run(
                "revenue",
                "100",
                side="prior",
                period_start=p_month.period_start,
                period_end=p_month.period_end,
            ),
            ("current", "revenue"): incomplete,
        },
    )
    incomplete_finding = next(
        item for item in incomplete_result.findings if item.measure == "revenue"
    )
    assert "current_measure_incomplete" in incomplete_finding.reason_codes
    assert incomplete_finding.absolute_variance is None


def test_comparison_request_requires_valid_ordered_non_overlapping_periods() -> None:
    adjacent = CoreFinancialComparisonRequest(
        prior_period={"period_start": "2026-10-01", "period_end": "2026-10-31"},
        current_period={"period_start": "2026-11-01", "period_end": "2026-11-30"},
    )
    assert adjacent.prior_period.period_end < adjacent.current_period.period_start

    with pytest.raises(ValidationError, match="period_end must not precede period_start"):
        CoreFinancialPeriodSelection(period_start=date(2026, 11, 1), period_end=date(2026, 10, 31))

    with pytest.raises(ValidationError, match="prior_period must end before current_period starts"):
        CoreFinancialComparisonRequest(
            prior_period={"period_start": "2026-10-01", "period_end": "2026-11-01"},
            current_period={"period_start": "2026-11-01", "period_end": "2026-11-30"},
        )
