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
        script = client.get("/assets/app.js")
        styles = client.get("/assets/styles.css")

    assert page.status_code == 200
    assert "SYNTHETIC DATA ONLY" in page.text
    assert "Create Review" in page.text
    assert "Position Decision Brief" in page.text
    assert "Record Review" in page.text
    assert "Content-Security-Policy" in page.headers
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert script.status_code == 200
    assert "businessflow.m10.access" in script.text
    assert styles.status_code == 200
    assert "--teal" in styles.text

    schema = app.openapi()
    paths = schema["paths"]
    assert "/api/v1/tenants/{tenant_id}/position-reviews/demo/seed" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/financial-imports" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/briefs" in paths
    assert "/api/v1/tenants/{tenant_id}/position-reviews/briefs/{review_id}/dispositions" in paths
