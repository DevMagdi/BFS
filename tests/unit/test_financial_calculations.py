from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from businessflow.calculation_core import (
    IndependentExpectedResult,
    SourceReference,
    verify_independent_expected_result,
)
from businessflow.calculation_core.decimal import decimal_text, parse_decimal_text
from businessflow.config import Settings
from businessflow.errors import ApiError
from businessflow.finance_sources.commitments import (
    AMOUNT_COMMITMENT_KEY_VERSION,
    finance_amount_commitment,
)
from businessflow.finance_sources.contracts import (
    FINANCE_SOURCE_CONTRACT_HASH,
    FINANCE_SOURCE_CONTRACT_KEY,
    FINANCE_SOURCE_CONTRACT_VERSION,
)
from businessflow.financial_calculations.calculation import (
    calculate_financial_calculation,
    verify_financial_calculation_replay,
)
from businessflow.financial_calculations.catalog import (
    FINANCIAL_FORMULA_CATALOG,
    SOURCE_GROUP_SUBTOTAL,
)
from businessflow.financial_calculations.contracts import (
    FinanceSourceEvidenceReference,
    FinanceSourceIntakeReference,
    FinancialAmountObservation,
    FinancialCalculationInputSnapshot,
    FinancialReconciliationGroupReference,
)
from businessflow.financial_calculations.service import ensure_financial_calculations_enabled

TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
INTAKE_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
SNAPSHOT_ID = UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")
RUN_ID = UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
CAPTURED_AT = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
SOURCE_AS_OF = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SECRET = b"unit-test-only-stable-secret-material-not-production"
SOURCE_KEY = "synthetic-m15-source"
FILE_HASH = "f" * 64
MAPPING_HASH = "a" * 64
GROUP_HASH = "b" * 64


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _intake(
    *,
    quality_status: str = "passed",
    freshness_status: str = "fresh",
    source_as_of_at: datetime | None = SOURCE_AS_OF,
    freshness_max_age_hours: int | None = 24,
) -> FinanceSourceIntakeReference:
    return FinanceSourceIntakeReference(
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        intake_id=INTAKE_ID,
        source_key=SOURCE_KEY,
        file_hash=FILE_HASH,
        input_hash="1" * 64,
        result_hash="2" * 64,
        contract_key=FINANCE_SOURCE_CONTRACT_KEY,
        contract_version=FINANCE_SOURCE_CONTRACT_VERSION,
        contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
        mapping_version=1,
        mapping_hash=MAPPING_HASH,
        quality_status=quality_status,
        freshness_status=freshness_status,
        source_as_of_at=source_as_of_at,
        freshness_max_age_hours=freshness_max_age_hours,
        reconciliation_status="failed",
    )


def _evidence(
    row_number: int,
    source_amount: str | None,
    *,
    group_hash: str = GROUP_HASH,
    row_status: str = "valid",
    commitment_hash: str | None = None,
) -> FinanceSourceEvidenceReference:
    row_hash = _hash(f"synthetic-row:{row_number}")
    identity_hash = _hash(f"synthetic-identity:{row_number}")
    if commitment_hash is None and source_amount is not None:
        commitment_hash = finance_amount_commitment(
            secret=SECRET,
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
            intake_id=INTAKE_ID,
            source_key=SOURCE_KEY,
            file_hash=FILE_HASH,
            source_row_number=row_number,
            source_row_hash=row_hash,
            source_record_identity_hash=identity_hash,
            reconciliation_group_hash=group_hash,
            contract_key=FINANCE_SOURCE_CONTRACT_KEY,
            contract_version=FINANCE_SOURCE_CONTRACT_VERSION,
            contract_hash=FINANCE_SOURCE_CONTRACT_HASH,
            mapping_version=1,
            mapping_hash=MAPPING_HASH,
            canonical_amount_text=decimal_text(parse_decimal_text(source_amount)),
        )
    return FinanceSourceEvidenceReference(
        row_evidence_id=UUID(int=row_number),
        source_row_number=row_number,
        source_row_hash=row_hash,
        source_record_identity_hash=identity_hash,
        reconciliation_group_hash=group_hash,
        amount_commitment_hash=commitment_hash,
        amount_commitment_key_version=(
            AMOUNT_COMMITMENT_KEY_VERSION if commitment_hash is not None else None
        ),
        row_status=row_status,
    )


def _observation(
    row_number: int,
    amount_text: str | None,
    *,
    source_amount: str | None = None,
    status: str = "valid",
    evidence_group_hash: str = GROUP_HASH,
    row_status: str = "valid",
    commitment_hash: str | None = None,
) -> FinancialAmountObservation:
    evidence = _evidence(
        row_number,
        source_amount if source_amount is not None else amount_text,
        group_hash=evidence_group_hash,
        row_status=row_status,
        commitment_hash=commitment_hash,
    )
    return FinancialAmountObservation(
        source_row_number=row_number,
        amount_text=amount_text,
        status=status,
        evidence=evidence,
    )


def _snapshot(
    observations: tuple[FinancialAmountObservation, ...] = (
        _observation(2, "1.25"),
        _observation(3, "-0.25"),
        _observation(4, "0.005"),
    ),
    *,
    source_row_count: int = 3,
    group_hash: str = GROUP_HASH,
    source_evidence_integrity_status: str = "verified",
    intake: FinanceSourceIntakeReference | None = None,
) -> FinancialCalculationInputSnapshot:
    return FinancialCalculationInputSnapshot(
        schema_version=1,
        snapshot_id=SNAPSHOT_ID,
        tenant_id=TENANT_ID,
        company_id=COMPANY_ID,
        captured_at=CAPTURED_AT,
        intake=intake or _intake(),
        requested_group_hash=group_hash,
        source_evidence_integrity_status=source_evidence_integrity_status,
        group=FinancialReconciliationGroupReference(
            reconciliation_group_hash=group_hash,
            period_start=datetime(2026, 11, 1, tzinfo=UTC).date(),
            period_end=datetime(2026, 11, 30, tzinfo=UTC).date(),
            currency_code="USD",
            source_row_count=source_row_count,
        ),
        observations=observations,
    )


def test_catalog_enables_only_source_group_exact_subtotal_and_defers_policy_formulas() -> None:
    enabled = [
        item for item in FINANCIAL_FORMULA_CATALOG if item.status == "executable_mechanical_only"
    ]
    deferred = [item for item in FINANCIAL_FORMULA_CATALOG if item.status == "deferred"]
    assert enabled == [SOURCE_GROUP_SUBTOTAL]
    assert {item.formula_id for item in deferred} == {
        "businessflow.finance.gross-profit",
        "businessflow.finance.margin-ratios",
        "businessflow.finance.period-rollup",
        "businessflow.finance.cash-forecast",
    }
    assert SOURCE_GROUP_SUBTOTAL.policy_state == "mechanical_only_not_accounting_policy"
    assert "no rounding" in SOURCE_GROUP_SUBTOTAL.rounding_rule.casefold()
    assert "10,000" in SOURCE_GROUP_SUBTOTAL.precision_rule


def test_m15_exact_subtotal_golden_is_bound_to_m11_trace_and_exact_decimal_inputs() -> None:
    snapshot = _snapshot()
    trace = calculate_financial_calculation(RUN_ID, snapshot, identity_secret=SECRET)

    assert trace.state == "calculated"
    assert trace.output is not None
    assert trace.output.value == "1.005"
    assert trace.output.currency_code == "USD"
    assert trace.output.period_start.isoformat() == "2026-11-01"
    assert trace.output.period_end.isoformat() == "2026-11-30"
    assert trace.output.source_row_numbers == (2, 3, 4)
    assert trace.output.rounding_applied is False
    assert trace.output.accounting_interpretation.startswith("mechanical_subtotal_only")
    assert trace.core_trace is not None and trace.core_trace.output is not None
    assert trace.core_trace.formula.formula_id == "businessflow.mechanical.exact-sum"
    assert trace.core_trace.rounding.quantization_performed is False

    independent_expected = IndependentExpectedResult(
        value="1.005",
        currency_code="USD",
        source=SourceReference(
            source_id="synthetic-independent-golden-control",
            source_hash="e" * 64,
            record_hash="d" * 64,
            dataset_type="independent_test_vector",
            mapping_version=1,
            record_locator="golden-vector:source-group-subtotal-v1",
            tenant_id=TENANT_ID,
            company_id=COMPANY_ID,
        ),
    )
    report = verify_independent_expected_result(
        trace.core_trace,
        independent_expected,
        absolute_tolerance="0",
        relative_tolerance="0",
    )
    assert report.state == "passed"
    assert report.variance == "0"
    assert verify_financial_calculation_replay(trace, identity_secret=SECRET).passed


def test_calculated_source_row_numbers_are_sorted_numerically() -> None:
    snapshot = _snapshot(
        (_observation(2, "1"), _observation(10, "2")),
        source_row_count=2,
    )
    trace = calculate_financial_calculation(RUN_ID, snapshot, identity_secret=SECRET)
    assert trace.output is not None
    assert trace.output.source_row_numbers == (2, 10)


def test_exact_lexical_input_changes_input_hash_even_when_decimal_value_is_equal() -> None:
    first = calculate_financial_calculation(RUN_ID, _snapshot(), identity_secret=SECRET)
    changed_snapshot = _snapshot(
        (
            _observation(2, "1.2500"),
            _observation(3, "-0.25"),
            _observation(4, "0.005"),
        )
    )
    second = calculate_financial_calculation(RUN_ID, changed_snapshot, identity_secret=SECRET)
    assert first.output is not None and second.output is not None
    assert first.output.value == second.output.value == "1.005"
    assert first.input_hash != second.input_hash
    assert first.trace_hash != second.trace_hash


@pytest.mark.parametrize(
    ("snapshot", "expected_reason"),
    [
        (
            _snapshot(
                (
                    _observation(2, None, source_amount="1.25", status="missing"),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "amount_missing",
        ),
        (
            _snapshot(
                (
                    _observation(2, "1e2", source_amount="1.25", status="invalid"),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "amount_invalid",
        ),
        (
            _snapshot(
                (
                    _observation(2, "1.25", status="duplicate", row_status="duplicate"),
                    _observation(2, "1.25", status="duplicate", row_status="duplicate"),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "duplicate_input_row",
        ),
        (
            _snapshot(
                (
                    _observation(2, "1.25", status="conflicting"),
                    _observation(2, "2.25", source_amount="1.25", status="conflicting"),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "conflicting_amounts_for_duplicate_row",
        ),
        (
            _snapshot(
                (
                    _observation(2, "1.25", commitment_hash="f" * 64),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "amount_commitment_unverifiable",
        ),
        (
            _snapshot(
                (
                    _observation(2, "1.25", evidence_group_hash="c" * 64),
                    _observation(3, "-0.25"),
                ),
                source_row_count=2,
            ),
            "incompatible_period_or_currency_evidence",
        ),
        (
            _snapshot(
                (_observation(2, "1.25"), _observation(3, "-0.25")),
                source_row_count=2,
                source_evidence_integrity_status="incomplete",
            ),
            "source_row_evidence_integrity_unverified",
        ),
        (
            _snapshot(
                (_observation(2, "1.25"), _observation(3, "-0.25")),
                source_row_count=2,
                intake=_intake(quality_status="failed"),
            ),
            "source_quality_not_passed",
        ),
        (
            _snapshot(
                (_observation(2, "1.25"), _observation(3, "-0.25")),
                source_row_count=2,
                intake=_intake(freshness_status="stale"),
            ),
            "source_not_fresh_at_calculation",
        ),
    ],
)
def test_incomplete_or_unverifiable_evidence_never_emits_output(
    snapshot: FinancialCalculationInputSnapshot,
    expected_reason: str,
) -> None:
    trace = calculate_financial_calculation(RUN_ID, snapshot, identity_secret=SECRET)
    assert trace.state == "incomplete"
    assert trace.output is None
    assert trace.core_trace is not None
    assert trace.core_trace.state == "abstained"
    assert expected_reason in trace.reason_codes
    assert verify_financial_calculation_replay(trace, identity_secret=SECRET).passed


def test_legacy_m14_rows_without_amount_commitments_are_not_eligible() -> None:
    uncommitted = FinancialAmountObservation(
        source_row_number=2,
        amount_text="1.25",
        status="valid",
        evidence=_evidence(2, None),
    )
    snapshot = _snapshot(
        (uncommitted, _observation(3, "-0.25")),
        source_row_count=2,
    )
    trace = calculate_financial_calculation(RUN_ID, snapshot, identity_secret=SECRET)
    assert trace.state == "incomplete"
    assert trace.output is None
    assert "amount_commitment_unverifiable" in trace.reason_codes


def test_source_freshness_is_rechecked_at_calculation_capture_time() -> None:
    stale_snapshot = _snapshot().model_copy(
        update={"captured_at": datetime(2026, 11, 22, 12, 0, tzinfo=UTC)}
    )
    trace = calculate_financial_calculation(RUN_ID, stale_snapshot, identity_secret=SECRET)
    assert trace.state == "incomplete"
    assert trace.output is None
    assert "source_not_fresh_at_calculation" in trace.reason_codes


def test_duplicate_conflicting_period_and_currency_evidence_cannot_be_combined() -> None:
    repeated = _snapshot(
        (
            _observation(2, "1.25"),
            _observation(2, "2.25", source_amount="1.25"),
            _observation(3, "-0.25"),
        ),
        source_row_count=2,
    )
    trace = calculate_financial_calculation(RUN_ID, repeated, identity_secret=SECRET)
    assert trace.output is None
    assert "conflicting_amounts_for_duplicate_row" in trace.reason_codes

    different_period_currency_group = _snapshot(
        (
            _observation(2, "1.25", evidence_group_hash="c" * 64),
            _observation(3, "-0.25"),
        ),
        source_row_count=2,
    )
    incompatible = calculate_financial_calculation(
        RUN_ID, different_period_currency_group, identity_secret=SECRET
    )
    assert incompatible.output is None
    assert "incompatible_period_or_currency_evidence" in incompatible.reason_codes


def test_group_size_boundary_and_input_contract_reject_unbounded_requests() -> None:
    oversized = _snapshot((), source_row_count=10_001)
    trace = calculate_financial_calculation(RUN_ID, oversized, identity_secret=SECRET)
    assert trace.output is None
    assert "source_group_size_limit_exceeded" in trace.reason_codes

    from businessflow.financial_calculations.contracts import FinancialCalculationRequest

    with pytest.raises(ValidationError):
        FinancialCalculationRequest(
            snapshot_id=SNAPSHOT_ID,
            reconciliation_group_hash=GROUP_HASH,
            observations=tuple(
                {"source_row_number": index + 2, "amount_text": "1"} for index in range(10_001)
            ),
        )


def test_financial_calculations_are_disabled_by_default_and_blocked_in_production() -> None:
    args = {
        "_env_file": None,
        "app_secret_key": "test-only-key-" + "x" * 48,
        "app_env": "test",
        "database_url": "postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
    }
    disabled = Settings(**args)
    assert disabled.financial_calculations_enabled is False
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_financial_calculations_enabled(disabled)

    m14_disabled = Settings(**args, financial_calculations_enabled=True)
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_financial_calculations_enabled(m14_disabled)

    enabled = Settings(
        **args,
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
    )
    ensure_financial_calculations_enabled(enabled)

    production = Settings(
        _env_file=None,
        app_secret_key="production-key-" + "x" * 48,
        app_env="production",
        database_url="postgresql+psycopg://bf_runtime:test-password@localhost/businessflow_test",
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
    )
    with pytest.raises(ApiError, match="disabled by deployment configuration"):
        ensure_financial_calculations_enabled(production)


def test_hash_tampering_is_detected_and_replay_recomputes_the_trace() -> None:
    trace = calculate_financial_calculation(RUN_ID, _snapshot(), identity_secret=SECRET)
    assert trace.output is not None
    tampered = trace.model_copy(update={"output": trace.output.model_copy(update={"value": "999"})})
    verification = verify_financial_calculation_replay(tampered, identity_secret=SECRET)
    assert verification.passed is False
    assert "recorded_trace_integrity_invalid" in verification.reason_codes
    assert "m15_result_hash_mismatch" in verification.reason_codes
