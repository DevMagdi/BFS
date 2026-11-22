from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from businessflow.data_imports import service as import_service
from businessflow.errors import ApiError
from tests.helpers import auth_headers, create_member, create_tenant, register_user

IMPORT_TABLES = ("data_imports", "data_import_rows", "data_import_record_links")


def _imports_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/imports"


def _upload_import(client, base: str, headers: dict[str, str], key: str, content: bytes):
    response = client.post(
        base,
        files={"file": ("import.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": key},
    )
    return response


def _stage_map_validate(
    client,
    base: str,
    headers: dict[str, str],
    key: str,
    *,
    dataset_type: str,
    content: bytes,
    fields: list[str],
    ignored_columns: list[str] | None = None,
):
    staged_response = _upload_import(client, base, headers, key, content)
    assert staged_response.status_code == 201, staged_response.text
    staged = staged_response.json()
    import_id = staged["import"]["id"]

    mapping_preview = client.get(
        f"{base}/{import_id}/mapping",
        params={"dataset_type": dataset_type, "worksheet_name": "CSV"},
        headers=headers,
    )
    assert mapping_preview.status_code == 200, mapping_preview.text
    mapping = {field: field for field in fields}
    save_mapping = client.put(
        f"{base}/{import_id}/mapping",
        json={
            "dataset_type": dataset_type,
            "worksheet_name": "CSV",
            "field_mapping": mapping,
            "ignored_columns": ignored_columns or [],
        },
        headers=headers,
    )
    assert save_mapping.status_code == 200, save_mapping.text

    validation_response = client.post(f"{base}/{import_id}/validate", headers=headers)
    assert validation_response.status_code == 200, validation_response.text
    return import_id, validation_response.json()


def _apply_valid_import(
    client,
    base: str,
    headers: dict[str, str],
    key: str,
    *,
    dataset_type: str,
    content: bytes,
    fields: list[str],
):
    import_id, report = _stage_map_validate(
        client,
        base,
        headers,
        key,
        dataset_type=dataset_type,
        content=content,
        fields=fields,
    )
    totals = report["validation_report"]["totals"]
    assert totals["invalid_rows"] == 0
    assert totals["eligible_rows"] == 1
    assert totals["warning_rows"] == 0

    confirm_response = client.post(
        f"{base}/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": False,
        },
        headers=headers,
    )
    assert confirm_response.status_code == 200, confirm_response.text
    apply_response = client.post(f"{base}/{import_id}/apply", headers=headers)
    assert apply_response.status_code == 200, apply_response.text
    result = apply_response.json()
    assert result["import"]["status"] == "applied"
    assert result["links_created"] == 1
    assert result["replayed"] is False
    return import_id, result


def test_employee_import_mapping_partial_apply_duplicate_links_and_idempotency(
    client, admin_engine
):
    owner = register_user(client, "import-employee-owner@example.com")
    tenant = create_tenant(
        client, owner, name="Import Employee Tenant", key="import-employee-tenant-001"
    )
    base = _imports_path(tenant["id"])
    owner_headers = auth_headers(owner)
    content = (
        b"employee_code,full_name,job_title,standard_weekly_capacity_hours,"
        b"employment_status,employment_start_date,employment_end_date,review_note\n"
        b"EMP-DUP,First Person,Engineer,40.00,active,2020-01-06,,not-a-field\n"
        b"emp-dup,Second Person,Manager,40.00,active,2021-02-01,,duplicate\n"
        b"EMP-BAD,Invalid Person,Engineer,not-a-number,active,2020-01-06,,bad-row\n"
    )
    key = "import-employees-idempotency-0001"

    staged_response = _upload_import(client, base, owner_headers, key, content)
    assert staged_response.status_code == 201, staged_response.text
    staged = staged_response.json()
    import_id = staged["import"]["id"]
    assert staged["import"]["status"] == "inspected"
    assert staged["inspection"]["total_rows"] == 3
    assert "employees" in staged["suggested_datasets"]

    replayed_upload = _upload_import(client, base, owner_headers, key, content)
    assert replayed_upload.status_code == 201, replayed_upload.text
    assert replayed_upload.json()["import"]["id"] == import_id
    conflict = _upload_import(client, base, owner_headers, key, content + b"\n")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"

    employee_fields = [
        "employee_code",
        "full_name",
        "job_title",
        "standard_weekly_capacity_hours",
        "employment_status",
        "employment_start_date",
        "employment_end_date",
    ]
    mapping_preview = client.get(
        f"{base}/{import_id}/mapping",
        params={"dataset_type": "employees", "worksheet_name": "CSV"},
        headers=owner_headers,
    )
    assert mapping_preview.status_code == 200, mapping_preview.text
    assert mapping_preview.json()["unmapped_columns"] == ["review_note"]

    invalidly_incomplete_mapping = client.put(
        f"{base}/{import_id}/mapping",
        json={
            "dataset_type": "employees",
            "worksheet_name": "CSV",
            "field_mapping": {field: field for field in employee_fields},
            "ignored_columns": [],
        },
        headers=owner_headers,
    )
    assert invalidly_incomplete_mapping.status_code == 422
    assert invalidly_incomplete_mapping.json()["error"]["code"] == "unaccounted_import_columns"

    member = register_user(client, "import-employee-member@example.com")
    create_member(client, owner, tenant["id"], "import-employee-member@example.com")
    member_headers = auth_headers(member)
    assert client.get(f"{base}/{import_id}", headers=member_headers).status_code == 200
    denied_mapping = client.put(
        f"{base}/{import_id}/mapping",
        json={
            "dataset_type": "employees",
            "worksheet_name": "CSV",
            "field_mapping": {field: field for field in employee_fields},
            "ignored_columns": ["review_note"],
        },
        headers=member_headers,
    )
    assert denied_mapping.status_code == 403

    saved_mapping = client.put(
        f"{base}/{import_id}/mapping",
        json={
            "dataset_type": "employees",
            "worksheet_name": "CSV",
            "field_mapping": {field: field for field in employee_fields},
            "ignored_columns": ["review_note"],
        },
        headers=owner_headers,
    )
    assert saved_mapping.status_code == 200, saved_mapping.text

    validation_response = client.post(f"{base}/{import_id}/validate", headers=owner_headers)
    assert validation_response.status_code == 200, validation_response.text
    report = validation_response.json()
    totals = report["validation_report"]["totals"]
    assert totals == {
        "total_rows": 3,
        "valid_rows": 2,
        "invalid_rows": 1,
        "warning_rows": 1,
        "duplicate_rows": 1,
        "eligible_rows": 1,
    }
    preview = client.get(f"{base}/{import_id}/preview", headers=owner_headers)
    assert preview.status_code == 200, preview.text
    assert [row["row_status"] for row in preview.json()["items"]] == [
        "valid",
        "duplicate",
        "invalid",
    ]

    wrong_hash = client.post(
        f"{base}/{import_id}/confirm",
        json={
            "validation_hash": "0" * 64,
            "allow_partial": True,
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert wrong_hash.status_code == 409
    assert wrong_hash.json()["error"]["code"] == "validation_hash_mismatch"

    no_partial = client.post(
        f"{base}/{import_id}/confirm",
        json={"validation_hash": report["import"]["validation_hash"]},
        headers=owner_headers,
    )
    assert no_partial.status_code == 409
    assert no_partial.json()["error"]["code"] == "invalid_rows_require_partial"
    no_warning_ack = client.post(
        f"{base}/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": True,
            "acknowledge_warnings": False,
        },
        headers=owner_headers,
    )
    assert no_warning_ack.status_code == 409
    assert no_warning_ack.json()["error"]["code"] == "warnings_require_acknowledgement"

    confirmed = client.post(
        f"{base}/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": True,
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied = client.post(f"{base}/{import_id}/apply", headers=owner_headers)
    assert applied.status_code == 200, applied.text
    result = applied.json()
    assert result["import"]["status"] == "applied_with_warnings"
    assert result["import"]["counts"]["created_rows"] == 1
    assert result["import"]["counts"]["skipped_rows"] == 2
    assert result["links_created"] == 2

    apply_replay = client.post(f"{base}/{import_id}/apply", headers=owner_headers)
    assert apply_replay.status_code == 200, apply_replay.text
    assert apply_replay.json()["replayed"] is True
    assert apply_replay.json()["links_created"] == 2

    duplicate_only_id, duplicate_only_report = _stage_map_validate(
        client,
        base,
        owner_headers,
        "import-existing-duplicate-key-0001",
        dataset_type="employees",
        content=(
            b"employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
            b"EMP-DUP,Replacement Person,Director,50,active\n"
        ),
        fields=[
            "employee_code",
            "full_name",
            "job_title",
            "standard_weekly_capacity_hours",
            "employment_status",
        ],
    )
    assert duplicate_only_report["validation_report"]["totals"]["duplicate_rows"] == 1
    assert duplicate_only_report["validation_report"]["totals"]["eligible_rows"] == 0
    duplicate_only_confirm = client.post(
        f"{base}/{duplicate_only_id}/confirm",
        json={
            "validation_hash": duplicate_only_report["import"]["validation_hash"],
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert duplicate_only_confirm.status_code == 200, duplicate_only_confirm.text
    duplicate_only_apply = client.post(f"{base}/{duplicate_only_id}/apply", headers=owner_headers)
    assert duplicate_only_apply.status_code == 200, duplicate_only_apply.text
    assert duplicate_only_apply.json()["import"]["counts"]["created_rows"] == 0
    assert duplicate_only_apply.json()["import"]["counts"]["skipped_rows"] == 1
    assert duplicate_only_apply.json()["links_created"] == 1

    after_apply_preview = client.get(f"{base}/{import_id}/preview", headers=owner_headers)
    assert after_apply_preview.status_code == 200, after_apply_preview.text
    assert all(row["source_values"] is None for row in after_apply_preview.json()["items"])

    with admin_engine.connect() as connection:
        source_links = connection.execute(
            text(
                "SELECT r.row_number, r.row_status, r.canonical_id, l.canonical_id AS linked_id "
                "FROM data_import_rows r LEFT JOIN data_import_record_links l "
                "ON l.source_row_id = r.id AND l.tenant_id = r.tenant_id "
                "AND l.company_id = r.company_id WHERE r.import_id = :import_id "
                "ORDER BY r.row_number"
            ),
            {"import_id": UUID(import_id)},
        ).all()
        employee_name = connection.scalar(
            text(
                "SELECT full_name FROM employees WHERE tenant_id = :tenant_id "
                "AND employee_code_key = 'emp-dup'"
            ),
            {"tenant_id": UUID(tenant["id"])},
        )
        audit_details = connection.scalars(
            text(
                "SELECT metadata::text FROM audit_events WHERE tenant_id = :tenant_id "
                "AND target_id = :import_id"
            ),
            {"tenant_id": UUID(tenant["id"]), "import_id": UUID(import_id)},
        ).all()
    assert employee_name == "First Person"
    assert [
        (row.row_status, row.canonical_id is not None, row.linked_id is not None)
        for row in source_links
    ] == [
        ("applied", True, True),
        ("skipped", True, True),
        ("skipped", False, False),
    ]
    assert source_links[0].canonical_id == source_links[1].canonical_id
    assert all("First Person" not in (details or "") for details in audit_details)

    other_owner = register_user(client, "import-employee-other@example.com")
    other_tenant = create_tenant(
        client, other_owner, name="Other Import Tenant", key="import-employee-other-tenant-001"
    )
    hidden = client.get(
        f"{_imports_path(other_tenant['id'])}/{import_id}", headers=auth_headers(other_owner)
    )
    assert hidden.status_code == 404


def test_imports_cover_skills_employee_skills_capacity_projects_and_assignments(client):
    owner = register_user(client, "import-relations-owner@example.com")
    tenant = create_tenant(
        client, owner, name="Import Relations Tenant", key="import-relations-tenant-001"
    )
    base = _imports_path(tenant["id"])
    headers = auth_headers(owner)
    company_base = f"/api/v1/tenants/{tenant['id']}"

    employee_response = client.post(
        f"{company_base}/employees",
        json={
            "employee_code": "IMP-REL",
            "full_name": "Import Relations Employee",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert employee_response.status_code == 201, employee_response.text

    _apply_valid_import(
        client,
        base,
        headers,
        "import-skills-flow-key-0001",
        dataset_type="skills",
        content=b"name,description\nTerraform,Infrastructure as code\n",
        fields=["name", "description"],
    )
    _apply_valid_import(
        client,
        base,
        headers,
        "import-employee-skills-flow-key-001",
        dataset_type="employee_skills",
        content=b"employee_code,skill_name,proficiency\nIMP-REL,Terraform,4\n",
        fields=["employee_code", "skill_name", "proficiency"],
    )
    _apply_valid_import(
        client,
        base,
        headers,
        "import-capacity-flow-key-0001",
        dataset_type="weekly_capacity",
        content=(
            b"employee_code,week_start_date,availability_status,scheduled_hours,"
            b"non_project_hours,unavailable_hours,source\n"
            b"IMP-REL,2026-11-16,confirmed,40.00,8.00,4.00,manual\n"
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
    _apply_valid_import(
        client,
        base,
        headers,
        "import-projects-flow-key-0001",
        dataset_type="projects",
        content=(
            b"project_code,name,status,start_date,planned_end_date,project_manager_employee_code\n"
            b"IMPORTED-PROJ,Imported Initiative,planned,2026-11-16,2026-11-30,IMP-REL\n"
        ),
        fields=[
            "project_code",
            "name",
            "status",
            "start_date",
            "planned_end_date",
            "project_manager_employee_code",
        ],
    )
    _apply_valid_import(
        client,
        base,
        headers,
        "import-assignments-flow-key-001",
        dataset_type="resource_assignments",
        content=(
            b"project_code,employee_code,start_date,end_date,planned_hours_per_week,status,source\n"
            b"IMPORTED-PROJ,IMP-REL,2026-11-16,2026-11-22,12.00,proposed,manual\n"
        ),
        fields=[
            "project_code",
            "employee_code",
            "start_date",
            "end_date",
            "planned_hours_per_week",
            "status",
            "source",
        ],
    )


def test_referenced_company_changes_invalidate_confirmation_and_expired_staging_is_purged(
    client, admin_engine
):
    owner = register_user(client, "import-stale-owner@example.com")
    tenant = create_tenant(client, owner, name="Import Stale Tenant", key="import-stale-tenant-001")
    base = _imports_path(tenant["id"])
    company_base = f"/api/v1/tenants/{tenant['id']}"
    headers = auth_headers(owner)

    employee = client.post(
        f"{company_base}/employees",
        json={
            "employee_code": "STALE-EMP",
            "full_name": "Stale Check Employee",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert employee.status_code == 201, employee.text
    skill = client.post(f"{company_base}/skills", json={"name": "Freshness Skill"}, headers=headers)
    assert skill.status_code == 201, skill.text

    import_id, validation = _stage_map_validate(
        client,
        base,
        headers,
        "import-stale-reference-key-0001",
        dataset_type="employee_skills",
        content=b"employee_code,skill_name,proficiency\nSTALE-EMP,Freshness Skill,3\n",
        fields=["employee_code", "skill_name", "proficiency"],
    )
    old_hash = validation["import"]["validation_hash"]
    update_employee = client.patch(
        f"{company_base}/employees/{employee.json()['id']}",
        json={"job_title": "Principal Engineer"},
        headers=headers,
    )
    assert update_employee.status_code == 200, update_employee.text

    stale = client.post(
        f"{base}/{import_id}/confirm",
        json={"validation_hash": old_hash},
        headers=headers,
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "import_validation_stale"

    refreshed = client.get(f"{base}/{import_id}/report", headers=headers)
    assert refreshed.status_code == 200, refreshed.text
    new_hash = refreshed.json()["import"]["validation_hash"]
    assert new_hash != old_hash
    confirmed = client.post(
        f"{base}/{import_id}/confirm",
        json={"validation_hash": new_hash},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    update_employee_again = client.patch(
        f"{company_base}/employees/{employee.json()['id']}",
        json={"job_title": "Staff Engineer"},
        headers=headers,
    )
    assert update_employee_again.status_code == 200, update_employee_again.text
    stale_apply = client.post(f"{base}/{import_id}/apply", headers=headers)
    assert stale_apply.status_code == 409
    assert stale_apply.json()["error"]["code"] == "import_validation_stale"

    refreshed_again = client.get(f"{base}/{import_id}/report", headers=headers)
    assert refreshed_again.status_code == 200, refreshed_again.text
    final_hash = refreshed_again.json()["import"]["validation_hash"]
    assert final_hash != new_hash
    reconfirmed = client.post(
        f"{base}/{import_id}/confirm",
        json={"validation_hash": final_hash},
        headers=headers,
    )
    assert reconfirmed.status_code == 200, reconfirmed.text
    applied = client.post(f"{base}/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text

    expired_content = (
        b"employee_code,full_name,job_title,standard_weekly_capacity_hours\n"
        b"EXPIRED,Expired,Engineer,40\n"
    )
    staged = _upload_import(client, base, headers, "import-expiry-purge-key-0001", expired_content)
    assert staged.status_code == 201, staged.text
    expired_id = staged.json()["import"]["id"]
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE data_imports SET staging_expires_at = now() - interval '1 minute' "
                "WHERE id = :import_id"
            ),
            {"import_id": UUID(expired_id)},
        )

    expired_summary = client.get(f"{base}/{expired_id}", headers=headers)
    assert expired_summary.status_code == 200
    assert expired_summary.json()["import"]["status"] == "expired"
    preview = client.get(f"{base}/{expired_id}/preview", headers=headers)
    assert preview.status_code == 410
    purge = client.post(f"{base}/purge-expired?limit=10", headers=headers)
    assert purge.status_code == 200, purge.text
    assert purge.json() == {"imports_purged": 1, "source_rows_purged": 1}

    with admin_engine.connect() as connection:
        purged_record = connection.execute(
            text(
                "SELECT status, (SELECT count(*) FROM data_import_rows "
                "WHERE import_id = data_imports.id AND raw_values IS NULL) AS purged_rows "
                "FROM data_imports WHERE id = :import_id"
            ),
            {"import_id": UUID(expired_id)},
        ).one()
    assert purged_record.status == "expired"
    assert purged_record.purged_rows == 1


def test_staged_import_tables_have_forced_rls_and_least_privilege_runtime_grants(
    runtime_engine, admin_engine
):
    with admin_engine.connect() as connection:
        tables = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(:table_names) ORDER BY c.relname"
            ),
            {"table_names": list(IMPORT_TABLES)},
        ).all()
        policies = connection.execute(
            text("SELECT tablename, cmd FROM pg_policies WHERE tablename = ANY(:table_names)"),
            {"table_names": list(IMPORT_TABLES)},
        ).all()
        grants = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete "
                "FROM unnest(CAST(:table_names AS text[])) AS grant_table(table_name)"
            ),
            {"table_names": list(IMPORT_TABLES)},
        ).all()

    assert len(tables) == len(IMPORT_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in tables)
    assert all(row.owner == "bf_migrator" for row in tables)
    assert len(policies) == len(IMPORT_TABLES)
    assert all(row.cmd == "ALL" for row in policies)
    grants_by_table = {row.table_name: row for row in grants}
    assert grants_by_table["data_imports"].can_select
    assert grants_by_table["data_imports"].can_insert
    assert grants_by_table["data_imports"].can_update
    assert not grants_by_table["data_imports"].can_delete
    assert grants_by_table["data_import_rows"].can_select
    assert grants_by_table["data_import_rows"].can_insert
    assert grants_by_table["data_import_rows"].can_update
    assert not grants_by_table["data_import_rows"].can_delete
    assert grants_by_table["data_import_record_links"].can_select
    assert grants_by_table["data_import_record_links"].can_insert
    assert not grants_by_table["data_import_record_links"].can_update
    assert not grants_by_table["data_import_record_links"].can_delete

    with runtime_engine.connect() as connection:
        visible_counts = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM data_imports), "
                "(SELECT count(*) FROM data_import_rows), "
                "(SELECT count(*) FROM data_import_record_links)"
            )
        ).one()
    assert all(count == 0 for count in visible_counts)


def test_concurrent_upload_retries_with_one_idempotency_key_return_one_import(app, admin_engine):
    content = (
        b"employee_code,full_name,job_title,standard_weekly_capacity_hours\n"
        b"EMP-CONC,Concurrent Employee,Engineer,40\n"
    )
    key = "concurrent-upload-idempotency-0001"

    with (
        TestClient(app, raise_server_exceptions=False) as first_client,
        TestClient(app, raise_server_exceptions=False) as second_client,
    ):
        owner = register_user(first_client, "import-concurrent-owner@example.com")
        tenant = create_tenant(
            first_client,
            owner,
            name="Concurrent Import Tenant",
            key="import-concurrent-tenant-001",
        )
        base = _imports_path(tenant["id"])
        headers = auth_headers(owner)
        clients = [first_client, second_client]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda test_client: _upload_import(test_client, base, headers, key, content),
                    clients,
                )
            )

    assert [response.status_code for response in responses] == [201, 201]
    import_ids = {response.json()["import"]["id"] for response in responses}
    assert len(import_ids) == 1
    import_id = UUID(import_ids.pop())
    with admin_engine.connect() as connection:
        import_count = connection.scalar(
            text("SELECT count(*) FROM data_imports WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant["id"])},
        )
        row_count = connection.scalar(
            text("SELECT count(*) FROM data_import_rows WHERE import_id = :import_id"),
            {"import_id": import_id},
        )
    assert import_count == 1
    assert row_count == 1


def test_concurrent_apply_requests_for_one_import_create_one_canonical_record(app, admin_engine):
    content = (
        b"employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
        b"EMP-APPLY-CONC,Concurrent Apply Employee,Engineer,40,active\n"
    )
    with (
        TestClient(app, raise_server_exceptions=False) as first_client,
        TestClient(app, raise_server_exceptions=False) as second_client,
    ):
        owner = register_user(first_client, "import-concurrent-apply-owner@example.com")
        tenant = create_tenant(
            first_client,
            owner,
            name="Concurrent Apply Tenant",
            key="import-concurrent-apply-tenant-001",
        )
        base = _imports_path(tenant["id"])
        headers = auth_headers(owner)
        import_id, report = _stage_map_validate(
            first_client,
            base,
            headers,
            "concurrent-apply-idempotency-key-0001",
            dataset_type="employees",
            content=content,
            fields=[
                "employee_code",
                "full_name",
                "job_title",
                "standard_weekly_capacity_hours",
                "employment_status",
            ],
        )
        confirmed = first_client.post(
            f"{base}/{import_id}/confirm",
            json={"validation_hash": report["import"]["validation_hash"]},
            headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text

        clients = [first_client, second_client]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda test_client: test_client.post(
                        f"{base}/{import_id}/apply", headers=headers
                    ),
                    clients,
                )
            )

    assert [response.status_code for response in responses] == [200, 200]
    results = [response.json() for response in responses]
    assert sorted(result["replayed"] for result in results) == [False, True]
    assert all(result["links_created"] == 1 for result in results)
    with admin_engine.connect() as connection:
        canonical_count = connection.scalar(
            text(
                "SELECT count(*) FROM employees WHERE tenant_id = :tenant_id "
                "AND employee_code_key = 'emp-apply-conc'"
            ),
            {"tenant_id": UUID(tenant["id"])},
        )
        link_count = connection.scalar(
            text("SELECT count(*) FROM data_import_record_links WHERE import_id = :import_id"),
            {"import_id": UUID(import_id)},
        )
    assert canonical_count == 1
    assert link_count == 1


@pytest.mark.parametrize("dataset_type", ["weekly_capacity", "resource_assignments"])
def test_concurrent_imports_revalidate_versioned_or_unconstrained_m3_identities(
    app, admin_engine, dataset_type
):
    with (
        TestClient(app, raise_server_exceptions=False) as first_client,
        TestClient(app, raise_server_exceptions=False) as second_client,
    ):
        owner = register_user(first_client, f"import-concurrent-{dataset_type}@example.com")
        tenant = create_tenant(
            first_client,
            owner,
            name=f"Concurrent {dataset_type} Tenant",
            key=f"import-concurrent-{dataset_type}-tenant-001",
        )
        base = _imports_path(tenant["id"])
        company_base = f"/api/v1/tenants/{tenant['id']}"
        headers = auth_headers(owner)
        employee_response = first_client.post(
            f"{company_base}/employees",
            json={
                "employee_code": "IMPORT-CONC-REF",
                "full_name": "Concurrent Import Reference",
                "job_title": "Engineer",
                "employment_status": "active",
                "employment_start_date": "2020-01-06",
                "standard_weekly_capacity_hours": "40.00",
            },
            headers=headers,
        )
        assert employee_response.status_code == 201, employee_response.text
        employee_id = employee_response.json()["id"]

        if dataset_type == "weekly_capacity":
            fields = [
                "employee_code",
                "week_start_date",
                "availability_status",
                "scheduled_hours",
                "non_project_hours",
                "unavailable_hours",
                "source",
            ]
            contents = [
                (
                    b"employee_code,week_start_date,availability_status,scheduled_hours,"
                    b"non_project_hours,unavailable_hours,source\n"
                    b"IMPORT-CONC-REF,2026-11-16,confirmed,40,8,4,manual\n"
                ),
                (
                    b"employee_code,week_start_date,availability_status,scheduled_hours,"
                    b"non_project_hours,unavailable_hours,source\n"
                    b"IMPORT-CONC-REF,2026-11-16,confirmed,38,6,4,manual\n"
                ),
            ]
        else:
            project_response = first_client.post(
                f"{company_base}/projects",
                json={
                    "project_code": "IMPORT-CONC-PROJ",
                    "name": "Concurrent Import Project",
                    "status": "planned",
                    "start_date": "2026-11-16",
                    "planned_end_date": "2026-11-30",
                },
                headers=headers,
            )
            assert project_response.status_code == 201, project_response.text
            project_id = project_response.json()["id"]
            fields = [
                "project_code",
                "employee_code",
                "start_date",
                "end_date",
                "planned_hours_per_week",
                "status",
                "source",
            ]
            contents = [
                (
                    b"project_code,employee_code,start_date,end_date,planned_hours_per_week,"
                    b"status,source\n"
                    b"IMPORT-CONC-PROJ,IMPORT-CONC-REF,2026-11-16,2026-11-22,12,proposed,manual\n"
                ),
                (
                    b"project_code,employee_code,start_date,end_date,planned_hours_per_week,"
                    b"status,source\n"
                    b"IMPORT-CONC-PROJ,IMPORT-CONC-REF,2026-11-16,2026-11-22,14,proposed,manual\n"
                ),
            ]

        import_ids: list[str] = []
        for index, content in enumerate(contents, start=1):
            import_id, report = _stage_map_validate(
                first_client,
                base,
                headers,
                f"concurrent-{dataset_type}-import-key-000{index}",
                dataset_type=dataset_type,
                content=content,
                fields=fields,
            )
            confirmed = first_client.post(
                f"{base}/{import_id}/confirm",
                json={"validation_hash": report["import"]["validation_hash"]},
                headers=headers,
            )
            assert confirmed.status_code == 200, confirmed.text
            import_ids.append(import_id)

        clients = [first_client, second_client]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(
                pool.map(
                    lambda pair: pair[0].post(f"{base}/{pair[1]}/apply", headers=headers),
                    zip(clients, import_ids, strict=True),
                )
            )

        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict_index = next(
            index for index, response in enumerate(responses) if response.status_code == 409
        )
        conflict = responses[conflict_index]
        assert conflict.json()["error"]["code"] == "import_validation_stale"
        retry_import_id = import_ids[conflict_index]
        refreshed = first_client.post(f"{base}/{retry_import_id}/validate", headers=headers)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["validation_report"]["totals"]["duplicate_rows"] == 1
        reconfirmed = first_client.post(
            f"{base}/{retry_import_id}/confirm",
            json={
                "validation_hash": refreshed.json()["import"]["validation_hash"],
                "acknowledge_warnings": True,
            },
            headers=headers,
        )
        assert reconfirmed.status_code == 200, reconfirmed.text
        replay = first_client.post(f"{base}/{retry_import_id}/apply", headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["import"]["status"] == "applied_with_warnings"
        assert replay.json()["import"]["counts"]["created_rows"] == 0
        assert replay.json()["links_created"] == 1

    with admin_engine.connect() as connection:
        if dataset_type == "weekly_capacity":
            canonical_count = connection.scalar(
                text(
                    "SELECT count(*) FROM employee_weekly_capacity WHERE tenant_id = :tenant_id "
                    "AND employee_id = :employee_id AND week_start_date = '2026-11-16'"
                ),
                {"tenant_id": UUID(tenant["id"]), "employee_id": UUID(employee_id)},
            )
            latest_version = connection.scalar(
                text(
                    "SELECT max(version) FROM employee_weekly_capacity "
                    "WHERE tenant_id = :tenant_id AND employee_id = :employee_id "
                    "AND week_start_date = '2026-11-16'"
                ),
                {"tenant_id": UUID(tenant["id"]), "employee_id": UUID(employee_id)},
            )
            assert latest_version == 1
        else:
            canonical_count = connection.scalar(
                text(
                    "SELECT count(*) FROM resource_assignments WHERE tenant_id = :tenant_id "
                    "AND project_id = :project_id AND employee_id = :employee_id "
                    "AND start_date = '2026-11-16' AND end_date = '2026-11-22'"
                ),
                {
                    "tenant_id": UUID(tenant["id"]),
                    "project_id": UUID(project_id),
                    "employee_id": UUID(employee_id),
                },
            )
        link_count = connection.scalar(
            text("SELECT count(*) FROM data_import_record_links WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant["id"])},
        )
    assert canonical_count == 1
    assert link_count == 2


def test_import_apply_rolls_back_the_entire_batch_after_a_mid_apply_failure(
    client, admin_engine, monkeypatch
):
    owner = register_user(client, "import-atomic-owner@example.com")
    tenant = create_tenant(
        client, owner, name="Atomic Import Tenant", key="import-atomic-tenant-001"
    )
    base = _imports_path(tenant["id"])
    headers = auth_headers(owner)
    content = (
        b"employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
        b"ATOMIC-01,First Atomic Employee,Engineer,40,active\n"
        b"ATOMIC-02,Second Atomic Employee,Engineer,40,active\n"
    )
    import_id, report = _stage_map_validate(
        client,
        base,
        headers,
        "import-atomic-rollback-key-0001",
        dataset_type="employees",
        content=content,
        fields=[
            "employee_code",
            "full_name",
            "job_title",
            "standard_weekly_capacity_hours",
            "employment_status",
        ],
    )
    assert report["validation_report"]["totals"]["eligible_rows"] == 2
    confirmed = client.post(
        f"{base}/{import_id}/confirm",
        json={"validation_hash": report["import"]["validation_hash"]},
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text

    original_create = import_service._create_canonical_record
    create_calls = 0

    def fail_on_second_create(session, user, tenant_id, plan, request_id):
        nonlocal create_calls
        create_calls += 1
        if create_calls == 2:
            raise ApiError(
                409, "forced_apply_failure", "Injected failure for rollback verification."
            )
        return original_create(session, user, tenant_id, plan, request_id)

    monkeypatch.setattr(import_service, "_create_canonical_record", fail_on_second_create)
    apply_response = client.post(f"{base}/{import_id}/apply", headers=headers)
    assert apply_response.status_code == 409
    assert apply_response.json()["error"]["code"] == "forced_apply_failure"
    assert create_calls == 2

    with admin_engine.connect() as connection:
        canonical_count = connection.scalar(
            text(
                "SELECT count(*) FROM employees WHERE tenant_id = :tenant_id "
                "AND employee_code IN ('ATOMIC-01', 'ATOMIC-02')"
            ),
            {"tenant_id": UUID(tenant["id"])},
        )
        import_state = connection.execute(
            text("SELECT status, created_rows FROM data_imports WHERE id = :import_id"),
            {"import_id": UUID(import_id)},
        ).one()
        linked_rows = connection.scalar(
            text("SELECT count(*) FROM data_import_record_links WHERE import_id = :import_id"),
            {"import_id": UUID(import_id)},
        )
        purged_rows = connection.scalar(
            text(
                "SELECT count(*) FROM data_import_rows "
                "WHERE import_id = :import_id AND raw_values IS NULL"
            ),
            {"import_id": UUID(import_id)},
        )
    assert canonical_count == 0
    assert import_state.status == "confirmed"
    assert import_state.created_rows == 0
    assert linked_rows == 0
    assert purged_rows == 0
