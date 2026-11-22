from __future__ import annotations

import re
from decimal import Decimal

from businessflow.metrics.calculation import calculate_metrics
from businessflow.metrics.snapshot import content_hash, deserialize_input_snapshot
from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK_START = "2026-11-16"


def _tenant_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _create_employee(client, base: str, headers: dict[str, str], code: str) -> dict:
    response = client.post(
        f"{base}/employees",
        json={
            "employee_code": code,
            "full_name": f"{code} Test",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _apply_import(
    client,
    base: str,
    headers: dict[str, str],
    *,
    key: str,
    dataset_type: str,
    content: bytes,
    fields: list[str],
) -> dict:
    import_base = f"{base}/imports"
    staged_response = client.post(
        import_base,
        files={"file": ("metric-source.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": key},
    )
    assert staged_response.status_code == 201, staged_response.text
    import_id = staged_response.json()["import"]["id"]
    preview = client.get(
        f"{import_base}/{import_id}/mapping",
        params={"dataset_type": dataset_type, "worksheet_name": "CSV"},
        headers=headers,
    )
    assert preview.status_code == 200, preview.text
    mapping = client.put(
        f"{import_base}/{import_id}/mapping",
        json={
            "dataset_type": dataset_type,
            "worksheet_name": "CSV",
            "field_mapping": {field: field for field in fields},
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert mapping.status_code == 200, mapping.text
    validated = client.post(f"{import_base}/{import_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    report = validated.json()
    totals = report["validation_report"]["totals"]
    assert totals["invalid_rows"] == 0
    assert totals["eligible_rows"] == 1
    assert totals["warning_rows"] == 0
    confirmed = client.post(
        f"{import_base}/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": False,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied = client.post(f"{import_base}/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["links_created"] == 1
    assert applied.json()["import"]["status"] == "applied"
    return {"id": import_id, "apply_response": applied.json()}


def test_metric_catalog_and_runs_are_versioned_reproducible_authorized_and_lineaged(
    client, admin_engine
):
    owner = register_user(client, "metrics-owner@example.com", display_name="Metrics Owner")
    tenant = create_tenant(client, owner, name="Metrics Tenant", key="metrics-tenant-key-0001")
    base = _tenant_path(tenant["id"])
    owner_headers = auth_headers(owner)

    assert client.get(f"{base}/metrics/catalog").status_code == 401
    bad_week = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": "2026-11-17", "week_count": 1},
        headers=owner_headers,
    )
    assert bad_week.status_code == 422
    excessive_horizon = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": WEEK_START, "week_count": 53},
        headers=owner_headers,
    )
    assert excessive_horizon.status_code == 422
    spoofed_scope = client.post(
        f"{base}/metrics/runs",
        json={
            "start_week_date": WEEK_START,
            "week_count": 1,
            "company_id": tenant["company"]["id"],
        },
        headers=owner_headers,
    )
    assert spoofed_scope.status_code == 422

    catalog_response = client.get(f"{base}/metrics/catalog", headers=owner_headers)
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    assert catalog["catalog_version"] == "businessflow.metrics.v1"
    assert catalog["catalog_status"] == "implementation_draft"
    assert catalog["decision_use"] == "observational_only_no_recommendation"
    assert catalog["calculation_version"] == "businessflow.capacity-weekly.v1"
    assert catalog["week_convention"].startswith("Monday through Sunday")
    assert {item["metric_id"] for item in catalog["metrics"]} >= {
        "available_capacity_hours",
        "recorded_committed_hours",
        "recorded_assignment_overload",
        "recorded_skill_inventory_coverage",
    }

    employee_1 = _create_employee(client, base, owner_headers, "EMP-METRIC-1")
    employee_2 = _create_employee(client, base, owner_headers, "EMP-METRIC-2")
    capacity_import = _apply_import(
        client,
        base,
        owner_headers,
        key="metrics-capacity-import-0001",
        dataset_type="weekly_capacity",
        content=(
            b"employee_code,week_start_date,availability_status,scheduled_hours,"
            b"non_project_hours,unavailable_hours,source\n"
            b"EMP-METRIC-1,2026-11-16,confirmed,40.00,8.00,2.00,calendar\n"
        ),
        fields=[
            "employee_code",
            "week_start_date",
            "availability_status",
            "scheduled_hours",
            "non_project_hours",
            "unavailable_hours",
            "source",
        ],
    )
    assert capacity_import["apply_response"]["links_created"] == 1
    unknown_capacity = client.post(
        f"{base}/employees/{employee_2['id']}/weekly-capacity/{WEEK_START}",
        json={"availability_status": "unknown"},
        headers=owner_headers,
    )
    assert unknown_capacity.status_code == 201, unknown_capacity.text

    skill_import = _apply_import(
        client,
        base,
        owner_headers,
        key="metrics-skill-import-0001",
        dataset_type="skills",
        content=b"name,description\nPython,Software development\n",
        fields=["name", "description"],
    )
    assert skill_import["apply_response"]["links_created"] == 1
    skills = client.get(f"{base}/skills?status=active", headers=owner_headers).json()["items"]
    python_skill = next(skill for skill in skills if skill["name"] == "Python")
    employee_skill = client.post(
        f"{base}/employees/{employee_1['id']}/skills",
        json={"skill_id": python_skill["id"], "proficiency": 4},
        headers=owner_headers,
    )
    assert employee_skill.status_code == 201, employee_skill.text

    project_response = client.post(
        f"{base}/projects",
        json={
            "project_code": "METRIC-PRJ-1",
            "name": "Metric test project",
            "status": "planned",
            "start_date": WEEK_START,
            "planned_end_date": "2026-11-29",
        },
        headers=owner_headers,
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    committed_1 = client.post(
        f"{base}/projects/{project['id']}/assignments",
        json={
            "employee_id": employee_1["id"],
            "start_date": WEEK_START,
            "end_date": "2026-11-22",
            "planned_hours_per_week": "32.00",
            "status": "committed",
        },
        headers=owner_headers,
    )
    committed_2 = client.post(
        f"{base}/projects/{project['id']}/assignments",
        json={
            "employee_id": employee_2["id"],
            "start_date": WEEK_START,
            "end_date": "2026-11-22",
            "planned_hours_per_week": "8.00",
            "status": "committed",
        },
        headers=owner_headers,
    )
    proposed = client.post(
        f"{base}/projects/{project['id']}/assignments",
        json={
            "employee_id": employee_1["id"],
            "start_date": WEEK_START,
            "end_date": "2026-11-22",
            "planned_hours_per_week": "12.00",
            "status": "proposed",
        },
        headers=owner_headers,
    )
    assert committed_1.status_code == committed_2.status_code == proposed.status_code == 201

    run_response = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": WEEK_START, "week_count": 2},
        headers=owner_headers,
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()
    assert run["tenant_id"] == tenant["id"]
    assert run["company_id"] == tenant["company"]["id"]
    assert run["metric_catalog_version"] == "businessflow.metrics.v1"
    assert run["calculation_version"] == "businessflow.capacity-weekly.v1"
    assert run["reliability_status"] == "partial"
    assert run["result_snapshot"]["metric_catalog_status"] == "implementation_draft"
    assert run["result_snapshot"]["decision_use"] == "observational_only_no_recommendation"
    capacity_definition = next(
        definition
        for definition in run["result_snapshot"]["metric_definition_snapshot"]
        if definition["metric_id"] == "available_capacity_hours"
    )
    assert capacity_definition["version"] == "1.0.0"
    assert capacity_definition["formula"] == (
        "scheduled_hours - non_project_hours - unavailable_hours"
    )
    comparison_definition = next(
        definition
        for definition in run["result_snapshot"]["metric_definition_snapshot"]
        if definition["metric_id"] == "capacity_minus_recorded_commitments_hours"
    )
    assert "active-employee recorded committed assignment hours" in comparison_definition["formula"]
    assert re.fullmatch(r"[0-9a-f]{64}", run["input_hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", run["result_hash"])

    result = run["result_snapshot"]
    first_week, second_week = result["weekly"]
    assert first_week["capacity"]["confirmed_employee_count"] == 1
    assert first_week["capacity"]["unknown_employee_count"] == 1
    assert first_week["capacity"]["confirmed_available_hours_subtotal"] == "30.00"
    assert first_week["capacity"]["complete_available_hours"] is None
    assert first_week["assignments"]["committed_hours_per_week"] == "40.00"
    assert first_week["assignments"]["proposed_hours_per_week"] == "12.00"
    assert first_week["comparison"]["capacity_minus_recorded_commitments_hours"] is None
    assert first_week["comparison"]["unresolved_employee_comparison_count"] == 1
    assert result["confirmed_overloads"][0]["employee_id"] == employee_1["id"]
    assert result["confirmed_overloads"][0]["overload_hours"] == "2.00"
    assert result["unresolved_employee_comparisons"][0]["employee_id"] == employee_2["id"]
    assert result["unresolved_employee_comparisons"][0]["weeks"][0]["capacity_state"] == "unknown"
    assert second_week["capacity"]["missing_employee_count"] == 2
    assert second_week["assignments"]["committed_hours_per_week"] is None
    assert result["skill_inventory"]["state"] == "partial"
    assert result["skill_inventory"]["active_employees_without_skill_records"] == 1
    assert result["skill_inventory"]["skills"][0]["proficiency_counts"]["4"] == 1
    assert result["provenance"]["distinct_import_source_row_count"] == 2
    assert result["provenance"]["facts_with_import_lineage"] >= 2

    input_capacity = next(
        row
        for row in run["input_snapshot"]["weekly_capacity"]
        if row["employee_id"] == employee_1["id"]
    )
    assert input_capacity["version"] == 1
    assert input_capacity["lineage"][0]["import_id"] == capacity_import["id"]
    assert input_capacity["lineage"][0]["mapping_version"] >= 1
    assert input_capacity["lineage"][0]["file_hash"]
    assert input_capacity["lineage"][0]["row_hash"]
    input_skill = run["input_snapshot"]["skills"][0]
    assert input_skill["lineage"][0]["import_id"] == skill_import["id"]

    replayed = calculate_metrics(deserialize_input_snapshot(run["input_snapshot"]))
    assert replayed == result
    assert content_hash(run["input_snapshot"]) == run["input_hash"]
    assert content_hash(replayed) == run["result_hash"]

    member = register_user(client, "metrics-member@example.com", display_name="Metrics Member")
    create_member(client, owner, tenant["id"], "metrics-member@example.com")
    member_headers = auth_headers(member)
    assert client.get(f"{base}/metrics/catalog", headers=member_headers).status_code == 200
    assert client.get(f"{base}/metrics/runs/{run['id']}", headers=member_headers).json() == run

    latest_unknown = client.post(
        f"{base}/employees/{employee_1['id']}/weekly-capacity/{WEEK_START}",
        json={"availability_status": "unknown"},
        headers=owner_headers,
    )
    assert latest_unknown.status_code == 201
    assert latest_unknown.json()["version"] == 2
    second_run_response = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": WEEK_START, "week_count": 2},
        headers=owner_headers,
    )
    assert second_run_response.status_code == 201, second_run_response.text
    second_run = second_run_response.json()
    second_run_capacity = next(
        row
        for row in second_run["input_snapshot"]["weekly_capacity"]
        if row["employee_id"] == employee_1["id"]
    )
    assert second_run_capacity["version"] == 2
    assert second_run["result_snapshot"]["confirmed_overloads"] == []
    assert second_run["result_snapshot"]["weekly"][0]["capacity"]["unknown_employee_count"] == 2
    assert second_run["input_hash"] != run["input_hash"]

    saved_run = client.get(f"{base}/metrics/runs/{run['id']}", headers=member_headers)
    assert saved_run.status_code == 200
    assert saved_run.json()["input_hash"] == run["input_hash"]
    assert saved_run.json()["result_hash"] == run["result_hash"]
    saved_capacity = next(
        row
        for row in saved_run.json()["input_snapshot"]["weekly_capacity"]
        if row["employee_id"] == employee_1["id"]
    )
    assert saved_capacity["version"] == 1
    assert Decimal(
        saved_run.json()["result_snapshot"]["confirmed_overloads"][0]["overload_hours"]
    ) == Decimal("2.00")

    outsider = register_user(client, "metrics-outsider@example.com", display_name="Outsider")
    outsider_tenant = create_tenant(
        client,
        outsider,
        name="Other Metrics Tenant",
        key="metrics-tenant-key-0002",
    )
    outsider_path = _tenant_path(outsider_tenant["id"])
    hidden = client.get(f"{outsider_path}/metrics/runs/{run['id']}", headers=auth_headers(outsider))
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "metric_run_not_found"
