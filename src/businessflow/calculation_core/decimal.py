from __future__ import annotations

import re
from decimal import (
    ROUND_05UP,
    ROUND_CEILING,
    ROUND_DOWN,
    ROUND_FLOOR,
    ROUND_HALF_DOWN,
    ROUND_HALF_EVEN,
    ROUND_HALF_UP,
    ROUND_UP,
    Decimal,
    DecimalException,
    InvalidOperation,
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


def _trimmed_decimal_parts(value: Decimal) -> tuple[int, tuple[int, ...], int]:
    sign, raw_digits, raw_exponent = value.as_tuple()
    if not isinstance(raw_exponent, int):
        raise ValueError("value must be finite")
    exponent = raw_exponent
    digits = list(raw_digits)
    while len(digits) > 1 and digits[-1] == 0:
        digits.pop()
        exponent += 1
    return sign, tuple(digits), exponent


def decimal_text(value: Decimal) -> str:
    """Return canonical, unrounded fixed-point text for a finite bounded Decimal."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    if value.is_zero():
        return "0"

    sign, digits, exponent = _trimmed_decimal_parts(value)
    point = len(digits) + exponent
    if exponent >= 0:
        rendered_length = sign + len(digits) + exponent
    elif point > 0:
        rendered_length = sign + len(digits) + 1
    else:
        rendered_length = sign + 2 - point + len(digits)
    if rendered_length > MAX_DECIMAL_OUTPUT_LENGTH:
        raise ValueError("rendered output exceeds the supported decimal length")

    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


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


def quantize_decimal(value: Decimal, *, scale: int, mode: RoundingMode) -> Decimal:
    """Quantize to an explicit decimal scale with a named rounding mode."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    _validate_scale(scale)
    quantum = Decimal(1).scaleb(-scale)
    try:
        with localcontext() as context:
            context.prec = 256
            context.rounding = rounding_constant(mode)
            rounded = value.quantize(quantum)
            return abs(rounded) if rounded.is_zero() else rounded
    except DecimalException as exc:
        raise ValueError("value cannot be represented at the requested decimal scale") from exc


def fixed_scale_text(value: Decimal, *, scale: int) -> str:
    """Render an already-quantized value while preserving its declared scale."""
    if not value.is_finite():
        raise ValueError("value must be finite")
    _validate_scale(scale)
    if value.is_zero():
        return "0" if scale == 0 else "0." + "0" * scale

    sign, digits, exponent = _trimmed_decimal_parts(value)
    if exponent < -scale:
        raise ValueError("value must be quantized to the requested decimal scale")
    integer_digits = max(1, len(digits) + exponent)
    rendered_length = sign + integer_digits + (scale + 1 if scale else 0)
    if rendered_length > MAX_DECIMAL_OUTPUT_LENGTH:
        raise ValueError("rendered output exceeds the supported decimal length")
    return format(value, f".{scale}f")


def round_currency_amount(value: Decimal, currency_code: str, *, mode: RoundingMode) -> str:
    """Round to ISO 4217 minor units using the caller's explicit rounding mode."""
    scale = currency_exponent(currency_code)
    rounded = quantize_decimal(value, scale=scale, mode=mode)
    return fixed_scale_text(rounded, scale=scale)
