from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from businessflow.universal_data.catalog import CanonicalField


class NormalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def safe_file_name(filename: str | None) -> str:
    if not filename:
        raise NormalizationError("invalid_file_name", "A CSV or XLSX file name is required.")
    leaf = filename.replace("\\", "/").split("/")[-1]
    safe = re.sub(r"[\x00-\x1f\x7f]", "_", leaf).strip(" .")
    if not safe or len(safe) > 255:
        raise NormalizationError("invalid_file_name", "The file name is invalid or too long.")
    return safe


def normalize_field(
    raw_value: str | None,
    field: CanonicalField,
    options: dict[str, Any],
) -> tuple[object | None, list[dict[str, object]]]:
    """Normalize one mapped value using only the explicitly versioned options."""
    if raw_value is None or not raw_value.strip():
        if field.required:
            raise NormalizationError(
                "required_value_missing", f"Required field '{field.name}' has no value."
            )
        return None, [
            {
                "code": "optional_value_missing",
                "severity": "info",
                "field": field.name,
                "message": f"Optional field '{field.name}' is blank in this source row.",
            }
        ]

    text_value = raw_value.strip() if options.get("trim_whitespace", True) else raw_value
    if field.field_type == "text":
        text_case = options.get("text_case", "preserve")
        if text_case == "upper":
            text_value = text_value.upper()
        elif text_case == "lower":
            text_value = text_value.lower()
        if not text_value.strip():
            if field.required:
                raise NormalizationError(
                    "required_value_missing", f"Required field '{field.name}' has no value."
                )
            return None, [
                {
                    "code": "optional_value_missing",
                    "severity": "info",
                    "field": field.name,
                    "message": f"Optional field '{field.name}' is blank in this source row.",
                }
            ]
        return text_value, []

    if field.field_type == "date":
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text_value):
                return date.fromisoformat(text_value).isoformat(), []
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}[T ].+", text_value):
                return datetime.fromisoformat(
                    text_value.replace("Z", "+00:00")
                ).date().isoformat(), []
        except ValueError:
            raise NormalizationError(
                "invalid_date", f"Field '{field.name}' is not a valid ISO date."
            ) from None
        date_format = options.get("date_format")
        if not date_format:
            raise NormalizationError(
                "date_format_unconfirmed",
                f"Field '{field.name}' is not ISO-formatted; choose a date format "
                "before validation.",
            )
        try:
            return datetime.strptime(text_value, date_format).date().isoformat(), []
        except ValueError:
            raise NormalizationError(
                "invalid_date", f"Field '{field.name}' does not match the selected date format."
            ) from None

    if field.field_type == "decimal":
        decimal_separator = options.get("decimal_separator")
        if decimal_separator not in {".", ","}:
            raise NormalizationError(
                "decimal_separator_unconfirmed",
                f"Choose the decimal separator for field '{field.name}' before validation.",
            )
        thousands_separator = options.get("thousands_separator")
        if thousands_separator == decimal_separator:
            raise NormalizationError(
                "number_format_invalid", "Decimal and thousands separators must differ."
            )
        value = text_value
        if value.count(decimal_separator) > 1:
            raise NormalizationError(
                "invalid_decimal", f"Field '{field.name}' contains multiple decimal separators."
            )
        integer_part, separator, fractional_part = value.partition(decimal_separator)
        sign = ""
        if integer_part.startswith(("+", "-")):
            sign, integer_part = integer_part[0], integer_part[1:]
        if not integer_part:
            raise NormalizationError(
                "invalid_decimal", f"Field '{field.name}' is not a decimal number."
            )
        if thousands_separator and thousands_separator in integer_part:
            groups = integer_part.split(thousands_separator)
            if not (1 <= len(groups[0]) <= 3 and groups[0].isdigit()) or any(
                len(group) != 3 or not group.isdigit() for group in groups[1:]
            ):
                raise NormalizationError(
                    "invalid_decimal", f"Field '{field.name}' has invalid thousands grouping."
                )
            integer_digits = "".join(groups)
        else:
            if not integer_part.isdigit():
                raise NormalizationError(
                    "invalid_decimal",
                    f"Field '{field.name}' contains an unconfigured numeric separator "
                    "or non-numeric text.",
                )
            integer_digits = integer_part
        if separator and (not fractional_part.isdigit() or not fractional_part):
            raise NormalizationError(
                "invalid_decimal", f"Field '{field.name}' has invalid decimal digits."
            )
        permitted_group_separator = {thousands_separator} if thousands_separator else set()
        unexpected_separators = {".", ","} - {decimal_separator} - permitted_group_separator
        if any(mark in text_value for mark in unexpected_separators):
            raise NormalizationError(
                "invalid_decimal",
                f"Field '{field.name}' contains an unconfigured numeric separator.",
            )
        normalized = sign + integer_digits + ("." + fractional_part if separator else "")
        try:
            number = Decimal(normalized)
        except InvalidOperation:
            raise NormalizationError(
                "invalid_decimal", f"Field '{field.name}' is not a valid decimal."
            ) from None
        if not number.is_finite():
            raise NormalizationError("invalid_decimal", f"Field '{field.name}' must be finite.")
        return format(number, "f"), []

    raise NormalizationError(
        "unsupported_field_type", f"Field '{field.name}' has an unsupported type."
    )
