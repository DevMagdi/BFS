from __future__ import annotations

import os

os.environ.setdefault("APP_SECRET_KEY", "m10-browser-test-secret-" + "x" * 40)
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://bf_runtime:m10-browser-test@127.0.0.1:5432/businessflow",
)

from fastapi.testclient import TestClient

from businessflow.main import app


def test_static_m10_workflow_and_company_scoped_api_routes_are_served() -> None:
    with TestClient(app) as client:
        page = client.get("/")
        api_client = client.get("/assets/api_client.js")
        script = client.get("/assets/app.js")
        styles = client.get("/assets/styles.css")
        core_financial_page = client.get("/core-financials.html")
        core_financial_script = client.get("/assets/core-financials.js")
        core_financial_styles = client.get("/assets/core-financials.css")

    assert page.status_code == 200
    assert "SYNTHETIC DATA ONLY" in page.text
    assert "Create a local account" not in page.text
    assert "Create Review" in page.text
    assert "Position Decision Brief" in page.text
    assert page.text.index("/assets/api_client.js") < page.text.index("/assets/app.js")
    assert "Record Review" in page.text
    assert "Content-Security-Policy" in page.headers
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert api_client.status_code == 200
    assert "refreshInFlight" in api_client.text
    assert script.status_code == 200
    assert "businessflow.m10.access" in script.text
    assert "/auth/register" not in script.text
    assert styles.status_code == 200
    assert "--teal" in styles.text
    assert "core-financials.html" in page.text
    assert core_financial_page.status_code == 200
    assert "Trace one" in core_financial_page.text
    assert "non-authoritative" in core_financial_page.text
    assert "Choose a ledger CSV" in core_financial_page.text
    assert "Content-Security-Policy" in core_financial_page.headers
    assert "script-src 'self'" in core_financial_page.headers["Content-Security-Policy"]
    assert core_financial_script.status_code == 200
    assert 'measure: "revenue"' in core_financial_script.text
    assert "bank_import_id" not in core_financial_script.text
    assert "/runs/${state.run.id}/evidence?metric=revenue" in core_financial_script.text
    assert "innerHTML" not in core_financial_script.text
    assert core_financial_styles.status_code == 200
    assert ".cf-workflow" in core_financial_styles.text

    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/tenants/{tenant_id}/position-reviews/demo/seed" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/financial-imports" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/briefs" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/briefs/{review_id}/dispositions" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/imports" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/runs" in paths
    run_request_schema = schema["components"]["schemas"]["CoreFinancialRunCreateRequest"]
    assert run_request_schema["properties"]["measure"]["const"] == "revenue"
    assert "ledger_import_id" in run_request_schema["properties"]
    assert "bank_import_id" not in run_request_schema["properties"]
    run_response_schema = schema["components"]["schemas"]["CoreFinancialRunResponse"]
    assert "bank_import_id" not in run_response_schema["properties"]
