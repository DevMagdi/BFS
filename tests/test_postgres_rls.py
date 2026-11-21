from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_tenant, register_user


def test_rls_is_forced_and_runtime_role_is_not_privileged(admin_engine, runtime_engine):
    with runtime_engine.connect() as connection:
        runtime = connection.execute(
            text(
                "SELECT current_user AS connected_role, rolsuper, rolbypassrls, "
                "rolcreatedb, rolcreaterole, rolinherit, "
                "pg_has_role(current_user, 'bf_migrator', 'MEMBER') AS migration_member "
                "FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        owns_database = connection.scalar(
            text(
                "SELECT datdba = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
                "FROM pg_database WHERE datname = current_database()"
            )
        )
        owns_protected_tables = connection.scalar(
            text(
                "SELECT EXISTS (SELECT 1 FROM pg_class "
                "WHERE relname IN ('tenants', 'companies', 'tenant_memberships', "
                "'tenant_onboarding_requests', 'audit_events') "
                "AND relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user))"
            )
        )
        can_create_in_public = connection.scalar(
            text("SELECT has_schema_privilege(current_user, 'public', 'CREATE')")
        )
        can_update_audit = connection.scalar(
            text("SELECT has_table_privilege(current_user, 'audit_events', 'UPDATE')")
        )
    assert runtime.connected_role == "bf_runtime"
    assert not runtime.rolsuper and not runtime.rolbypassrls
    assert not runtime.rolcreatedb and not runtime.rolcreaterole and not runtime.rolinherit
    assert not runtime.migration_member and not owns_database and not owns_protected_tables
    assert not can_create_in_public and not can_update_audit

    with admin_engine.connect() as connection:
        roles = connection.execute(
            text(
                "SELECT rolname, rolsuper, rolbypassrls FROM pg_roles "
                "WHERE rolname IN ('bf_runtime', 'bf_migrator') ORDER BY rolname"
            )
        ).all()
        tables = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname IN ('tenants', 'companies', 'tenant_memberships', "
                "'tenant_onboarding_requests', 'audit_events') ORDER BY c.relname"
            )
        ).all()
    assert {row.rolname for row in roles} == {"bf_migrator", "bf_runtime"}
    runtime = next(row for row in roles if row.rolname == "bf_runtime")
    assert runtime.rolsuper is False and runtime.rolbypassrls is False
    assert len(tables) == 5
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in tables)
    assert all(row.owner == "bf_migrator" for row in tables)


def test_rls_filters_unscoped_queries_fails_closed_and_context_does_not_leak(
    client, runtime_engine, admin_engine
):
    owner_a = register_user(client, "rls-a@example.com")
    tenant_a = create_tenant(
        client, owner_a, name="RLS A", company_name="Company RLS A", key="rls-a-key-001"
    )
    owner_b = register_user(client, "rls-b@example.com")
    tenant_b = create_tenant(
        client, owner_b, name="RLS B", company_name="Company RLS B", key="rls-b-key-001"
    )

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_a["id"]}
        )
        backend_pid_a = session.scalar(text("SELECT pg_backend_pid()"))
        rows = session.execute(text("SELECT tenant_id, legal_name FROM companies")).all()
        assert [(str(row.tenant_id), row.legal_name) for row in rows] == [
            (tenant_a["id"], "Company RLS A")
        ]
        changed = session.execute(
            text("UPDATE companies SET legal_name = 'Changed A' RETURNING tenant_id")
        ).all()
        assert len(changed) == 1

    with factory.begin() as session:
        backend_pid_no_context = session.scalar(text("SELECT pg_backend_pid()"))
        current_tenant = session.scalar(text("SELECT current_setting('app.tenant_id', true)"))
        assert backend_pid_no_context == backend_pid_a
        assert current_tenant in (None, "")
        assert session.scalar(text("SELECT count(*) FROM companies")) == 0
        assert session.scalar(text("SELECT count(*) FROM tenants")) == 0
        assert session.scalar(text("SELECT count(*) FROM audit_events")) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_b["id"]}
        )
        backend_pid_b = session.scalar(text("SELECT pg_backend_pid()"))
        rows = session.execute(text("SELECT tenant_id, legal_name FROM companies")).all()
        assert backend_pid_b == backend_pid_a
        assert [(str(row.tenant_id), row.legal_name) for row in rows] == [
            (tenant_b["id"], "Company RLS B")
        ]
        session.execute(text("UPDATE companies SET legal_name = 'Changed B'"))

    owner_a_user_id = client.get("/api/v1/auth/me", headers=auth_headers(owner_a)).json()["id"]
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.user_id', :id, true)"), {"id": owner_a_user_id}
        )
        # Only this user's membership is visible without a tenant setting; own-user visibility
        # does not grant tenant membership writes or tenant-onboarding writes.
        assert session.scalar(text("SELECT count(*) FROM tenant_memberships")) == 1
        membership_update = session.execute(
            text(
                "UPDATE tenant_memberships SET updated_at = now() "
                "WHERE tenant_id = :tenant_id AND user_id = :user_id"
            ),
            {"tenant_id": tenant_a["id"], "user_id": owner_a_user_id},
        )
        assert membership_update.rowcount == 0
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "INSERT INTO companies (id, tenant_id, legal_name, base_currency) "
                    "VALUES (gen_random_uuid(), :tenant_id, 'No Context', 'USD')"
                ),
                {"tenant_id": tenant_a["id"]},
            )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.user_id', :id, true)"), {"id": owner_a_user_id}
        )
        session.execute(
            text(
                "INSERT INTO tenant_onboarding_requests "
                "(user_id, key_hash, request_hash, tenant_id) "
                "VALUES (:user_id, :key_hash, :request_hash, :tenant_id)"
            ),
            {
                "user_id": owner_a_user_id,
                "key_hash": "a" * 64,
                "request_hash": "b" * 64,
                "tenant_id": tenant_b["id"],
            },
        )

    # RLS filters an UPDATE that omits its tenant predicate; physical deletion is not granted.
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_a["id"]}
        )
        result = session.execute(text("UPDATE companies SET legal_name = 'Unfiltered Update'"))
        assert result.rowcount == 1

    # A denied statement aborts its transaction, so verify the write privilege separately.
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_a["id"]}
        )
        session.execute(text("DELETE FROM companies"))

    with admin_engine.connect() as connection:
        names = connection.execute(
            text("SELECT tenant_id, legal_name FROM companies ORDER BY tenant_id")
        ).all()
    names_by_tenant = {str(row.tenant_id): row.legal_name for row in names}
    assert names_by_tenant[tenant_a["id"]] == "Unfiltered Update"
    assert names_by_tenant[tenant_b["id"]] == "Changed B"


def test_simultaneous_requests_keep_tenant_context_isolated(app):
    client_a = TestClient(app, raise_server_exceptions=False)
    client_b = TestClient(app, raise_server_exceptions=False)
    with client_a as a, client_b as b:
        tokens_a = register_user(a, "parallel-a@example.com")
        tenant_a = create_tenant(
            a, tokens_a, name="Parallel A", company_name="A", key="parallel-a-key-001"
        )
        tokens_b = register_user(b, "parallel-b@example.com")
        tenant_b = create_tenant(
            b, tokens_b, name="Parallel B", company_name="B", key="parallel-b-key-001"
        )

        cases = [
            (a, tokens_a, tenant_a["id"], "A"),
            (b, tokens_b, tenant_b["id"], "B"),
        ] * 12

        def read_company(case):
            test_client, tokens, tenant_id, _expected = case
            response = test_client.get(
                f"/api/v1/tenants/{tenant_id}/company", headers=auth_headers(tokens)
            )
            return response.status_code, response.json().get("legal_name")

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(read_company, cases))
    assert results == [(200, expected) for _, _, _, expected in cases]
