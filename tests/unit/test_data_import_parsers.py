from __future__ import annotations

from datetime import datetime
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from openpyxl import Workbook

from businessflow.data_imports.parsers import (
    ImportFileError,
    parse_file,
    read_limited_upload,
)


def _workbook_bytes(*, formula: bool = False, hidden_sheet: bool = False) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Employees"
    sheet.append(["employee_code", "employment_start_date"])
    sheet.append(["EMP-001", datetime(2026, 11, 21)])
    if formula:
        sheet["A2"] = "=1+1"
    if hidden_sheet:
        hidden = workbook.create_sheet("Hidden")
        hidden.sheet_state = "hidden"
        hidden.append(["value"])
    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _rewrite_archive(content: bytes, extra_entries: dict[str, bytes]) -> bytes:
    output = BytesIO()
    with ZipFile(BytesIO(content)) as source, ZipFile(output, "w", ZIP_DEFLATED) as target:
        for name in source.namelist():
            target.writestr(name, source.read(name))
        for name, value in extra_entries.items():
            target.writestr(name, value)
    return output.getvalue()


def test_csv_parsing_accepts_utf8_bom_unicode_quoted_fields_and_ignores_blank_rows() -> None:
    parsed = parse_file(
        "staff.CSV",
        '\ufeffemployee_code,full_name,notes\nEMP-001,Zoë Haddad,"Cairo, Egypt"\n\n'.encode(),
        max_rows=10,
    )

    assert parsed.file_format == "csv"
    assert parsed.total_rows == 1
    assert parsed.worksheets[0].headers == ["employee_code", "full_name", "notes"]
    assert parsed.worksheets[0].rows[0].row_number == 2
    assert parsed.worksheets[0].rows[0].values == ["EMP-001", "Zoë Haddad", "Cairo, Egypt"]
    assert parsed.worksheets[0].blank_rows_ignored == 1


def test_csv_parser_options_use_explicit_delimiter_encoding_and_header_row() -> None:
    content = "export for review\ncustomer_code;name\nCUS-1;Café Ltd\n".encode("cp1252")
    parsed = parse_file(
        "customers.csv",
        content,
        max_rows=10,
        csv_delimiter=";",
        csv_encoding="cp1252",
        header_row=2,
    )

    assert parsed.worksheets[0].headers == ["customer_code", "name"]
    assert parsed.worksheets[0].rows[0].row_number == 3
    assert parsed.worksheets[0].rows[0].values == ["CUS-1", "Café Ltd"]

    utf16 = parse_file(
        "customers.csv",
        "customer_code|name\nCUS-2|Second\n".encode("utf-16"),
        max_rows=10,
        csv_delimiter="|",
        csv_encoding="utf-16",
    )
    assert utf16.worksheets[0].rows[0].values == ["CUS-2", "Second"]


def test_csv_formula_like_values_remain_inert_text() -> None:
    parsed = parse_file("formula.csv", b'name\n=HYPERLINK("https://invalid")\n', max_rows=1)
    assert parsed.worksheets[0].rows[0].values == ['=HYPERLINK("https://invalid")']


def test_csv_rejects_invalid_encoding_duplicate_normalized_headers_and_extra_cells() -> None:
    cases = [
        (b"name\n\xff", "does not match the selected text encoding"),
        (b"Employee Code,employee_code\nA,B\n", "Column headers must be unique"),
        (b"name\nAlice,extra\n", "contains values beyond the header columns"),
    ]
    for content, message in cases:
        with pytest.raises(ImportFileError, match=message):
            parse_file("data.csv", content, max_rows=10)


def test_csv_row_and_cell_limits_are_enforced() -> None:
    with pytest.raises(ImportFileError) as row_error:
        parse_file("data.csv", b"name\nA\nB\n", max_rows=1)
    assert row_error.value.code == "too_many_rows"

    huge_cell = b"name\n" + (b"x" * 65_537) + b"\n"
    with pytest.raises(ImportFileError) as cell_error:
        parse_file("data.csv", huge_cell, max_rows=1)
    assert cell_error.value.code == "malformed_file"


def test_xlsx_date_cells_become_iso_dates_and_formula_cells_are_rejected() -> None:
    parsed = parse_file("workbook.xlsx", _workbook_bytes(), max_rows=10)
    assert parsed.worksheets[0].name == "Employees"
    assert parsed.worksheets[0].rows[0].values == ["EMP-001", "2026-11-21"]

    with pytest.raises(ImportFileError, match="Formula cells are not accepted"):
        parse_file("workbook.xlsx", _workbook_bytes(formula=True), max_rows=10)


def test_xlsx_rejects_hidden_sheets_external_relationships_macros_and_unsafe_paths() -> None:
    with pytest.raises(ImportFileError, match="Hidden worksheets"):
        parse_file("workbook.xlsx", _workbook_bytes(hidden_sheet=True), max_rows=10)

    valid = _workbook_bytes()
    with pytest.raises(ImportFileError, match="External workbook relationships"):
        parse_file(
            "workbook.xlsx",
            _rewrite_archive(
                valid,
                {"xl/other.rels": b'<Relationships TargetMode="External" />'},
            ),
            max_rows=10,
        )
    with pytest.raises(ImportFileError, match="Macro-enabled workbooks"):
        parse_file("workbook.xlsx", _rewrite_archive(valid, {"xl/vbaProject.bin": b"macro"}), 10)
    with pytest.raises(ImportFileError, match="unsafe ZIP path"):
        parse_file("workbook.xlsx", _rewrite_archive(valid, {"../outside.xml": b"x"}), 10)


def test_xlsx_archive_entry_count_and_upload_size_are_bounded() -> None:
    valid = _workbook_bytes()
    too_many_entries = _rewrite_archive(
        valid, {f"custom/part-{index}.xml": b"x" for index in range(4096)}
    )
    with pytest.raises(ImportFileError) as archive_error:
        parse_file("workbook.xlsx", too_many_entries, max_rows=10)
    assert archive_error.value.code == "too_many_archive_entries"

    with pytest.raises(ImportFileError) as upload_error:
        read_limited_upload(BytesIO(b"12345"), max_file_bytes=4)
    assert upload_error.value.code == "file_too_large"
