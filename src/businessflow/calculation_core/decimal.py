from __future__ import annotations

import re
from collections.abc import Iterable
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Context,
    Decimal,
    DecimalException,
    DivisionByZero,
    Inexact,
    InvalidOperation,
    Overflow,
    Rounded,
    localcontext,
)
from typing import Final, Literal

from iso4217 import Currency

RoundingMode = Literal[
    "half_even",
    "half_up",
    "half_down",
    "down",
    "up",
    "floor",
    "ceiling",
    "05up",
]

MAX_DECIMAL_INPUT_LENGTH: Final = 64
MAX_DECIMAL_OUTPUT_LENGTH: Final = 512
MAX_DECIMAL_SCALE: Final = 18
_DECIMAL_TEXT_PATTERN: Final = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")

ROUNDING_MODES: Final[dict[RoundingMode, str]] = {
    "half_even": ROUND_HALF_EVEN,
    "half_up": ROUND_HALF_UP,
    "half_down": ROUND_HALF_DOWN,
    "down": ROUND_DOWN,
    "up": ROUND_UP,
    "floor": ROUND_FLOOR,
    "ceiling": ROUND_CEILING,
    "05up": ROUND_05UP,
}


def parse_decimal_text(value: object) -> Decimal:
    """Parse plain decimal text without accepting floats, exponents, or non-finite values."""
    if (
        not isinstance(value, str)
        or len(value) > MAX_DECIMAL_INPUT_LENGTH
        or not _DECIMAL_TEXT_PATTERN.fullmatch(value)
    ):
        raise ValueError("value must be a plain base-10 decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("value must be a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError("value must be finite")
    return parsed


def parse_output_decimal_text(value: object) -> Decimal:
    """Parse a serialized calculation output using the wider bounded output limit."""
    if (
        not isinstance(value, str)
        or len(value) > MAX_DECIMAL_OUTPUT_LENGTH
        or not _DECIMAL_TEXT_PATTERN.fullmatch(value)
    ):
        raise ValueError("output must be a finite plain base-10 decimal string")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError("output must be a valid decimal") from exc
    if not parsed.is_finite():
        raise ValueError("output must be finite")
    return parsed


def _validate_scale(scale: int) -> None:
    if not isinstance(scale, int) or isinstance(scale, bool) or not 0 <= scale <= MAX_DECIMAL_SCALE:
        raise ValueError(f"scale must be an integer between 0 and {MAX_DECIMAL_SCALE}")


def _trimmed_decimal_parts(value: Decimal) -> tuple[int, tuple[int, ...], int, int]:
    sign, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        raise ValueError("value must be finite")
    digit_count = len(raw_digits)
    exponent = raw_exponent
    while digit_count > 1 and raw_digits[digit_count - 1] == 0:
        digit_count -= 1
        exponent += 1
    return sign, raw_digits, digit_count, exponent


def decimal_text(value: Decimal) -> str:
    """Return canonical, unrounded fixed-point text for a finite bounded Decimal."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value.is_zero():
        return "0"

    sign, raw_digits, digit_count, exponent = _trimmed_decimal_parts(value)
    point = digit_count + exponent
    if exponent >= 0:
        rendered_length = sign + digit_count + exponent
    elif point > 0:
        rendered_length = sign + digit_count + 1
    else:
        rendered_length = sign + 2 - point + digit_count
    if rendered_length > MAX_DECIMAL_OUTPUT_LENGTH:
        raise ValueError("rendered output exceeds the supported decimal length")

    coefficient = "".join(map(str, raw_digits[:digit_count]))
    if exponent >= 0:
        rendered = coefficient + "0" * exponent
    elif point > 0:
        rendered = coefficient[:point] + "." + coefficient[point:]
    else:
        rendered = "0." + "0" * -point + coefficient
    return ("-" if sign else "") + rendered


def validate_currency_code(value: object) -> str:
    """Return an ISO 4217 currency code that has a defined minor-unit exponent."""
    if not isinstance(value, str) or len(value) != 3 or not value.isascii() or not value.isupper():
        raise ValueError("currency_code must be a three-letter uppercase ISO 4217 code")
    try:
        currency = Currency(value)
    except ValueError as exc:
        raise ValueError("currency_code must be a supported ISO 4217 currency") from exc
    if not isinstance(currency.exponent, int) or currency.exponent < 0:
        raise ValueError("currency_code must have a defined non-negative minor-unit exponent")
    return value


def currency_exponent(currency_code: str) -> int:
    """Return the ISO 4217 minor-unit exponent; no exchange-rate conversion is performed."""
    validate_currency_code(currency_code)
    currency = Currency(currency_code)
    exponent = currency.exponent
    if not isinstance(exponent, int) or exponent < 0:
        raise ValueError("currency_code must have a defined non-negative minor-unit exponent")
    return exponent


def rounding_constant(mode: RoundingMode) -> str:
    try:
        return ROUNDING_MODES[mode]
    except KeyError as exc:
        raise ValueError("unsupported decimal rounding mode") from exc


def _new_decimal_context(precision: int, mode: RoundingMode = "half_even") -> Context:
    """Create an isolated context so caller-local settings cannot affect results."""
    return Context(
        prec=precision,
        rounding=rounding_constant(mode),
        Emin=-999_999,
        Emax=999_999,
        capitals=1,
        clamp=0,
        flags=[],
        traps=[InvalidOperation, DivisionByZero, Overflow],
    )


def exact_decimal_sum(values: Iterable[Decimal]) -> Decimal:
    """Sum a bounded caller-owned sequence exactly under M11's isolated precision rules.

    This is a mechanical arithmetic primitive only: it does not classify inputs, supply a
    missing value, choose a period, or assign financial meaning to the result.
    """
    count = 0
    try:
        with localcontext(_new_decimal_context(precision=256)) as context:
            total = Decimal(0)
            for value in values:
                if not isinstance(value, Decimal) or not value.is_finite():
                    raise ValueError("exact decimal sum requires finite Decimal values")
                if count >= 10_000:
                    raise ValueError("exact decimal sum exceeds the 10,000-input limit")
                total += value
                count += 1
            if count == 0:
                raise ValueError("exact decimal sum requires at least one value")
            if context.flags[Inexact] or context.flags[Rounded]:
                raise ValueError("exact decimal sum exceeded the supported precision")
    except DecimalException as exc:
        raise ValueError("exact decimal sum exceeded the supported precision") from exc
    return total


def quantize_decimal(value: Decimal, *, scale: int, mode: RoundingMode) -> Decimal:
    """Quantize to an explicit decimal scale with a named rounding mode."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    _validate_scale(scale)
    try:
        with localcontext(_new_decimal_context(precision=256, mode=mode)):
            quantum = Decimal(1).scaleb(-scale)
            rounded = value.quantize(quantum)
            return rounded.copy_abs() if rounded.is_zero() else rounded
    except DecimalException as exc:
        raise ValueError("value cannot be represented at the requested decimal scale") from exc


def fixed_scale_text(value: Decimal, *, scale: int) -> str:
    """Render an already-quantized value while preserving its declared scale."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    _validate_scale(scale)
    if value.is_zero():
        return "0" if scale == 0 else "0." + "0" * scale

    sign, raw_digits, digit_count, exponent = _trimmed_decimal_parts(value)
    if exponent < -scale:
        raise ValueError("value must be quantized to the requested decimal scale")
    integer_digits = max(1, digit_count + exponent)
    rendered_length = sign + integer_digits + (scale + 1 if scale else 0)
    if rendered_length > MAX_DECIMAL_OUTPUT_LENGTH:
        raise ValueError("rendered output exceeds the supported decimal length")
    del raw_digits
    return format(value, f".{scale}f")


def round_currency_amount(value: Decimal, currency_code: str, *, mode: RoundingMode) -> str:
    """Round to ISO 4217 minor units using the caller's explicit rounding mode."""
    scale = currency_exponent(currency_code)
    rounded = quantize_decimal(value, scale=scale, mode=mode)
    return fixed_scale_text(rounded, scale=scale)
