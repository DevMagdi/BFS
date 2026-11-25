from __future__ import annotations


def test_capacity_workspace_page_assets_and_existing_workflow_routes_are_served(
    client, app
) -> None:
    page = client.get("/capacity-workspace.html")
    script = client.get("/assets/capacity-workspace.js")
    styles = client.get("/assets/capacity-workspace.css")
    api_client = client.get("/assets/api_client.js")
    main_page = client.get("/")

    assert page.status_code == 200
    assert "EVIDENCE-BACKED CAPACITY DECISION WORKSPACE" in page.text
    assert "Import, map, validate, apply." in page.text
    assert "Define the proposed weekly workload." in page.text
    assert "Record a capacity-stage decision." in page.text
    assert "An authorized different owner/admin" in page.text
    assert "Planned hours are not actual work or utilization" in page.text
    assert "aggregate capacity is not employee allocation or skills matching" in page.text
    assert "overall business feasibility" in page.text
    assert page.text.index("/assets/api_client.js") < page.text.index(
        "/assets/capacity-workspace.js"
    )
    assert "Content-Security-Policy" in page.headers
    assert "script-src 'self'" in page.headers["Content-Security-Policy"]

    assert script.status_code == 200
    assert "businessflow.m10.access" in script.text
    assert "imports/${importId}/mapping?${query}" in script.text
    assert "/project-intakes/${intakeId}/assessments" in script.text
    assert "/decisions/${decision.id}/approval" in script.text
    assert "expected_decision_hash" in script.text
    assert "expected_evidence_hash" in script.text
    assert "Overall business feasibility: INCOMPLETE" in script.text
    assert "innerHTML" not in script.text
    assert styles.status_code == 200
    assert ".cw-result-table" in styles.text
    assert api_client.status_code == 200

    assert main_page.status_code == 200
    assert 'id="capacity-workspace-link"' in main_page.text

    paths = app.openapi()["paths"]
    assert "/api/v1/tenants/{tenant_id}/imports" in paths
    assert "/api/v1/tenants/{tenant_id}/project-intakes" in paths
    assert "/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/assessments" in paths
    assert "/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/decisions" in paths
    assert (
        "/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/decisions/{decision_id}/approval"
        in paths
    )
