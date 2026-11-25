from __future__ import annotations

from typing import Any

from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK_FEASIBLE = "2026-11-23"
WEEK_NOT_FEASIBLE = "2026-11-30"
WEEK_INCOMPLETE = "2026-12-07"


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _import_csv(
    client,
    base: str,
    headers: dict[str, str],
    *,
    key: str,
    filename: str,
    dataset: str,
    content: bytes,
) -> dict[str, Any]:
    staged_response = client.post(
        f"{base}/imports",
        files={"file": (filename, content, "text/csv")},
        headers={**headers, "Idempotency-Key": key},
    )
    assert staged_response.status_code == 201, staged_response.text
    staged = staged_response.json()
    import_id = staged["import"]["id"]
    assert "CSV" in [sheet["name"] for sheet in staged["inspection"]["worksheets"]]
    assert dataset in staged["suggested_datasets"]

    mapping_response = client.get(
        f"{base}/imports/{import_id}/mapping",
        params={"dataset_type": dataset, "worksheet_name": "CSV"},
        headers=headers,
    )
    assert mapping_response.status_code == 200, mapping_response.text
    fields = [field["name"] for field in mapping_response.json()["field_definitions"]]
    mapped_headers = content.decode("utf-8").splitlines()[0].split(",")
    mapping = {field: field for field in fields if field in mapped_headers}
    assert set(mapping.values()) == set(mapped_headers)

    save_mapping = client.put(
        f"{base}/imports/{import_id}/mapping",
        json={
            "dataset_type": dataset,
            "worksheet_name": "CSV",
            "field_mapping": mapping,
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert save_mapping.status_code == 200, save_mapping.text
    report_response = client.post(f"{base}/imports/{import_id}/validate", headers=headers)
    assert report_response.status_code == 200, report_response.text
    report = report_response.json()
    assert report["import"]["counts"]["invalid_rows"] == 0
    assert report["import"]["counts"]["warning_rows"] == 0
    assert report["import"]["validation_hash"]

    confirmation = client.post(
        f"{base}/imports/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": False,
        },
        headers=headers,
    )
    assert confirmation.status_code == 200, confirmation.text
    applied = client.post(f"{base}/imports/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["import"]["status"] == "applied"
    assert result["links_created"] > 0
    return result["import"]


def _intake_payload(code: str, week: str, hours: str) -> dict[str, object]:
    return {
        "project_code": code,
        "name": f"Proposed workload {code}",
        "start_week_date": week,
        "week_count": 1,
        "total_demand_hours": hours,
        "demand_unit": "team_hours",
        "weekly_demand": [{"week_start_date": week, "hours": hours}],
        "demand_distribution_confirmed": True,
        "notes": None,
    }


def _create_assessment(client, base: str, headers: dict[str, str], intake: dict) -> dict:
    response = client.post(
        f"{base}/project-intakes/{intake['id']}/assessments",
        json={
            "intake_version": intake["current_version"],
            "assumptions": {
                "capacity_data_current_confirmed": True,
                "recorded_commitments_complete_confirmed": True,
            },
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _submit_decision(
    client,
    base: str,
    headers: dict[str, str],
    intake: dict,
    assessment: dict,
    outcome: str,
    *,
    expected_version: int | None = None,
    requested_information: list[str] | None = None,
) -> dict:
    body: dict[str, object] = {
        "assessment_id": assessment["id"],
        "outcome": outcome,
        "rationale": "A human reviewed this exact capacity-stage evidence.",
        "conditions": [],
        "requested_information": requested_information or [],
    }
    if expected_version is not None:
        body["expected_version"] = expected_version
    response = client.post(
        f"{base}/project-intakes/{intake['id']}/decisions",
        json=body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _review_decision(
    client,
    base: str,
    headers: dict[str, str],
    intake: dict,
    decision: dict,
    outcome: str,
) -> Any:
    return client.post(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}/approval",
        json={
            "expected_decision_hash": decision["decision_hash"],
            "expected_evidence_hash": decision["evidence_hash"],
            "outcome": outcome,
            "rationale": "Reviewed the exact current decision and its evidence hashes.",
        },
        headers=headers,
    )


def test_m4_to_m7_workspace_covers_statuses_lineage_source_change_and_separate_review(
    client,
):
    owner = register_user(
        client, "capacity-workspace-owner@example.com", display_name="Capacity Submitter"
    )
    tenant = create_tenant(
        client,
        owner,
        name="Capacity Workspace Tenant",
        company_name="Capacity Workspace Company",
        key="capacity-workspace-tenant-001",
    )
    base = _base(tenant["id"])
    owner_headers = auth_headers(owner)

    reviewer = register_user(
        client, "capacity-workspace-reviewer@example.com", display_name="Independent Reviewer"
    )
    create_member(client, owner, tenant["id"], "capacity-workspace-reviewer@example.com", "admin")
    reviewer_headers = auth_headers(reviewer)
    member = register_user(
        client, "capacity-workspace-reader@example.com", display_name="Read Only Member"
    )
    create_member(client, owner, tenant["id"], "capacity-workspace-reader@example.com", "member")
    member_headers = auth_headers(member)

    employee_import = _import_csv(
        client,
        base,
        owner_headers,
        key="capacity-workspace-employees-001",
        filename="company-employees.csv",
        dataset="employees",
        content=(
            b"employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
            b"CAP-EMP-001,Test Colleague,Engineer,40.00,active\n"
        ),
    )
    capacity_import = _import_csv(
        client,
        base,
        owner_headers,
        key="capacity-workspace-capacity-001",
        filename="weekly-capacity.csv",
        dataset="weekly_capacity",
        content=(
            "employee_code,week_start_date,availability_status,scheduled_hours,"
            "non_project_hours,unavailable_hours,source\n"
            f"CAP-EMP-001,{WEEK_FEASIBLE},confirmed,40.00,0.00,0.00,manual\n"
            f"CAP-EMP-001,{WEEK_NOT_FEASIBLE},confirmed,40.00,0.00,0.00,manual\n"
        ).encode(),
    )

    member_import_history = client.get(f"{base}/imports?limit=100&offset=0", headers=member_headers)
    assert member_import_history.status_code == 200
    assert {item["id"] for item in member_import_history.json()["items"]} >= {
        employee_import["id"],
        capacity_import["id"],
    }
    member_write = client.post(
        f"{base}/project-intakes",
        json=_intake_payload("CAP-MEMBER-DENIED", WEEK_FEASIBLE, "10.00"),
        headers=member_headers,
    )
    assert member_write.status_code == 403

    # Feasible result: follow M4 imports through M5/M6, then submit an M7 decision.
    feasible_intake_response = client.post(
        f"{base}/project-intakes",
        json=_intake_payload("CAP-FEASIBLE", WEEK_FEASIBLE, "20.00"),
        headers=owner_headers,
    )
    assert feasible_intake_response.status_code == 201, feasible_intake_response.text
    feasible_intake = feasible_intake_response.json()
    first_assessment = _create_assessment(client, base, owner_headers, feasible_intake)
    assert first_assessment["capacity_status"] == "feasible"
    assert first_assessment["business_feasibility_status"] == "incomplete"
    assert first_assessment["result_snapshot"]["capacity_scope"] == "company_aggregate"
    assert first_assessment["result_snapshot"]["project_or_employee_assignments_created"] is False
    feasible_week = first_assessment["result_snapshot"]["weekly"][0]
    assert feasible_week["available_capacity_hours"] == "40.00"
    assert feasible_week["demand"]["hours"] == "20.00"
    assert feasible_week["existing_committed_hours"] == "0.00"
    assert feasible_week["existing_commitments_state"] == "human_attested_zero"
    assert feasible_week["remaining_capacity_before_project_hours"] == "40.00"

    metric = client.get(
        f"{base}/metrics/runs/{first_assessment['metric_run_id']}", headers=owner_headers
    )
    assert metric.status_code == 200, metric.text
    metric_input = metric.json()["input_snapshot"]
    capacity_input = next(
        item for item in metric_input["weekly_capacity"] if item["week_start_date"] == WEEK_FEASIBLE
    )
    capacity_lineage = capacity_input["lineage"]
    assert any(
        item["import_id"] == capacity_import["id"]
        and item["file_hash"] == capacity_import["file_hash"]
        and item["mapping_version"] == capacity_import["mapping_version"]
        for item in capacity_lineage
    )
    assert any(
        item["import_id"] == employee_import["id"]
        for item in metric_input["employees"][0]["lineage"]
    )
    assert metric.json()["input_hash"] == first_assessment["metric_input_hash"]
    assert metric.json()["result_hash"] == first_assessment["metric_result_hash"]

    first_decision = _submit_decision(
        client,
        base,
        owner_headers,
        feasible_intake,
        first_assessment,
        "proceed_to_financial_evaluation",
    )
    assert first_decision["status"] == "pending_approval"
    assert first_decision["assessment_id"] == first_assessment["id"]
    assert first_decision["metric_run_id"] == first_assessment["metric_run_id"]
    assert first_decision["business_feasibility_status"] == "incomplete"

    self_review = _review_decision(
        client, base, owner_headers, feasible_intake, first_decision, "approved"
    )
    assert self_review.status_code == 403
    assert self_review.json()["error"]["code"] == "decision_separation_of_duties"

    # A changed canonical capacity becomes stale for approval after a newer M5 run.
    employee_page = client.get(f"{base}/employees?limit=100&offset=0", headers=owner_headers)
    assert employee_page.status_code == 200, employee_page.text
    employee_id = employee_page.json()["items"][0]["id"]
    changed = client.post(
        f"{base}/employees/{employee_id}/weekly-capacity/{WEEK_FEASIBLE}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": "50.00",
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
            "source": "manual",
        },
        headers=owner_headers,
    )
    assert changed.status_code == 201, changed.text
    newer_metric = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": WEEK_FEASIBLE, "week_count": 1},
        headers=owner_headers,
    )
    assert newer_metric.status_code == 201, newer_metric.text
    assert newer_metric.json()["input_hash"] != first_assessment["metric_input_hash"]
    blocked = _review_decision(
        client, base, reviewer_headers, feasible_intake, first_decision, "approved"
    )
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "capacity_assessment_stale"
    stale_detail = client.get(
        f"{base}/project-intakes/{feasible_intake['id']}/decisions/{first_decision['id']}",
        headers=owner_headers,
    )
    assert stale_detail.status_code == 200
    assert stale_detail.json()["evidence_freshness"] == "stale"
    assert stale_detail.json()["approval"] is None

    current_assessment = _create_assessment(client, base, owner_headers, feasible_intake)
    assert current_assessment["capacity_status"] == "feasible"
    second_decision = _submit_decision(
        client,
        base,
        owner_headers,
        feasible_intake,
        current_assessment,
        "proceed_to_financial_evaluation",
        expected_version=1,
    )
    assert second_decision["version"] == 2
    assert second_decision["supersedes_decision_id"] == first_decision["id"]
    approved = _review_decision(
        client, base, reviewer_headers, feasible_intake, second_decision, "approved"
    )
    assert approved.status_code == 201, approved.text
    approval = approved.json()
    assert approval["decision_hash"] == second_decision["decision_hash"]
    assert approval["evidence_hash"] == second_decision["evidence_hash"]
    assert approval["assessment_id"] == current_assessment["id"]
    assert approval["metric_run_id"] == current_assessment["metric_run_id"]

    # Not feasible: no weekly averaging and the only decline option remains capacity-stage.
    not_feasible_response = client.post(
        f"{base}/project-intakes",
        json=_intake_payload("CAP-NOT-FEASIBLE", WEEK_NOT_FEASIBLE, "45.00"),
        headers=owner_headers,
    )
    assert not_feasible_response.status_code == 201, not_feasible_response.text
    not_feasible_intake = not_feasible_response.json()
    not_feasible_assessment = _create_assessment(client, base, owner_headers, not_feasible_intake)
    assert not_feasible_assessment["capacity_status"] == "not_feasible"
    assert not_feasible_assessment["business_feasibility_status"] == "incomplete"
    assert not_feasible_assessment["result_snapshot"]["weekly"][0]["deficit_hours"] == "5.00"
    declined = _submit_decision(
        client,
        base,
        owner_headers,
        not_feasible_intake,
        not_feasible_assessment,
        "capacity_declined",
    )
    change_request = _review_decision(
        client, base, reviewer_headers, not_feasible_intake, declined, "changes_requested"
    )
    assert change_request.status_code == 201, change_request.text
    assert change_request.json()["outcome"] == "changes_requested"

    # Incomplete: a missing capacity week remains unknown, never zero, and can request data.
    incomplete_response = client.post(
        f"{base}/project-intakes",
        json=_intake_payload("CAP-INCOMPLETE", WEEK_INCOMPLETE, "20.00"),
        headers=owner_headers,
    )
    assert incomplete_response.status_code == 201, incomplete_response.text
    incomplete_intake = incomplete_response.json()
    incomplete_assessment = _create_assessment(client, base, owner_headers, incomplete_intake)
    assert incomplete_assessment["capacity_status"] == "incomplete"
    incomplete_week = incomplete_assessment["result_snapshot"]["weekly"][0]
    assert incomplete_week["available_capacity_hours"] is None
    assert incomplete_week["existing_committed_hours"] is None
    assert incomplete_week["projected_remaining_capacity_hours"] is None
    assert incomplete_week["demand"]["hours"] == "20.00"
    assert any(
        item["code"] == "capacity_incomplete"
        for item in incomplete_assessment["result_snapshot"]["blockers"]
    )
    information_request = _submit_decision(
        client,
        base,
        owner_headers,
        incomplete_intake,
        incomplete_assessment,
        "request_information",
        requested_information=["Provide current weekly capacity evidence for the missing week."],
    )
    assert information_request["capacity_status"] == "incomplete"
    requested = _review_decision(
        client, base, reviewer_headers, incomplete_intake, information_request, "changes_requested"
    )
    assert requested.status_code == 201, requested.text
    assert requested.json()["outcome"] == "changes_requested"

    final_history = client.get(
        f"{base}/project-intakes/{incomplete_intake['id']}/decisions",
        headers=owner_headers,
    )
    assert final_history.status_code == 200, final_history.text
    assert final_history.json()["items"][0]["approval"]["outcome"] == "changes_requested"
