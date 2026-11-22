from __future__ import annotations

from decimal import Decimal, DecimalException, DivisionByZero, Inexact, localcontext
from typing import Literal

from businessflow.calculation_core.contracts import (
    CalculationInputSnapshot,
    CalculationOutput,
    CalculationTrace,
    FormulaDefinition,
    MissingEvidence,
    NumericAssumption,
    NumericObservation,
    RoundingTrace,
    formula_contract_hash,
    formula_hash,
    input_snapshot_hash,
)
from businessflow.calculation_core.decimal import (
    RoundingMode,
    currency_exponent,
    decimal_text,
    fixed_scale_text,
    parse_decimal_text,
    quantize_decimal,
    rounding_constant,
)
from businessflow.metrics.snapshot import content_hash

ENGINE_VERSION = "businessflow.calculation-core.v1"
CALCULATION_PRECISION = 256
ROUNDING_PRECISION = 256

InputRecord = NumericObservation | NumericAssumption
InputKind = Literal["observation", "assumption"]


class CalculationError(ValueError):
    """Base class for deterministic calculation contract and numeric failures."""

    code = "calculation_error"


class CalculationDomainError(CalculationError):
    """Raised when a formula requests unsupported or invalid numeric semantics."""

    code = "calculation_domain_error"


class CalculationPrecisionError(CalculationError):
    """Raised rather than silently serializing an inexact unrounded result."""

    code = "calculation_precision_error"


def _find_input(
    snapshot: CalculationInputSnapshot, key: str, kind: InputKind
) -> tuple[InputRecord | None, MissingEvidence | None]:
    observations = {item.key: item for item in snapshot.observations}
    assumptions = {item.key: item for item in snapshot.assumptions}
    expected_records = observations if kind == "observation" else assumptions
    alternate_records = assumptions if kind == "observation" else observations
    record = expected_records.get(key)
    if record is not None:
        return record, None
    if key in alternate_records:
        recorded_kind = "assumption" if kind == "observation" else "observation"
        return None, MissingEvidence(
            code="input_kind_mismatch",
            input_key=key,
            policy_id=None,
            message=(
                f"Input {key!r} is recorded as {recorded_kind} evidence, "
                f"but the formula requires {kind} evidence."
            ),
        )
    if kind == "observation":
        missing = MissingEvidence(
            code="required_observation_missing",
            input_key=key,
            policy_id=None,
            message=f"Required observation {key!r} is absent from the input snapshot.",
        )
    else:
        missing = MissingEvidence(
            code="required_assumption_missing",
            input_key=key,
            policy_id=None,
            message=f"Required assumption {key!r} is absent from the input snapshot.",
        )
    return None, missing


def _result_currency(
    operation: str, left_currency: str | None, right_currency: str | None
) -> str | None:
    if operation in {"add", "subtract"}:
        if left_currency != right_currency:
            raise CalculationDomainError(
                "addition and subtraction require identical currencies; FX is not performed"
            )
        return left_currency
    if operation == "multiply":
        if left_currency is not None and right_currency is not None:
            raise CalculationDomainError(
                "multiplication of two currency-bearing values is not supported"
            )
        return left_currency or right_currency
    if operation == "divide":
        if right_currency is not None and left_currency is None:
            raise CalculationDomainError("a scalar cannot be divided by a currency-bearing value")
        if left_currency is not None and right_currency is not None:
            if left_currency != right_currency:
                raise CalculationDomainError(
                    "division across currencies is not supported; FX is not performed"
                )
            return None
        return left_currency
    raise CalculationDomainError("unsupported formula operation")


def _evaluate_operation(
    operation: str, left: Decimal, right: Decimal, mode: RoundingMode
) -> tuple[Decimal, bool]:
    try:
        with localcontext() as context:
            context.prec = CALCULATION_PRECISION
            context.rounding = rounding_constant(mode)
            context.clear_flags()
            if operation == "add":
                result = left + right
            elif operation == "subtract":
                result = left - right
            elif operation == "multiply":
                result = left * right
            elif operation == "divide":
                result = left / right
            else:
                raise CalculationDomainError("unsupported formula operation")
            division_inexact = context.flags[Inexact]
    except DivisionByZero as exc:
        raise CalculationDomainError("division by zero is not defined") from exc
    except DecimalException as exc:
        raise CalculationPrecisionError("decimal operation exceeded supported precision") from exc
    if not result.is_finite():
        raise CalculationPrecisionError("calculation produced a non-finite result")
    return result, division_inexact


def _rounding_trace_without_result(formula: FormulaDefinition) -> RoundingTrace:
    policy = formula.rounding_policy
    return RoundingTrace(
        target=policy.target,
        mode=policy.mode,
        requested_scale=policy.scale,
        currency_exponent=None,
        calculation_precision=CALCULATION_PRECISION,
        rounding_precision=ROUNDING_PRECISION,
        division_inexact=False,
        quantization_performed=False,
        value_changed_by_rounding=False,
        unrounded_value=None,
        rounded_value=None,
    )


def _rounded_output(
    value: Decimal,
    *,
    currency_code: str | None,
    formula: FormulaDefinition,
    division_inexact: bool,
) -> tuple[str, RoundingTrace]:
    policy = formula.rounding_policy
    currency_scale: int | None = None
    if policy.target == "none":
        if division_inexact:
            raise CalculationPrecisionError(
                "inexact division requires an explicit currency or fixed-scale rounding target"
            )
        output_text = decimal_text(value)
        return output_text, RoundingTrace(
            target=policy.target,
            mode=policy.mode,
            requested_scale=None,
            currency_exponent=None,
            calculation_precision=CALCULATION_PRECISION,
            rounding_precision=ROUNDING_PRECISION,
            division_inexact=division_inexact,
            quantization_performed=False,
            value_changed_by_rounding=False,
            unrounded_value=decimal_text(value),
            rounded_value=output_text,
        )

    if policy.target == "currency_minor_unit":
        if currency_code is None:
            raise CalculationDomainError(
                "currency_minor_unit rounding requires a currency-bearing output"
            )
        currency_scale = currency_exponent(currency_code)
        scale = currency_scale
    else:
        requested_scale = policy.scale
        if requested_scale is None:
            raise CalculationDomainError("fixed_scale rounding requires an explicit scale")
        scale = requested_scale

    rounded = quantize_decimal(value, scale=scale, mode=policy.mode)
    output_text = fixed_scale_text(rounded, scale=scale)
    return output_text, RoundingTrace(
        target=policy.target,
        mode=policy.mode,
        requested_scale=policy.scale,
        currency_exponent=currency_scale,
        calculation_precision=CALCULATION_PRECISION,
        rounding_precision=ROUNDING_PRECISION,
        division_inexact=division_inexact,
        quantization_performed=True,
        value_changed_by_rounding=rounded != value,
        unrounded_value=decimal_text(value),
        rounded_value=output_text,
    )


def _make_trace(
    *,
    formula: FormulaDefinition,
    snapshot: CalculationInputSnapshot,
    state: Literal["calculated", "abstained"],
    missing_evidence: tuple[MissingEvidence, ...],
    rounding: RoundingTrace,
    output: CalculationOutput | None,
) -> CalculationTrace:
    placeholder = CalculationTrace(
        schema_version=1,
        engine_version=ENGINE_VERSION,
        tenant_id=snapshot.tenant_id,
        company_id=snapshot.company_id,
        formula=formula,
        formula_hash=formula_hash(formula),
        formula_contract_hash=formula_contract_hash(formula),
        input_snapshot=snapshot,
        input_snapshot_hash=input_snapshot_hash(snapshot),
        state=state,
        missing_evidence=missing_evidence,
        rounding=rounding,
        output=output,
        trace_hash="0" * 64,
    )
    payload = placeholder.model_dump(mode="json", exclude={"trace_hash"})
    return placeholder.model_copy(update={"trace_hash": content_hash(payload)})


def calculate_formula(
    formula: FormulaDefinition,
    snapshot: CalculationInputSnapshot,
) -> CalculationTrace:
    """Execute one explicit, versioned binary Decimal formula or abstain without output."""
    formula = FormulaDefinition.model_validate(formula.model_dump(mode="python"))
    snapshot = CalculationInputSnapshot.model_validate(snapshot.model_dump(mode="python"))
    canonical_snapshot = snapshot.model_copy(
        update={
            "observations": tuple(sorted(snapshot.observations, key=lambda item: item.key)),
            "assumptions": tuple(sorted(snapshot.assumptions, key=lambda item: item.key)),
        }
    )
    operands: tuple[tuple[str, InputKind], ...] = (
        (formula.left_input_key, formula.left_input_kind),
        (formula.right_input_key, formula.right_input_kind),
    )
    resolved: list[InputRecord | None] = []
    missing: list[MissingEvidence] = []
    for key, kind in operands:
        record, reason = _find_input(canonical_snapshot, key, kind)
        resolved.append(record)
        if reason is not None and reason not in missing:
            missing.append(reason)

    binding = formula.policy_binding
    if binding is not None and binding.approval_status != "approved":
        missing.append(
            MissingEvidence(
                code="policy_approval_required",
                input_key=None,
                policy_id=binding.policy_id,
                message=(
                    f"Formula requires approval under policy {binding.policy_id!r} "
                    f"version {binding.policy_version!r}."
                ),
            )
        )

    if missing:
        return _make_trace(
            formula=formula,
            snapshot=canonical_snapshot,
            state="abstained",
            missing_evidence=tuple(missing),
            rounding=_rounding_trace_without_result(formula),
            output=None,
        )

    left_record, right_record = resolved
    if left_record is None or right_record is None:
        raise CalculationError("resolved formula inputs are unexpectedly unavailable")

    left_value = parse_decimal_text(left_record.value)
    right_value = parse_decimal_text(right_record.value)
    output_currency = _result_currency(
        formula.operation, left_record.currency_code, right_record.currency_code
    )
    unrounded, division_inexact = _evaluate_operation(
        formula.operation, left_value, right_value, formula.rounding_policy.mode
    )
    output_value, rounding = _rounded_output(
        unrounded,
        currency_code=output_currency,
        formula=formula,
        division_inexact=division_inexact,
    )
    output = CalculationOutput(
        schema_version=1,
        key=formula.output_key,
        label=formula.output_label,
        value=output_value,
        currency_code=output_currency,
    )
    return _make_trace(
        formula=formula,
        snapshot=canonical_snapshot,
        state="calculated",
        missing_evidence=(),
        rounding=rounding,
        output=output,
    )
