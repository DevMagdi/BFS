from __future__ import annotations

import csv
import io
import json
from uuid import UUID

from openpyxl import load_workbook

from businessflow.business_reports.contracts import ReportExportCreateRequest
from businessflow.business_reports.rendering import (
    render_report,
    safe_spreadsheet_text,
)


def _document(title: str) -> dict[str, object]:
    return {
        "manifest": {
            "schema_version": 1,
            "engine_version": "businessflow.reporting.v1",
            "report_id": "b89b6d4a-4e1e-4d21-9f8b-ec881e46f95e",
            "tenant_id": "7c6dcf21-5d78-48df-9954-c10da20b6cc6",
            "company_id": "323e8a4c-8b10-459b-93f0-a3c29f3a6191",
            "title": title,
            "format": "xlsx",
            "mode": "nonproduction_prototype",
            "generated_at": "2026-11-21T12:00:00+00:00",
            "expires_at": "2026-11-28T12:00:00+00:00",
            "scope": {"currency_codes": ["USD", "EUR"]},
            "governance": {"workbook_formulas": "none"},
            "sources": [
                {
                    "kind": "business_intelligence_analysis",
                    "id": "d7e1d0a5-7e33-463f-aae7-e2b027fc5164",
                    "quality": {
                        "analysis_status": "complete",
                        "source_freshness_status": "within_policy_at_analysis_time",
                    },
                    "assumptions": ["Source authority remains unverified."],
                    "lineage": {"input_hash": "a" * 64, "result_hash": "b" * 64},
                }
            ],
        },
        "sources": [
            {
                "kind": "business_intelligence_analysis",
                "id": "d7e1d0a5-7e33-463f-aae7-e2b027fc5164",
                "label": "sales.source_amount",
                "status": "complete",
                "currency_codes": ["USD"],
                "quality": {
                    "analysis_status": "complete",
                    "source_freshness_status": "within_policy_at_analysis_time",
                },
                "assumptions": ["Source authority remains unverified."],
                "evidence_count": 1,
                "lineage": {"input_hash": "a" * 64, "result_hash": "b" * 64},
                "evidence_references": [
                    {
                        "evidence_kind": "universal_record",
                        "record_id": "6b5e6807-564c-4a42-b962-74ddf1b13831",
                        "row_hash": "c" * 64,
                    }
                ],
                "result": {"groups": []},
                "data_points": [
                    {
                        "period_start": "2026-01-01",
                        "period_end": "2026-01-31",
                        "dimensions": {"category": "Retail"},
                        "value": "125.50",
                        "currency_code": "USD",
                        "unit": "source currency amount",
                        "status": "complete",
                        "details": {"source_text": '=HYPERLINK("https://example.invalid")'},
                    }
                ],
            }
        ],
    }


def test_spreadsheet_text_neutralizes_formula_prefixes_and_preserves_plain_text():
    assert safe_spreadsheet_text("=1+1") == "'=1+1"
    assert safe_spreadsheet_text('  =HYPERLINK("x")') == '\'  =HYPERLINK("x")'
    assert safe_spreadsheet_text("\t@SUM(A1:A2)") == "'\t@SUM(A1:A2)"
    assert safe_spreadsheet_text("-12.50") == "'-12.50"
    assert safe_spreadsheet_text("BusinessFlow report") == "BusinessFlow report"


def test_csv_escapes_user_text_and_keeps_values_and_lineage():
    title = '=HYPERLINK("https://example.invalid","open")'
    content, media_type, extension = render_report(_document(title), "csv")

    assert media_type == "text/csv; charset=utf-8"
    assert extension == "csv"
    records = list(csv.DictReader(io.StringIO(content.decode("utf-8"))))
    assert len(records) == 2
    assert records[0]["record_type"] == "report_manifest"
    manifest = json.loads(records[0]["report_manifest_json"])
    assert manifest["scope"]["currency_codes"] == ["USD", "EUR"]
    assert manifest["sources"][0]["quality"]["source_freshness_status"] == (
        "within_policy_at_analysis_time"
    )
    assert manifest["sources"][0]["assumptions"] == ["Source authority remains unverified."]
    assert manifest["sources"][0]["lineage"]["input_hash"] == "a" * 64

    data_record = records[1]
    assert data_record["record_type"] == "data_point"
    assert data_record["report_title"] == "'" + title
    assert data_record["value"] == "125.50"
    assert json.loads(data_record["details_json"])["source_text"].startswith("=HYPERLINK")
    assert data_record["input_hash"] == "a" * 64


def test_xlsx_contains_no_formula_cells_and_keeps_governance_and_evidence():
    title = "=1+1"
    content, media_type, extension = render_report(_document(title), "xlsx")
    workbook = load_workbook(io.BytesIO(content), data_only=False)

    assert media_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    assert extension == "xlsx"
    assert workbook.sheetnames == ["Report", "Data", "Lineage"]
    assert all(
        cell.data_type != "f"
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    assert workbook["Report"]["B3"].value == "'" + title
    assert workbook["Report"]["B12"].value == '{"workbook_formulas":"none"}'
    assert workbook["Data"]["M2"].value == "125.50"
    assert workbook["Lineage"]["D2"].value == "a" * 64
    assert "universal_record" in str(workbook["Lineage"]["D4"].value)
    assert any(row[2].value == "quality" for row in workbook["Lineage"].iter_rows(min_row=2))
    assert any(
        row[2].value == "assumptions" and "Source authority" in str(row[3].value)
        for row in workbook["Lineage"].iter_rows(min_row=2)
    )


def test_json_export_is_canonical_and_retains_nonproduction_provenance():
    content, media_type, extension = render_report(_document("Quarterly snapshot"), "json")
    parsed = json.loads(content)

    assert media_type == "application/json; charset=utf-8"
    assert extension == "json"
    assert parsed["manifest"]["mode"] == "nonproduction_prototype"
    assert parsed["manifest"]["governance"]["workbook_formulas"] == "none"
    assert parsed["sources"][0]["lineage"]["input_hash"] == "a" * 64


def test_report_request_requires_verified_inputs_for_m15_and_rejects_duplicates():
    source_id = UUID("d7e1d0a5-7e33-463f-aae7-e2b027fc5164")
    request = {
        "title": "M15 report",
        "format": "json",
        "sources": [
            {
                "kind": "financial_calculation_run",
                "source_id": str(source_id),
                "intake_id": "6b5e6807-564c-4a42-b962-74ddf1b13831",
                "replay_request": {
                    "snapshot_id": "6f20e4e9-f499-416c-a25e-e65da2d2df04",
                    "reconciliation_group_hash": "a" * 64,
                    "observations": [],
                },
            }
        ],
    }
    assert ReportExportCreateRequest.model_validate(request).sources[0].kind == (
        "financial_calculation_run"
    )

    invalid = {
        "title": "Missing replay input",
        "format": "json",
        "sources": [
            {
                "kind": "financial_calculation_run",
                "source_id": str(source_id),
                "intake_id": "6b5e6807-564c-4a42-b962-74ddf1b13831",
            }
        ],
    }
    try:
        ReportExportCreateRequest.model_validate(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("M15 report inputs must be explicitly re-submitted and verified")
