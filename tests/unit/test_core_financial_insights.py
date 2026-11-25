from __future__ import annotations

from datetime import date
from decimal import Decimal
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
from pydantic import ValidationError

from businessflow.core_financials.contracts import (
    FINANCIAL_MEASURES,
    CoreFinancialComparisonContributor,
    CoreFinancialComparisonFinding,
    CoreFinancialComparisonResponse,
    CoreFinancialComparisonRunReference,
    CoreFinancialInsightRequest,
    CoreFinancialOverviewSource,
    CoreFinancialPeriodSelection,
)
from businessflow.core_financials.insights import build_core_financial_insights

_TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_POLICY_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
_POLICY_HASH = "a" * 64
_ENGINE = "businessflow.core-financials.v3-shared-measures"
_GOLDEN_PERIODS = (
    CoreFinancialPeriodSelection(period_start=date(2026, 1, 1), period_end=date(2026, 1, 31)),
    CoreFinancialPeriodSelection(period_start=date(2026, 2, 1), period_end=date(2026, 2, 28)),
    CoreFinancialPeriodSelection(period_start=date(2026, 3, 1), period_end=date(2026, 3, 31)),
    CoreFinancialPeriodSelection(period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)),
)
# Independent golden values and expected M14 six-place percentages. These values are not
# produced by the financial calculator under test.
_GOLDEN_VALUES = {
    "revenue": ("980", "1020", "1100", "1650"),
    "expenses": ("400", "440", "500", "540"),
    "gross_profit": ("580", "590", "600", "1110"),
    "operating_profit": ("380", "380", "380", "900"),
    "net_profit": ("250", "260", "280", "430"),
    "cash_flow": ("500", "550", "570", "580"),
}
_GOLDEN_PERCENTAGES = {
    "revenue": ("4.081633", "7.843137", "50.000000"),
    "expenses": ("10.000000", "13.636364", "8.000000"),
    "gross_profit": ("1.724138", "1.694915", "85.000000"),
    "operating_profit": ("0.000000", "0.000000", "136.842105"),
    "net_profit": ("4.000000", "7.692308", "53.571429"),
    "cash_flow": ("10.000000", "3.636364", "1.754386"),
}
_GOLDEN_MARGIN_PERCENTAGES = {
    "gross_profit": ("59.183673", "57.843137", "54.545455", "67.272727"),
    "operating_profit": ("38.775510", "37.254902", "34.545455", "54.545455"),
    "net_profit": ("25.510204", "25.490196", "25.454545", "26.060606"),
}


def _hash(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _source(
    period_index: int,
    source_type: str,
    period: CoreFinancialPeriodSelection,
) -> CoreFinancialOverviewSource:
    import_id = uuid5(NAMESPACE_URL, f"m15-golden-import:{period_index}:{source_type}")
    return CoreFinancialOverviewSource(
        id=import_id,
        source_key=f"golden-{source_type}",
        source_type=source_type,
        file_name=f"golden-period-{period_index + 1}.csv",
        file_hash=_hash(f"source-file:{period_index}:{source_type}"),
        input_hash=_hash(f"source-input:{period_index}:{source_type}"),
        result_hash=_hash(f"source-result:{period_index}:{source_type}"),
        mapping_version=1,
        mapping_hash=_hash("golden-mapping"),
        policy_id=_POLICY_ID,
        policy_version=1,
        policy_hash=_POLICY_HASH,
        period_start=period.period_start,
        period_end=period.period_end,
        period_completeness="complete",
        completeness_authority="caller_asserted_not_verified",
        quality_status="passed",
        total_rows=7,
        accepted_rows=7,
        rejected_rows=0,
        duplicate_rows=0,
        import_url=f"/api/v1/tenants/{_TENANT_ID}/core-financials/imports/{import_id}",
    )


def _run_reference(
    period_index: int,
    measure: str,
    value: str,
    period: CoreFinancialPeriodSelection,
) -> CoreFinancialComparisonRunReference:
    source_type = "bank_activity" if measure == "cash_flow" else "general_ledger"
    source = _source(period_index, source_type, period)
    run_id = uuid5(NAMESPACE_URL, f"m15-golden-run:{period_index}:{measure}")
    return CoreFinancialComparisonRunReference(
        run_id=run_id,
        measure=measure,
        run_url=f"/api/v1/tenants/{_TENANT_ID}/core-financials/runs/{run_id}",
        evidence_url=(
            f"/api/v1/tenants/{_TENANT_ID}/core-financials/runs/{run_id}/evidence?metric={measure}"
        ),
        metric_status="calculated",
        exact_value=value,
        reported_value=value,
        period_start=period.period_start,
        period_end=period.period_end,
        currency_code="USD",
        basis="cash_movement" if measure == "cash_flow" else "accrual",
        run_state="calculated",
        engine_version=_ENGINE,
        policy_id=_POLICY_ID,
        policy_version=1,
        policy_hash=_POLICY_HASH,
        input_hash=_hash(f"run-input:{period_index}:{measure}"),
        result_hash=_hash(f"run-result:{period_index}:{measure}"),
        trace_hash=_hash(f"run-trace:{period_index}:{measure}"),
        evidence_count=7,
        source_import=source,
        category_totals={},
        formula_terms=(),
        reason_codes=(),
    )


def _contributors(
    measure: str,
    prior_run: CoreFinancialComparisonRunReference,
    current_run: CoreFinancialComparisonRunReference,
) -> list[CoreFinancialComparisonContributor]:
    if measure == "revenue":
        return [
            CoreFinancialComparisonContributor(
                category="revenue",
                prior_contribution="1130",
                current_contribution="1700",
                absolute_variance="570",
                direction="increase",
                prior_evidence_count=3,
                current_evidence_count=4,
                prior_evidence_url=f"{prior_run.evidence_url}&category=revenue",
                current_evidence_url=f"{current_run.evidence_url}&category=revenue",
            ),
            CoreFinancialComparisonContributor(
                category="revenue_reduction",
                prior_contribution="-30",
                current_contribution="-50",
                absolute_variance="-20",
                direction="decrease",
                prior_evidence_count=1,
                current_evidence_count=1,
                prior_evidence_url=f"{prior_run.evidence_url}&category=revenue_reduction",
                current_evidence_url=f"{current_run.evidence_url}&category=revenue_reduction",
            ),
        ]
    if measure == "expenses":
        return [
            CoreFinancialComparisonContributor(
                category="cost_of_sales",
                prior_contribution="280",
                current_contribution="330",
                absolute_variance="50",
                direction="increase",
                prior_evidence_count=2,
                current_evidence_count=3,
                prior_evidence_url=f"{prior_run.evidence_url}&category=cost_of_sales",
                current_evidence_url=f"{current_run.evidence_url}&category=cost_of_sales",
            ),
            CoreFinancialComparisonContributor(
                category="operating_expense",
                prior_contribution="200",
                current_contribution="190",
                absolute_variance="-10",
                direction="decrease",
                prior_evidence_count=2,
                current_evidence_count=1,
                prior_evidence_url=f"{prior_run.evidence_url}&category=operating_expense",
                current_evidence_url=f"{current_run.evidence_url}&category=operating_expense",
            ),
        ]
    return []


def golden_comparisons(
    periods: tuple[CoreFinancialPeriodSelection, ...] = _GOLDEN_PERIODS,
) -> tuple[CoreFinancialComparisonResponse, ...]:
    comparisons: list[CoreFinancialComparisonResponse] = []
    for pair_index in range(len(periods) - 1):
        findings: list[CoreFinancialComparisonFinding] = []
        for measure in FINANCIAL_MEASURES:
            prior_value = _GOLDEN_VALUES[measure][pair_index]
            current_value = _GOLDEN_VALUES[measure][pair_index + 1]
            absolute_variance = str(Decimal(current_value) - Decimal(prior_value))
            prior = _run_reference(pair_index, measure, prior_value, periods[pair_index])
            current = _run_reference(
                pair_index + 1, measure, current_value, periods[pair_index + 1]
            )
            margins = _GOLDEN_MARGIN_PERCENTAGES.get(measure)
            margin_status = "calculated" if margins else "not_applicable"
            prior_margin = margins[pair_index] if margins else None
            current_margin = margins[pair_index + 1] if margins else None
            margin_variance = (
                f"{Decimal(current_margin) - Decimal(prior_margin):.6f}"
                if prior_margin is not None and current_margin is not None
                else None
            )
            pair_contributors = (
                _contributors(measure, prior, current)
                if pair_index == len(periods) - 2 and measure in {"revenue", "expenses"}
                else []
            )
            if measure == "cash_flow":
                contributor_status = "not_applicable"
                contributor_reasons = ("cash_flow_category_contributors_not_supported",)
            elif measure in {"revenue", "expenses"} and pair_contributors:
                contributor_status = "calculated"
                contributor_reasons = ()
            elif measure in {"revenue", "expenses"}:
                contributor_status = "abstained"
                contributor_reasons = ("category_level_evidence_unavailable",)
            else:
                contributor_status = "calculated"
                contributor_reasons = ()
            findings.append(
                CoreFinancialComparisonFinding(
                    measure=measure,
                    disposition="calculated",
                    reason_codes=(),
                    reasons=(),
                    prior_run=prior,
                    current_run=current,
                    absolute_variance=absolute_variance,
                    percentage_variance=_GOLDEN_PERCENTAGES[measure][pair_index],
                    percentage_variance_status="calculated",
                    percentage_variance_reason_codes=(),
                    direction=(
                        "increase"
                        if Decimal(current_value) > Decimal(prior_value)
                        else "decrease"
                        if Decimal(current_value) < Decimal(prior_value)
                        else "unchanged"
                    ),
                    prior_margin=prior_margin,
                    current_margin=current_margin,
                    margin_variance_percentage_points=margin_variance,
                    margin_status=margin_status,
                    margin_reason_codes=(),
                    margin_caution_codes=(),
                    contributors_status=contributor_status,
                    contributors_reason_codes=contributor_reasons,
                    contributors=pair_contributors,
                )
            )
        comparisons.append(
            CoreFinancialComparisonResponse(
                tenant_id=_TENANT_ID,
                company_id=_COMPANY_ID,
                selection_rule="latest_persisted_run_per_measure_exact_period",
                prior_period=periods[pair_index],
                current_period=periods[pair_index + 1],
                period_alignment="equal_calendar_month_count",
                period_reason_codes=(),
                findings=tuple(findings),
                limitations=(),
                assumptions=(),
            )
        )
    return tuple(comparisons)


def _request(
    periods: tuple[CoreFinancialPeriodSelection, ...] = _GOLDEN_PERIODS,
    threshold: str = "10",
) -> CoreFinancialInsightRequest:
    return CoreFinancialInsightRequest(
        periods=periods,
        attention_threshold_percent=threshold,
    )


def test_independent_golden_dataset_builds_prioritized_evidence_backed_insights() -> None:
    comparisons = golden_comparisons()
    response = build_core_financial_insights(
        tenant_id=_TENANT_ID,
        company_id=_COMPANY_ID,
        request=_request(),
        comparisons=comparisons,
    )

    assert len(response.comparisons) == 3
    assert [
        item.measure for item in response.insights if item.kind == "profitability_analysis"
    ] == [
        "gross_profit",
        "operating_profit",
        "net_profit",
    ]
    drivers = {
        item.kind: item
        for item in response.insights
        if item.kind in {"revenue_driver", "expense_driver"}
    }
    assert drivers["revenue_driver"].title.endswith("revenue")
    assert drivers["expense_driver"].title.endswith("cost of sales")
    assert all(item.category == "revenue" for item in drivers["revenue_driver"].evidence)
    assert all(item.category == "cost_of_sales" for item in drivers["expense_driver"].evidence)
    assert "1130 to 1700 USD" in drivers["revenue_driver"].observed_facts[0].text
    assert "570 USD" in drivers["revenue_driver"].observed_facts[0].text
    assert "103.636364%" in drivers["revenue_driver"].calculations[0].text
    assert "280 to 330 USD" in drivers["expense_driver"].observed_facts[0].text
    assert "50 USD" in drivers["expense_driver"].observed_facts[0].text
    assert "125.000000%" in drivers["expense_driver"].calculations[0].text
    gross_profit = next(
        item
        for item in response.insights
        if item.kind == "profitability_analysis" and item.measure == "gross_profit"
    )
    assert "600 to 1110 USD" in gross_profit.observed_facts[0].text
    assert any("12.727272 percentage points" in item.text for item in gross_profit.calculations)

    attention = [item.measure for item in response.insights if item.kind == "attention_movement"]
    assert attention == ["operating_profit", "gross_profit"]
    outliers = [item for item in response.insights if item.kind == "potential_outlier"]
    assert [item.measure for item in outliers] == ["gross_profit"]
    assert "510 USD" in outliers[0].observed_facts[0].text
    assert "5100.000000%" in outliers[0].calculations[0].text

    for insight in response.insights:
        assert insight.hypotheses == ()
        assert insight.recommendations
        assert insight.expected_impact is None
        assert insight.expected_impact_status == "not_estimated"
        assert insight.expected_impact_reason_codes == (
            "historical_variance_is_not_a_defensible_impact_estimate",
        )
        assert insight.evidence
        for statement in (
            *insight.observed_facts,
            *insight.calculations,
            *insight.recommendations,
        ):
            assert statement.evidence
            assert all(len(reference.source_file_hash) == 64 for reference in statement.evidence)
            assert all(len(reference.run_input_hash) == 64 for reference in statement.evidence)
            assert all(len(reference.run_result_hash) == 64 for reference in statement.evidence)
            assert all(len(reference.run_trace_hash) == 64 for reference in statement.evidence)

    revenue_assessment = next(
        item
        for item in response.assessments
        if item.kind == "revenue_driver" and item.measure == "revenue"
    )
    assert revenue_assessment.status == "insight_generated"
    operating_outlier = next(
        item
        for item in response.assessments
        if item.kind == "potential_outlier" and item.measure == "operating_profit"
    )
    assert operating_outlier.status == "abstained"
    assert "zero_historical_movement_baseline" in operating_outlier.reason_codes
    assert any("not a proof of anomaly or cause" in item for item in response.limitations)


def test_limited_history_incompatible_contexts_and_missing_categories_abstain() -> None:
    two_periods = _GOLDEN_PERIODS[:2]
    comparisons = golden_comparisons(two_periods)
    response = build_core_financial_insights(
        tenant_id=_TENANT_ID,
        company_id=_COMPANY_ID,
        request=_request(two_periods),
        comparisons=comparisons,
    )
    outlier_assessments = [
        item for item in response.assessments if item.kind == "potential_outlier"
    ]
    assert len(outlier_assessments) == len(FINANCIAL_MEASURES)
    assert all(item.status == "abstained" for item in outlier_assessments)
    assert all("insufficient_history" in item.reason_codes for item in outlier_assessments)

    latest = golden_comparisons()[-1]
    replaced_findings = []
    for finding in latest.findings:
        if finding.measure == "revenue":
            replaced_findings.append(
                finding.model_copy(
                    update={
                        "disposition": "abstained",
                        "reason_codes": ("currency_mismatch_no_fx",),
                        "absolute_variance": None,
                        "percentage_variance": None,
                        "percentage_variance_status": "abstained",
                        "percentage_variance_reason_codes": ("comparison_not_comparable",),
                        "direction": None,
                        "contributors_status": "abstained",
                        "contributors_reason_codes": ("currency_mismatch_no_fx",),
                        "contributors": [],
                    }
                )
            )
        elif finding.measure == "expenses":
            replaced_findings.append(
                finding.model_copy(
                    update={
                        "contributors_status": "abstained",
                        "contributors_reason_codes": ("category_coverage_not_complete",),
                        "contributors": [],
                    }
                )
            )
        elif finding.measure == "gross_profit":
            replaced_findings.append(
                finding.model_copy(
                    update={
                        "disposition": "abstained",
                        "reason_codes": ("current_source_period_completeness_not_asserted",),
                        "absolute_variance": None,
                        "percentage_variance": None,
                        "percentage_variance_status": "abstained",
                        "percentage_variance_reason_codes": ("comparison_not_comparable",),
                        "direction": None,
                    }
                )
            )
        elif finding.measure == "cash_flow":
            replaced_findings.append(
                finding.model_copy(
                    update={
                        "percentage_variance": None,
                        "percentage_variance_status": "abstained",
                        "percentage_variance_reason_codes": (
                            "zero_prior_value_percentage_unavailable",
                        ),
                    }
                )
            )
        else:
            replaced_findings.append(finding)
    incompatible_latest = latest.model_copy(update={"findings": tuple(replaced_findings)})
    incompatible_comparisons = (*golden_comparisons()[:-1], incompatible_latest)
    incompatible = build_core_financial_insights(
        tenant_id=_TENANT_ID,
        company_id=_COMPANY_ID,
        request=_request(),
        comparisons=incompatible_comparisons,
    )
    assert not any(item.kind == "revenue_driver" for item in incompatible.insights)
    assert not any(
        item.kind == "attention_movement" and item.measure == "revenue"
        for item in incompatible.insights
    )
    assert any(
        item.kind == "revenue_driver"
        and item.measure == "revenue"
        and item.status == "abstained"
        and "currency_mismatch_no_fx" in item.reason_codes
        for item in incompatible.assessments
    )
    assert any(
        item.kind == "expense_driver"
        and item.measure == "expenses"
        and item.status == "abstained"
        and "category_coverage_not_complete" in item.reason_codes
        for item in incompatible.assessments
    )
    revenue_outlier = next(
        item
        for item in incompatible.assessments
        if item.kind == "potential_outlier" and item.measure == "revenue"
    )
    assert revenue_outlier.status == "abstained"
    assert "currency_mismatch_no_fx" in revenue_outlier.reason_codes
    incomplete_profit = next(
        item
        for item in incompatible.assessments
        if item.kind == "profitability_analysis" and item.measure == "gross_profit"
    )
    assert incomplete_profit.status == "abstained"
    assert "current_source_period_completeness_not_asserted" in incomplete_profit.reason_codes
    zero_prior_attention = next(
        item
        for item in incompatible.assessments
        if item.kind == "attention_movement" and item.measure == "cash_flow"
    )
    assert zero_prior_attention.status == "abstained"
    assert "zero_prior_value_percentage_unavailable" in zero_prior_attention.reason_codes


def test_second_independent_golden_dataset_preserves_negative_baseline_direction() -> None:
    periods = _GOLDEN_PERIODS[:2]
    comparison = golden_comparisons(periods)[0]
    findings = []
    for finding in comparison.findings:
        if finding.measure != "revenue":
            findings.append(finding)
            continue
        assert finding.prior_run is not None and finding.current_run is not None
        findings.append(
            finding.model_copy(
                update={
                    "prior_run": finding.prior_run.model_copy(
                        update={"exact_value": "-100", "reported_value": "-100"}
                    ),
                    "current_run": finding.current_run.model_copy(
                        update={"exact_value": "-50", "reported_value": "-50"}
                    ),
                    "absolute_variance": "50",
                    "percentage_variance": "50.000000",
                    "direction": "increase",
                }
            )
        )
    negative_comparison = comparison.model_copy(update={"findings": tuple(findings)})
    response = build_core_financial_insights(
        tenant_id=_TENANT_ID,
        company_id=_COMPANY_ID,
        request=_request(periods),
        comparisons=(negative_comparison,),
    )
    revenue_attention = next(
        item
        for item in response.insights
        if item.kind == "attention_movement" and item.measure == "revenue"
    )
    assert "changed from -100 to -50 USD" in revenue_attention.observed_facts[0].text
    assert "50.000000%" in revenue_attention.observed_facts[0].text
    assert "increase" in revenue_attention.observed_facts[0].text
    assert (
        "does not classify the movement as favorable or unfavorable"
        in revenue_attention.calculations[0].text
    )


def test_non_contiguous_selected_periods_abstain_from_outlier_detection() -> None:
    gapped_periods = (
        _GOLDEN_PERIODS[0],
        _GOLDEN_PERIODS[1],
        CoreFinancialPeriodSelection(period_start=date(2026, 4, 1), period_end=date(2026, 4, 30)),
        CoreFinancialPeriodSelection(period_start=date(2026, 5, 1), period_end=date(2026, 5, 31)),
    )
    response = build_core_financial_insights(
        tenant_id=_TENANT_ID,
        company_id=_COMPANY_ID,
        request=_request(gapped_periods),
        comparisons=golden_comparisons(gapped_periods),
    )
    assessments = [item for item in response.assessments if item.kind == "potential_outlier"]
    assert len(assessments) == len(FINANCIAL_MEASURES)
    assert all(item.status == "abstained" for item in assessments)
    assert all("non_contiguous_history" in item.reason_codes for item in assessments)


def test_insight_request_bounds_threshold_period_count_and_order() -> None:
    valid = _request(threshold="0")
    assert valid.attention_threshold_percent == "0"
    with pytest.raises(ValidationError, match="between 0 and 100"):
        _request(threshold="100.0001")
    with pytest.raises(ValidationError, match="periods must be ordered"):
        _request((_GOLDEN_PERIODS[1], _GOLDEN_PERIODS[0]))
    with pytest.raises(ValidationError, match="at least 2 items"):
        _request((_GOLDEN_PERIODS[0],))
    with pytest.raises(ValidationError, match="at most 6 items"):
        _request((*_GOLDEN_PERIODS, *_GOLDEN_PERIODS[:3]))
