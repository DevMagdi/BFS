from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

from businessflow.metrics.snapshot import content_hash
from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK_START = "2025-11-03"


def _tenant_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _apply_import(
    client,
    base: str,
    headers: dict[str, str],
    *,
    dataset_type: str,
    file_name: str,
    content: bytes,
    fields: list[str],
) -> str:
    import_base = f"{base}/imports"
    staged = client.post(
        import_base,
        files={"file": (file_name, content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"m13-{uuid.uuid4().hex}"},
    )
    assert staged.status_code == 201, staged.text
    import_id = staged.json()["import"]["id"]
    mapped = client.put(
        f"{import_base}/{import_id}/mapping",
        json={
            "dataset_type": dataset_type,
            "worksheet_name": "CSV",
            "field_mapping": {field: field for field in fields},
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert mapped.status_code == 200, mapped.text
    validated = client.post(f"{import_base}/{import_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    validation = validated.json()
    totals = validation["validation_report"]["totals"]
    assert totals["invalid_rows"] == 0
    assert totals["warning_rows"] == 0
    assert totals["eligible_rows"] == content.count(b"\n") - 1
    confirmed = client.post(
        f"{import_base}/{import_id}/confirm",
        json={
            "validation_hash": validation["import"]["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": False,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied = client.post(f"{import_base}/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["import"]["status"] == "applied"
    return import_id


def _register_source(
    client,
    base: str,
    headers: dict[str, str],
    *,
    dataset_type: str,
    contract_version: str,
    source_key: str,
) -> dict:
    response = client.post(
        f"{base}/company-data/sources",
        json={
            "source_key": source_key,
            "display_name": f"M13 {dataset_type} source",
            "dataset_type": dataset_type,
            "contract_version": contract_version,
            "freshness_max_age_hours": 24,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _record_ingestion(
    client,
    base: str,
    headers: dict[str, str],
    *,
    source_id: str,
    import_id: str,
    source_as_of_at: datetime,
) -> dict:
    response = client.post(
        f"{base}/company-data/sources/{source_id}/ingestions",
        json={"import_id": import_id, "source_as_of_at": source_as_of_at.isoformat()},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_metric_run(client, base: str, headers: dict[str, str], start_week_date: str) -> dict:
    response = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": start_week_date, "week_count": 1},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_insight(
    client,
    base: str,
    headers: dict[str, str],
    *,
    baseline_id: str,
    comparison_id: str,
    metric_id: str = "available_capacity_hours",
):
    return client.post(
        f"{base}/metrics/insights/runs",
        json={
            "baseline_metric_run_id": baseline_id,
            "comparison_metric_run_id": comparison_id,
            "metric_id": metric_id,
        },
        headers=headers,
    )


def test_metric_insight_runs_are_versioned_evidence_gated_replayable_and_tenant_scoped(
    client,
):
    owner = register_user(client, "m13-metrics-owner@example.com", display_name="M13 Owner")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M13 Metrics Tenant",
        company_name="M13 Metrics Company",
        key="m13-metrics-tenant-0001",
    )
    base = _tenant_path(tenant["id"])
    company_data_base = f"{base}/company-data"

    catalog_response = client.get(f"{base}/metrics/insights/catalog", headers=headers)
    assert catalog_response.status_code == 200, catalog_response.text
    catalog = catalog_response.json()
    assert catalog["catalog_version"] == "businessflow.metric-insight-catalog.v2"
    assert catalog["source_metric_catalog_version"] == "businessflow.metrics.v1"
    assert catalog["owner_approved_question"]["status"] == "unavailable"
    assert catalog["owner_approved_question"]["reason"] == (
        "owner_approved_business_question_not_configured"
    )
    assert catalog["interpretation_status"] == "unavailable"
    assert "not value judgments" in catalog["direction_semantics"]
    assert "local midnight following its period end" in catalog["period_convention"]
    assert {item["metric_id"] for item in catalog["supported_comparisons"]} == {
        "available_capacity_hours",
        "recorded_committed_hours",
        "capacity_minus_recorded_commitments_hours",
    }
    assert client.get(f"{base}/metrics/insights/catalog").status_code == 401

    contracts_response = client.get(f"{company_data_base}/contracts", headers=headers)
    assert contracts_response.status_code == 200, contracts_response.text
    contracts = contracts_response.json()["items"]
    employee_contract = next(item for item in contracts if item["dataset_type"] == "employees")
    capacity_contract = next(
        item for item in contracts if item["dataset_type"] == "weekly_capacity"
    )

    employee_import = _apply_import(
        client,
        base,
        headers,
        dataset_type="employees",
        file_name="m13-employees.csv",
        content=(
            b"employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
            b"M13-EMP-001,M13 Evidence Employee,Analyst,40.00,active\n"
        ),
        fields=[
            "employee_code",
            "full_name",
            "job_title",
            "standard_weekly_capacity_hours",
            "employment_status",
        ],
    )
    capacity_import = _apply_import(
        client,
        base,
        headers,
        dataset_type="weekly_capacity",
        file_name="m13-capacity.csv",
        content=(
            b"employee_code,week_start_date,availability_status,scheduled_hours,"
            b"non_project_hours,unavailable_hours,source\n"
            b"M13-EMP-001,2025-11-03,confirmed,40.00,8.00,2.00,calendar\n"
            b"M13-EMP-001,2025-11-10,confirmed,40.00,4.00,1.00,calendar\n"
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
    employee_source = _register_source(
        client,
        base,
        headers,
        dataset_type="employees",
        contract_version=employee_contract["version"],
        source_key="m13-employees",
    )
    capacity_source = _register_source(
        client,
        base,
        headers,
        dataset_type="weekly_capacity",
        contract_version=capacity_contract["version"],
        source_key="m13-capacity",
    )
    now = datetime.now(UTC)
    employee_ingestion = _record_ingestion(
        client,
        base,
        headers,
        source_id=employee_source["id"],
        import_id=employee_import,
        source_as_of_at=now,
    )
    capacity_ingestion = _record_ingestion(
        client,
        base,
        headers,
        source_id=capacity_source["id"],
        import_id=capacity_import,
        source_as_of_at=now,
    )
    assert employee_ingestion["ingestion"]["quality_status"] == "passed"
    assert capacity_ingestion["ingestion"]["freshness_status"] == "fresh"

    baseline = _create_metric_run(client, base, headers, "2025-11-03")
    comparison = _create_metric_run(client, base, headers, "2025-11-10")
    assert (
        baseline["result_snapshot"]["weekly"][0]["capacity"]["complete_available_hours"] == "30.00"
    )
    assert (
        comparison["result_snapshot"]["weekly"][0]["capacity"]["complete_available_hours"]
        == "35.00"
    )

    invalid_order = _create_insight(
        client,
        base,
        headers,
        baseline_id=comparison["id"],
        comparison_id=baseline["id"],
    )
    assert invalid_order.status_code == 422
    assert invalid_order.json()["error"]["code"] == "invalid_metric_comparison_period"
    unsupported = _create_insight(
        client,
        base,
        headers,
        baseline_id=baseline["id"],
        comparison_id=comparison["id"],
        metric_id="recorded_skill_inventory_coverage",
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "metric_comparison_unavailable"

    created = _create_insight(
        client,
        base,
        headers,
        baseline_id=baseline["id"],
        comparison_id=comparison["id"],
    )
    assert created.status_code == 201, created.text
    insight = created.json()
    assert insight["tenant_id"] == tenant["id"]
    assert insight["company_id"] == tenant["company"]["id"]
    assert insight["metric_id"] == "available_capacity_hours"
    assert insight["insight_catalog_version"] == "businessflow.metric-insight-catalog.v2"
    assert insight["engine_version"] == "businessflow.metric-insight.v2"
    assert insight["comparison_definition_version"] == (
        "businessflow.metric-comparison.completed-adjacent-weekly.v2"
    )
    assert insight["availability_status"] == "available"
    assert re.fullmatch(r"[0-9a-f]{64}", insight["input_hash"])
    assert re.fullmatch(r"[0-9a-f]{64}", insight["result_hash"])
    assert content_hash(insight["input_snapshot"]) == insight["input_hash"]
    assert content_hash(insight["result_snapshot"]) == insight["result_hash"]

    result = insight["result_snapshot"]
    assert result["metric"]["version"] == "1.0.0"
    assert result["metric"]["definition_hash"]
    assert result["availability_status"] == "available"
    assert result["comparisons"][0]["baseline_value"] == "30.00"
    assert result["comparisons"][0]["comparison_value"] == "35.00"
    assert result["comparisons"][0]["delta"] == "5.00"
    assert result["comparisons"][0]["direction"] == "increased"
    assert result["comparisons"][0]["percentage_change"] == "16.67"
    assert result["comparisons"][0]["percentage_change_status"] == "computed"
    assert result["period_alignment"]["completion_check"]["status"] == "completed"
    assert result["data_evidence"]["status"] == "verified"
    assert {item["verification_status"] for item in result["data_evidence"]["source_imports"]} == {
        "verified"
    }
    assert {item["dataset_types"][0] for item in result["data_evidence"]["source_imports"]} == {
        "employees",
        "weekly_capacity",
    }
    assert result["insights"]["status"] == "unavailable"
    assert result["insights"]["items"] == []
    assert result["causality"]["status"] == "not_evaluated"
    assert any("user-reported" in item for item in result["limitations"])

    replay = client.get(f"{base}/metrics/insights/runs/{insight['id']}/replay", headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["matches_saved_result"] is True
    assert replay.json()["replayed_result_hash"] == insight["result_hash"]
    assert replay.json()["result_snapshot"] == result
    saved = client.get(f"{base}/metrics/insights/runs/{insight['id']}", headers=headers)
    assert saved.status_code == 200, saved.text
    assert saved.json() == insight

    member_email = "m13-workforce-member@example.com"
    member = register_user(client, member_email, display_name="M13 Workforce Member")
    create_member(client, owner, tenant["id"], member_email)
    member_headers = auth_headers(member)
    member_catalog = client.get(f"{base}/metrics/insights/catalog", headers=member_headers)
    assert member_catalog.status_code == 200, member_catalog.text
    denied_member_create = _create_insight(
        client,
        base,
        member_headers,
        baseline_id=baseline["id"],
        comparison_id=comparison["id"],
    )
    assert denied_member_create.status_code == 403
    assert (
        client.get(
            f"{base}/metrics/insights/runs/{insight['id']}", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/metrics/insights/runs/{insight['id']}/replay", headers=member_headers
        ).status_code
        == 403
    )

    later_capacity_import = _apply_import(
        client,
        base,
        headers,
        dataset_type="weekly_capacity",
        file_name="m13-stale-capacity.csv",
        content=(
            b"employee_code,week_start_date,availability_status,scheduled_hours,"
            b"non_project_hours,unavailable_hours,source\n"
            b"M13-EMP-001,2025-11-17,confirmed,40.00,10.00,0.00,calendar\n"
            b"M13-EMP-001,2025-11-24,confirmed,40.00,12.00,0.00,calendar\n"
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
    old_ingestion = _record_ingestion(
        client,
        base,
        headers,
        source_id=capacity_source["id"],
        import_id=later_capacity_import,
        source_as_of_at=datetime.now(UTC) - timedelta(days=4),
    )
    assert old_ingestion["ingestion"]["quality_status"] == "passed"
    assert old_ingestion["ingestion"]["freshness_status"] == "stale"
    stale_baseline = _create_metric_run(client, base, headers, "2025-11-17")
    stale_comparison = _create_metric_run(client, base, headers, "2025-11-24")
    stale_created = _create_insight(
        client,
        base,
        headers,
        baseline_id=stale_baseline["id"],
        comparison_id=stale_comparison["id"],
    )
    assert stale_created.status_code == 201, stale_created.text
    stale_result = stale_created.json()["result_snapshot"]
    assert stale_created.json()["availability_status"] == "abstained"
    assert stale_result["comparisons"] == []
    assert any(item["reason"] == "source_freshness_stale" for item in stale_result["abstentions"])
    stale_source = next(
        item
        for item in stale_result["data_evidence"]["source_imports"]
        if item["import_id"] == later_capacity_import
    )
    assert stale_source["verification_status"] == "stale"
    assert stale_source["company_data"]["freshness_status"] == "stale"

    outsider = register_user(client, "m13-outsider@example.com", display_name="M13 Outsider")
    outsider_tenant = create_tenant(
        client,
        outsider,
        name="M13 Other Tenant",
        key="m13-metrics-tenant-0002",
    )
    hidden = client.get(
        f"{_tenant_path(outsider_tenant['id'])}/metrics/insights/runs/{insight['id']}",
        headers=auth_headers(outsider),
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "metric_insight_run_not_found"


def test_metric_insight_rejects_not_yet_completed_periods(client):
    owner = register_user(client, "m13-incomplete-owner@example.com", display_name="M13 Owner")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M13 Incomplete Tenant",
        company_name="M13 Incomplete Company",
        key="m13-incomplete-tenant-0001",
    )
    base = _tenant_path(tenant["id"])
    today = datetime.now(UTC).date()
    days_until_next_monday = 7 - today.weekday()
    next_monday = today + timedelta(days=days_until_next_monday)
    baseline = _create_metric_run(client, base, headers, next_monday.isoformat())
    comparison = _create_metric_run(
        client, base, headers, (next_monday + timedelta(days=7)).isoformat()
    )

    response = _create_insight(
        client,
        base,
        headers,
        baseline_id=baseline["id"],
        comparison_id=comparison["id"],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "metric_comparison_period_incomplete"


def test_metric_insight_rejects_runs_with_incompatible_company_timezone_scope(client):
    owner = register_user(client, "m13-timezone-owner@example.com", display_name="M13 Owner")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M13 Timezone Tenant",
        company_name="M13 Timezone Company",
        key="m13-timezone-tenant-0001",
    )
    base = _tenant_path(tenant["id"])
    baseline = _create_metric_run(client, base, headers, "2025-11-03")

    timezone_update = client.patch(
        f"{base}/company",
        json={"timezone": "Africa/Cairo"},
        headers=headers,
    )
    assert timezone_update.status_code == 200, timezone_update.text
    comparison = _create_metric_run(client, base, headers, "2025-11-10")
    response = _create_insight(
        client,
        base,
        headers,
        baseline_id=baseline["id"],
        comparison_id=comparison["id"],
    )

    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "metric_comparison_scope_incompatible"
