from __future__ import annotations

from decimal import Inexact, localcontext
from typing import Literal

from pydantic import field_validator

from businessflow.calculation_core.contracts import (
    CalculationInputSnapshot,
    CalculationTrace,
    ContractModel,
    CurrencyCode,
    FormulaDefinition,
    HashText,
    IndependentExpectedResult,
    OutputDecimalText,
    SourceReference,
    formula_hash,
    input_snapshot_hash,
)
from businessflow.calculation_core.decimal import (
    _new_decimal_context,
    decimal_text,
    parse_decimal_text,
    parse_output_decimal_text,
    validate_currency_code,
)
from businessflow.calculation_core.engine import ENGINE_VERSION, calculate_formula
from businessflow.metrics.snapshot import content_hash

VerificationState = Literal["passed", "failed", "not_verifiable"]
RECONCILIATION_PRECISION = 256


class ReconciliationReport(ContractModel):
    schema_version: Literal[1] = 1
    arithmetic_precision: int
    state: VerificationState
    reason_codes: tuple[str, ...]
    expected_value: OutputDecimalText | None
    actual_value: OutputDecimalText | None
    currency_code: CurrencyCode | None
    variance: OutputDecimalText | None
    absolute_variance: OutputDecimalText | None
    absolute_tolerance: OutputDecimalText | None
    relative_tolerance: OutputDecimalText | None
    effective_tolerance: OutputDecimalText | None
    expected_source: SourceReference | None
    actual_trace_hash: HashText | None

    @field_validator("currency_code")
    @classmethod
    def currency_must_be_supported(cls, value: str | None) -> str | None:
        if value is not None:
            validate_currency_code(value)
        return value

    @field_validator(
        "expected_value",
        "actual_value",
        "variance",
        "absolute_variance",
        "absolute_tolerance",
        "relative_tolerance",
        "effective_tolerance",
    )
    @classmethod
    def output_values_must_be_decimal_text(cls, value: str | None) -> str | None:
        if value is not None:
            parse_output_decimal_text(value)
        return value


class ReplayVerificationReport(ContractModel):
    schema_version: Literal[1] = 1
    passed: bool
    reason_codes: tuple[str, ...]
    recorded_trace_hash: HashText
    replayed_trace_hash: HashText
    recorded_trace_integrity_valid: bool
    engine_version: str


def reconcile_values(
    *,
    expected_value: str,
    actual_value: str,
    absolute_tolerance: str,
    relative_tolerance: str,
    currency_code: str | None,
) -> ReconciliationReport:
    """Compare two exact decimal values with explicit absolute and relative tolerances."""
    expected = parse_decimal_text(expected_value)
    actual = parse_output_decimal_text(actual_value)
    absolute = parse_decimal_text(absolute_tolerance)
    relative = parse_decimal_text(relative_tolerance)
    if absolute < 0 or relative < 0:
        raise ValueError("reconciliation tolerances must be non-negative")
    if currency_code is not None:
        validate_currency_code(currency_code)

    with localcontext(_new_decimal_context(precision=RECONCILIATION_PRECISION)) as context:
        variance = actual - expected
        relative_tolerance_value = expected.copy_abs() * relative
        effective_tolerance = max(absolute, relative_tolerance_value)
        inexact = context.flags[Inexact]
    if inexact:
        raise ValueError("reconciliation exceeded the supported exact Decimal precision")
    absolute_variance = variance.copy_abs()
    return ReconciliationReport(
        arithmetic_precision=RECONCILIATION_PRECISION,
        state="passed" if absolute_variance <= effective_tolerance else "failed",
        reason_codes=(),
        expected_value=decimal_text(expected),
        actual_value=decimal_text(actual),
        currency_code=currency_code,
        variance=decimal_text(variance),
        absolute_variance=decimal_text(absolute_variance),
        absolute_tolerance=decimal_text(absolute),
        relative_tolerance=decimal_text(relative),
        effective_tolerance=decimal_text(effective_tolerance),
        expected_source=None,
        actual_trace_hash=None,
    )


def _expected_source_overlaps_input_evidence(
    expected_source: SourceReference,
    snapshot: CalculationInputSnapshot,
) -> bool:
    input_sources = tuple(item.source for item in snapshot.observations) + tuple(
        item.source for item in snapshot.assumptions if item.source is not None
    )
    return any(
        expected_source.source_hash == source.source_hash
        or (
            expected_source.record_hash is not None
            and expected_source.record_hash == source.record_hash
        )
        or (
            expected_source.source_id == source.source_id
            and expected_source.record_locator == source.record_locator
        )
        for source in input_sources
    )


def verify_independent_expected_result(
    trace: CalculationTrace,
    expected: IndependentExpectedResult,
    *,
    absolute_tolerance: str,
    relative_tolerance: str,
) -> ReconciliationReport:
    """Reconcile a calculated output to a separately sourced expected result."""
    if (
        expected.source.tenant_id != trace.tenant_id
        or expected.source.company_id != trace.company_id
    ):
        raise ValueError(
            "independent expected-result evidence must match the trace tenant and company"
        )
    absolute = parse_decimal_text(absolute_tolerance)
    relative = parse_decimal_text(relative_tolerance)
    if absolute < 0 or relative < 0:
        raise ValueError("reconciliation tolerances must be non-negative")
    replay = verify_replay(trace.formula, trace.input_snapshot, trace)
    if not replay.passed:
        return ReconciliationReport(
            arithmetic_precision=RECONCILIATION_PRECISION,
            state="not_verifiable",
            reason_codes=("calculation_replay_invalid", *replay.reason_codes),
            expected_value=expected.value,
            actual_value=None,
            currency_code=expected.currency_code,
            variance=None,
            absolute_variance=None,
            absolute_tolerance=decimal_text(absolute),
            relative_tolerance=decimal_text(relative),
            effective_tolerance=None,
            expected_source=expected.source,
            actual_trace_hash=trace.trace_hash,
        )

    if trace.state == "abstained" or trace.output is None:
        return ReconciliationReport(
            arithmetic_precision=RECONCILIATION_PRECISION,
            state="not_verifiable",
            reason_codes=("calculation_abstained",),
            expected_value=expected.value,
            actual_value=None,
            currency_code=expected.currency_code,
            variance=None,
            absolute_variance=None,
            absolute_tolerance=decimal_text(absolute),
            relative_tolerance=decimal_text(relative),
            effective_tolerance=None,
            expected_source=expected.source,
            actual_trace_hash=trace.trace_hash,
        )

    output = trace.output
    if output.currency_code != expected.currency_code:
        return ReconciliationReport(
            arithmetic_precision=RECONCILIATION_PRECISION,
            state="not_verifiable",
            reason_codes=("currency_mismatch",),
            expected_value=expected.value,
            actual_value=output.value,
            currency_code=None,
            variance=None,
            absolute_variance=None,
            absolute_tolerance=decimal_text(absolute),
            relative_tolerance=decimal_text(relative),
            effective_tolerance=None,
            expected_source=expected.source,
            actual_trace_hash=trace.trace_hash,
        )

    if _expected_source_overlaps_input_evidence(expected.source, trace.input_snapshot):
        return ReconciliationReport(
            arithmetic_precision=RECONCILIATION_PRECISION,
            state="not_verifiable",
            reason_codes=("expected_source_overlaps_input_evidence",),
            expected_value=expected.value,
            actual_value=output.value,
            currency_code=output.currency_code,
            variance=None,
            absolute_variance=None,
            absolute_tolerance=decimal_text(absolute),
            relative_tolerance=decimal_text(relative),
            effective_tolerance=None,
            expected_source=expected.source,
            actual_trace_hash=trace.trace_hash,
        )

    report = reconcile_values(
        expected_value=expected.value,
        actual_value=output.value,
        absolute_tolerance=absolute_tolerance,
        relative_tolerance=relative_tolerance,
        currency_code=output.currency_code,
    )
    return report.model_copy(
        update={"expected_source": expected.source, "actual_trace_hash": trace.trace_hash}
    )


def _trace_integrity_valid(trace: CalculationTrace) -> bool:
    payload = trace.model_dump(mode="json", exclude={"trace_hash"})
    return content_hash(payload) == trace.trace_hash


def verify_replay(
    formula: FormulaDefinition,
    snapshot: CalculationInputSnapshot,
    recorded_trace: CalculationTrace,
) -> ReplayVerificationReport:
    """Re-run a formula against its immutable input snapshot and verify the trace hash."""
    replayed = calculate_formula(formula, snapshot)
    integrity_valid = _trace_integrity_valid(recorded_trace)
    reasons: list[str] = []
    if not integrity_valid:
        reasons.append("recorded_trace_integrity_invalid")
    if formula_hash(formula) != recorded_trace.formula_hash:
        reasons.append("formula_hash_mismatch")
    if input_snapshot_hash(snapshot) != recorded_trace.input_snapshot_hash:
        reasons.append("input_snapshot_hash_mismatch")
    if recorded_trace.engine_version != ENGINE_VERSION:
        reasons.append("engine_version_mismatch")
    if replayed.trace_hash != recorded_trace.trace_hash:
        reasons.append("replay_trace_hash_mismatch")
    return ReplayVerificationReport(
        passed=not reasons,
        reason_codes=tuple(reasons),
        recorded_trace_hash=recorded_trace.trace_hash,
        replayed_trace_hash=replayed.trace_hash,
        recorded_trace_integrity_valid=integrity_valid,
        engine_version=ENGINE_VERSION,
    )
