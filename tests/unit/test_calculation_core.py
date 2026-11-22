from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from pydantic import ValidationError

from businessflow.calculation_core import (
    CalculationDomainError,
    CalculationInputSnapshot,
    FormulaDefinition,
    FormulaPolicyBinding,
    IndependentExpectedResult,
    NumericAssumption,
    NumericObservation,
    RoundingPolicy,
    SourceReference,
    calculate_formula,
    currency_exponent,
    formula_contract_hash,
    parse_decimal_text,
    reconcile_values,
    round_currency_amount,
    verify_independent_expected_result,
    verify_replay,
)
from businessflow.calculation_core.contracts import FormulaOperation, InputKind, RoundingTarget
from businessflow.calculation_core.decimal import RoundingMode
from businessflow.calculation_core.engine import CalculationPrecisionError
from businessflow.metrics.snapshot import content_hash

TENANT_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
COMPANY_ID = UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
USER_ID = UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
CAPTURED_AT = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)


def _source(
    source_id: str = "import:csv",
    *,
    tenant_id: UUID = TENANT_ID,
    dataset_type: str | None = None,
    mapping_version: int | None = None,
    record_hash: str | None = None,
) -> SourceReference:
    return SourceReference(
        source_id=source_id,
        source_hash="a" * 64,
        record_hash=record_hash,
        dataset_type=dataset_type,
        mapping_version=mapping_version,
        record_locator="CSV:2",
        tenant_id=tenant_id,
        company_id=COMPANY_ID,
    )


def _observation(
    key: str,
    value: str,
    *,
    currency_code: str | None = None,
    source_id: str | None = None,
    tenant_id: UUID = TENANT_ID,
    dataset_type: str | None = None,
    mapping_version: int | None = None,
    record_hash: str | None = None,
) -> NumericObservation:
    return NumericObservation(
        key=key,
        value=value,
        currency_code=currency_code,
        source=_source(
            source_id or f"import:{key}",
            tenant_id=tenant_id,
            dataset_type=dataset_type,
            mapping_version=mapping_version,
            record_hash=record_hash,
        ),
    )


def _assumption(
    key: str,
    value: str,
    *,
    currency_code: str | None = None,
    source: SourceReference | None = None,
) -> NumericAssumption:
    return NumericAssumption(
        key=key,
        version="1.0",
        value=value,
        currency_code=currency_code,
        rationale="Explicit test fixture value; not a financial policy.",
        attributed_by_user_id=USER_ID,
        attributed_at=CAPTURED_AT,
        source=source,
    )


def _snapshot(
    observations: tuple[NumericObservation, ...] = (),
    assumptions: tuple[NumericAssumption, ...] = (),
    *,
    tenant_id: UUID = TENANT_ID,
) -> CalculationInputSnapshot:
    return CalculationInputSnapshot(
        snapshot_id=UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd"),
        tenant_id=tenant_id,
        company_id=COMPANY_ID,
        captured_at=CAPTURED_AT,
        observations=observations,
        assumptions=assumptions,
    )


def _formula(
    *,
    operation: FormulaOperation = "add",
    left_key: str = "left",
    right_key: str = "right",
    left_kind: InputKind = "observation",
    right_kind: InputKind = "observation",
    target: RoundingTarget = "none",
    mode: RoundingMode = "half_even",
    scale: int | None = None,
) -> FormulaDefinition:
    return FormulaDefinition(
        formula_id="test.generic-arithmetic",
        formula_version="1.0.0",
        operation=operation,
        left_input_key=left_key,
        left_input_kind=left_kind,
        right_input_key=right_key,
        right_input_kind=right_kind,
        output_key="result",
        output_label="Generic arithmetic test output",
        rounding_policy=RoundingPolicy(target=target, mode=mode, scale=scale),
    )


def _two_values(
    left: str = "1.10",
    right: str = "2.30",
    *,
    left_currency: str | None = None,
    right_currency: str | None = None,
) -> CalculationInputSnapshot:
    return _snapshot(
        (
            _observation("left", left, currency_code=left_currency),
            _observation("right", right, currency_code=right_currency),
        )
    )


def test_decimal_parser_requires_bounded_plain_decimal_text() -> None:
    assert parse_decimal_text("-120.0050") == Decimal("-120.0050")
    assert parse_decimal_text("0") == Decimal(0)

    for invalid in (1.25, Decimal("1.25"), "1e2", "NaN", "Infinity", " 1.0", ".5", "01"):
        with pytest.raises(ValueError):
            parse_decimal_text(invalid)
    with pytest.raises(ValueError, match="plain base-10"):
        parse_decimal_text("1" * 65)


def test_maximum_input_precision_is_preserved_in_decimal_multiplication() -> None:
    value = "9" * 63
    trace = calculate_formula(
        _formula(operation="multiply"),
        _two_values(value, value),
    )
    assert trace.output is not None
    assert trace.output.value == str(int(value) * int(value))
    assert trace.rounding.division_inexact is False


def test_numeric_contracts_reject_float_and_invalid_currency() -> None:
    source = _source()
    with pytest.raises(ValidationError):
        NumericObservation.model_validate(
            {"key": "amount", "value": 1.25, "currency_code": "USD", "source": source}
        )
    with pytest.raises(ValidationError):
        _observation("amount", "1.25", currency_code="XXX")
    with pytest.raises(ValidationError):
        _observation("amount", "1.25", currency_code="usd")


def test_currency_exponents_and_rounding_are_explicit() -> None:
    assert currency_exponent("USD") == 2
    assert currency_exponent("EGP") == 2
    assert currency_exponent("JPY") == 0
    assert currency_exponent("KWD") == 3
    assert round_currency_amount(Decimal("10.005"), "USD", mode="half_even") == "10.00"
    assert round_currency_amount(Decimal("10.005"), "USD", mode="half_up") == "10.01"
    assert round_currency_amount(Decimal("10.5"), "JPY", mode="half_even") == "10"
    assert round_currency_amount(Decimal("1.2345"), "KWD", mode="half_up") == "1.235"
    assert round_currency_amount(Decimal("-0.005"), "USD", mode="half_even") == "0.00"
    with pytest.raises(ValueError):
        currency_exponent("XXX")


def test_rounding_policy_requires_explicit_scale_only_for_fixed_scale() -> None:
    with pytest.raises(ValidationError, match="requires an explicit scale"):
        RoundingPolicy(target="fixed_scale", mode="half_even")
    with pytest.raises(ValidationError, match="only valid for fixed_scale"):
        RoundingPolicy(target="none", mode="half_even", scale=2)


def test_formula_trace_is_golden_and_captures_exact_assumption_and_source() -> None:
    formula = FormulaDefinition(
        formula_id="test.generic-scale",
        formula_version="1.0.0",
        operation="multiply",
        left_input_key="measured_value",
        left_input_kind="observation",
        right_input_key="explicit_factor",
        right_input_kind="assumption",
        output_key="scaled_value",
        output_label="Generic scaled value",
        rounding_policy=RoundingPolicy(target="currency_minor_unit", mode="half_even"),
    )
    snapshot = _snapshot(
        observations=(
            _observation(
                "measured_value",
                "1.25",
                currency_code="USD",
                source_id="ledger:row-2",
                dataset_type="finance_actuals",
                mapping_version=3,
                record_hash="b" * 64,
            ),
        ),
        assumptions=(_assumption("explicit_factor", "2"),),
    )

    trace = calculate_formula(formula, snapshot)

    assert trace.state == "calculated"
    assert trace.output is not None
    assert trace.output.value == "2.50"
    assert trace.output.currency_code == "USD"
    assert trace.formula.formula_version == "1.0.0"
    assert (
        trace.input_snapshot_hash
        == "fe3b87216271129ed4d9cb8f4e863840ae9fb2751a51aa5232617a33c873cd4e"
    )
    assert trace.input_snapshot.assumptions[0].version == "1.0"
    assert trace.input_snapshot.assumptions[0].rationale.startswith("Explicit test fixture")
    source = trace.input_snapshot.observations[0].source
    assert source.record_locator == "CSV:2"
    assert source.dataset_type == "finance_actuals"
    assert source.mapping_version == 3
    assert source.record_hash == "b" * 64
    assert trace.rounding.currency_exponent == 2
    assert trace.rounding.quantization_performed is True
    assert trace.rounding.value_changed_by_rounding is False
    assert trace.trace_hash == "8a8f4cf7de5cb774dc34ebb1cf471843d1b4bb11b9998853d7f849fb8ecdb88e"


def test_addition_commutes_and_subtraction_is_antisymmetric() -> None:
    snapshot = _two_values("7.25", "2.5")
    add_forward = calculate_formula(_formula(), snapshot)
    add_reverse = calculate_formula(_formula(left_key="right", right_key="left"), snapshot)
    subtract_forward = calculate_formula(_formula(operation="subtract"), snapshot)
    subtract_reverse = calculate_formula(
        _formula(operation="subtract", left_key="right", right_key="left"), snapshot
    )

    assert add_forward.output is not None and add_reverse.output is not None
    assert add_forward.output.value == add_reverse.output.value == "9.75"
    assert subtract_forward.output is not None and subtract_reverse.output is not None
    assert Decimal(subtract_forward.output.value) + Decimal(subtract_reverse.output.value) == 0


def test_input_order_does_not_change_calculation_or_snapshot_hash() -> None:
    formula = _formula()
    observations = (
        _observation("left", "1.10"),
        _observation("right", "2.30"),
    )
    forward = calculate_formula(formula, _snapshot(observations))
    reverse = calculate_formula(formula, _snapshot(tuple(reversed(observations))))

    assert forward.trace_hash == reverse.trace_hash
    assert forward.input_snapshot_hash == reverse.input_snapshot_hash
    assert forward.output == reverse.output
    assert [item.key for item in reverse.input_snapshot.observations] == ["left", "right"]


def test_rounding_contracts_and_money_currency_rules() -> None:
    formula = _formula(target="currency_minor_unit")
    rounded = calculate_formula(
        formula,
        _two_values("10.005", "0", left_currency="USD", right_currency="USD"),
    )
    assert rounded.output is not None
    assert rounded.output.value == "10.00"
    assert rounded.rounding.unrounded_value == "10.005"
    assert rounded.rounding.value_changed_by_rounding is True

    with pytest.raises(CalculationDomainError, match="identical currencies"):
        calculate_formula(
            formula,
            _two_values("10", "2", left_currency="USD", right_currency="EUR"),
        )
    with pytest.raises(CalculationDomainError, match="two currency-bearing"):
        calculate_formula(
            _formula(operation="multiply"),
            _two_values("10", "2", left_currency="USD", right_currency="EUR"),
        )


def test_division_requires_explicit_rounding_when_result_is_inexact() -> None:
    exact = calculate_formula(
        _formula(operation="divide"),
        _two_values("1", "2"),
    )
    assert exact.output is not None and exact.output.value == "0.5"
    assert exact.rounding.division_inexact is False

    with pytest.raises(CalculationPrecisionError, match="explicit"):
        calculate_formula(_formula(operation="divide"), _two_values("1", "3"))

    rounded = calculate_formula(
        _formula(operation="divide", target="fixed_scale", scale=4),
        _two_values("1", "3"),
    )
    assert rounded.output is not None and rounded.output.value == "0.3333"
    assert rounded.rounding.division_inexact is True
    assert rounded.rounding.requested_scale == 4


def test_maximum_ratio_has_enough_precision_for_declared_output_scale() -> None:
    numerator = "1" + "0" * 62
    denominator = "0." + "0" * 61 + "3"
    trace = calculate_formula(
        _formula(operation="divide", target="fixed_scale", scale=18),
        _two_values(numerator, denominator),
    )

    assert trace.output is not None
    scaled_quotient, remainder = divmod(10**142, 3)
    assert remainder * 2 < 3
    whole, fraction = divmod(scaled_quotient, 10**18)
    assert trace.output.value == f"{whole}.{fraction:018d}"
    assert trace.rounding.calculation_precision == 256
    assert trace.rounding.division_inexact is True


def test_missing_evidence_and_unapproved_policy_abstain_without_output() -> None:
    missing = calculate_formula(_formula(), _snapshot())
    assert missing.state == "abstained"
    assert missing.output is None
    assert {item.code for item in missing.missing_evidence} == {"required_observation_missing"}
    assert {item.input_key for item in missing.missing_evidence} == {"left", "right"}

    pending_formula = FormulaDefinition.model_validate(
        {
            **_formula().model_dump(),
            "policy_binding": {
                "policy_id": "policy.synthetic-test",
                "policy_version": "1.0",
                "approval_status": "pending",
            },
        }
    )
    pending = calculate_formula(pending_formula, _two_values())
    assert pending.state == "abstained"
    assert pending.output is None
    assert "policy_approval_required" in {item.code for item in pending.missing_evidence}


def test_wrong_input_kind_and_cross_tenant_source_are_not_silently_used() -> None:
    formula = _formula()
    wrong_kind = calculate_formula(
        formula,
        _snapshot(assumptions=(_assumption("left", "2"),)),
    )
    assert wrong_kind.state == "abstained"
    assert "input_kind_mismatch" in {item.code for item in wrong_kind.missing_evidence}

    with pytest.raises(ValidationError, match="tenant and company"):
        _snapshot(
            (_observation("left", "1", tenant_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")),)
        )


def test_policy_approval_must_bind_exact_formula_contract() -> None:
    pending = FormulaDefinition.model_validate(
        {
            **_formula().model_dump(),
            "policy_binding": {
                "policy_id": "policy.test-only",
                "policy_version": "1.0",
                "approval_status": "pending",
            },
        }
    )
    binding = FormulaPolicyBinding(
        policy_id="policy.test-only",
        policy_version="1.0",
        approval_status="approved",
        approved_by_user_id=USER_ID,
        approved_at=CAPTURED_AT,
        approval_reference="test-only approval fixture",
        approved_formula_hash=formula_contract_hash(pending),
    )
    approved = FormulaDefinition.model_validate(
        {**pending.model_dump(), "policy_binding": binding.model_dump()}
    )
    assert calculate_formula(approved, _two_values()).state == "calculated"

    bad_binding = binding.model_copy(update={"approved_formula_hash": "b" * 64})
    with pytest.raises(ValidationError, match="exact formula contract"):
        FormulaDefinition.model_validate(
            {**pending.model_dump(), "policy_binding": bad_binding.model_dump()}
        )
    forged_formula = approved.model_copy(update={"policy_binding": bad_binding})
    with pytest.raises(ValidationError, match="exact formula contract"):
        calculate_formula(forged_formula, _two_values())


def test_decimal_reconciliation_uses_exact_tolerances_including_zero_actual() -> None:
    within = reconcile_values(
        expected_value="100",
        actual_value="101",
        absolute_tolerance="0",
        relative_tolerance="0.01",
        currency_code="USD",
    )
    assert within.state == "passed"
    assert within.variance == "1"
    assert within.effective_tolerance == "1"

    outside = reconcile_values(
        expected_value="100",
        actual_value="101.01",
        absolute_tolerance="0",
        relative_tolerance="0.01",
        currency_code="USD",
    )
    assert outside.state == "failed"
    assert outside.variance == "1.01"

    zero_expected = reconcile_values(
        expected_value="0",
        actual_value="0.005",
        absolute_tolerance="0.01",
        relative_tolerance="0.5",
        currency_code=None,
    )
    assert zero_expected.state == "passed"
    with pytest.raises(ValueError, match="non-negative"):
        reconcile_values(
            expected_value="1",
            actual_value="1",
            absolute_tolerance="-0.01",
            relative_tolerance="0",
            currency_code=None,
        )


def test_independent_expected_result_requires_same_tenant_and_matches_source() -> None:
    formula = _formula()
    snapshot = _two_values("2", "3")
    trace = calculate_formula(formula, snapshot)
    expected = IndependentExpectedResult(
        value="5.00",
        currency_code=None,
        source=_source("independent:expected-result"),
    )

    passed = verify_independent_expected_result(
        trace, expected, absolute_tolerance="0", relative_tolerance="0"
    )
    assert passed.state == "passed"
    assert passed.variance == "0"
    assert passed.expected_source == expected.source
    assert passed.actual_trace_hash == trace.trace_hash

    different = verify_independent_expected_result(
        trace,
        expected.model_copy(update={"value": "5.02"}),
        absolute_tolerance="0.01",
        relative_tolerance="0",
    )
    assert different.state == "failed"

    assert trace.output is not None
    forged = trace.model_copy(update={"output": trace.output.model_copy(update={"value": "999"})})
    forged_payload = forged.model_dump(mode="json", exclude={"trace_hash"})
    forged = forged.model_copy(update={"trace_hash": content_hash(forged_payload)})
    forged_expected = expected.model_copy(update={"value": "999"})
    unverifiable = verify_independent_expected_result(
        forged, forged_expected, absolute_tolerance="0", relative_tolerance="0"
    )
    assert unverifiable.state == "not_verifiable"
    assert "calculation_replay_invalid" in unverifiable.reason_codes

    other_tenant = expected.model_copy(
        update={
            "source": _source(
                "independent:foreign", tenant_id=UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
            )
        }
    )
    with pytest.raises(ValueError, match="tenant and company"):
        verify_independent_expected_result(
            trace, other_tenant, absolute_tolerance="0", relative_tolerance="0"
        )


def test_replay_checks_hashes_and_detects_trace_tampering() -> None:
    formula = _formula()
    snapshot = _two_values()
    trace = calculate_formula(formula, snapshot)
    replay = verify_replay(formula, snapshot, trace)
    assert replay.passed is True
    assert replay.recorded_trace_integrity_valid is True
    assert replay.recorded_trace_hash == replay.replayed_trace_hash

    assert trace.output is not None
    tampered = trace.model_copy(update={"output": trace.output.model_copy(update={"value": "999"})})
    failed = verify_replay(formula, snapshot, tampered)
    assert failed.passed is False
    assert failed.recorded_trace_integrity_valid is False
    assert "recorded_trace_integrity_invalid" in failed.reason_codes

    forged_payload = tampered.model_dump(mode="json", exclude={"trace_hash"})
    forged = tampered.model_copy(update={"trace_hash": content_hash(forged_payload)})
    replay_mismatch = verify_replay(formula, snapshot, forged)
    assert replay_mismatch.passed is False
    assert replay_mismatch.recorded_trace_integrity_valid is True
    assert "replay_trace_hash_mismatch" in replay_mismatch.reason_codes
