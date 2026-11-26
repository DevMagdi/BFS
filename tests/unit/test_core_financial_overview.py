from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from businessflow.core_financials.contracts import (
    FINANCIAL_CATEGORIES,
    CoreFinancialOverviewMeasure,
    CoreFinancialOverviewSource,
    FormulaTerm,
    MetricReconciliation,
    ProfitAndLossMeasure,
)
from businessflow.core_financials.overview import build_overview_checks

_POLICY_ID = UUID("11111111-1111-4111-8111-111111111111")
_IMPORT_ID = UUID("22222222-2222-4222-8222-222222222222")
_POLICY_HASH = "a" * 64
_FORMULAS: dict[ProfitAndLossMeasure, tuple[FormulaTerm, ...]] = {
    "revenue": (FormulaTerm(category="revenue", coefficient=1),),
    "expenses": (FormulaTerm(category="cost_of_sales", coefficient=1),),
    "gross_profit": (
        FormulaTerm(category="revenue", coefficient=1),
        FormulaTerm(category="cost_of_sales", coefficient=-1),
    ),
    "operating_profit": (
        FormulaTerm(category="revenue", coefficient=1),
        FormulaTerm(category="cost_of_sales", coefficient=-1),
        FormulaTerm(category="operating_expense", coefficient=-1),
    ),
    "net_profit": (
        FormulaTerm(category="revenue", coefficient=1),
        FormulaTerm(category="cost_of_sales", coefficient=-1),
        FormulaTerm(category="operating_expense", coefficient=-1),
        FormulaTerm(category="non_operating_income", coefficient=1),
        FormulaTerm(category="non_operating_expense", coefficient=-1),
        FormulaTerm(category="tax_expense", coefficient=-1),
    ),
}
_COVERAGE = {category: "complete" for category in FINANCIAL_CATEGORIES}
_CATEGORY_TOTALS: dict[str, dict[str, str]] = {
    "revenue": {"revenue": "100"},
    "expenses": {"cost_of_sales": "30"},
    "gross_profit": {"revenue": "100", "cost_of_sales": "30"},
    "operating_profit": {
        "revenue": "100",
        "cost_of_sales": "30",
        "operating_expense": "20",
    },
    "net_profit": {
        "revenue": "100",
        "cost_of_sales": "30",
        "operating_expense": "20",
        "non_operating_income": "3",
        "non_operating_expense": "1",
        "tax_expense": "2",
    },
}
_VALUES = {
    "revenue": "100",
    "expenses": "30",
    "gross_profit": "70",
    "operating_profit": "50",
    "net_profit": "50",
}


def _source() -> CoreFinancialOverviewSource:
    return CoreFinancialOverviewSource(
        id=_IMPORT_ID,
        source_key="unit-ledger",
        source_type="general_ledger",
        source_identity_status="caller_asserted_not_verified",
        source_authority_status="not_established",
        source_archive_status="retained",
        file_name="unit-ledger.csv",
        file_hash="b" * 64,
        input_hash="d" * 64,
        result_hash="e" * 64,
        mapping_version=1,
        mapping_hash="c" * 64,
        policy_id=_POLICY_ID,
        policy_version=1,
        policy_hash=_POLICY_HASH,
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30),
        period_completeness="complete",
        completeness_authority="caller_asserted_not_verified",
        quality_status="passed",
        total_rows=10,
        accepted_rows=10,
        rejected_rows=0,
        duplicate_rows=0,
        import_url=f"/api/v1/tenants/tenant/core-financials/imports/{_IMPORT_ID}",
    )


def _measure(
    name: ProfitAndLossMeasure,
    *,
    value: str | None = None,
    status: str = "available",
    currency_code: str = "USD",
    period_start: date = date(2026, 11, 1),
    category_totals: dict[str, str] | None = None,
    reason_codes: tuple[str, ...] = (),
) -> CoreFinancialOverviewMeasure:
    actual_value = _VALUES[name] if value is None and status == "available" else value
    metric_status = "calculated" if status == "available" else "incomplete"
    run_id = uuid4()
    return CoreFinancialOverviewMeasure(
        measure=name,
        label=name.replace("_", " ").title(),
        status=status,
        metric_status=metric_status,
        reported_value=actual_value,
        exact_value=actual_value,
        currency_code=currency_code,
        basis="accrual",
        period_start=period_start,
        period_end=date(2026, 11, 30),
        period_rule="inclusive_transaction_date",
        fx_policy="one_currency_no_fx",
        run_id=run_id,
        run_url=f"/api/v1/tenants/tenant/core-financials/runs/{run_id}",
        evidence_url=f"/api/v1/tenants/tenant/core-financials/runs/{run_id}/evidence",
        evidence_status="available",
        evidence_count=2,
        run_state="calculated" if status == "available" else "incomplete",
        engine_version="businessflow.core-financials.v3-shared-measures",
        policy_id=_POLICY_ID,
        policy_version=1,
        policy_hash=_POLICY_HASH,
        policy_url=f"/api/v1/tenants/tenant/core-financials/policies/{_POLICY_ID}",
        policy_authority="illustrative_not_authoritative",
        reporting_framework="management_reporting",
        accounting_policy_status="not_established",
        source_imports=(_source(),),
        reconciliation=MetricReconciliation(
            state="not_provided",
            expected_value=None,
            actual_value=actual_value,
            variance=None,
            effective_tolerance=None,
            control_source_hash=None,
        ),
        category_totals=category_totals or _CATEGORY_TOTALS[name],
        formula_terms=_FORMULAS[name],
        policy_formulas=_FORMULAS,
        category_coverage=_COVERAGE,
        reason_codes=reason_codes,
        limitations=("Unit-test limitation: caller source is illustrative.",),
        assumptions=("Unit-test assumption: source currency is unchanged.",),
    )


def _measures() -> tuple[CoreFinancialOverviewMeasure, ...]:
    return tuple(_measure(name) for name in _FORMULAS)


def _check(checks, key: str):
    return next(item for item in checks if item.key == key)


def test_policy_supported_bridges_and_cost_of_sales_identity_are_consistent() -> None:
    checks = build_overview_checks(_measures())

    assert {item.status for item in checks} == {"consistent"}
    assert _check(checks, "gross_profit_bridge").equation == (
        "Gross Profit = Revenue - cost of sales"
    )
    assert _check(checks, "revenue_expenses_gross_profit").variance == "0"
    assert _check(checks, "operating_profit_bridge").equation == (
        "Operating Profit = Gross Profit - operating expense"
    )
    assert _check(checks, "net_profit_bridge").variance == "0"
    assert all(item.run_references for item in checks)
    assert all(
        item.run_references[0].source_imports[0].source_type == "general_ledger" for item in checks
    )


def test_incomplete_measure_is_explicitly_unverifiable() -> None:
    measures = [
        item.model_copy(update={"category_coverage": {**_COVERAGE, "cost_of_sales": "unknown"}})
        for item in _measures()
    ]
    measures[1] = _measure(
        "expenses",
        status="incomplete",
        value=None,
        reason_codes=("category_coverage_unknown:cost_of_sales",),
    )

    check = _check(build_overview_checks(tuple(measures)), "revenue_expenses_gross_profit")

    assert check.status == "unverifiable"
    assert "measure_incomplete" in check.reason_codes
    assert "policy_category_coverage_unknown:cost_of_sales" in check.reason_codes
    assert "expenses:category_coverage_unknown:cost_of_sales" in check.reason_codes
    assert check.variance is None
    assert any(reference.measure == "expenses" for reference in check.run_references)


def test_different_period_or_currency_is_non_comparable_without_arithmetic() -> None:
    measures = list(_measures())
    measures[-1] = _measure("net_profit", currency_code="EUR", value="50")
    checks = build_overview_checks(tuple(measures))

    check = _check(checks, "net_profit_bridge")

    assert check.status == "non_comparable"
    assert "currency_mismatch_no_fx" in check.reason_codes
    assert check.variance is None

    measures[-1] = _measure("net_profit", period_start=date(2026, 10, 1), value="50")
    period_check = _check(build_overview_checks(tuple(measures)), "net_profit_bridge")
    assert period_check.status == "non_comparable"
    assert "reporting_period_mismatch" in period_check.reason_codes
    assert period_check.variance is None


def test_different_policy_version_or_source_import_is_non_comparable() -> None:
    measures = list(_measures())
    measures[2] = measures[2].model_copy(update={"policy_version": 2, "policy_hash": "d" * 64})
    policy_check = _check(build_overview_checks(tuple(measures)), "gross_profit_bridge")
    assert policy_check.status == "non_comparable"
    assert "policy_version_mismatch" in policy_check.reason_codes
    assert policy_check.variance is None

    measures = list(_measures())
    alternate_source = _source().model_copy(
        update={"id": UUID("33333333-3333-4333-8333-333333333333")}
    )
    measures[2] = measures[2].model_copy(update={"source_imports": (alternate_source,)})
    source_check = _check(build_overview_checks(tuple(measures)), "gross_profit_bridge")
    assert source_check.status == "non_comparable"
    assert "source_import_mismatch" in source_check.reason_codes
    assert source_check.variance is None


def test_inconsistent_saved_values_produce_an_exact_nonzero_residual() -> None:
    measures = list(_measures())
    measures[2] = _measure("gross_profit", value="72")
    checks = build_overview_checks(tuple(measures))

    gross_bridge = _check(checks, "gross_profit_bridge")
    expense_bridge = _check(checks, "revenue_expenses_gross_profit")
    assert gross_bridge.status == "inconsistent"
    assert gross_bridge.variance == "-2"
    assert expense_bridge.status == "inconsistent"
    assert expense_bridge.variance == "-2"


def test_expenses_are_not_assumed_to_be_cost_of_sales() -> None:
    policy_formulas = dict(_FORMULAS)
    policy_formulas["expenses"] = (
        FormulaTerm(category="cost_of_sales", coefficient=1),
        FormulaTerm(category="operating_expense", coefficient=1),
        FormulaTerm(category="non_operating_expense", coefficient=1),
        FormulaTerm(category="tax_expense", coefficient=1),
    )
    measures = tuple(
        item.model_copy(update={"policy_formulas": policy_formulas}) for item in _measures()
    )

    check = _check(build_overview_checks(measures), "revenue_expenses_gross_profit")

    assert check.status == "unverifiable"
    assert check.policy_relation_supported is False
    assert "policy_does_not_define_expenses_as_gross_profit_cost_base" in check.reason_codes
    assert check.variance is None
