from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import text

from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK_START = date(2026, 11, 16)
WEEK_START_TEXT = WEEK_START.isoformat()


def _tenant_path(tenant: dict) -> str:
    return f"/api/v1/tenants/{tenant['id']}"


def _create_employee(
    client,
    headers,
    base: str,
    *,
    code: str = "EMP-001",
    name: str = "Ada Lovelace",
    department_id: str | None = None,
) -> dict:
    response = client.post(
        f"{base}/employees",
        json={
            "employee_code": code,
            "full_name": name,
            "job_title": "Software Engineer",
            "department_id": department_id,
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_company_organization_employee_api_rbac_pagination_and_isolation(client, admin_engine):
    owner_tokens = register_user(client, "context-org-owner@example.com", display_name="Owner")
    tenant = create_tenant(client, owner_tokens, name="Org Tenant", key="context-org-key-001")
    base = _tenant_path(tenant)
    owner_headers = auth_headers(owner_tokens)

    openapi_paths = client.get("/openapi.json").json()["paths"]
    assert "/api/v1/tenants/{tenant_id}/business-units" in openapi_paths
    assert "/api/v1/tenants/{tenant_id}/company-context" in openapi_paths

    unit_response = client.post(
        f"{base}/business-units",
        json={"name": "Engineering", "description": "Product engineering"},
        headers=owner_headers,
    )
    assert unit_response.status_code == 201, unit_response.text
    unit = unit_response.json()
    assert unit["tenant_id"] == tenant["id"]
    assert unit["company_id"] == tenant["company"]["id"]

    duplicate_unit = client.post(
        f"{base}/business-units", json={"name": " engineering "}, headers=owner_headers
    )
    assert duplicate_unit.status_code == 409

    department_response = client.post(
        f"{base}/departments",
        json={"name": "Platform", "business_unit_id": unit["id"]},
        headers=owner_headers,
    )
    assert department_response.status_code == 201, department_response.text
    department = department_response.json()
    employees = [
        _create_employee(
            client,
            owner_headers,
            base,
            code="EMP-001",
            name="Ada Lovelace",
            department_id=department["id"],
        ),
        _create_employee(
            client,
            owner_headers,
            base,
            code="EMP-002",
            name="Grace Hopper",
            department_id=department["id"],
        ),
    ]

    page = client.get(
        f"{base}/employees?department_id={department['id']}&sort_by=full_name&limit=1&offset=0",
        headers=owner_headers,
    )
    assert page.status_code == 200, page.text
    assert page.json()["total"] == 2
    assert page.json()["limit"] == 1
    assert [item["full_name"] for item in page.json()["items"]] == ["Ada Lovelace"]
    assert page.json()["items"][0]["business_unit_id"] == unit["id"]

    member_tokens = register_user(client, "context-org-member@example.com", display_name="Member")
    create_member(client, owner_tokens, tenant["id"], "context-org-member@example.com")
    member_headers = auth_headers(member_tokens)
    assert client.get(f"{base}/departments", headers=member_headers).status_code == 200
    assert client.get(f"{base}/employees", headers=member_headers).status_code == 403
    assert (
        client.get(f"{base}/employees/{employees[0]['id']}", headers=member_headers).status_code
        == 403
    )
    assert client.get(f"{base}/skills", headers=member_headers).status_code == 403
    assert client.get(f"{base}/projects", headers=member_headers).status_code == 403
    assert client.get(f"{base}/resource-assignments", headers=member_headers).status_code == 403
    assert client.get(f"{base}/company-context", headers=member_headers).status_code == 403
    assert (
        client.post(
            f"{base}/business-units", json={"name": "Member Write"}, headers=member_headers
        ).status_code
        == 403
    )

    admin_tokens = register_user(client, "context-org-admin@example.com", display_name="Admin")
    create_member(client, owner_tokens, tenant["id"], "context-org-admin@example.com", "admin")
    admin_write = client.post(
        f"{base}/business-units", json={"name": "Operations"}, headers=auth_headers(admin_tokens)
    )
    assert admin_write.status_code == 201

    other_owner = register_user(client, "context-org-other@example.com", display_name="Other")
    other_tenant = create_tenant(client, other_owner, name="Other Org", key="context-other-key-001")
    other_base = _tenant_path(other_tenant)
    assert (
        client.get(
            f"{other_base}/business-units/{unit['id']}",
            headers=auth_headers(other_owner),
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"{other_base}/business-units/{unit['id']}",
            json={"name": "Cross-tenant edit"},
            headers=auth_headers(other_owner),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"{other_base}/business-units/{unit['id']}",
            headers=auth_headers(other_owner),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{other_base}/business-units",
            json={"name": "Spoofed", "tenant_id": tenant["id"], "company_id": unit["company_id"]},
            headers=auth_headers(other_owner),
        ).status_code
        == 422
    )
    assert client.get(f"{other_base}/employees", headers=owner_headers).status_code == 404

    assert (
        client.patch(
            f"{base}/business-units/{unit['id']}",
            json={"status": "inactive"},
            headers=owner_headers,
        ).status_code
        == 409
    )
    assert (
        client.patch(
            f"{base}/departments/{department['id']}",
            json={"status": "inactive"},
            headers=owner_headers,
        ).status_code
        == 409
    )
    for employee in employees:
        response = client.delete(f"{base}/employees/{employee['id']}", headers=owner_headers)
        assert response.status_code == 204
    assert (
        client.delete(f"{base}/departments/{department['id']}", headers=owner_headers).status_code
        == 204
    )
    assert (
        client.delete(f"{base}/business-units/{unit['id']}", headers=owner_headers).status_code
        == 204
    )
    blocked_department_reactivation = client.patch(
        f"{base}/departments/{department['id']}",
        json={"status": "active"},
        headers=owner_headers,
    )
    assert blocked_department_reactivation.status_code == 404
    blocked_employee_reactivation = client.patch(
        f"{base}/employees/{employees[0]['id']}",
        json={"employment_status": "active"},
        headers=owner_headers,
    )
    assert blocked_employee_reactivation.status_code == 404
    restored_unit = client.patch(
        f"{base}/business-units/{unit['id']}", json={"status": "active"}, headers=owner_headers
    )
    assert restored_unit.status_code == 200
    restored_department = client.patch(
        f"{base}/departments/{department['id']}",
        json={"status": "active"},
        headers=owner_headers,
    )
    assert restored_department.status_code == 200

    audit = client.get(f"{base}/audit-events", headers=owner_headers)
    assert audit.status_code == 200
    event_types = {item["event_type"] for item in audit.json()["items"]}
    assert {"business_unit.created", "department.created", "employee.created"} <= event_types
    with admin_engine.connect() as connection:
        employee_event_metadata = connection.scalar(
            text("SELECT metadata::text FROM audit_events WHERE target_id = :target_id"),
            {"target_id": employees[0]["id"]},
        )
    assert employee_event_metadata is not None
    assert "Ada Lovelace" not in employee_event_metadata


def test_skills_weekly_capacity_projects_assignments_and_factual_summary(client):
    owner_tokens = register_user(client, "context-summary-owner@example.com", display_name="Owner")
    tenant = create_tenant(
        client, owner_tokens, name="Summary Tenant", key="context-summary-key-001"
    )
    base = _tenant_path(tenant)
    headers = auth_headers(owner_tokens)

    unit = client.post(f"{base}/business-units", json={"name": "Delivery"}, headers=headers).json()
    department = client.post(
        f"{base}/departments",
        json={"name": "Implementation", "business_unit_id": unit["id"]},
        headers=headers,
    ).json()
    employee = _create_employee(client, headers, base, department_id=department["id"])

    skill_response = client.post(
        f"{base}/skills",
        json={"name": "Python", "description": "Python development"},
        headers=headers,
    )
    assert skill_response.status_code == 201, skill_response.text
    skill = skill_response.json()
    employee_skill_response = client.post(
        f"{base}/employees/{employee['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": 3},
        headers=headers,
    )
    assert employee_skill_response.status_code == 201, employee_skill_response.text
    assert employee_skill_response.json()["proficiency_label"] == "proficient"
    employee_skill_id = employee_skill_response.json()["id"]
    proficiency_update = client.patch(
        f"{base}/employees/{employee['id']}/skills/{skill['id']}",
        json={"proficiency": 4},
        headers=headers,
    )
    assert proficiency_update.status_code == 200
    assert proficiency_update.json()["proficiency_label"] == "advanced"
    skill_page = client.get(f"{base}/employees/{employee['id']}/skills", headers=headers)
    assert skill_page.status_code == 200
    assert skill_page.json()["total"] == 1
    assert skill_page.json()["items"][0]["id"] == employee_skill_id
    inactive_employee_skill = client.patch(
        f"{base}/employees/{employee['id']}/skills/{skill['id']}",
        json={"status": "inactive"},
        headers=headers,
    )
    assert inactive_employee_skill.status_code == 200
    assert (
        client.get(f"{base}/employees/{employee['id']}/skills", headers=headers).json()["total"]
        == 0
    )
    restored_employee_skill = client.patch(
        f"{base}/employees/{employee['id']}/skills/{skill['id']}",
        json={"status": "active"},
        headers=headers,
    )
    assert restored_employee_skill.status_code == 200
    assert restored_employee_skill.json()["proficiency_label"] == "advanced"

    unknown_capacity = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK_START_TEXT}",
        json={"availability_status": "unknown"},
        headers=headers,
    )
    assert unknown_capacity.status_code == 201, unknown_capacity.text
    assert unknown_capacity.json()["available_hours"] is None
    confirmed_capacity = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK_START_TEXT}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": "40.00",
            "non_project_hours": "8.00",
            "unavailable_hours": "2.00",
        },
        headers=headers,
    )
    assert confirmed_capacity.status_code == 201, confirmed_capacity.text
    assert confirmed_capacity.json()["version"] == 2
    assert Decimal(str(confirmed_capacity.json()["available_hours"])) == Decimal("30.00")

    history = client.get(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK_START_TEXT}/history?limit=1&offset=0",
        headers=headers,
    )
    assert history.status_code == 200
    assert history.json()["total"] == 2
    assert [item["version"] for item in history.json()["items"]] == [2]
    history_page_2 = client.get(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK_START_TEXT}/history?limit=1&offset=1",
        headers=headers,
    )
    assert [item["availability_status"] for item in history_page_2.json()["items"]] == ["unknown"]
    latest_capacity = client.get(
        f"{base}/employees/{employee['id']}/weekly-capacity?start_week={WEEK_START_TEXT}",
        headers=headers,
    )
    assert latest_capacity.status_code == 200
    assert latest_capacity.json()["total"] == 1
    assert latest_capacity.json()["items"][0]["version"] == 2
    bad_week = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/2026-11-17",
        json={"availability_status": "unknown"},
        headers=headers,
    )
    assert bad_week.status_code == 422

    project_response = client.post(
        f"{base}/projects",
        json={
            "project_code": "PRJ-001",
            "name": "Platform delivery",
            "status": "planned",
            "start_date": WEEK_START_TEXT,
            "planned_end_date": "2026-11-30",
            "project_manager_employee_id": employee["id"],
        },
        headers=headers,
    )
    assert project_response.status_code == 201, project_response.text
    project = project_response.json()
    assignment_response = client.post(
        f"{base}/projects/{project['id']}/assignments",
        json={
            "employee_id": employee["id"],
            "start_date": WEEK_START_TEXT,
            "end_date": "2026-11-22",
            "planned_hours_per_week": "12.00",
            "status": "proposed",
        },
        headers=headers,
    )
    assert assignment_response.status_code == 201, assignment_response.text
    assignment = assignment_response.json()
    committed = client.patch(
        f"{base}/resource-assignments/{assignment['id']}",
        json={"status": "committed"},
        headers=headers,
    )
    assert committed.status_code == 200
    assert committed.json()["status"] == "committed"

    project_assignments = client.get(
        f"{base}/projects/{project['id']}/assignments?limit=10", headers=headers
    )
    assert project_assignments.status_code == 200
    assert project_assignments.json()["total"] == 1
    assignment_filter = client.get(
        f"{base}/resource-assignments?employee_id={employee['id']}&period_start=2026-11-17&period_end=2026-11-18",
        headers=headers,
    )
    assert assignment_filter.status_code == 200
    assert assignment_filter.json()["total"] == 1

    member = register_user(client, "context-assignment-member@example.com")
    create_member(client, owner_tokens, tenant["id"], "context-assignment-member@example.com")
    member_headers = auth_headers(member)
    for path in (
        f"{base}/projects/{project['id']}",
        f"{base}/projects/{project['id']}/assignments",
        f"{base}/resource-assignments/{assignment['id']}",
        f"{base}/resource-assignments/{assignment['id']}/history",
    ):
        assert client.get(path, headers=member_headers).status_code == 403

    summary = client.get(
        f"{base}/company-context?week_start_date={WEEK_START_TEXT}", headers=headers
    )
    assert summary.status_code == 200, summary.text
    data = summary.json()
    assert data["company"]["id"] == tenant["company"]["id"]
    assert data["employees"]["active_count"] == 1
    assert data["skills"]["active_catalog_count"] == 1
    assert data["skills"]["employees_with_active_skills"] == 1
    assert data["skills"]["coverage_complete"] is True
    assert data["weekly_capacity"]["week_end_date"] == "2026-11-22"
    assert data["weekly_capacity"]["confirmed_count"] == 1
    assert data["weekly_capacity"]["unknown_count"] == 0
    assert data["weekly_capacity"]["missing_count"] == 0
    assert Decimal(str(data["weekly_capacity"]["confirmed_available_hours_total"])) == Decimal(
        "30.00"
    )
    assert data["weekly_capacity"]["coverage_complete"] is True
    assert data["projects"]["planned_count"] == 1
    assert data["assignments"]["committed_count"] == 1
    assert Decimal(str(data["assignments"]["known_committed_hours_per_week_total"])) == Decimal(
        "12.00"
    )
    assert data["assignments"]["coverage_status"] == "recorded"
    assert (
        "weekly_capacity_missing_for_some_active_employees"
        not in data["missing_or_incomplete_indicators"]
    )

    unknown_latest = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK_START_TEXT}",
        json={"availability_status": "unknown"},
        headers=headers,
    )
    assert unknown_latest.status_code == 201
    unknown_summary = client.get(
        f"{base}/company-context?week_start_date={WEEK_START_TEXT}", headers=headers
    ).json()
    assert unknown_summary["weekly_capacity"]["unknown_count"] == 1
    assert unknown_summary["weekly_capacity"]["coverage_complete"] is False
    assert unknown_summary["weekly_capacity"]["confirmed_available_hours_total"] is None
    assert (
        "weekly_capacity_explicitly_unknown_for_some_active_employees"
        in unknown_summary["missing_or_incomplete_indicators"]
    )

    project_activated = client.patch(
        f"{base}/projects/{project['id']}", json={"status": "active"}, headers=headers
    )
    assert project_activated.status_code == 200
    blocked_close = client.patch(
        f"{base}/projects/{project['id']}", json={"status": "cancelled"}, headers=headers
    )
    assert blocked_close.status_code == 409
    assert blocked_close.json()["error"]["code"] == "project_has_open_assignments"

    assignment_history = client.get(
        f"{base}/resource-assignments/{assignment['id']}/history?limit=1&offset=0",
        headers=headers,
    )
    assert assignment_history.status_code == 200
    assert assignment_history.json()["total"] == 2
    assert [item["event_type"] for item in assignment_history.json()["items"]] == [
        "resource_assignment.created"
    ]

    canceled_assignment = client.delete(
        f"{base}/resource-assignments/{assignment['id']}", headers=headers
    )
    assert canceled_assignment.status_code == 204
    terminal_assignment_edit = client.patch(
        f"{base}/resource-assignments/{assignment['id']}",
        json={"notes": "attempted edit after cancellation"},
        headers=headers,
    )
    assert terminal_assignment_edit.status_code == 409
    full_assignment_history = client.get(
        f"{base}/resource-assignments/{assignment['id']}/history?limit=10",
        headers=headers,
    )
    assert full_assignment_history.status_code == 200
    assert full_assignment_history.json()["total"] == 3

    missing_actual_end = client.patch(
        f"{base}/projects/{project['id']}", json={"status": "completed"}, headers=headers
    )
    assert missing_actual_end.status_code == 422
    completed_project = client.patch(
        f"{base}/projects/{project['id']}",
        json={"status": "completed", "actual_end_date": "2026-11-22"},
        headers=headers,
    )
    assert completed_project.status_code == 200
    assert completed_project.json()["actual_end_date"] == "2026-11-22"
    immutable_project = client.patch(
        f"{base}/projects/{project['id']}",
        json={"name": "Renamed terminal project"},
        headers=headers,
    )
    assert immutable_project.status_code == 409


def test_company_context_summary_reports_missing_capacity_as_unknown(client):
    owner_tokens = register_user(client, "context-missing-capacity@example.com")
    tenant = create_tenant(
        client,
        owner_tokens,
        name="Missing Capacity Tenant",
        key="context-missing-capacity-key-001",
    )
    base = _tenant_path(tenant)
    employee = _create_employee(client, auth_headers(owner_tokens), base)

    summary = client.get(
        f"{base}/company-context?week_start_date={WEEK_START_TEXT}",
        headers=auth_headers(owner_tokens),
    )
    assert summary.status_code == 200, summary.text
    coverage = summary.json()["weekly_capacity"]
    assert coverage["active_employee_count"] == 1
    assert coverage["records_count"] == 0
    assert coverage["missing_count"] == 1
    assert coverage["confirmed_available_hours_total"] is None
    assert coverage["coverage_complete"] is False
    assert (
        "weekly_capacity_missing_for_some_active_employees"
        in summary.json()["missing_or_incomplete_indicators"]
    )
    assert employee["standard_weekly_capacity_hours"] == "40.00"


def test_company_context_rejects_invalid_domain_inputs_and_pagination(client):
    owner_tokens = register_user(client, "context-validation-owner@example.com")
    tenant = create_tenant(
        client, owner_tokens, name="Validation Tenant", key="context-valid-key-001"
    )
    base = _tenant_path(tenant)
    headers = auth_headers(owner_tokens)

    blank_name = client.post(f"{base}/business-units", json={"name": "   "}, headers=headers)
    assert blank_name.status_code == 422

    terminated_without_date = client.post(
        f"{base}/employees",
        json={
            "employee_code": "EMP-TERM",
            "full_name": "Terminated Person",
            "job_title": "Engineer",
            "employment_status": "terminated",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert terminated_without_date.status_code == 422

    invalid_proficiency = client.post(
        f"{base}/employees/00000000-0000-0000-0000-000000000001/skills",
        json={"skill_id": "00000000-0000-0000-0000-000000000002", "proficiency": 6},
        headers=headers,
    )
    assert invalid_proficiency.status_code == 422

    unknown_with_hours = client.post(
        f"{base}/employees/00000000-0000-0000-0000-000000000001/weekly-capacity/{WEEK_START_TEXT}",
        json={"availability_status": "unknown", "scheduled_hours": "40.00"},
        headers=headers,
    )
    assert unknown_with_hours.status_code == 422

    adjustment_without_reason = client.post(
        f"{base}/employees/00000000-0000-0000-0000-000000000001/weekly-capacity/{WEEK_START_TEXT}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": "40.00",
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
            "source": "manager_adjustment",
        },
        headers=headers,
    )
    assert adjustment_without_reason.status_code == 422

    reversed_project_dates = client.post(
        f"{base}/projects",
        json={
            "project_code": "BAD-DATES",
            "name": "Invalid dates",
            "start_date": "2026-11-30",
            "planned_end_date": WEEK_START_TEXT,
        },
        headers=headers,
    )
    assert reversed_project_dates.status_code == 422

    assignment_date_employee = _create_employee(client, headers, base, code="EMP-DATE-VALIDATION")
    assignment_date_project = client.post(
        f"{base}/projects",
        json={
            "project_code": "DATE-CHECK",
            "name": "Assignment date validation",
            "start_date": WEEK_START_TEXT,
            "planned_end_date": "2026-11-30",
            "project_manager_employee_id": assignment_date_employee["id"],
        },
        headers=headers,
    )
    assert assignment_date_project.status_code == 201, assignment_date_project.text
    reversed_assignment_dates = client.post(
        f"{base}/projects/{assignment_date_project.json()['id']}/assignments",
        json={
            "employee_id": assignment_date_employee["id"],
            "start_date": "2026-11-22",
            "end_date": WEEK_START_TEXT,
            "planned_hours_per_week": "8.00",
        },
        headers=headers,
    )
    assert reversed_assignment_dates.status_code == 422

    assignment_adjustment_without_note = client.post(
        f"{base}/projects/00000000-0000-0000-0000-000000000002/assignments",
        json={
            "employee_id": "00000000-0000-0000-0000-000000000003",
            "start_date": WEEK_START_TEXT,
            "end_date": "2026-11-22",
            "planned_hours_per_week": "8.00",
            "source": "manager_adjustment",
        },
        headers=headers,
    )
    assert assignment_adjustment_without_note.status_code == 422

    invalid_page = client.get(f"{base}/business-units?limit=0", headers=headers)
    assert invalid_page.status_code == 422
    assert "Terminated Person" not in terminated_without_date.text


def test_upgrade_from_milestone_2_schema_preserves_existing_data(admin_engine):
    import os
    from uuid import uuid4

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    from tests.conftest import ROOT, TEST_ADMIN_DATABASE_URL, TEST_MIGRATION_DATABASE_URL

    database_name = f"businessflow_upgrade_{uuid4().hex[:12]}_test"
    migration_url = make_url(TEST_MIGRATION_DATABASE_URL).set(database=database_name)
    admin_url = make_url(TEST_ADMIN_DATABASE_URL).set(database=database_name)
    migration_url_string = migration_url.render_as_string(hide_password=False)
    prior_migration_url = os.environ.get("MIGRATION_DATABASE_URL")

    with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" OWNER bf_migrator')

    scratch_migration_engine = create_engine(migration_url, hide_parameters=True)
    scratch_admin_engine = create_engine(admin_url, hide_parameters=True)
    try:
        with scratch_admin_engine.begin() as connection:
            connection.execute(text(f"REVOKE ALL ON DATABASE {database_name} FROM PUBLIC"))
            connection.execute(
                text(f"GRANT CONNECT ON DATABASE {database_name} TO bf_runtime, bf_migrator")
            )
        with scratch_admin_engine.begin() as connection:
            connection.execute(text("REVOKE ALL ON SCHEMA public FROM PUBLIC"))
            connection.execute(text("ALTER SCHEMA public OWNER TO bf_migrator"))
            connection.execute(text("GRANT USAGE ON SCHEMA public TO bf_runtime"))
            connection.execute(text("REVOKE CREATE ON SCHEMA public FROM bf_runtime"))

        os.environ["MIGRATION_DATABASE_URL"] = migration_url_string
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", migration_url_string.replace("%", "%%"))
        command.upgrade(config, "0001_backend_foundation")
        with scratch_admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
                )
                is True
            )

        tenant_id, company_id, owner_id = uuid4(), uuid4(), uuid4()
        with scratch_migration_engine.begin() as connection:
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(tenant_id)},
            )
            connection.execute(
                text(
                    "INSERT INTO users (id, email, display_name, password_hash) "
                    "VALUES (:id, 'legacy-owner@example.com', 'Legacy Owner', 'test-hash')"
                ),
                {"id": owner_id},
            )
            connection.execute(
                text(
                    "INSERT INTO tenants (id, name, primary_company_id) "
                    "VALUES (:tenant_id, 'Legacy Tenant', :company_id)"
                ),
                {"tenant_id": tenant_id, "company_id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO companies (id, tenant_id, legal_name, base_currency) "
                    "VALUES (:company_id, :tenant_id, 'Legacy Company', 'USD')"
                ),
                {"company_id": company_id, "tenant_id": tenant_id},
            )
            connection.execute(
                text(
                    "INSERT INTO tenant_memberships (id, tenant_id, user_id, role) "
                    "VALUES (:membership_id, :tenant_id, :user_id, 'owner')"
                ),
                {"membership_id": uuid4(), "tenant_id": tenant_id, "user_id": owner_id},
            )

        command.upgrade(config, "head")
        with scratch_admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT legal_name FROM companies WHERE id = :company_id"),
                    {"company_id": company_id},
                )
                == "Legacy Company"
            )
            assert (
                connection.scalar(
                    text("SELECT name FROM tenants WHERE id = :tenant_id"),
                    {"tenant_id": tenant_id},
                )
                == "Legacy Tenant"
            )
            legacy_user = connection.execute(
                text("SELECT email, display_name, status FROM users WHERE id = :owner_id"),
                {"owner_id": owner_id},
            ).one()
            assert (
                legacy_user.email,
                legacy_user.display_name,
                legacy_user.status,
            ) == ("legacy-owner@example.com", "Legacy Owner", "active")
            legacy_membership = connection.execute(
                text(
                    "SELECT user_id, role, status FROM tenant_memberships "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant_id},
            ).one()
            assert (
                legacy_membership.user_id,
                legacy_membership.role,
                legacy_membership.status,
            ) == (
                owner_id,
                "owner",
                "active",
            )
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
                )
                is False
            )
            assert (
                connection.scalar(text("SELECT version_num FROM alembic_version"))
                == "0026_forecasting_engine"
            )
            assert (
                connection.scalar(
                    text("SELECT relforcerowsecurity FROM pg_class WHERE relname = 'employees'")
                )
                is True
            )
    finally:
        scratch_admin_engine.dispose()
        scratch_migration_engine.dispose()
        if prior_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = prior_migration_url
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}" WITH (FORCE)')
