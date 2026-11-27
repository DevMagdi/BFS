from __future__ import annotations


def test_company_data_foundation_page_exposes_scoped_inventory_and_existing_workflows(client, app):
    page = client.get("/data-foundation.html")
    script = client.get("/assets/data-foundation.js")
    styles = client.get("/assets/data-foundation.css")
    capacity_page = client.get("/capacity-workspace.html")

    assert page.status_code == 200
    assert "COMPANY DATA FOUNDATION" in page.text
    assert "Bounded inventory, not a readiness score." in page.text
    assert "Register an expected source" in page.text
    assert "Record an applied import" in page.text
    assert "user-reported" in page.text
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]
    assert page.text.index("/assets/api_client.js") < page.text.index("/assets/data-foundation.js")

    assert script.status_code == 200
    assert "const base = `/tenants/${encodeURIComponent(tenantId)}/company-data`" in script.text
    assert "requestWithExpectedPermission(`${base}/coverage`)" in script.text
    assert (
        "requestWithExpectedPermission(`${base}/sources?limit=${PAGE_SIZE}&offset=0`)"
        in script.text
    )
    assert (
        "requestWithExpectedPermission(`/tenants/${encodeURIComponent(tenantId)}/imports?limit=${PAGE_SIZE}&offset=0`)"
        in script.text
    )
    assert (
        "requestWithExpectedPermission(`${commitmentPath}?limit=${PAGE_SIZE}&offset=0${commitmentQuery}`)"
        in script.text
    )
    assert "if (error.status === 403) return null;" in script.text
    assert "The static entity catalog remains Company-Context-readable" in script.text
    assert "customer commitments keep their separate customer_commitment permissions" in script.text
    assert "const $$ = (selector, root = document)" in script.text
    assert "company-data/sources/${encodeURIComponent(sourceId)}/ingestions" in script.text
    assert "function clearTenantWorkspace()" in script.text
    assert "if (changedWorkspace) clearTenantWorkspace();" in script.text
    assert "state.tenantDataReady = false;" in script.text
    assert "$(selector).reset();" in script.text
    assert "Universal Data Hub" in page.text
    assert 'id="ud-review-panel"' in page.text
    assert "function renderUniversalData()" in script.text
    assert "function loadUniversalProfileHistory" in script.text
    assert "function applyUniversalMapping" in script.text
    assert "function downloadUniversalEvidence" in script.text
    assert "function loadUniversalRecordLineage" in script.text
    assert "Idempotency-Key" in script.text
    assert "function idempotencyKeyForAttempt" in script.text
    assert 'idempotencyKeyForAttempt("universalUploadAttempt"' in script.text
    assert 'idempotencyKeyForAttempt("universalSyncAttempt"' in script.text
    assert "textContent" in script.text
    assert "innerHTML" not in script.text
    assert "http://localhost" not in script.text
    assert styles.status_code == 200
    assert ".df-coverage-grid" in styles.text

    assert capacity_page.status_code == 200
    assert 'href="/data-foundation.html"' in capacity_page.text

    paths = app.openapi()["paths"]
    coverage_path = "/api/v1/tenants/{tenant_id}/company-data/coverage"
    assert coverage_path in paths
    assert set(paths[coverage_path]) == {"get"}
    universal_prefix = "/api/v1/tenants/{tenant_id}/company-data/universal"
    universal_source = f"{universal_prefix}/sources/{{source_id}}"
    universal_ingestion = f"{universal_source}/ingestions/{{ingestion_id}}"
    assert f"{universal_source}/profile-history" in paths
    assert f"{universal_source}/sync" in paths
    assert f"{universal_ingestion}/evidence" in paths
    assert f"{universal_ingestion}/promote" in paths
    assert f"{universal_prefix}/records/{{record_id}}/lineage" in paths
