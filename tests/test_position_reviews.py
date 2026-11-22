from __future__ import annotations

import csv
import io
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _position_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/position-reviews"


def _apply_synthetic_operational_template(
    client,
    tenant_id: str,
    headers: dict[str, str],
    dataset_type: str,
    *,
    attempt: str = "0001",
) -> dict[str, object]:
    base = f"/api/v1/tenants/{tenant_id}"
    template = client.get(
        f"{_position_path(tenant_id)}/demo/templates/{dataset_type}", headers=headers
    )
    assert template.status_code == 200, template.text
    content = template.content
    csv_headers = next(csv.reader(io.StringIO(content.decode("utf-8"))))

    staged = client.post(
        f"{base}/imports",
        files={"file": (f"synthetic_demo_{dataset_type}.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"m10-import-{dataset_type}-{attempt}"},
    )
    assert staged.status_code == 201, staged.text
    import_id = staged.json()["import"]["id"]

    mapped = client.put(
        f"{base}/imports/{import_id}/mapping",
        json={
            "dataset_type": dataset_type,
            "worksheet_name": "CSV",
            "field_mapping": {header: header for header in csv_headers},
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert mapped.status_code == 200, mapped.text

    validated = client.post(f"{base}/imports/{import_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    report = validated.json()
    import_summary = report["import"]
    counts = import_summary["counts"]
    assert counts["invalid_rows"] == 0, report
    assert import_summary["validation_hash"]
    acknowledge_warnings = counts["warning_rows"] > 0
    if dataset_type in {"weekly_capacity", "resource_assignments"}:
        assert counts["warning_rows"] == 0, report
        assert counts["duplicate_rows"] == 0, report
    else:
        preview = client.get(
            f"{base}/imports/{import_id}/preview?limit=50&offset=0", headers=headers
        )
        assert preview.status_code == 200, preview.text
        issue_codes = {issue["code"] for row in preview.json()["items"] for issue in row["issues"]}
        assert issue_codes <= {"duplicate_existing", "duplicate_in_file"}

    confirmed = client.post(
        f"{base}/imports/{import_id}/confirm",
        json={
            "validation_hash": import_summary["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": acknowledge_warnings,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied = client.post(f"{base}/imports/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["import"]["status"] in {"applied", "applied_with_warnings"}
    return applied.json()["import"]


def _register_m10_company_data_source(
    client,
    tenant_id: str,
    headers: dict[str, str],
    dataset_type: str,
    import_id: str,
    *,
    source_key: str,
    source_as_of_at: datetime,
) -> dict[str, object]:
    base = f"/api/v1/tenants/{tenant_id}/company-data"
    source = client.post(
        f"{base}/sources",
        json={
            "source_key": source_key,
            "display_name": f"Synthetic M10 {dataset_type} source",
            "dataset_type": dataset_type,
            "contract_version": "1.0.0",
            "freshness_max_age_hours": 24,
        },
        headers=headers,
    )
    assert source.status_code == 201, source.text
    ingestion = client.post(
        f"{base}/sources/{source.json()['id']}/ingestions",
        json={"import_id": import_id, "source_as_of_at": source_as_of_at.isoformat()},
        headers=headers,
    )
    assert ingestion.status_code == 201, ingestion.text
    return ingestion.json()


def test_synthetic_position_review_full_workflow_finance_provenance_and_disposition(
    client, migration_engine, runtime_engine
) -> None:
    email = f"m10-owner-{uuid4().hex}@example.com"
    owner = register_user(client, email, display_name="Synthetic Review Owner")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M10 Synthetic Demo",
        company_name="Synthetic Demo Company",
        key=f"m10-tenant-{uuid4().hex[:16]}",
    )
    tenant_id = tenant["id"]
    position = _position_path(tenant_id)

    workspace = client.get(f"{position}/demo", headers=headers)
    assert workspace.status_code == 200, workspace.text
    assert workspace.json()["demo_seed"] is None

    seeded = client.post(f"{position}/demo/seed", headers=headers)
    assert seeded.status_code == 201, seeded.text
    seed = seeded.json()
    assert seed["synthetic_label"] == "SYNTHETIC DEMO DATA — NOT CUSTOMER DATA"
    assert seed["baseline_calculation_version"] == "businessflow.capacity-weekly.v1"
    assert seed["baseline_start_week_date"] < seed["current_start_week_date"]
    assert set(seed["current_template_hashes"]) == {
        "employees",
        "projects",
        "weekly_capacity",
        "resource_assignments",
        "financial_export",
    }
    replayed_seed = client.post(f"{position}/demo/seed", headers=headers)
    assert replayed_seed.status_code == 201, replayed_seed.text
    assert replayed_seed.json()["id"] == seed["id"]
    assert replayed_seed.json()["reused"] is True

    direct_write = client.post(
        f"/api/v1/tenants/{tenant_id}/employees",
        json={
            "employee_code": "REAL-001",
            "full_name": "Not Synthetic",
            "job_title": "Engineer",
            "employment_status": "active",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert direct_write.status_code == 409
    assert direct_write.json()["error"]["code"] == "synthetic_demo_operational_api_read_only"
    employee_list = client.get(f"/api/v1/tenants/{tenant_id}/employees", headers=headers)
    assert employee_list.status_code == 200
    assert all(item["employee_code"] != "REAL-001" for item in employee_list.json()["items"])

    # M10 refuses non-template files before storing any parsed source rows.
    finance_history = f"{position}/financial-imports"
    fake_finance = b"source_report,exported_at\nSynthetic test,2026-11-21T12:00:00Z\n"
    refused = client.post(
        finance_history,
        files={"file": ("synthetic-looking.csv", fake_finance, "text/csv")},
        headers={**headers, "Idempotency-Key": "m10-non-template-finance-0001"},
    )
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "synthetic_financial_template_required"
    assert client.get(finance_history, headers=headers).json()["total"] == 0

    operational_history = f"/api/v1/tenants/{tenant_id}/imports"
    non_template = client.post(
        operational_history,
        files={
            "file": (
                "ordinary-workforce.csv",
                b"employee_code,full_name\nREAL-001,Not synthetic\n",
                "text/csv",
            )
        },
        headers={**headers, "Idempotency-Key": "m10-non-template-m4-0001"},
    )
    assert non_template.status_code == 422
    assert non_template.json()["error"]["code"] == "synthetic_operational_template_required"
    assert client.get(operational_history, headers=headers).json()["total"] == 4

    # Even an allowed synthetic byte stream cannot be remapped into different facts.
    employee_template = client.get(f"{position}/demo/templates/employees", headers=headers)
    staged_for_bad_mapping = client.post(
        operational_history,
        files={
            "file": (
                "synthetic_demo_employees.csv",
                employee_template.content,
                "text/csv",
            )
        },
        headers={**headers, "Idempotency-Key": "m10-mapping-negative-0001"},
    )
    assert staged_for_bad_mapping.status_code == 201, staged_for_bad_mapping.text
    bad_mapping = {
        header: header for header in next(csv.reader(io.StringIO(employee_template.text)))
    }
    bad_mapping["employee_code"], bad_mapping["full_name"] = (
        bad_mapping["full_name"],
        bad_mapping["employee_code"],
    )
    rejected_mapping = client.put(
        f"{operational_history}/{staged_for_bad_mapping.json()['import']['id']}/mapping",
        json={
            "dataset_type": "employees",
            "worksheet_name": "CSV",
            "field_mapping": bad_mapping,
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert rejected_mapping.status_code == 422
    assert rejected_mapping.json()["error"]["code"] == "synthetic_operational_mapping_required"

    applied_imports = {
        dataset: _apply_synthetic_operational_template(client, tenant_id, headers, dataset)
        for dataset in ("employees", "projects", "weekly_capacity", "resource_assignments")
    }
    for dataset, import_summary in applied_imports.items():
        assert import_summary["dataset_type"] == dataset
        assert import_summary["file_hash"] == seed["current_template_hashes"][dataset]

    stale_import_id = str(applied_imports["employees"]["id"])

    finance_template = client.get(f"{position}/demo/templates/financial_export", headers=headers)
    assert finance_template.status_code == 200, finance_template.text
    finance_upload = client.post(
        finance_history,
        files={
            "file": (
                "synthetic_demo_financial_export.csv",
                finance_template.content,
                "text/csv",
            )
        },
        headers={**headers, "Idempotency-Key": "m10-synthetic-finance-0001"},
    )
    assert finance_upload.status_code == 201, finance_upload.text
    finance_response = finance_upload.json()
    assert finance_response["financial_import"]["status"] == "validated"
    assert finance_response["validation_report"]["financial_calculations_performed"] is False
    assert finance_response["validation_report"]["currency_conversion_performed"] is False
    confirmed_finance = client.post(
        f"{finance_history}/{finance_response['financial_import']['id']}/confirm",
        json={"validation_hash": finance_response["financial_import"]["validation_hash"]},
        headers=headers,
    )
    assert confirmed_finance.status_code == 200, confirmed_finance.text
    assert confirmed_finance.json()["financial_import"]["status"] == "confirmed"

    attestations = {
        "m5_definition_acknowledged": True,
        "active_cohort_comparability_attested": True,
        "current_capacity_currentness_attested": True,
        "baseline_capacity_currentness_attested": True,
        "committed_assignment_completeness_attested": True,
    }
    body = {
        "start_week_date": seed["current_start_week_date"],
        "baseline_metric_run_id": seed["baseline_metric_run_id"],
        "operational_imports": {
            dataset: applied_imports[dataset]["id"]
            for dataset in ("employees", "projects", "weekly_capacity", "resource_assignments")
        },
        "financial_import_id": finance_response["financial_import"]["id"],
        "synthetic_data_acknowledged": True,
        "attestations": attestations,
    }
    legacy_brief = client.post(
        f"{position}/briefs",
        json=body,
        headers={**headers, "Idempotency-Key": "m10-legacy-review-0001"},
    )
    assert legacy_brief.status_code == 201, legacy_brief.text
    assert all(
        item.get("company_data") is None for item in legacy_brief.json()["operational_imports"]
    )

    stale_ingestion = _register_m10_company_data_source(
        client,
        tenant_id,
        headers,
        "employees",
        stale_import_id,
        source_key="m10-employees-stale",
        source_as_of_at=datetime.now(UTC) - timedelta(days=2),
    )
    assert stale_ingestion["ingestion"]["freshness_status"] == "stale"

    stale_attempt = client.post(
        f"{position}/briefs",
        json=body,
        headers={**headers, "Idempotency-Key": "m10-stale-review-0001"},
    )
    assert stale_attempt.status_code == 409
    assert stale_attempt.json()["error"]["code"] == "company_data_not_eligible_for_m10"

    # A new exact synthetic M4 version can be registered with fresh source-as-of evidence.
    applied_imports["employees"] = _apply_synthetic_operational_template(
        client, tenant_id, headers, "employees", attempt="0002"
    )
    company_data_ingestions = {}
    for dataset, import_summary in applied_imports.items():
        company_data_ingestions[dataset] = _register_m10_company_data_source(
            client,
            tenant_id,
            headers,
            dataset,
            str(import_summary["id"]),
            source_key=f"m10-{dataset.replace('_', '-')}-fresh",
            source_as_of_at=datetime.now(UTC),
        )
        assert company_data_ingestions[dataset]["ingestion"]["freshness_status"] == "fresh"
    body["operational_imports"] = {
        dataset: applied_imports[dataset]["id"]
        for dataset in ("employees", "projects", "weekly_capacity", "resource_assignments")
    }

    create_headers = {**headers, "Idempotency-Key": "m10-synthetic-review-0001"}
    created = client.post(f"{position}/briefs", json=body, headers=create_headers)
    assert created.status_code == 201, created.text
    brief = created.json()
    comparison = brief["result_snapshot"]["operational"]["comparison"]
    assert brief["result_state"] == "review_ready_with_limitations"
    assert comparison["state"] == "available"
    assert comparison["unit"] == "planned hours per week, summed across four weekly buckets"
    assert comparison["baseline_recorded_planned_hours"] == "200.00"
    assert comparison["current_recorded_planned_hours"] == "256.00"
    assert comparison["change_in_recorded_planned_hours"] == "56.00"
    assert brief["result_snapshot"]["financial"]["comparison_state"] == "abstained"
    assert brief["result_snapshot"]["financial"]["observations"]
    assert brief["result_snapshot"]["external_action_taken"] is False
    assert len(brief["operational_imports"]) == 4
    assert all(
        item["file_hash"] == seed["current_template_hashes"][item["dataset_type"]]
        for item in brief["operational_imports"]
    )
    assert all(
        item["company_data"]["freshness_status"] == "fresh" for item in brief["operational_imports"]
    )
    assert {
        item["dataset_type"]: item["company_data"]["ingestion_id"]
        for item in brief["operational_imports"]
    } == {
        dataset: company_data_ingestions[dataset]["ingestion"]["id"]
        for dataset in company_data_ingestions
    }

    # The database trigger independently rejects stale bindings, even when bypassing the API.
    with pytest.raises(DBAPIError) as bridge_error, runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        connection.execute(
            text(
                "INSERT INTO position_review_operational_imports "
                "(tenant_id, company_id, review_id, dataset_type, import_id, "
                "company_data_ingestion_id, file_hash, validation_hash, mapping_version) "
                "VALUES (:tenant_id, :company_id, :review_id, 'employees', :import_id, "
                ":ingestion_id, :file_hash, :validation_hash, :mapping_version)"
            ),
            {
                "tenant_id": tenant_id,
                "company_id": tenant["company"]["id"],
                "review_id": brief["id"],
                "import_id": stale_import_id,
                "ingestion_id": stale_ingestion["ingestion"]["id"],
                "file_hash": stale_ingestion["ingestion"]["file_hash"],
                "validation_hash": stale_ingestion["ingestion"]["validation_hash"],
                "mapping_version": stale_ingestion["ingestion"]["mapping_version"],
            },
        )
    assert (
        bridge_error.value.orig.diag.constraint_name == "position_review_company_data_binding_guard"
    )

    idempotent = client.post(f"{position}/briefs", json=body, headers=create_headers)
    assert idempotent.status_code == 201, idempotent.text
    assert idempotent.json()["id"] == brief["id"]

    loaded = client.get(f"{position}/briefs/{brief['id']}", headers=headers)
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["result_hash"] == brief["result_hash"]
    assert loaded.json()["result_snapshot"] == brief["result_snapshot"]

    abstained_body = {
        **body,
        "financial_import_id": None,
        "attestations": {
            "m5_definition_acknowledged": True,
            "active_cohort_comparability_attested": True,
            "current_capacity_currentness_attested": False,
            "baseline_capacity_currentness_attested": True,
            "committed_assignment_completeness_attested": False,
        },
    }
    abstained_response = client.post(
        f"{position}/briefs",
        json=abstained_body,
        headers={**headers, "Idempotency-Key": "m10-abstained-review-0001"},
    )
    assert abstained_response.status_code == 201, abstained_response.text
    abstained_brief = abstained_response.json()
    abstained_comparison = abstained_brief["result_snapshot"]["operational"]["comparison"]
    assert abstained_comparison["state"] == "abstained"
    assert "change_in_recorded_planned_hours" not in abstained_comparison
    assert abstained_brief["result_snapshot"]["financial"]["state"] == "not_provided"
    assert abstained_brief["result_snapshot"]["financial"]["observations"] == []
    assert any(
        item["area"] == "four_week_operational_change"
        for item in abstained_brief["result_snapshot"]["abstentions"]
    )

    disposition_body = {
        "review_hash": brief["result_hash"],
        "disposition": "follow_up_required",
        "rationale": "Please verify current capacity and assignment completeness.",
    }
    disposition = client.post(
        f"{position}/briefs/{brief['id']}/dispositions",
        json=disposition_body,
        headers=headers,
    )
    assert disposition.status_code == 201, disposition.text
    assert disposition.json()["review_hash"] == brief["result_hash"]
    replayed_disposition = client.post(
        f"{position}/briefs/{brief['id']}/dispositions",
        json=disposition_body,
        headers=headers,
    )
    assert replayed_disposition.status_code == 201, replayed_disposition.text
    assert replayed_disposition.json()["id"] == disposition.json()["id"]
    changed_disposition = client.post(
        f"{position}/briefs/{brief['id']}/dispositions",
        json={
            **disposition_body,
            "disposition": "deferred",
            "rationale": "Changing an immutable decision is not allowed.",
        },
        headers=headers,
    )
    assert changed_disposition.status_code == 409
    assert changed_disposition.json()["error"]["code"] == "position_review_disposition_immutable"
    stale_disposition = client.post(
        f"{position}/briefs/{brief['id']}/dispositions",
        json={**disposition_body, "review_hash": "0" * 64},
        headers=headers,
    )
    assert stale_disposition.status_code == 409
    assert stale_disposition.json()["error"]["code"] == "position_review_version_mismatch"

    member_email = f"m10-member-{uuid4().hex}@example.com"
    member = register_user(client, member_email, display_name="Read-only M10 Member")
    create_member(client, owner, tenant_id, member_email)
    member_headers = auth_headers(member)
    assert client.get(f"{position}/demo", headers=member_headers).status_code == 200
    assert client.get(finance_history, headers=member_headers).status_code == 403
    assert client.get(f"{position}/briefs/{brief['id']}", headers=member_headers).status_code == 403
    denied_disposition = client.post(
        f"{position}/briefs/{brief['id']}/dispositions",
        json=disposition_body,
        headers=member_headers,
    )
    assert denied_disposition.status_code == 403

    other_tenant = create_tenant(
        client,
        owner,
        name="Other Synthetic Workspace",
        company_name="Other Synthetic Company",
        key=f"m10-other-{uuid4().hex[:16]}",
    )
    isolated = client.get(
        f"{_position_path(other_tenant['id'])}/briefs/{brief['id']}", headers=headers
    )
    assert isolated.status_code == 404

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        connection.execute(
            text("UPDATE position_review_briefs SET result_hash = :result_hash WHERE id = :id"),
            {"result_hash": "0" * 64, "id": brief["id"]},
        )

    with pytest.raises(DBAPIError, match="permission denied"), runtime_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM position_review_dispositions WHERE id = :id"),
            {"id": disposition.json()["id"]},
        )


def test_demo_seed_refuses_a_nonempty_company_without_persisting_a_baseline(client) -> None:
    owner = register_user(
        client,
        f"m10-nonempty-owner-{uuid4().hex}@example.com",
        display_name="Nonempty Workspace Owner",
    )
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M10 Nonempty Demo",
        company_name="Synthetic Existing Company",
        key=f"m10-nonempty-{uuid4().hex[:16]}",
    )
    tenant_id = tenant["id"]
    employee = client.post(
        f"/api/v1/tenants/{tenant_id}/employees",
        json={
            "employee_code": "EXISTING-001",
            "full_name": "Existing Employee",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert employee.status_code == 201, employee.text

    rejected = client.post(f"{_position_path(tenant_id)}/demo/seed", headers=headers)
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "demo_company_not_empty"
    workspace = client.get(f"{_position_path(tenant_id)}/demo", headers=headers)
    assert workspace.status_code == 200
    assert workspace.json()["demo_seed"] is None
    assert client.get(f"/api/v1/tenants/{tenant_id}/imports", headers=headers).json()["total"] == 0
