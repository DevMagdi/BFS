from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable, Mapping
from uuid import UUID

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

MAX_REPORT_ARTIFACT_BYTES = 8 * 1024 * 1024
JSON_MEDIA_TYPE = "application/json; charset=utf-8"
CSV_MEDIA_TYPE = "text/csv; charset=utf-8"
XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_DATA_HEADERS = (
    "report_id",
    "report_title",
    "mode",
    "source_kind",
    "source_id",
    "source_label",
    "period_start",
    "period_end",
    "currency_code",
    "unit",
    "status",
    "dimensions_json",
    "value",
    "details_json",
    "evidence_count",
    "input_hash",
    "result_hash",
)


def canonical_json_bytes(value: Mapping[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def safe_spreadsheet_text(value: str) -> str:
    """Force formula-like untrusted text to remain literal in common spreadsheet programs."""
    probe = value.lstrip(" \t\r\n\v\f\x00\ufeff")
    if value.startswith(("\t", "\r", "\n")) or probe.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _json_cell(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def _as_text(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (str, int, float, bool)):
        return str(value)
    if isinstance(value, UUID):
        return str(value)
    return _json_cell(value)


def _source_data_rows(source: Mapping[str, object]) -> list[dict[str, object]]:
    points = source.get("data_points")
    if not isinstance(points, list) or not points:
        return [
            {
                "period_start": source.get("period_start"),
                "period_end": source.get("period_end"),
                "currency_code": _single_currency(source),
                "dimensions": {},
                "value": None,
                "details": source.get("result", {}),
                "status": source.get("status", "not_available"),
            }
        ]
    return [point for point in points if isinstance(point, dict)]


def _single_currency(source: Mapping[str, object]) -> str | None:
    currencies = source.get("currency_codes")
    if isinstance(currencies, list) and len(currencies) == 1 and isinstance(currencies[0], str):
        return currencies[0]
    return None


def _data_records(document: Mapping[str, object]) -> Iterable[dict[str, object]]:
    manifest = document.get("manifest")
    sources = document.get("sources")
    if not isinstance(manifest, dict) or not isinstance(sources, list):
        raise ValueError("report document is missing its manifest or source list")
    report_id = manifest.get("report_id", "")
    title = manifest.get("title", "")
    mode = manifest.get("mode", "")
    for source in sources:
        if not isinstance(source, dict):
            continue
        lineage = source.get("lineage")
        lineage = lineage if isinstance(lineage, dict) else {}
        for point in _source_data_rows(source):
            dimensions = point.get("dimensions", {})
            details = point.get("details", {})
            yield {
                "report_id": report_id,
                "report_title": title,
                "mode": mode,
                "source_kind": source.get("kind", ""),
                "source_id": source.get("id", ""),
                "source_label": source.get("label", ""),
                "period_start": point.get("period_start", source.get("period_start")),
                "period_end": point.get("period_end", source.get("period_end")),
                "currency_code": point.get("currency_code") or _single_currency(source),
                "unit": point.get("unit", source.get("unit", "")),
                "status": point.get("status", source.get("status", "")),
                "dimensions_json": dimensions,
                "value": point.get("value"),
                "details_json": details,
                "evidence_count": source.get("evidence_count", 0),
                "input_hash": lineage.get("input_hash", ""),
                "result_hash": lineage.get("result_hash", ""),
            }


def _csv_value(value: object) -> str:
    return safe_spreadsheet_text(_as_text(value))


def render_csv(document: Mapping[str, object]) -> bytes:
    manifest = document.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("report document is missing its manifest")
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=("record_type", *_DATA_HEADERS, "report_manifest_json"),
        extrasaction="ignore",
        lineterminator="\r\n",
    )
    writer.writeheader()
    writer.writerow(
        {
            "record_type": "report_manifest",
            "report_manifest_json": _csv_value(manifest),
        }
    )
    for record in _data_records(document):
        writer.writerow(
            {
                "record_type": "data_point",
                **{key: _csv_value(record.get(key)) for key in _DATA_HEADERS},
            }
        )
    return output.getvalue().encode("utf-8")


def _safe_cell(value: object) -> str:
    return safe_spreadsheet_text(_as_text(value))


def _set_sheet_defaults(sheet) -> None:
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="18324B")
        cell.alignment = Alignment(vertical="top", wrap_text=True)
    for column in sheet.columns:
        values = list(column)
        if not values:
            continue
        width = min(max(max(len(str(cell.value or "")) for cell in values[:100]) + 2, 12), 42)
        sheet.column_dimensions[values[0].column_letter].width = width
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)


def render_xlsx(document: Mapping[str, object]) -> bytes:
    manifest = document.get("manifest")
    sources = document.get("sources")
    if not isinstance(manifest, dict) or not isinstance(sources, list):
        raise ValueError("report document is missing its manifest or source list")

    workbook = Workbook()
    summary = workbook.active
    summary.title = "Report"
    summary.append(["Field", "Value"])
    summary_fields = (
        ("Report ID", manifest.get("report_id")),
        ("Title", manifest.get("title")),
        ("Mode", manifest.get("mode")),
        ("Schema version", manifest.get("schema_version")),
        ("Engine version", manifest.get("engine_version")),
        ("Generated at", manifest.get("generated_at")),
        ("Company ID", manifest.get("company_id")),
        ("Output format", manifest.get("format")),
        ("Retention expires at", manifest.get("expires_at")),
        ("Scope", manifest.get("scope")),
        ("Governance and limitations", manifest.get("governance")),
    )
    for key, value in summary_fields:
        summary.append([_safe_cell(key), _safe_cell(value)])
    _set_sheet_defaults(summary)

    data = workbook.create_sheet("Data")
    data.append([_safe_cell(header) for header in _DATA_HEADERS])
    for record in _data_records(document):
        data.append([_safe_cell(record.get(header)) for header in _DATA_HEADERS])
    _set_sheet_defaults(data)

    lineage = workbook.create_sheet("Lineage")
    lineage.append(["Source kind", "Source ID", "Evidence kind", "Reference"])
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_kind = source.get("kind", "")
        source_id = source.get("id", "")
        source_lineage = source.get("lineage", {})
        if isinstance(source_lineage, dict):
            for key, value in sorted(source_lineage.items()):
                lineage.append(
                    [
                        _safe_cell(source_kind),
                        _safe_cell(source_id),
                        _safe_cell(key),
                        _safe_cell(value),
                    ]
                )
        evidence = source.get("evidence_references", [])
        if isinstance(evidence, list):
            for item in evidence:
                if isinstance(item, dict):
                    lineage.append(
                        [
                            _safe_cell(source_kind),
                            _safe_cell(source_id),
                            _safe_cell(item.get("evidence_kind", "evidence")),
                            _safe_cell(item),
                        ]
                    )
        quality = source.get("quality")
        if isinstance(quality, dict):
            lineage.append(
                [_safe_cell(source_kind), _safe_cell(source_id), "quality", _safe_cell(quality)]
            )
        assumptions = source.get("assumptions")
        if isinstance(assumptions, list):
            lineage.append(
                [
                    _safe_cell(source_kind),
                    _safe_cell(source_id),
                    "assumptions",
                    _safe_cell(assumptions),
                ]
            )
    _set_sheet_defaults(lineage)

    # No formulas, macros, external workbook links, or formula-derived calculations are created.
    output = io.BytesIO()
    workbook.save(output)
    return output.getvalue()


def render_report(document: Mapping[str, object], report_format: str) -> tuple[bytes, str, str]:
    if report_format == "json":
        content = canonical_json_bytes(document)
        media_type, extension = JSON_MEDIA_TYPE, "json"
    elif report_format == "csv":
        content = render_csv(document)
        media_type, extension = CSV_MEDIA_TYPE, "csv"
    elif report_format == "xlsx":
        content = render_xlsx(document)
        media_type, extension = XLSX_MEDIA_TYPE, "xlsx"
    else:
        raise ValueError("unsupported report export format")
    if not content or len(content) > MAX_REPORT_ARTIFACT_BYTES:
        raise ValueError("rendered report exceeds the supported artifact size")
    return content, media_type, extension
