from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_tenant, register_user

COMPANY_CONTEXT_TABLES = (
    "business_units",
    "departments",
    "employees",
    "skills",
    "employee_skills",
    "employee_weekly_capacity",
    "projects",
    "resource_assignments",
)
TABLE_COUNT_STATEMENTS = {
    "business_units": text("SELECT count(*) FROM business_units"),
    "departments": text("SELECT count(*) FROM departments"),
    "employees": text("SELECT count(*) FROM employees"),
    "skills": text("SELECT count(*) FROM skills"),
    "employee_skills": text("SELECT count(*) FROM employee_skills"),
    "employee_weekly_capacity": text("SELECT count(*) FROM employee_weekly_capacity"),
    "projects": text("SELECT count(*) FROM projects"),
    "resource_assignments": text("SELECT count(*) FROM resource_assignments"),
}


def _create_company_context_rows(client, tokens, tenant):
    base = f"/api/v1/tenants/{tenant['id']}"
    headers = auth_headers(tokens)
    unit = client.post(
        f"{base}/business-units", json={"name": f"Unit-{tenant['id'][:8]}"}, headers=headers
    ).json()
    department = client.post(
        f"{base}/departments",
        json={"name": f"Department-{tenant['id'][:8]}", "business_unit_id": unit["id"]},
        headers=headers,
    ).json()
    employee = client.post(
        f"{base}/employees",
        json={
            "employee_code": "EMP-RLS",
            "full_name": "RLS Employee",
            "job_title": "Engineer",
            "department_id": department["id"],
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    ).json()
    skill = client.post(
        f"{base}/skills", json={"name": f"Skill-{tenant['id'][:8]}"}, headers=headers
    ).json()
    employee_skill = client.post(
        f"{base}/employees/{employee['id']}/skills",
        json={"skill_id": skill["id"], "proficiency": 2},
        headers=headers,
    )
    assert employee_skill.status_code == 201, employee_skill.text
    capacity = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/2026-11-16",
        json={"availability_status": "unknown"},
        headers=headers,
    )
    assert capacity.status_code == 201, capacity.text
    project = client.post(
        f"{base}/projects",
        json={
            "project_code": "PRJ-RLS",
            "name": "RLS Project",
            "status": "planned",
            "start_date": "2026-11-16",
            "planned_end_date": "2026-11-30",
        },
        headers=headers,
    )
    assert project.status_code == 201, project.text
    assignment = client.post(
        f"{base}/projects/{project.json()['id']}/assignments",
        json={
            "employee_id": employee["id"],
            "start_date": "2026-11-16",
            "end_date": "2026-11-22",
            "planned_hours_per_week": "8.00",
            "status": "proposed",
        },
        headers=headers,
    )
    assert assignment.status_code == 201, assignment.text
    return {"unit": unit, "department": department, "employee": employee, "project": project.json()}


def test_company_context_rls_is_forced_fails_closed_and_resets_on_pooled_connections(
    client, runtime_engine, admin_engine
):
    owner_a = register_user(client, "context-rls-a@example.com")
    tenant_a = create_tenant(client, owner_a, name="Context RLS A", key="context-rls-a-key-001")
    rows_a = _create_company_context_rows(client, owner_a, tenant_a)
    owner_b = register_user(client, "context-rls-b@example.com")
    tenant_b = create_tenant(client, owner_b, name="Context RLS B", key="context-rls-b-key-001")
    rows_b = _create_company_context_rows(client, owner_b, tenant_b)

    with admin_engine.connect() as connection:
        security_rows = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(:table_names) ORDER BY c.relname"
            ),
            {"table_names": list(COMPANY_CONTEXT_TABLES)},
        ).all()
        policies = connection.execute(
            text(
                "SELECT tablename, policyname, permissive, roles, cmd "
                "FROM pg_policies WHERE tablename = ANY(:table_names)"
            ),
            {"table_names": list(COMPANY_CONTEXT_TABLES)},
        ).all()
        capacity_privileges = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'employee_weekly_capacity', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'employee_weekly_capacity', 'INSERT') "
                "AS can_insert, "
                "has_table_privilege('bf_runtime', 'employee_weekly_capacity', 'UPDATE') "
                "AS can_update, "
                "has_table_privilege('bf_runtime', 'employee_weekly_capacity', 'DELETE') "
                "AS can_delete"
            )
        ).one()
    assert len(security_rows) == len(COMPANY_CONTEXT_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security_rows)
    assert all(row.owner == "bf_migrator" for row in security_rows)
    assert len(policies) == len(COMPANY_CONTEXT_TABLES)
    assert all(row.cmd == "ALL" for row in policies)
    assert capacity_privileges.can_select and capacity_privileges.can_insert
    assert not capacity_privileges.can_update and not capacity_privileges.can_delete

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        backend_pid_a = session.scalar(text("SELECT pg_backend_pid()"))
        current_tenant = session.scalar(text("SELECT current_setting('app.tenant_id', true)"))
        assert current_tenant in (None, "")
        for table in COMPANY_CONTEXT_TABLES:
            assert session.scalar(TABLE_COUNT_STATEMENTS[table]) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        assert session.scalar(text("SELECT pg_backend_pid()")) == backend_pid_a
        visible = session.execute(
            text("SELECT tenant_id, name FROM business_units ORDER BY name")
        ).all()
        assert [(str(row.tenant_id), row.name) for row in visible] == [
            (tenant_a["id"], rows_a["unit"]["name"])
        ]
        for table in COMPANY_CONTEXT_TABLES:
            assert session.scalar(TABLE_COUNT_STATEMENTS[table]) == 1

    with factory.begin() as session:
        assert session.scalar(text("SELECT pg_backend_pid()")) == backend_pid_a
        assert session.scalar(text("SELECT current_setting('app.tenant_id', true)")) in (None, "")
        assert session.scalar(text("SELECT count(*) FROM business_units")) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        assert session.scalar(text("SELECT pg_backend_pid()")) == backend_pid_a
        visible = session.execute(text("SELECT tenant_id, name FROM business_units")).all()
        assert [(str(row.tenant_id), row.name) for row in visible] == [
            (tenant_b["id"], rows_b["unit"]["name"])
        ]
        for table in COMPANY_CONTEXT_TABLES[1:]:
            assert session.scalar(TABLE_COUNT_STATEMENTS[table]) == 1

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text(
                "INSERT INTO business_units (id, tenant_id, company_id, name) "
                "VALUES (:id, :tenant_id, :company_id, 'Cross-tenant context write')"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_a["company"]["id"],
            },
        )

    # RLS permits the tenant-A context, but the composite tenant/company FK prevents a
    # tenant-A organization row from referring to tenant B's company.
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        session.execute(
            text(
                "INSERT INTO business_units (id, tenant_id, company_id, name) "
                "VALUES (:id, :tenant_id, :company_id, 'Mismatched company')"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_b["company"]["id"],
            },
        )

    # Direct runtime writes without context fail closed, even when the values name a real tenant.
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text(
                "INSERT INTO business_units (id, tenant_id, company_id, name) "
                "VALUES (:id, :tenant_id, :company_id, 'Missing context')"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_a["company"]["id"],
            },
        )
