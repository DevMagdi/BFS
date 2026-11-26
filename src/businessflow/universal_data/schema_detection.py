from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from businessflow.data_imports.parsers import ParsedSheet

_ISO_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[T ].*)?$")
_AMBIGUOUS_DATE_PATTERN = re.compile(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$")
_AMBIGUOUS_NUMBER_PATTERN = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+(?:\.\d+)?$")


def _is_iso_date(value: str) -> bool:
    if not _ISO_DATE_PATTERN.fullmatch(value.strip()):
        return False
    try:
        if "T" in value or " " in value:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _is_plain_number(value: str) -> bool:
    try:
        Decimal(value.strip())
    except InvalidOperation:
        return False
    return bool(re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value.strip()))


def infer_column_type(values: list[str | None]) -> dict[str, object]:
    samples = [value.strip() for value in values[:100] if value is not None and value.strip()]
    if not samples:
        return {
            "inferred_type": "empty",
            "confidence": 0.0,
            "uncertain": True,
            "sample_count": 0,
            "note": "No non-empty sample values were available.",
        }

    lowered = {value.casefold() for value in samples}
    if lowered <= {"true", "false", "yes", "no", "0", "1"}:
        return {
            "inferred_type": "boolean",
            "confidence": 0.75,
            "uncertain": True,
            "sample_count": len(samples),
            "note": "Boolean-like values overlap with ordinary text and numbers; "
            "confirm explicitly.",
        }

    if all(_is_iso_date(value) for value in samples):
        return {
            "inferred_type": "date",
            "confidence": 0.99,
            "uncertain": False,
            "sample_count": len(samples),
            "note": "Every sampled value matches an unambiguous ISO date or timestamp.",
        }
    if all(_AMBIGUOUS_DATE_PATTERN.fullmatch(value) for value in samples):
        return {
            "inferred_type": "date",
            "confidence": 0.45,
            "uncertain": True,
            "sample_count": len(samples),
            "note": "Slash or dash dates are ambiguous; choose an explicit day/month format.",
        }

    if all(_is_plain_number(value) for value in samples):
        inferred = (
            "integer" if all(re.fullmatch(r"[+-]?\d+", value) for value in samples) else "decimal"
        )
        return {
            "inferred_type": inferred,
            "confidence": 0.98,
            "uncertain": False,
            "sample_count": len(samples),
            "note": "Every sampled value uses plain numeric notation; confirm the "
            "target field type.",
        }
    if all(_AMBIGUOUS_NUMBER_PATTERN.fullmatch(value) for value in samples):
        return {
            "inferred_type": "decimal",
            "confidence": 0.45,
            "uncertain": True,
            "sample_count": len(samples),
            "note": "Thousands and decimal separators are ambiguous; choose both explicitly.",
        }

    if any(re.search(r"[A-Za-z\u00c0-\u024f]", value) for value in samples):
        return {
            "inferred_type": "text",
            "confidence": 0.9,
            "uncertain": False,
            "sample_count": len(samples),
            "note": "Text-like values were observed; this is an inference, not a field mapping.",
        }

    return {
        "inferred_type": "mixed",
        "confidence": 0.25,
        "uncertain": True,
        "sample_count": len(samples),
        "note": "Sample values do not share one confidently detected type.",
    }


def inspect_sheet_schema(sheet: ParsedSheet) -> list[dict[str, object]]:
    columns: list[dict[str, object]] = []
    for index, header in enumerate(sheet.headers):
        values = [row.values[index] if index < len(row.values) else None for row in sheet.rows]
        inference = infer_column_type(values)
        columns.append({"name": header, "position": index + 1, **inference})
    return columns


def inspect_parsed_file(parsed: object, *, header_row: int) -> dict[str, object]:
    worksheets = getattr(parsed, "worksheets", [])
    return {
        "file_format": getattr(parsed, "file_format", "unknown"),
        "header_row": header_row,
        "header_detection": {
            "method": "user-selected row; no header is inferred or silently promoted",
            "confidence": "confirmed_by_selection",
        },
        "worksheet_count": len(worksheets),
        "worksheets": [
            {
                "name": sheet.name,
                "headers": sheet.headers,
                "row_count": len(sheet.rows),
                "blank_rows_ignored": sheet.blank_rows_ignored,
                "columns": inspect_sheet_schema(sheet),
            }
            for sheet in worksheets
        ],
        "total_rows": sum(len(sheet.rows) for sheet in worksheets),
    }
