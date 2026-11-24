from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from pydantic import ValidationError

from businessflow.api.core_financial_routes import router as core_financial_router
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
) -> CanonicalFinancialFact:
    return CanonicalFinancialFact(
        id=UUID(int=row_number),
        import_id=import_id,
        source_record_id=f"record-{row_number}",
        transaction_date=transaction_date,
        currency_code="USD",
        account_code=account_code,
        category=category,  # type: ignore[arg-type]
        event_type="normal",
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
    )


def _metric_map(results):
    return {item.metric: item for item in results}


def test_policy_template_is_explicit_but_unknown_coverage_never_yields_authoritative_numbers() -> (
    None
):
    template = illustrative_policy_template()
    assert set(template.category_coverage.values()) == {"unknown"}
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
    assert len([line for line in lines if line.metric == "revenue"]) == 2
    assert any(statement.code == "cash_flow_scope" for statement in analysis)


def test_first_persisted_workflow_calculates_only_revenue_with_exact_control_and_evidence() -> None:
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
    assert revenue.reconciliation.state == "passed"
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


def test_run_api_contract_is_revenue_only_and_requires_general_ledger_input() -> None:
    request = CoreFinancialRunCreateRequest(
        policy_id=POLICY_ID,
        ledger_import_id=LEDGER_IMPORT_ID,
        period_start=REQUEST_START,
        period_end=REQUEST_END,
        currency_code="USD",
    )
    assert request.measure == "revenue"
    assert "bank_import_id" not in request.model_dump(mode="json")
    assert "bank_import_id" not in CoreFinancialRunCreateRequest.model_fields

    with pytest.raises(ValidationError):
        CoreFinancialRunCreateRequest(
            policy_id=POLICY_ID,
            measure="net_profit",
            ledger_import_id=LEDGER_IMPORT_ID,
            period_start=REQUEST_START,
            period_end=REQUEST_END,
            currency_code="USD",
        )
    with pytest.raises(ValidationError):
        CoreFinancialRunCreateRequest(
            policy_id=POLICY_ID,
            ledger_import_id=LEDGER_IMPORT_ID,
            bank_import_id=BANK_IMPORT_ID,
            period_start=REQUEST_START,
            period_end=REQUEST_END,
            currency_code="USD",
        )


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


def test_control_overlap_is_not_verified_and_independent_control_reconciles_exactly() -> None:
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
    assert reconciled.state == "passed"
    assert reconciled.variance == "0"
    assert reconciled.source_independence == "caller_asserted_not_verified"


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
    app.include_router(core_financial_router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "/api/v1/tenants/{tenant_id}/core-financials/template" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/imports" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/runs/{run_id}/verify" in paths

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
