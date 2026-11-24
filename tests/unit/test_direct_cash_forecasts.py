from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from businessflow.config import Settings
from businessflow.direct_cash_forecasts.calculation import (
    calculate_direct_cash_forecast,
    direct_cash_input_snapshot_hash,
    direct_cash_result_snapshot_hash,
)
from businessflow.direct_cash_forecasts.catalog import (
    DIRECT_CASH_FORECAST_CONTRACT_DEFINITION,
    DIRECT_CASH_FORECAST_FORMULA_HASH,
    DIRECT_CASH_FORECAST_HORIZON_WEEKS,
)
from businessflow.direct_cash_forecasts.commitments import (
    direct_cash_amount_input_fingerprint,
)
from businessflow.direct_cash_forecasts.contracts import (
    DirectCashForecastInputItem,
    DirectCashForecastInputSnapshot,
    DirectCashForecastRequest,
    DirectCashMovementRequest,
    DirectCashOpeningRequest,
    FinanceSourceEvidenceReference,
    FinanceSourceIntakeReference,
    FinancialReconciliationGroupReference,
)
from businessflow.direct_cash_forecasts.service import (
    _input_snapshot_from_json_value,
    _result_snapshot_from_json_value,
    direct_cash_forecast_formula_hash,
    ensure_direct_cash_forecasts_enabled,
)
from businessflow.errors import ApiError

TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
INTAKE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SNAPSHOT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
CAPTURED_AT = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
AS_OF_DATE = date(2026, 11, 21)
SOURCE_AS_OF_AT = CAPTURED_AT - timedelta(minutes=10)
SECRET = b"unit-test-only-stable-secret-material-not-production"
SOURCE_KEY = "synthetic-m16-source"
FILE_HASH = "f" * 64
MAPPING_HASH = "a" * 64
GROUP_HASH = "b" * 64


def _intake_reference() -> FinanceSourceIntakeReference:
    return FinanceSourceIntakeReference(
        schema_version=1,
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        intake_id=INTAKE_ID,
        source_key=SOURCE_KEY,
        file_hash=FILE_HASH,
        input_hash="1" * 64,
        result_hash="2" * 64,
        contract_key="businessflow.finance.source-observations",
        contract_version="1.0.0",
        contract_hash="3" * 64,
        mapping_version=1,
        mapping_hash=MAPPING_HASH,
        quality_status="passed",
        freshness_status="fresh",
        source_as_of_at=SOURCE_AS_OF_AT,
        freshness_max_age_hours=24,
        reconciliation_status="passed",
    )


def _evidence(row_number: int) -> FinanceSourceEvidenceReference:
    return FinanceSourceEvidenceReference(
        schema_version=1,
        row_evidence_id=UUID(int=row_number),
        source_row_number=row_number,
        source_row_hash=f"{row_number:064x}",
        source_record_identity_hash=f"{row_number + 1:064x}",
        reconciliation_group_hash=GROUP_HASH,
        amount_commitment_hash=f"{row_number + 2:064x}",
        amount_commitment_key_version="app-secret.v1",
        row_status="valid",
    )


def _input_item(
    *,
    input_id: str,
    row_number: int,
    origin: str = "movement",
    cash_flow_type: str = "receipt",
    basis: str | None = "actual",
    cash_date: date | None = AS_OF_DATE,
    amount_status: str = "verified",
    supersedes: int | None = None,
    issues: tuple[str, ...] = (),
) -> DirectCashForecastInputItem:
    return DirectCashForecastInputItem(
        schema_version=1,
        input_id=input_id,
        origin=origin,
        source_row_number=row_number,
        amount_fingerprint=f"{row_number + 3:064x}",
        amount_fingerprint_key_version="app-secret.v1",
        amount_commitment_status=amount_status,
        cash_flow_type=cash_flow_type,
        basis=basis,
        cash_date=cash_date,
        supersedes_source_row_number=supersedes,
        evidence=_evidence(row_number),
        issue_codes=issues,
    )


def _snapshot_and_amounts(
    movements: tuple[DirectCashForecastInputItem, ...],
    *,
    opening_amount: Decimal | None = Decimal("100.50"),
    opening_status: str = "verified",
    opening_issues: tuple[str, ...] = (),
    source_quality: str = "passed",
    source_freshness: str = "fresh",
    source_reconciliation: str = "passed",
    source_evidence_integrity: str = "verified",
    company_timezone: str = "UTC",
) -> tuple[DirectCashForecastInputSnapshot, dict[str, Decimal]]:
    opening = (
        _input_item(
            input_id="opening-cash",
            row_number=2,
            origin="opening_cash",
            cash_flow_type="opening_cash",
            basis=None,
            cash_date=None,
            amount_status=opening_status,
            issues=opening_issues,
        )
        if opening_amount is not None or opening_status != "not_submitted"
        else None
    )
    row_numbers = {item.source_row_number for item in movements}
    if opening is not None:
        row_numbers.add(opening.source_row_number)
    snapshot = DirectCashForecastInputSnapshot(
        schema_version=1,
        snapshot_id=SNAPSHOT_ID,
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        captured_at=CAPTURED_AT,
        as_of_date=AS_OF_DATE,
        company_timezone=company_timezone,
        horizon_weeks=13,
        intake=_intake_reference().model_copy(
            update={
                "quality_status": source_quality,
                "freshness_status": source_freshness,
                "reconciliation_status": source_reconciliation,
            }
        ),
        requested_group_hash=GROUP_HASH,
        source_evidence_integrity_status=source_evidence_integrity,
        group=FinancialReconciliationGroupReference(
            schema_version=1,
            reconciliation_group_hash=GROUP_HASH,
            period_start=date(2026, 11, 1),
            period_end=date(2026, 11, 30),
            currency_code="USD",
            source_row_count=len(row_numbers),
        ),
        opening_cash=opening,
        movements=movements,
    )
    amounts = {
        item.input_id: Decimal(0)
        for item in movements
        if item.amount_commitment_status == "verified" and not item.issue_codes
    }
    if opening is not None and opening_status == "verified" and not opening_issues:
        amounts[opening.input_id] = opening_amount or Decimal(0)
    return snapshot, amounts


def _complete_forecast(
    *,
    receipt_actual_week_one: Decimal = Decimal(0),
) -> tuple[DirectCashForecastInputSnapshot, dict[str, Decimal]]:
    movements: list[DirectCashForecastInputItem] = []
    amounts: dict[str, Decimal] = {}
    row_number = 3
    for week in range(DIRECT_CASH_FORECAST_HORIZON_WEEKS):
        cash_date = AS_OF_DATE + timedelta(days=7 * week)
        for direction in ("receipt", "disbursement"):
            for basis in ("actual", "forecast"):
                input_id = f"week-{week + 1:02d}-{direction}-{basis}"
                movements.append(
                    _input_item(
                        input_id=input_id,
                        row_number=row_number,
                        cash_flow_type=direction,
                        basis=basis,
                        cash_date=cash_date,
                    )
                )
                amount = (
                    receipt_actual_week_one
                    if week == 0 and direction == "receipt" and basis == "actual"
                    else Decimal(0)
                )
                amounts[input_id] = amount
                row_number += 1
    snapshot, opening_amounts = _snapshot_and_amounts(tuple(movements))
    return snapshot, {**opening_amounts, **amounts}


def test_contract_catalog_is_versioned_and_horizon_is_13_contiguous_weeks() -> None:
    assert DIRECT_CASH_FORECAST_HORIZON_WEEKS == 13
    assert direct_cash_forecast_formula_hash() == DIRECT_CASH_FORECAST_FORMULA_HASH
    assert DIRECT_CASH_FORECAST_CONTRACT_DEFINITION["formula"]["expression"] == (
        "closing_cash = opening_cash + receipts - disbursements"
    )
    snapshot, amounts = _complete_forecast()
    result = calculate_direct_cash_forecast(snapshot, amounts)
    assert result.calculation_state == "calculated"
    assert result.forecast_completeness_status == "not_established_by_m14"
    assert result.horizon_end_date == AS_OF_DATE + timedelta(days=90)
    assert len(result.weeks) == 13
    assert result.weeks[0].period_start == AS_OF_DATE
    assert result.weeks[-1].period_end == result.horizon_end_date
    assert [week.week_number for week in result.weeks] == list(range(1, 14))


def test_complete_itemized_forecast_carries_exact_opening_balance_without_rounding() -> None:
    snapshot, amounts = _complete_forecast(receipt_actual_week_one=Decimal("0.005"))
    result = calculate_direct_cash_forecast(snapshot, amounts)

    assert result.calculation_state == "calculated"
    assert result.opening_cash_status == "known"
    assert result.opening_cash_source_row_number == 2
    assert result.currency_code == "USD"
    assert result.weeks[0].opening_cash == "100.5"
    assert result.weeks[0].actual_receipts.known_subtotal == "0.005"
    assert result.weeks[0].receipts_known_subtotal == "0.005"
    assert result.weeks[0].closing_cash_known_item_rollforward == "100.505"
    assert result.weeks[1].opening_cash == "100.505"
    assert result.weeks[-1].closing_cash_known_item_rollforward == "100.505"
    assert all(week.closing_cash_status == "known_item_rollforward" for week in result.weeks)
    assert result.governance_status.production_customer_data_authorization == "not_authorized"
    assert "cash_semantics_not_authenticated_by_m14" in result.reason_codes


def test_absent_cash_categories_are_unknown_and_not_zero_filled() -> None:
    movement = _input_item(
        input_id="receipt-actual-week-one",
        row_number=3,
        cash_flow_type="receipt",
        basis="actual",
        cash_date=AS_OF_DATE,
    )
    snapshot, amounts = _snapshot_and_amounts((movement,))
    amounts[movement.input_id] = Decimal("4.25")

    result = calculate_direct_cash_forecast(snapshot, amounts)

    assert result.calculation_state == "incomplete"
    assert result.weeks[0].actual_receipts.status == "known_subtotal"
    assert result.weeks[0].actual_receipts.known_subtotal == "4.25"
    assert result.weeks[0].forecast_receipts.status == "unknown"
    assert result.weeks[0].forecast_receipts.known_subtotal is None
    assert result.weeks[0].closing_cash_known_item_rollforward is None
    assert "cash_category_has_no_explicit_evidence" in result.reason_codes


def test_missing_opening_cash_and_missing_rows_remain_unresolved() -> None:
    omitted = _input_item(
        input_id="missing-row-3",
        row_number=3,
        origin="missing_source_row",
        cash_flow_type="unknown",
        basis="unknown",
        cash_date=None,
        amount_status="not_submitted",
        issues=("cash_item_missing_from_request",),
    )
    snapshot, amounts = _snapshot_and_amounts(
        (omitted,),
        opening_amount=None,
        opening_status="not_submitted",
    )

    result = calculate_direct_cash_forecast(snapshot, amounts)

    assert result.calculation_state == "incomplete"
    assert result.opening_cash_status == "missing_or_invalid"
    assert result.weeks[0].opening_cash is None
    assert result.item_dispositions[0].status == "missing_request"
    assert "opening_cash_missing_or_invalid" in result.reason_codes
    assert "cash_item_missing_from_request" in result.reason_codes


def test_overdue_forecast_and_negative_flow_are_visible_and_block_rollforward() -> None:
    snapshot, amounts = _complete_forecast()
    overdue_row = 100
    overdue = _input_item(
        input_id="overdue-receipt",
        row_number=overdue_row,
        cash_flow_type="receipt",
        basis="forecast",
        cash_date=AS_OF_DATE - timedelta(days=1),
    )
    negative_row = 101
    negative = _input_item(
        input_id="negative-disbursement",
        row_number=negative_row,
        cash_flow_type="disbursement",
        basis="actual",
        cash_date=AS_OF_DATE,
    )
    snapshot, amounts = _snapshot_and_amounts(
        (*snapshot.movements, overdue, negative),
        opening_amount=Decimal("100.50"),
    )
    amounts.update({item.input_id: Decimal("1") for item in snapshot.movements})
    amounts[overdue.input_id] = Decimal("2")
    amounts[negative.input_id] = Decimal("-3")

    result = calculate_direct_cash_forecast(snapshot, amounts)
    dispositions = {item.input_id: item for item in result.item_dispositions}

    assert result.calculation_state == "incomplete"
    assert dispositions[overdue.input_id].status == "overdue_forecast_unresolved"
    assert dispositions[negative.input_id].status == "invalid_evidence"
    assert "overdue_forecast_item" in result.reason_codes
    assert "negative_flow_magnitude" in result.reason_codes
    assert result.weeks[0].closing_cash_known_item_rollforward is None


def test_explicit_actual_supersession_excludes_old_row_once() -> None:
    snapshot, amounts = _complete_forecast(receipt_actual_week_one=Decimal("5"))
    old_item = next(
        item
        for item in snapshot.movements
        if item.cash_flow_type == "receipt"
        and item.basis == "actual"
        and item.cash_date == AS_OF_DATE
    )
    replacement = _input_item(
        input_id="replacement-receipt",
        row_number=55,
        cash_flow_type="receipt",
        basis="actual",
        cash_date=AS_OF_DATE,
        supersedes=old_item.source_row_number,
    )
    snapshot, amounts = _snapshot_and_amounts((*snapshot.movements, replacement))
    amounts.update(
        {
            item.input_id: (
                Decimal("5") if item.source_row_number == old_item.source_row_number else Decimal(0)
            )
            for item in snapshot.movements
        }
    )
    amounts[replacement.input_id] = Decimal("8")

    result = calculate_direct_cash_forecast(snapshot, amounts)
    dispositions = {item.input_id: item for item in result.item_dispositions}

    assert result.calculation_state == "calculated"
    assert dispositions[old_item.input_id].status == "superseded_actual_excluded"
    assert dispositions[replacement.input_id].status == "included_in_known_subtotal"
    assert result.weeks[0].actual_receipts.known_subtotal == "8"
    assert "superseded_actuals_excluded" in result.reason_codes


def test_invalid_supersession_cycle_is_unresolved_not_silently_applied() -> None:
    first = _input_item(
        input_id="actual-one",
        row_number=3,
        cash_flow_type="receipt",
        basis="actual",
        cash_date=AS_OF_DATE,
        supersedes=4,
    )
    second = _input_item(
        input_id="actual-two",
        row_number=4,
        cash_flow_type="receipt",
        basis="actual",
        cash_date=AS_OF_DATE,
        supersedes=3,
    )
    snapshot, amounts = _snapshot_and_amounts((first, second))
    amounts.update({first.input_id: Decimal("1"), second.input_id: Decimal("2")})

    result = calculate_direct_cash_forecast(snapshot, amounts)

    assert result.calculation_state == "incomplete"
    assert "supersession_cycle" in result.reason_codes
    assert result.weeks[0].actual_receipts.status == "partial"
    assert all(item.status == "invalid_supersession" for item in result.item_dispositions)


def test_historical_actuals_are_excluded_but_out_of_horizon_items_are_reported() -> None:
    historical = _input_item(
        input_id="historical-actual",
        row_number=3,
        cash_flow_type="receipt",
        basis="actual",
        cash_date=AS_OF_DATE - timedelta(days=1),
    )
    snapshot, amounts = _snapshot_and_amounts((historical,))
    amounts[historical.input_id] = Decimal("99")
    result = calculate_direct_cash_forecast(snapshot, amounts)
    assert result.item_dispositions[0].status == "historical_actual_excluded"
    assert "historical_actuals_before_as_of_excluded" in result.reason_codes

    outside = _input_item(
        input_id="outside-horizon",
        row_number=3,
        cash_flow_type="receipt",
        basis="forecast",
        cash_date=AS_OF_DATE + timedelta(days=91),
    )
    snapshot, amounts = _snapshot_and_amounts((outside,))
    amounts[outside.input_id] = Decimal("99")
    outside_result = calculate_direct_cash_forecast(snapshot, amounts)
    assert outside_result.item_dispositions[0].status == "out_of_horizon_excluded"
    assert "out_of_horizon_items_excluded" in outside_result.reason_codes


def test_snapshot_json_roundtrip_is_strict_and_redacts_input_amount_text() -> None:
    snapshot, amounts = _complete_forecast(receipt_actual_week_one=Decimal("0.005"))
    input_payload = snapshot.model_dump(mode="json")
    serialized_input = str(input_payload)
    assert "amount_text" not in input_payload
    assert "0.005" not in serialized_input

    restored_input = _input_snapshot_from_json_value(input_payload)
    result = calculate_direct_cash_forecast(restored_input, amounts)
    restored_result = _result_snapshot_from_json_value(result.model_dump(mode="json"))

    assert restored_input == snapshot
    assert restored_result == result
    assert direct_cash_input_snapshot_hash(restored_input) == direct_cash_input_snapshot_hash(
        snapshot
    )
    assert direct_cash_result_snapshot_hash(restored_result) == direct_cash_result_snapshot_hash(
        result
    )


def test_exact_amount_fingerprint_is_keyed_and_binds_lexical_request_text() -> None:
    fingerprint_args = {
        "secret": SECRET,
        "tenant_id": TENANT_ID,
        "company_id": COMPANY_ID,
        "intake_id": INTAKE_ID,
        "snapshot_id": SNAPSHOT_ID,
        "input_id": "movement-00000",
        "source_row_number": 10,
    }
    first = direct_cash_amount_input_fingerprint(**fingerprint_args, amount_text="1.00")
    replay = direct_cash_amount_input_fingerprint(**fingerprint_args, amount_text="1.00")
    lexical_variant = direct_cash_amount_input_fingerprint(**fingerprint_args, amount_text="1.0")
    rotated_secret = direct_cash_amount_input_fingerprint(
        **{**fingerprint_args, "secret": b"another-unit-test-secret-material"},
        amount_text="1.00",
    )

    assert first == replay
    assert first != lexical_variant
    assert first != rotated_secret


def test_request_wire_validation_and_supersession_guard() -> None:
    payload = {
        "snapshot_id": str(SNAPSHOT_ID),
        "reconciliation_group_hash": GROUP_HASH,
        "as_of_date": AS_OF_DATE.isoformat(),
        "opening_cash": {"source_row_number": 2, "amount_text": "100.00"},
        "movements": [
            {
                "source_row_number": 3,
                "amount_text": "1.25",
                "cash_flow_type": "receipt",
                "basis": "actual",
                "cash_date": AS_OF_DATE.isoformat(),
            }
        ],
    }
    request = DirectCashForecastRequest.model_validate(payload)
    assert request.snapshot_id == SNAPSHOT_ID
    assert request.opening_cash == DirectCashOpeningRequest(
        source_row_number=2, amount_text="100.00"
    )
    assert request.movements[0].cash_date == AS_OF_DATE

    with pytest.raises(ValidationError, match="canonical YYYY-MM-DD"):
        DirectCashForecastRequest.model_validate({**payload, "as_of_date": "20261121"})
    movement = payload["movements"][0]
    with pytest.raises(ValidationError, match="calendar dates, not timestamps"):
        DirectCashForecastRequest.model_validate(
            {
                **payload,
                "movements": [{**movement, "cash_date": f"{AS_OF_DATE.isoformat()}T00:00:00Z"}],
            }
        )

    with pytest.raises(ValidationError, match="cannot supersede itself"):
        DirectCashMovementRequest(
            source_row_number=3,
            amount_text="1",
            cash_flow_type="receipt",
            basis="actual",
            cash_date=AS_OF_DATE,
            supersedes_source_row_number=3,
        )


def test_direct_cash_forecast_flag_is_default_disabled_and_nonproduction_only() -> None:
    args = {
        "_env_file": None,
        "app_secret_key": "test-only-key-" + "x" * 48,
        "database_url": "postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
        "app_env": "test",
    }
    disabled = Settings(
        **args, finance_source_intake_enabled=True, financial_calculations_enabled=True
    )
    assert disabled.direct_cash_forecasts_enabled is False
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_direct_cash_forecasts_enabled(disabled)

    enabled = Settings(
        **args,
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
        direct_cash_forecasts_enabled=True,
    )
    ensure_direct_cash_forecasts_enabled(enabled)

    for disabled_flag in (
        Settings(
            **args,
            finance_source_intake_enabled=False,
            financial_calculations_enabled=True,
            direct_cash_forecasts_enabled=True,
        ),
        Settings(
            **args,
            finance_source_intake_enabled=True,
            financial_calculations_enabled=False,
            direct_cash_forecasts_enabled=True,
        ),
    ):
        with pytest.raises(ApiError, match="disabled by deployment configuration"):
            ensure_direct_cash_forecasts_enabled(disabled_flag)

    production = Settings(
        _env_file=None,
        app_secret_key="production-key-" + "x" * 48,
        app_env="production",
        database_url=args["database_url"],
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
        direct_cash_forecasts_enabled=True,
    )
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_direct_cash_forecasts_enabled(production)
