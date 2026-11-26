from __future__ import annotations

import csv
import io
import re
import zipfile
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import PurePosixPath
from typing import Any, BinaryIO

from openpyxl import load_workbook
from openpyxl.xml import DEFUSEDXML

MAX_COLUMNS = 256
MAX_CELL_CHARACTERS = 65_536
MAX_WORKSHEETS = 20
MAX_XLSX_ZIP_ENTRIES = 4096
MAX_RELATIONSHIP_FILE_BYTES = 2 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
MAX_EXPANDED_BYTES = 100 * 1024 * 1024


class ImportFileError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ParsedRow:
    row_number: int
    values: list[str | None]


@dataclass(frozen=True, slots=True)
class ParsedSheet:
    name: str
    headers: list[str]
    rows: list[ParsedRow]
    blank_rows_ignored: int


@dataclass(frozen=True, slots=True)
class ParsedFile:
    file_format: str
    worksheets: list[ParsedSheet]

    @property
    def total_rows(self) -> int:
        return sum(len(sheet.rows) for sheet in self.worksheets)


def _normalized_header(value: str) -> str:
    return re.sub(r"_+", "_", re.sub(r"[^a-z0-9]+", "_", value.casefold())).strip("_")


def _cell_text(value: object, cell: Any = None) -> str | None:
    if cell is not None and cell.data_type == "f":
        raise ImportFileError(
            422,
            "unsafe_file",
            "Formula cells are not accepted. Replace formulas with reviewed static values.",
        )
    if value is None:
        return None
    if isinstance(value, bool):
        result = "true" if value else "false"
    elif isinstance(value, (int, float)):
        result = str(value)
    elif isinstance(value, datetime):
        result = value.date().isoformat() if value.time() == time.min else value.isoformat()
    elif isinstance(value, date) or hasattr(value, "isoformat"):
        result = value.isoformat()
    else:
        result = str(value)
    if len(result) > MAX_CELL_CHARACTERS:
        raise ImportFileError(
            422, "malformed_file", "A cell exceeds the supported 65,536-character limit."
        )
    return result


def _validate_headers(raw_headers: Sequence[str | None]) -> list[str]:
    headers = [value.strip() if value is not None else "" for value in raw_headers]
    if not headers or all(not value for value in headers):
        raise ImportFileError(422, "malformed_file", "The first row must contain column headers.")
    if any(not value for value in headers):
        raise ImportFileError(
            422, "malformed_file", "Every header cell must have a non-empty column name."
        )
    if any(len(value) > 128 for value in headers):
        raise ImportFileError(
            422, "malformed_file", "Column headers may not exceed 128 characters."
        )
    normalized = [_normalized_header(value) for value in headers]
    if any(not value for value in normalized) or len(set(normalized)) != len(normalized):
        raise ImportFileError(
            422,
            "malformed_file",
            "Column headers must be unique after case and punctuation normalization.",
        )
    if len(headers) > MAX_COLUMNS:
        raise ImportFileError(
            422, "malformed_file", f"A worksheet may not contain more than {MAX_COLUMNS} columns."
        )
    return headers


def _normalize_csv_record(values: list[str], width: int, row_number: int) -> list[str | None]:
    while len(values) > width and not values[-1].strip():
        values.pop()
    if len(values) > width:
        raise ImportFileError(
            422,
            "malformed_file",
            f"CSV record {row_number} contains values beyond the header columns.",
        )
    values += [""] * (width - len(values))
    if all(not value.strip() for value in values):
        return []
    normalized = [value if value.strip() else None for value in values]
    if any(value is not None and len(value) > MAX_CELL_CHARACTERS for value in normalized):
        raise ImportFileError(
            422, "malformed_file", "A cell exceeds the supported 65,536-character limit."
        )
    return normalized


def _parse_csv(
    content: bytes,
    max_rows: int,
    *,
    delimiter: str = ",",
    encoding: str = "utf-8-sig",
    header_row: int = 1,
) -> ParsedFile:
    if delimiter not in {",", ";", chr(9), "|"} or len(delimiter) != 1:
        raise ImportFileError(
            422, "invalid_csv_options", "Choose comma, semicolon, tab, or pipe as the delimiter."
        )
    if encoding not in {"utf-8-sig", "utf-16", "cp1252"}:
        raise ImportFileError(
            422,
            "invalid_csv_options",
            "Choose UTF-8, UTF-16, or Windows-1252 encoding explicitly.",
        )
    if not 1 <= header_row <= 25:
        raise ImportFileError(
            422, "invalid_csv_options", "The header row must be between 1 and 25."
        )
    try:
        text = content.decode(encoding, errors="strict")
    except (UnicodeDecodeError, LookupError):
        raise ImportFileError(
            422,
            "malformed_file",
            "The CSV file does not match the selected text encoding.",
        ) from None
    if "\x00" in text:
        raise ImportFileError(422, "malformed_file", "CSV files may not contain NUL characters.")
    try:
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True)
        raw_headers: list[str] | None = None
        for _ in range(header_row):
            raw_headers = next(reader, None)
            if raw_headers is None:
                break
        if raw_headers is None:
            raise ImportFileError(422, "malformed_file", "The selected header row is not present.")
        headers = _validate_headers(raw_headers)
        rows: list[ParsedRow] = []
        blank_rows = 0
        for record_number, values in enumerate(reader, start=header_row + 1):
            normalized = _normalize_csv_record(values, len(headers), record_number)
            if not normalized:
                blank_rows += 1
                continue
            rows.append(ParsedRow(row_number=record_number, values=normalized))
            if len(rows) > max_rows:
                raise ImportFileError(
                    413,
                    "too_many_rows",
                    f"The upload exceeds the configured limit of {max_rows} data rows.",
                )
    except ImportFileError:
        raise
    except csv.Error:
        raise ImportFileError(422, "malformed_file", "The CSV file is malformed.") from None
    return ParsedFile(
        file_format="csv",
        worksheets=[
            ParsedSheet(name="CSV", headers=headers, rows=rows, blank_rows_ignored=blank_rows)
        ],
    )


def _check_xlsx_archive(content: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_XLSX_ZIP_ENTRIES:
                raise ImportFileError(
                    413,
                    "too_many_archive_entries",
                    f"An XLSX package may not contain more than {MAX_XLSX_ZIP_ENTRIES} entries.",
                )

            names: set[str] = set()
            expanded = 0
            for info in infos:
                name = info.filename
                normalized_name = name.rstrip("/")
                path = PurePosixPath(normalized_name)
                path_parts = normalized_name.split("/")
                if (
                    not normalized_name
                    or path.is_absolute()
                    or "\\" in name
                    or any(part in {"", ".", ".."} for part in path_parts)
                    or ":" in path_parts[0]
                    or name in names
                ):
                    raise ImportFileError(
                        422, "unsafe_file", "The XLSX package contains an unsafe ZIP path."
                    )
                names.add(name)
                if info.flag_bits & 0x1:
                    raise ImportFileError(
                        422, "unsupported_file", "Encrypted XLSX workbooks are not supported."
                    )
                expanded += info.file_size
                if info.file_size and (
                    info.compress_size == 0
                    or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise ImportFileError(
                        422, "unsafe_file", "The XLSX package has an unsafe compression ratio."
                    )
                if expanded > MAX_EXPANDED_BYTES:
                    raise ImportFileError(
                        413, "file_too_large", "The expanded XLSX workbook is too large."
                    )

                lowered_name = name.casefold()
                if lowered_name.startswith("xl/externallinks/") or lowered_name.endswith(
                    "vbaproject.bin"
                ):
                    raise ImportFileError(
                        422,
                        "unsafe_file",
                        "Macro-enabled workbooks and external workbook links are not accepted.",
                    )
                if lowered_name.endswith(".rels"):
                    if info.file_size > MAX_RELATIONSHIP_FILE_BYTES:
                        raise ImportFileError(
                            413,
                            "file_too_large",
                            "An XLSX relationship file exceeds the supported size limit.",
                        )
                    relationship_data = archive.read(info).decode("utf-8", errors="ignore")
                    if re.search(
                        r"TargetMode\s*=\s*['\"]external['\"]",
                        relationship_data,
                        re.IGNORECASE,
                    ):
                        raise ImportFileError(
                            422,
                            "unsafe_file",
                            "External workbook relationships are not accepted.",
                        )
            required_parts = {"[Content_Types].xml", "xl/workbook.xml"}
            if not required_parts.issubset(names):
                raise ImportFileError(
                    422, "malformed_file", "The XLSX file is not a valid workbook."
                )
    except ImportFileError:
        raise
    except Exception:
        raise ImportFileError(
            422, "malformed_file", "The XLSX file is not a valid workbook."
        ) from None


def _parse_xlsx(content: bytes, max_rows: int, *, header_row: int = 1) -> ParsedFile:
    if not 1 <= header_row <= 25:
        raise ImportFileError(
            422, "invalid_csv_options", "The header row must be between 1 and 25."
        )
    _check_xlsx_archive(content)
    if not DEFUSEDXML:
        raise ImportFileError(
            503, "safe_xlsx_parser_unavailable", "Safe XLSX parsing is temporarily unavailable."
        )
    workbook: Any = None
    try:
        workbook = load_workbook(
            io.BytesIO(content),
            read_only=True,
            data_only=False,
            keep_links=False,
        )
        if len(workbook.sheetnames) > MAX_WORKSHEETS:
            raise ImportFileError(
                413,
                "too_many_worksheets",
                f"An XLSX workbook may not contain more than {MAX_WORKSHEETS} worksheets.",
            )
        parsed_sheets: list[ParsedSheet] = []
        worksheet_names: set[str] = set()
        total_rows = 0
        for worksheet in workbook.worksheets:
            worksheet_name = worksheet.title
            if (
                not worksheet_name
                or len(worksheet_name) > 128
                or any(ord(character) < 32 or ord(character) == 127 for character in worksheet_name)
                or worksheet_name.casefold() in worksheet_names
            ):
                raise ImportFileError(
                    422, "malformed_file", "Worksheet names must be unique and safely bounded."
                )
            worksheet_names.add(worksheet_name.casefold())
            if worksheet.sheet_state != "visible":
                raise ImportFileError(
                    422,
                    "unsafe_file",
                    "Hidden worksheets are not accepted. Make intended sheets visible and retry.",
                )
            if worksheet.max_column is not None and worksheet.max_column > MAX_COLUMNS:
                raise ImportFileError(
                    413,
                    "too_many_columns",
                    f"A worksheet may not contain more than {MAX_COLUMNS} columns.",
                )
            if worksheet.max_row is not None and worksheet.max_row > max_rows + header_row:
                raise ImportFileError(
                    413,
                    "too_many_rows",
                    f"A worksheet exceeds the configured limit of {max_rows} data rows.",
                )
            iterator = worksheet.iter_rows()
            try:
                header_cells = next(iterator)
                for _ in range(1, header_row):
                    header_cells = next(iterator)
            except StopIteration:
                raise ImportFileError(
                    422,
                    "malformed_file",
                    "The selected header row is not present in every worksheet.",
                ) from None
            headers = _validate_headers([_cell_text(cell.value, cell) for cell in header_cells])
            rows: list[ParsedRow] = []
            blank_rows = 0
            for row_number, cells in enumerate(iterator, start=header_row + 1):
                values = [_cell_text(cell.value, cell) for cell in cells]
                if len(values) > len(headers):
                    extras = values[len(headers) :]
                    if any(value is not None and value.strip() for value in extras):
                        raise ImportFileError(
                            422,
                            "malformed_file",
                            f"Worksheet '{worksheet.title}' row {row_number} contains values "
                            "beyond its header columns.",
                        )
                    values = values[: len(headers)]
                values += [None] * (len(headers) - len(values))
                if all(value is None or not value.strip() for value in values):
                    blank_rows += 1
                    continue
                rows.append(ParsedRow(row_number=row_number, values=values))
                total_rows += 1
                if total_rows > max_rows:
                    raise ImportFileError(
                        413,
                        "too_many_rows",
                        f"The workbook exceeds the configured limit of {max_rows} data rows.",
                    )
            parsed_sheets.append(
                ParsedSheet(
                    name=worksheet.title,
                    headers=headers,
                    rows=rows,
                    blank_rows_ignored=blank_rows,
                )
            )
        if not parsed_sheets:
            raise ImportFileError(422, "malformed_file", "The XLSX workbook has no worksheets.")
        return ParsedFile(file_format="xlsx", worksheets=parsed_sheets)
    except ImportFileError:
        raise
    except Exception:
        # Parser exceptions can contain attacker-controlled XML fragments or internal paths.
        raise ImportFileError(
            422, "malformed_file", "The XLSX file is malformed or unsafe."
        ) from None
    finally:
        if workbook is not None:
            workbook.close()


def parse_file(
    filename: str,
    content: bytes,
    max_rows: int,
    *,
    csv_delimiter: str = ",",
    csv_encoding: str = "utf-8-sig",
    header_row: int = 1,
) -> ParsedFile:
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if not 1 <= header_row <= 25:
        raise ImportFileError(
            422, "invalid_csv_options", "The header row must be between 1 and 25."
        )
    if suffix == "csv":
        return _parse_csv(
            content,
            max_rows,
            delimiter=csv_delimiter,
            encoding=csv_encoding,
            header_row=header_row,
        )
    if suffix == "xlsx":
        return _parse_xlsx(content, max_rows, header_row=header_row)
    raise ImportFileError(
        415, "unsupported_file", "Only UTF-8 CSV and non-macro XLSX files are supported."
    )


def read_limited_upload(upload: BinaryIO, max_file_bytes: int) -> bytes:
    content = bytearray()
    while True:
        chunk = upload.read(min(64 * 1024, max_file_bytes + 1 - len(content)))
        if not chunk:
            break
        content.extend(chunk)
        if len(content) > max_file_bytes:
            raise ImportFileError(
                413,
                "file_too_large",
                f"The upload exceeds the configured {max_file_bytes}-byte file limit.",
            )
    if not content:
        raise ImportFileError(422, "malformed_file", "The uploaded file is empty.")
    return bytes(content)
