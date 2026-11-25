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
    assert "Trace a financial" in core_financial_page.text
    assert "non-authoritative" in core_financial_page.text
    assert "INTEGRATED FINANCIAL OVERVIEW" in core_financial_page.text
    assert "Six measures. One evidence-linked view." in core_financial_page.text
    assert 'id="overview-measures"' in core_financial_page.text
    assert 'id="overview-checks"' in core_financial_page.text
    assert "Choose a general-ledger CSV" in core_financial_page.text
    assert "Load synthetic bank example" in core_financial_page.text
    assert "Load synthetic Revenue control example" in core_financial_page.text
    assert "Select a saved import to restore" in core_financial_page.text
    assert "Content-Security-Policy" in core_financial_page.headers
    assert "script-src 'self'" in core_financial_page.headers["Content-Security-Policy"]
    assert core_financial_script.status_code == 200
    assert "const MEASURE_LABELS" in core_financial_script.text
    assert "measure," in core_financial_script.text
    assert "cash_flow" in core_financial_script.text
    assert "Select saved import" in core_financial_script.text
    assert "The synthetic control example is Revenue-only." in core_financial_script.text
    assert "Measure changed. Select a separate control" in core_financial_script.text
    assert "async function loadOverview()" in core_financial_script.text
    assert "renderOverviewCheck" in core_financial_script.text
    assert 'id="compare-periods-button"' in core_financial_page.text
    assert 'id="generate-insights-button"' in core_financial_page.text
    assert 'id="add-insight-period-button"' in core_financial_page.text
    assert 'id="attention-threshold-percent"' in core_financial_page.text
    assert 'id="prior-period-start"' in core_financial_page.text
    assert 'id="current-period-end"' in core_financial_page.text
    assert "async function compareSelectedPeriods()" in core_financial_script.text
    assert "async function generateSelectedInsights()" in core_financial_script.text
    assert "function renderInsightCard(insight)" in core_financial_script.text
    assert "renderInsightEvidence" in core_financial_script.text
    assert (
        "No causal root cause or expected impact is asserted without supporting evidence."
        in core_financial_script.text
    )
    assert "renderComparisonFinding" in core_financial_script.text
    assert "revealComparisonEvidence" in core_financial_script.text
    assert "Period dates changed. Analyze again to refresh" in core_financial_script.text
    assert "Saved run history is refreshing; analyze again" in core_financial_script.text
    assert (
        "Company changed. The API will load only the selected Company's records."
        in core_financial_script.text
    )
    assert "await loadCompany();" in core_financial_script.text
    assert "state.comparisonRequestId += 1;" in core_financial_script.text
    assert (
        "requestId !== state.comparisonRequestId || state.tenant?.id !== tenantId"
        in core_financial_script.text
    )
    assert '$("#comparison-results").replaceChildren();' in core_financial_script.text
    assert "View evidence" in core_financial_script.text
    assert "innerHTML" not in core_financial_script.text
    assert 'id="load-more-imports"' in core_financial_page.text
    assert "async function loadMoreImports()" in core_financial_script.text
    assert "async function selectSavedImport(importId)" in core_financial_script.text
    assert "/imports/${encodeURIComponent(importId)}" in core_financial_script.text
    assert "/policies/${encodeURIComponent(imported.policy_id)}" in core_financial_script.text
    assert (
        "mapping v${imported.mapping_version} · policy v${imported.policy_version}"
        in core_financial_script.text
    )
    assert "bank_import_id" in core_financial_script.text
    assert "metric=${encodeURIComponent(metric)}" in core_financial_script.text
    assert "policy-definition" in core_financial_script.text
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
    assert "/api/v1/tenants/{tenant_id}/core-financials/overview" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/comparisons" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/insights" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/policies/{policy_id}" in paths
    assert "/api/v1/tenants/{tenant_id}/core-financials/runs" in paths
    evidence_path = "/api/v1/tenants/{tenant_id}/core-financials/runs/{run_id}/evidence"
    assert any(
        parameter["name"] == "category" for parameter in paths[evidence_path]["get"]["parameters"]
    )
    comparison_request_schema = schema["components"]["schemas"]["CoreFinancialComparisonRequest"]
    assert "prior_period" in comparison_request_schema["properties"]
    assert "current_period" in comparison_request_schema["properties"]
    insight_request_schema = schema["components"]["schemas"]["CoreFinancialInsightRequest"]
    assert "periods" in insight_request_schema["properties"]
    assert insight_request_schema["properties"]["periods"]["minItems"] == 2
    assert insight_request_schema["properties"]["periods"]["maxItems"] == 6
    assert "attention_threshold_percent" in insight_request_schema["properties"]
    run_request_schema = schema["components"]["schemas"]["CoreFinancialRunCreateRequest"]
    assert run_request_schema["properties"]["measure"]["enum"] == [
        "revenue",
        "expenses",
        "gross_profit",
        "operating_profit",
        "net_profit",
        "cash_flow",
    ]
    assert "ledger_import_id" in run_request_schema["properties"]
    assert "bank_import_id" in run_request_schema["properties"]
    run_response_schema = schema["components"]["schemas"]["CoreFinancialRunResponse"]
    assert "bank_import_id" in run_response_schema["properties"]
