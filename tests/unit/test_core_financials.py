from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from businessflow.api.core_financial_routes import (
    readiness_router as core_financial_readiness_router,
)
from businessflow.api.core_financial_routes import (
    router as core_financial_router,
)
from businessflow.api.finance_source_limits import finance_source_request_body_limit
from businessflow.api.finance_source_upload_limit import _upload_body_limit
from businessflow.config import Settings
from businessflow.core_financials.adapters import PriorCanonicalRecord, normalize_source_rows
from businessflow.core_financials.calculations import (
    CanonicalFinancialFact,
    SourceImportState,
    calculate_financial_metrics,
)
from businessflow.core_financials.catalog import illustrative_policy_template
from businessflow.core_financials.contracts import (
    BANK_FIELDS,
    LEDGER_FIELDS,
    ControlSource,
    CoreFinancialImportMetadata,
    CoreFinancialMappingCreateRequest,
    CoreFinancialPolicyCreateRequest,
    CoreFinancialRunCreateRequest,
    CoreFinancialRunEvidenceResponse,
    MetricControl,
)
from businessflow.core_financials.service import ensure_core_financials_enabled
from businessflow.data_imports.parsers import parse_file
from businessflow.errors import ApiError

TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
POLICY_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
LEDGER_IMPORT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
BANK_IMPORT_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
REQUEST_START = date(2026, 11, 1)
REQUEST_END = date(2026, 11, 30)
PERIOD_START = date(2026, 1, 1)
PERIOD_END = date(2026, 12, 31)


def _policy(
    *,
    unknown_categories: tuple[str, ...] = (),
    not_applicable_categories: tuple[str, ...] = (),
) -> CoreFinancialPolicyCreateRequest:
    payload = illustrative_policy_template().model_dump(mode="json")
    payload["category_coverage"] = {
        category: (
            "unknown"
            if category in unknown_categories
            else "not_applicable"
            if category in not_applicable_categories
            else "complete"
        )
        for category in payload["category_coverage"]
    }
    for category in not_applicable_categories:
        payload["account_map"] = {
            account: rule
            for account, rule in payload["account_map"].items()
            if rule["category"] != category
        }
    return CoreFinancialPolicyCreateRequest.model_validate(payload)


def _import_state(
    import_id: UUID,
    source_type: str,
    *,
    period_start: date = PERIOD_START,
    period_end: date = PERIOD_END,
    period_completeness: str = "complete",
    quality_status: str = "passed",
    file_hash: str | None = None,
    source_archive_status: str = "retained",
) -> SourceImportState:
    return SourceImportState(
        id=import_id,
        source_key=f"source-{source_type}",
        source_type=source_type,  # type: ignore[arg-type]
        file_hash=file_hash or ("a" if source_type == "general_ledger" else "b") * 64,
        mapping_hash="c" * 64,
        mapping_version=1,
        policy_hash="d" * 64,
        period_start=period_start,
        period_end=period_end,
        period_completeness=period_completeness,
        quality_status=quality_status,
        accepted_rows=8,
        rejected_rows=0,
        duplicate_rows=0,
        source_archive_status=source_archive_status,
    )


def _fact(
    *,
    import_id: UUID,
    source_type: str,
    category: str | None,
    amount: str,
    row_number: int,
    transaction_date: date = date(2026, 11, 10),
    account_code: str | None = None,
    currency_code: str = "USD",
    event_type: str = "normal",
) -> CanonicalFinancialFact:
    return CanonicalFinancialFact(
        id=UUID(int=row_number),
        import_id=import_id,
        source_record_id=f"record-{row_number}",
        transaction_date=transaction_date,
        currency_code=currency_code,
        account_code=account_code,
        category=category,  # type: ignore[arg-type]
        event_type=event_type,
        signed_amount=amount,
        source_row_number=row_number,
        source_row_hash=f"{row_number:064x}",
        source_key=f"source-{source_type}",
        source_type=source_type,  # type: ignore[arg-type]
        source_file_hash=("a" if source_type == "general_ledger" else "b") * 64,
    )


def _facts(*, include_non_operating_income: bool = True) -> tuple[CanonicalFinancialFact, ...]:
    facts = [
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="1000",
            row_number=2,
            account_code="4000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue_reduction",
            amount="100",
            row_number=3,
            account_code="4090",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="cost_of_sales",
            amount="300",
            row_number=4,
            account_code="5000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="operating_expense",
            amount="200",
            row_number=5,
            account_code="6000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="non_operating_expense",
            amount="10",
            row_number=6,
            account_code="7100",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="tax_expense",
            amount="20",
            row_number=7,
            account_code="8000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="500",
            row_number=8,
            transaction_date=date(2026, 10, 31),
            account_code="4000",
        ),
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="750",
            row_number=9,
            account_code="operating-1",
        ),
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="-100",
            row_number=10,
            account_code="operating-1",
        ),
    ]
    if include_non_operating_income:
        facts.insert(
            4,
            _fact(
                import_id=LEDGER_IMPORT_ID,
                source_type="general_ledger",
                category="non_operating_income",
                amount="50",
                row_number=11,
                account_code="7000",
            ),
        )
    return tuple(facts)


def _run(
    *,
    policy: CoreFinancialPolicyCreateRequest | None = None,
    facts=None,
    ledger=None,
    bank=None,
    controls=(),
    requested_metrics=None,
    legacy_v2_replay=False,
):
    return calculate_financial_metrics(
        policy=policy or _policy(),
        policy_version=1,
        request_period_start=REQUEST_START,
        request_period_end=REQUEST_END,
        currency_code="USD",
        ledger_import=ledger
        if ledger is not None
        else _import_state(LEDGER_IMPORT_ID, "general_ledger"),
        bank_import=bank if bank is not None else _import_state(BANK_IMPORT_ID, "bank_activity"),
        facts=_facts() if facts is None else facts,
        controls=controls,
        requested_metrics=requested_metrics,
        legacy_v2_replay=legacy_v2_replay,
    )


def _metric_map(results):
    return {item.metric: item for item in results}


def test_policy_template_is_explicit_but_unknown_coverage_never_yields_authoritative_numbers() -> (
    None
):
    template = illustrative_policy_template()
    assert set(template.category_coverage.values()) == {"unknown"}
    assert template.cash_flow_policy is not None
    assert template.cash_flow_policy.transfer_treatment == "exclude_marked_transfers"
    results, _analysis, _lines, state = _run(policy=template)
    metrics = _metric_map(results)
    assert state == "incomplete"
    assert metrics["revenue"].status == "incomplete"
    assert metrics["revenue"].exact_value is None
    assert any(
        code.startswith("category_coverage_unknown:") for code in metrics["revenue"].reason_codes
    )


def test_policy_requires_explicit_coverage_and_complete_categories_need_accounts() -> None:
    payload = illustrative_policy_template().model_dump(mode="json")
    payload["category_coverage"].pop("tax_expense")
    with pytest.raises(ValidationError, match="explicitly classify every P&L category"):
        CoreFinancialPolicyCreateRequest.model_validate(payload)

    payload = illustrative_policy_template().model_dump(mode="json")
    payload["category_coverage"] = {
        category: "complete" for category in payload["category_coverage"]
    }
    payload["account_map"].pop("8000")
    with pytest.raises(ValidationError, match="complete category coverage requires"):
        CoreFinancialPolicyCreateRequest.model_validate(payload)


def test_complete_policy_calculates_signed_p_and_l_and_net_bank_movement_exactly() -> None:
    results, analysis, lines, state = _run()
    metrics = _metric_map(results)

    assert state == "calculated"
    assert {key: metrics[key].exact_value for key in metrics} == {
        "revenue": "900",
        "expenses": "530",
        "gross_profit": "600",
        "operating_profit": "400",
        "net_profit": "420",
        "cash_flow": "650",
    }
    assert metrics["revenue"].excluded_outside_period_count == 1
    assert metrics["cash_flow"].basis == "cash_movement"
    assert metrics["cash_flow"].included_record_count == 2
    assert metrics["revenue"].reconciliation.state == "not_provided"
    assert metrics["revenue"].reconciliation.arithmetic_comparison == "not_computed"
    assert len([line for line in lines if line.metric == "revenue"]) == 2
    assert any(statement.code == "cash_flow_scope" for statement in analysis)


@pytest.mark.parametrize(
    ("metric", "rows", "expected"),
    [
        (
            "revenue",
            (("revenue", "1000", "4000", "normal"), ("revenue_reduction", "100", "4090", "refund")),
            "900",
        ),
        (
            "expenses",
            (
                ("cost_of_sales", "300", "5000", "normal"),
                ("operating_expense", "200", "6000", "normal"),
                ("non_operating_expense", "10", "7100", "adjustment"),
                ("tax_expense", "20", "8000", "normal"),
            ),
            "530",
        ),
        (
            "gross_profit",
            (
                ("revenue", "1000", "4000", "normal"),
                ("revenue_reduction", "100", "4090", "refund"),
                ("cost_of_sales", "300", "5000", "normal"),
            ),
            "600",
        ),
        (
            "operating_profit",
            (
                ("revenue", "1000", "4000", "normal"),
                ("revenue_reduction", "100", "4090", "refund"),
                ("cost_of_sales", "300", "5000", "normal"),
                ("operating_expense", "200", "6000", "normal"),
            ),
            "400",
        ),
        (
            "net_profit",
            (
                ("revenue", "1000", "4000", "normal"),
                ("revenue_reduction", "100", "4090", "refund"),
                ("cost_of_sales", "300", "5000", "normal"),
                ("operating_expense", "200", "6000", "normal"),
                ("non_operating_income", "50", "7000", "normal"),
                ("non_operating_expense", "10", "7100", "normal"),
                ("tax_expense", "20", "8000", "normal"),
            ),
            "420",
        ),
        (
            "cash_flow",
            (
                (None, "750", "operating-1", "normal"),
                (None, "-100", "operating-1", "refund"),
                (None, "50", "operating-1", "transfer"),
            ),
            "650",
        ),
    ],
)
def test_independent_golden_dataset_for_each_measure(metric, rows, expected) -> None:
    source_type = "bank_activity" if metric == "cash_flow" else "general_ledger"
    import_id = BANK_IMPORT_ID if metric == "cash_flow" else LEDGER_IMPORT_ID
    facts = tuple(
        _fact(
            import_id=import_id,
            source_type=source_type,
            category=category,
            amount=amount,
            row_number=index + 20,
            account_code=account_code,
            event_type=event_type,
        )
        for index, (category, amount, account_code, event_type) in enumerate(rows)
    )

    results, _analysis, lines, state = _run(facts=facts, requested_metrics=(metric,))

    result = results[0]
    assert state == "calculated"
    assert result.metric == metric
    assert result.exact_value == expected
    assert result.reported_value == expected
    assert result.source_import_ids == (import_id,)
    if metric == "cash_flow":
        assert result.excluded_transfer_count == 1
        assert {line.fact.event_type for line in lines} == {"normal", "refund"}
    else:
        assert result.excluded_transfer_count == 0
        assert all(line.fact.source_type == "general_ledger" for line in lines)


def test_p_and_l_measures_reconcile_cross_metric_identities_but_cash_flow_remains_distinct() -> (
    None
):
    results, _analysis, _lines, state = _run()
    metrics = _metric_map(results)
    assert state == "calculated"
    assert metrics["gross_profit"].exact_value == (str(int(metrics["revenue"].exact_value) - 300))
    assert metrics["operating_profit"].exact_value == (
        str(int(metrics["gross_profit"].exact_value) - 200)
    )
    assert metrics["net_profit"].exact_value == (
        str(int(metrics["operating_profit"].exact_value) + 50 - 10 - 20)
    )
    assert metrics["expenses"].exact_value == "530"
    assert metrics["cash_flow"].exact_value == "650"
    assert metrics["cash_flow"].exact_value != metrics["net_profit"].exact_value
    assert metrics["cash_flow"].source_import_ids == (BANK_IMPORT_ID,)
    assert metrics["net_profit"].source_import_ids == (LEDGER_IMPORT_ID,)
    cash_flow_scope = next(item for item in _run()[1] if item.code == "cash_flow_scope")
    assert "Opening and closing balances are not reported or inferred" in cash_flow_scope.text
    assert not any("balance" in field for field in BANK_FIELDS)


def test_cash_flow_transfer_rule_is_explicit_and_unmatched_pnl_rows_never_supply_cash() -> None:
    facts = (
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="100",
            row_number=30,
            account_code="operating-1",
        ),
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="-40",
            row_number=31,
            account_code="operating-1",
            event_type="transfer",
        ),
    )
    excluded = _metric_map(_run(facts=facts, requested_metrics=("cash_flow",))[0])["cash_flow"]
    assert excluded.exact_value == "100"
    assert excluded.excluded_transfer_count == 1

    policy_payload = _policy().model_dump(mode="json")
    policy_payload["cash_flow_policy"]["transfer_treatment"] = (
        "include_marked_transfers_as_observed_movements"
    )
    included_policy = CoreFinancialPolicyCreateRequest.model_validate(policy_payload)
    included = _metric_map(
        _run(policy=included_policy, facts=facts, requested_metrics=("cash_flow",))[0]
    )["cash_flow"]
    assert included.exact_value == "60"
    assert included.excluded_transfer_count == 0

    pnl_only = (
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="5000",
            row_number=32,
            account_code="4000",
        ),
    )
    results, _analysis, lines, state = calculate_financial_metrics(
        policy=_policy(),
        policy_version=1,
        request_period_start=REQUEST_START,
        request_period_end=REQUEST_END,
        currency_code="USD",
        ledger_import=_import_state(LEDGER_IMPORT_ID, "general_ledger"),
        bank_import=None,
        facts=pnl_only,
        controls=(),
        requested_metrics=("cash_flow",),
    )
    cash_flow = results[0]
    assert state == "incomplete"
    assert cash_flow.status == "missing_source"
    assert cash_flow.exact_value is None
    assert "required_source_missing" in cash_flow.reason_codes
    assert lines == ()


def test_cash_flow_compares_arithmetic_without_claiming_independent_reconciliation() -> None:
    facts = (
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="100",
            row_number=35,
            account_code="operating-1",
        ),
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="-40",
            row_number=36,
            account_code="operating-1",
            event_type="transfer",
        ),
    )
    independent_control = MetricControl(
        metric="cash_flow",
        expected_value="100",
        currency_code="USD",
        source=ControlSource(
            source_id="separate-bank-close-control",
            source_hash="e" * 64,
            record_locator="control:row:1",
        ),
    )

    results, _analysis, lines, state = _run(
        facts=facts,
        controls=(independent_control,),
        requested_metrics=("cash_flow",),
    )
    cash_flow = results[0]
    assert state == "calculated"
    assert cash_flow.exact_value == "100"
    assert cash_flow.excluded_transfer_count == 1
    assert cash_flow.reconciliation.state == "not_verifiable"
    assert cash_flow.reconciliation.arithmetic_comparison == "within_tolerance"
    assert cash_flow.reconciliation.reason_codes == ("control_source_independence_not_verified",)
    assert cash_flow.reconciliation.source_independence == "caller_asserted_not_verified"
    assert [line.fact.event_type for line in lines] == ["normal"]

    overlapping_control = independent_control.model_copy(
        update={"source": independent_control.source.model_copy(update={"source_hash": "b" * 64})}
    )
    overlapped = _metric_map(
        _run(
            facts=facts,
            controls=(overlapping_control,),
            requested_metrics=("cash_flow",),
        )[0]
    )["cash_flow"].reconciliation
    assert overlapped.state == "not_verifiable"
    assert "control_source_overlaps_calculation_evidence" in overlapped.reason_codes


def test_source_currency_quality_period_and_cash_policy_gaps_withhold_values() -> None:
    mixed_currency_facts = (
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="100",
            row_number=40,
            account_code="4000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="100",
            row_number=41,
            account_code="4000",
            currency_code="EUR",
        ),
    )
    revenue = _metric_map(_run(facts=mixed_currency_facts, requested_metrics=("revenue",))[0])[
        "revenue"
    ]
    assert revenue.status == "incomplete"
    assert revenue.exact_value is None
    assert revenue.excluded_other_currency_count == 1
    assert "source_currency_mismatch_no_fx" in revenue.reason_codes

    mixed_bank_facts = (
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="100",
            row_number=42,
            account_code="operating-1",
        ),
        _fact(
            import_id=BANK_IMPORT_ID,
            source_type="bank_activity",
            category=None,
            amount="-10",
            row_number=43,
            account_code="operating-1",
            currency_code="EUR",
        ),
    )
    bank_cash_flow = _metric_map(_run(facts=mixed_bank_facts, requested_metrics=("cash_flow",))[0])[
        "cash_flow"
    ]
    assert bank_cash_flow.status == "incomplete"
    assert bank_cash_flow.exact_value is None
    assert bank_cash_flow.excluded_other_currency_count == 1
    assert "source_currency_mismatch_no_fx" in bank_cash_flow.reason_codes

    incomplete_bank = _import_state(
        BANK_IMPORT_ID,
        "bank_activity",
        period_completeness="partial",
        quality_status="incomplete",
        period_start=date(2026, 11, 15),
    )
    cash = _metric_map(_run(bank=incomplete_bank, requested_metrics=("cash_flow",))[0])["cash_flow"]
    assert cash.status == "incomplete"
    assert cash.exact_value is None
    assert {
        "source_period_completeness_not_asserted",
        "source_contains_rejected_or_duplicate_rows",
        "source_period_does_not_cover_requested_period",
    }.issubset(cash.reason_codes)

    legacy_policy_payload = _policy().model_dump(mode="json")
    legacy_policy_payload["cash_flow_policy"] = None
    legacy_policy = CoreFinancialPolicyCreateRequest.model_validate(legacy_policy_payload)
    legacy_cash = _metric_map(_run(policy=legacy_policy, requested_metrics=("cash_flow",))[0])[
        "cash_flow"
    ]
    assert legacy_cash.status == "incomplete"
    assert legacy_cash.exact_value is None
    assert "cash_flow_policy_missing" in legacy_cash.reason_codes


@pytest.mark.parametrize(
    "metric",
    ("revenue", "expenses", "gross_profit", "operating_profit", "net_profit", "cash_flow"),
)
def test_missing_required_source_withholds_each_measure(metric) -> None:
    results, _analysis, lines, state = calculate_financial_metrics(
        policy=_policy(),
        policy_version=1,
        request_period_start=REQUEST_START,
        request_period_end=REQUEST_END,
        currency_code="USD",
        ledger_import=(
            _import_state(LEDGER_IMPORT_ID, "general_ledger") if metric == "cash_flow" else None
        ),
        bank_import=(
            None if metric == "cash_flow" else _import_state(BANK_IMPORT_ID, "bank_activity")
        ),
        facts=(),
        controls=(),
        requested_metrics=(metric,),
    )

    result = results[0]
    assert state == "incomplete"
    assert result.status == "missing_source"
    assert result.exact_value is None
    assert result.reason_codes == ("required_source_missing",)
    assert result.source_import_ids == ()
    assert lines == ()


@pytest.mark.parametrize(
    ("metric", "unknown_category"),
    (
        ("revenue", "revenue"),
        ("expenses", "cost_of_sales"),
        ("gross_profit", "revenue"),
        ("operating_profit", "operating_expense"),
        ("net_profit", "tax_expense"),
    ),
)
def test_unknown_coverage_withholds_each_p_and_l_measure(metric, unknown_category) -> None:
    results, _analysis, _lines, state = _run(
        policy=_policy(unknown_categories=(unknown_category,)),
        requested_metrics=(metric,),
    )
    result = results[0]
    assert state == "incomplete"
    assert result.status == "incomplete"
    assert result.exact_value is None
    assert f"category_coverage_unknown:{unknown_category}" in result.reason_codes


def test_unknown_p_and_l_coverage_does_not_gate_bank_cash_flow() -> None:
    categories = tuple(illustrative_policy_template().category_coverage)
    results, _analysis, _lines, state = _run(
        policy=_policy(unknown_categories=categories),
        requested_metrics=("cash_flow",),
    )
    assert state == "calculated"
    assert results[0].exact_value == "650"


@pytest.mark.parametrize(
    "metric",
    ("revenue", "expenses", "gross_profit", "operating_profit", "net_profit", "cash_flow"),
)
def test_currency_mismatch_withholds_each_measure(metric) -> None:
    if metric == "cash_flow":
        facts = (
            _fact(
                import_id=BANK_IMPORT_ID,
                source_type="bank_activity",
                category=None,
                amount="100",
                row_number=50,
                account_code="operating-1",
            ),
            _fact(
                import_id=BANK_IMPORT_ID,
                source_type="bank_activity",
                category=None,
                amount="20",
                row_number=51,
                account_code="operating-1",
                currency_code="EUR",
            ),
        )
    else:
        category, account_code = (
            ("cost_of_sales", "5000") if metric == "expenses" else ("revenue", "4000")
        )
        facts = (
            _fact(
                import_id=LEDGER_IMPORT_ID,
                source_type="general_ledger",
                category=category,
                amount="100",
                row_number=50,
                account_code=account_code,
            ),
            _fact(
                import_id=LEDGER_IMPORT_ID,
                source_type="general_ledger",
                category=category,
                amount="20",
                row_number=51,
                account_code=account_code,
                currency_code="EUR",
            ),
        )

    results, _analysis, _lines, state = _run(facts=facts, requested_metrics=(metric,))
    result = results[0]
    assert state == "incomplete"
    assert result.status == "incomplete"
    assert result.exact_value is None
    assert result.excluded_other_currency_count == 1
    assert "source_currency_mismatch_no_fx" in result.reason_codes


@pytest.mark.parametrize(
    "metric",
    ("revenue", "expenses", "gross_profit", "operating_profit", "net_profit", "cash_flow"),
)
def test_source_period_coverage_withholds_each_measure(metric) -> None:
    source_start = date(2026, 11, 15)
    source = _import_state(
        BANK_IMPORT_ID if metric == "cash_flow" else LEDGER_IMPORT_ID,
        "bank_activity" if metric == "cash_flow" else "general_ledger",
        period_start=source_start,
    )
    results, _analysis, _lines, state = _run(
        bank=source if metric == "cash_flow" else None,
        ledger=source if metric != "cash_flow" else None,
        requested_metrics=(metric,),
    )
    result = results[0]
    assert state == "incomplete"
    assert result.status == "incomplete"
    assert result.exact_value is None
    assert "source_period_does_not_cover_requested_period" in result.reason_codes


def test_legacy_import_without_archived_bytes_cannot_supply_a_new_measure() -> None:
    legacy_import = _import_state(
        LEDGER_IMPORT_ID,
        "general_ledger",
        source_archive_status="legacy_unavailable",
    )

    results, analysis, lines, state = _run(ledger=legacy_import, requested_metrics=("revenue",))

    assert state == "incomplete"
    assert results[0].status == "incomplete"
    assert results[0].exact_value is None
    assert "original_source_artifact_not_retained" in results[0].reason_codes
    assert lines == ()
    assert any(item.code == "source_import_incomplete" for item in analysis)


@pytest.mark.parametrize(
    "metric",
    ("revenue", "expenses", "gross_profit", "operating_profit", "net_profit", "cash_flow"),
)
def test_wrong_source_type_withholds_each_measure(metric) -> None:
    wrong_source = _import_state(
        BANK_IMPORT_ID if metric == "cash_flow" else LEDGER_IMPORT_ID,
        "general_ledger" if metric == "cash_flow" else "bank_activity",
    )
    results, _analysis, _lines, state = _run(
        bank=wrong_source if metric == "cash_flow" else None,
        ledger=wrong_source if metric != "cash_flow" else None,
        requested_metrics=(metric,),
    )
    result = results[0]
    assert state == "incomplete"
    assert result.status == "incomplete"
    assert result.exact_value is None
    assert "required_source_type_mismatch" in result.reason_codes


def test_legacy_v2_replay_preserves_revenue_currency_exclusion_semantics() -> None:
    facts = (
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="100",
            row_number=44,
            account_code="4000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount="75",
            row_number=45,
            account_code="4000",
            currency_code="EUR",
        ),
    )

    v3_results, _analysis, _lines, v3_state = _run(
        facts=facts,
        requested_metrics=("revenue",),
    )
    legacy_results, _analysis, legacy_lines, legacy_state = _run(
        facts=facts,
        requested_metrics=("revenue",),
        legacy_v2_replay=True,
    )

    v3_revenue = v3_results[0]
    legacy_revenue = legacy_results[0]
    assert v3_state == "incomplete"
    assert v3_revenue.exact_value is None
    assert v3_revenue.excluded_other_currency_count == 1
    assert legacy_state == "calculated"
    assert legacy_revenue.exact_value == "100"
    assert legacy_revenue.excluded_other_currency_count == 1
    assert len(legacy_lines) == 1
    assert legacy_lines[0].fact.currency_code == "USD"

    with pytest.raises(ValueError, match="only for the Revenue measure"):
        _run(requested_metrics=("net_profit",), legacy_v2_replay=True)


def test_revenue_measure_keeps_exact_control_arithmetic_and_evidence() -> None:
    control = MetricControl(
        metric="revenue",
        expected_value="900",
        currency_code="USD",
        source=ControlSource(
            source_id="illustrative-control",
            source_hash="e" * 64,
            record_locator="csv:row:2/revenue_total",
        ),
    )
    results, analysis, lines, state = _run(
        requested_metrics=("revenue",),
        controls=(control,),
    )
    assert state == "calculated"
    assert len(results) == 1
    revenue = results[0]
    assert revenue.metric == "revenue"
    assert revenue.exact_value == "900"
    assert revenue.reported_value == "900"
    assert revenue.category_totals == {"revenue": "1000", "revenue_reduction": "100"}
    assert revenue.included_record_count == 2
    assert revenue.reconciliation.state == "not_verifiable"
    assert revenue.reconciliation.arithmetic_comparison == "within_tolerance"
    assert revenue.reconciliation.variance == "0"
    assert {line.contribution_amount for line in lines} == {"1000", "-100"}
    assert {line.metric for line in lines} == {"revenue"}
    assert not any(statement.code == "cash_flow_scope" for statement in analysis)


def test_revenue_formula_and_evidence_preserve_precision_beyond_default_decimal_context() -> None:
    large_amount = "123456789012345678901234567890.12"
    facts = (
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue",
            amount=large_amount,
            row_number=2,
            account_code="4000",
        ),
        _fact(
            import_id=LEDGER_IMPORT_ID,
            source_type="general_ledger",
            category="revenue_reduction",
            amount="0.01",
            row_number=3,
            account_code="4090",
        ),
    )

    results, _analysis, lines, state = _run(facts=facts, requested_metrics=("revenue",))

    revenue = results[0]
    assert state == "calculated"
    assert revenue.category_totals == {"revenue": large_amount, "revenue_reduction": "0.01"}
    assert revenue.exact_value == "123456789012345678901234567890.11"
    assert {line.contribution_amount for line in lines} == {large_amount, "-0.01"}


def test_run_api_contract_selects_measure_and_requires_its_matching_source() -> None:
    common = {
        "policy_id": POLICY_ID,
        "period_start": REQUEST_START,
        "period_end": REQUEST_END,
        "currency_code": "USD",
    }
    revenue = CoreFinancialRunCreateRequest(
        **common,
        ledger_import_id=LEDGER_IMPORT_ID,
    )
    assert revenue.measure == "revenue"
    assert revenue.ledger_import_id == LEDGER_IMPORT_ID
    assert revenue.bank_import_id is None

    for measure in ("expenses", "gross_profit", "operating_profit", "net_profit"):
        request = CoreFinancialRunCreateRequest(
            **common,
            measure=measure,
            ledger_import_id=LEDGER_IMPORT_ID,
        )
        assert request.measure == measure

    cash_flow = CoreFinancialRunCreateRequest(
        **common,
        measure="cash_flow",
        bank_import_id=BANK_IMPORT_ID,
    )
    assert cash_flow.bank_import_id == BANK_IMPORT_ID
    assert cash_flow.ledger_import_id is None

    with pytest.raises(ValidationError, match="cash_flow requires a bank import"):
        CoreFinancialRunCreateRequest(
            **common,
            measure="cash_flow",
            ledger_import_id=LEDGER_IMPORT_ID,
        )
    with pytest.raises(ValidationError, match="P&L measures require a ledger import"):
        CoreFinancialRunCreateRequest(
            **common,
            measure="net_profit",
            bank_import_id=BANK_IMPORT_ID,
        )
    with pytest.raises(ValidationError, match="cash_flow requires a bank import"):
        CoreFinancialRunCreateRequest(
            **common,
            measure="cash_flow",
            ledger_import_id=LEDGER_IMPORT_ID,
            bank_import_id=BANK_IMPORT_ID,
        )
    with pytest.raises(ValidationError, match="P&L measures require a ledger import"):
        CoreFinancialRunCreateRequest(
            **common,
            measure="revenue",
            ledger_import_id=LEDGER_IMPORT_ID,
            bank_import_id=BANK_IMPORT_ID,
        )
    with pytest.raises(ValidationError):
        CoreFinancialRunCreateRequest(**common)


def test_run_rejects_multiple_controls_for_the_same_measure() -> None:
    control = MetricControl(
        metric="revenue",
        expected_value="900",
        currency_code="USD",
        source=ControlSource(
            source_id="separate-control-export",
            source_hash="e" * 64,
            record_locator="control:row:1",
        ),
    )

    with pytest.raises(ValidationError, match="one reconciliation control per metric"):
        CoreFinancialRunCreateRequest(
            policy_id=POLICY_ID,
            ledger_import_id=LEDGER_IMPORT_ID,
            period_start=REQUEST_START,
            period_end=REQUEST_END,
            currency_code="USD",
            controls=(control, control),
        )
    with pytest.raises(ValueError, match="one reconciliation control per metric"):
        _run(controls=(control, control))


def test_not_applicable_formula_categories_contribute_zero_but_surprise_rows_abstain() -> None:
    policy = _policy(not_applicable_categories=("non_operating_income",))
    clean_facts = _facts(include_non_operating_income=False)
    results, _analysis, _lines, state = _run(policy=policy, facts=clean_facts)
    assert state == "calculated"
    assert _metric_map(results)["net_profit"].exact_value == "370"

    inconsistent_results, _analysis, _lines, inconsistent_state = _run(policy=policy)
    net = _metric_map(inconsistent_results)["net_profit"]
    assert inconsistent_state == "incomplete"
    assert net.status == "incomplete"
    assert "records_in_category_declared_not_applicable:non_operating_income" in net.reason_codes


def test_incomplete_source_period_withholds_metric_and_records_precise_reason() -> None:
    ledger = _import_state(
        LEDGER_IMPORT_ID,
        "general_ledger",
        period_start=date(2026, 11, 15),
    )
    results, _analysis, _lines, state = _run(ledger=ledger)
    revenue = _metric_map(results)["revenue"]
    assert state == "incomplete"
    assert revenue.status == "incomplete"
    assert revenue.exact_value is None
    assert "source_period_does_not_cover_requested_period" in revenue.reason_codes


def test_control_overlap_is_not_verified_and_control_arithmetic_is_labeled_only() -> None:
    overlapping = MetricControl(
        metric="revenue",
        expected_value="900",
        currency_code="USD",
        source=ControlSource(
            source_id="source-general_ledger",
            source_hash="e" * 64,
            record_locator="csv:row:2",
        ),
    )
    independent = MetricControl(
        metric="revenue",
        expected_value="900",
        currency_code="USD",
        source=ControlSource(
            source_id="separate-control-export",
            source_hash="e" * 64,
            record_hash="f" * 64,
            record_locator="control:row:1",
        ),
    )

    overlapped = _metric_map(_run(controls=(overlapping,))[0])["revenue"].reconciliation
    reconciled = _metric_map(_run(controls=(independent,))[0])["revenue"].reconciliation

    assert overlapped.state == "not_verifiable"
    assert "control_source_overlaps_calculation_evidence" in overlapped.reason_codes
    assert reconciled.state == "not_verifiable"
    assert reconciled.arithmetic_comparison == "within_tolerance"
    assert reconciled.variance == "0"
    assert reconciled.source_independence == "caller_asserted_not_verified"
    assert reconciled.reason_codes == ("control_source_independence_not_verified",)


@pytest.mark.parametrize(
    ("source_rows", "existing_ids", "expected_duplicates"),
    [
        (
            (
                "duplicate-1,2026-11-10,4000,0,100,USD,normal,",
                "duplicate-1,2026-11-11,4000,0,200,USD,normal,",
            ),
            set(),
            2,
        ),
        (
            ("duplicate-1,2026-11-10,4000,0,100,USD,normal,",),
            {"duplicate-1"},
            1,
        ),
    ],
    ids=("within-import", "prior-import"),
)
def test_duplicate_source_record_ids_are_quarantined(
    source_rows, existing_ids, expected_duplicates
) -> None:
    headers = tuple(f"ledger_column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    csv = ",".join(headers) + "\n" + "\n".join(source_rows) + "\n"
    sheet = parse_file("duplicate-ledger.csv", csv.encode(), max_rows=10).worksheets[0]

    rows = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
        existing_record_ids=existing_ids,
    )

    assert len(rows) == expected_duplicates
    assert {row.row_status for row in rows} == {"duplicate"}
    assert all("duplicate_source_record_id" in row.issues for row in rows)
    assert all(row.signed_amount is not None for row in rows)


def test_ledger_adapter_uses_explicit_mapping_normal_balance_and_declared_period() -> None:
    headers = tuple(f"column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    csv = (
        ",".join(headers)
        + "\n"
        + "sale-1,2026-11-10,4000,0,100,USD,normal,\n"
        + "sale-2,2027-01-01,4000,0,50,USD,normal,\n"
    )
    sheet = parse_file("ledger.csv", csv.encode(), max_rows=10).worksheets[0]
    mapping = CoreFinancialMappingCreateRequest(
        source_key="ledger-main",
        source_type="general_ledger",
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
    )

    rows = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key=mapping.source_key,
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=mapping.field_mapping,
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )

    assert rows[0].row_status == "accepted"
    assert rows[0].category == "revenue"
    assert rows[0].signed_amount == "100"
    assert rows[1].row_status == "rejected"
    assert "transaction_date_outside_declared_source_period" in rows[1].issues


def test_ledger_adapter_preserves_exact_large_amounts_and_signed_65_character_results() -> None:
    headers = tuple(f"column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    large_amount = "123456789012345678901234567890.12"
    maximum_amount = "9" * 64
    csv = (
        ",".join(headers)
        + "\n"
        + f"large-credit,2026-11-10,4000,0,{large_amount},USD,normal,\n"
        + f"large-debit,2026-11-11,4000,{maximum_amount},0,USD,normal,\n"
    )
    sheet = parse_file("large-ledger.csv", csv.encode(), max_rows=10).worksheets[0]

    rows = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )

    assert [row.row_status for row in rows] == ["accepted", "accepted"]
    assert rows[0].signed_amount == large_amount
    assert rows[1].signed_amount == f"-{maximum_amount}"
    assert len(rows[1].signed_amount) == 65


def test_bank_adapter_requires_bank_account_identity_and_preserves_cash_movement_sign() -> None:
    headers = tuple(f"bank_column_{index}" for index, _ in enumerate(BANK_FIELDS))
    csv = (
        ",".join(headers)
        + "\n"
        + "bank-1,2026-11-10,operating-1,75,0,USD,normal,\n"
        + "bank-2,2026-11-11,,0,25,USD,normal,\n"
        + f"bank-large-out,2026-11-12,operating-1,0,{'9' * 64},USD,normal,\n"
    )
    sheet = parse_file("bank.csv", csv.encode(), max_rows=10).worksheets[0]
    mapping = CoreFinancialMappingCreateRequest(
        source_key="bank-main",
        source_type="bank_activity",
        field_mapping=dict(zip(BANK_FIELDS, headers, strict=True)),
    )

    rows = normalize_source_rows(
        sheet,
        source_type="bank_activity",
        source_key=mapping.source_key,
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=mapping.field_mapping,
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )

    assert rows[0].row_status == "accepted"
    assert rows[0].account_code == "operating-1"
    assert rows[0].signed_amount == "75"
    assert rows[1].row_status == "rejected"
    assert "bank_account_id_invalid" in rows[1].issues
    assert rows[2].row_status == "accepted"
    assert rows[2].signed_amount == f"-{'9' * 64}"


def test_transfer_event_is_accepted_only_by_the_bank_adapter() -> None:
    bank_headers = tuple(f"bank_column_{index}" for index, _ in enumerate(BANK_FIELDS))
    bank_sheet = parse_file(
        "bank-transfer.csv",
        (
            ",".join(bank_headers)
            + "\n"
            + "bank-transfer,2026-11-10,operating-1,0,25,USD,transfer,\n"
        ).encode(),
        max_rows=10,
    ).worksheets[0]
    bank_rows = normalize_source_rows(
        bank_sheet,
        source_type="bank_activity",
        source_key="bank-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(BANK_FIELDS, bank_headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )
    assert bank_rows[0].row_status == "accepted"
    assert bank_rows[0].event_type == "transfer"

    ledger_headers = tuple(f"ledger_column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    ledger_sheet = parse_file(
        "ledger-transfer.csv",
        (
            ",".join(ledger_headers) + "\n" + "ledger-transfer,2026-11-10,4000,0,25,USD,transfer,\n"
        ).encode(),
        max_rows=10,
    ).worksheets[0]
    ledger_rows = normalize_source_rows(
        ledger_sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, ledger_headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )
    assert ledger_rows[0].row_status == "rejected"
    assert "event_type_invalid" in ledger_rows[0].issues


def test_reversal_must_be_exact_opposite_and_binds_to_policy_version() -> None:
    headers = tuple(f"ledger_column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    csv = (
        ",".join(headers)
        + "\n"
        + "sale-reversal,2026-11-12,4000,0,100,USD,reversal,sale-original\n"
    )
    sheet = parse_file("reversal.csv", csv.encode(), max_rows=10).worksheets[0]
    prior = PriorCanonicalRecord(
        source_record_id="sale-original",
        category="revenue",
        currency_code="USD",
        signed_amount="-100",
        account_code="4000",
        event_type="normal",
        reverses_record_id=None,
        policy_id=str(POLICY_ID),
    )
    rows = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
        prior_records={"sale-original": prior},
    )
    assert rows[0].row_status == "accepted"
    assert rows[0].signed_amount == "100"
    assert rows[0].reverses_record_id == "sale-original"

    wrong_policy = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
        policy=_policy(),
        policy_id="other-policy-version",
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
        prior_records={"sale-original": prior},
    )
    assert wrong_policy[0].row_status == "rejected"
    assert "reversal_original_missing_or_policy_version_mismatch" in wrong_policy[0].issues


def test_reversal_comparison_does_not_round_large_amounts_into_a_false_match() -> None:
    headers = tuple(f"ledger_column_{index}" for index, _ in enumerate(LEDGER_FIELDS))
    original_amount = "123456789012345678901234567890"
    mismatched_reversal = "123456789012345678901234567891"
    csv = (
        ",".join(headers)
        + "\n"
        + f"sale-original,2026-11-10,4000,0,{original_amount},USD,normal,\n"
        + f"sale-reversal,2026-11-12,4000,{mismatched_reversal},0,USD,reversal,sale-original\n"
    )
    sheet = parse_file("large-reversal.csv", csv.encode(), max_rows=10).worksheets[0]

    rows = normalize_source_rows(
        sheet,
        source_type="general_ledger",
        source_key="ledger-main",
        file_hash="a" * 64,
        mapping_hash="b" * 64,
        policy_hash="c" * 64,
        field_mapping=dict(zip(LEDGER_FIELDS, headers, strict=True)),
        policy=_policy(),
        policy_id=str(POLICY_ID),
        source_period_start=REQUEST_START,
        source_period_end=REQUEST_END,
    )

    assert rows[0].row_status == "accepted"
    assert rows[0].signed_amount == original_amount
    assert rows[1].row_status == "rejected"
    assert rows[1].signed_amount == f"-{mismatched_reversal}"
    assert "reversal_must_be_linked_exact_opposite_entry" in rows[1].issues


def test_core_financials_flag_defaults_off_and_cannot_be_enabled_in_production() -> None:
    base_settings = {
        "_env_file": None,
        "app_secret_key": "unit-test-key-" + "x" * 48,
        "database_url": "postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
    }
    disabled = Settings(**base_settings, app_env="test")
    assert disabled.core_financials_enabled is False
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_core_financials_enabled(disabled)

    enabled = Settings(**base_settings, app_env="test", core_financials_enabled=True)
    ensure_core_financials_enabled(enabled)

    production = Settings(
        _env_file=None,
        app_secret_key="production-key-" + "x" * 48,
        app_env="production",
        database_url="postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
        core_financials_enabled=True,
    )
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_core_financials_enabled(production)


def test_core_financial_routes_are_registered_and_import_body_limit_includes_metadata() -> None:
    app = FastAPI()
    app.include_router(core_financial_readiness_router, prefix="/api/v1")
    app.include_router(core_financial_router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "/api/v1/tenants/{tenant_id}/core-financials/template" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/imports" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/imports/{import_id}/source" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/readiness" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/runs/{run_id}/verify" in paths
    run_create = paths["/api/v1/tenants/{tenant_id}/core-financials/runs"]["post"]
    idempotency_parameter = next(
        parameter
        for parameter in run_create["parameters"]
        if parameter["name"] == "Idempotency-Key"
    )
    assert idempotency_parameter["in"] == "header"
    assert idempotency_parameter["required"] is True
    assert idempotency_parameter["schema"]["minLength"] == 16
    assert idempotency_parameter["schema"]["maxLength"] == 128

    maximum_file_bytes = 1024
    limit = _upload_body_limit(
        {
            "method": "POST",
            "path": "/api/v1/tenants/tenant-id/core-financials/imports",
            "root_path": "",
        },
        maximum_file_bytes,
    )
    assert limit is not None
    assert limit.max_bytes == finance_source_request_body_limit(maximum_file_bytes)
    assert "Core Financials" in limit.message


def test_evidence_contribution_allows_one_sign_character_beyond_bounded_source_amount() -> None:
    amount = "9" * 64
    evidence = CoreFinancialRunEvidenceResponse(
        id=uuid4(),
        metric="revenue",
        record_id=uuid4(),
        source_import_id=uuid4(),
        source_key="ledger-main",
        source_type="general_ledger",
        source_file_hash="a" * 64,
        source_row_number=2,
        source_row_hash="b" * 64,
        source_record_id="record-1",
        transaction_date=REQUEST_START,
        currency_code="USD",
        account_code="4000",
        category="revenue",
        event_type="normal",
        source_signed_amount=amount,
        formula_coefficient=-1,
        contribution_amount=f"-{amount}",
    )
    assert len(evidence.source_signed_amount) == 64
    assert len(evidence.contribution_amount) == 65


def test_import_metadata_requires_ordered_period_boundaries() -> None:
    with pytest.raises(ValidationError, match="period_end must not precede period_start"):
        CoreFinancialImportMetadata(
            source_key="ledger-main",
            source_type="general_ledger",
            mapping_id=uuid4(),
            policy_id=uuid4(),
            period_start=REQUEST_END,
            period_end=REQUEST_START,
            period_completeness="complete",
        )
