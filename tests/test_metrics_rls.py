from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_tenant, register_user


def _metric_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/metrics"


def test_metric_runs_use_forced_tenant_rls_and_are_append_only(
    client, runtime_engine, admin_engine
):
    owner_a = register_user(client, "metrics-rls-a@example.com")
    owner_a_id = client.get("/api/v1/auth/me", headers=auth_headers(owner_a)).json()["id"]
    tenant_a = create_tenant(client, owner_a, name="Metrics RLS A", key="metrics-rls-a-key-001")
    run_response = client.post(
        f"{_metric_path(tenant_a['id'])}/runs",
        json={"start_week_date": "2026-11-16", "week_count": 1},
        headers=auth_headers(owner_a),
    )
    assert run_response.status_code == 201, run_response.text
    run = run_response.json()

    owner_b = register_user(client, "metrics-rls-b@example.com")
    tenant_b = create_tenant(client, owner_b, name="Metrics RLS B", key="metrics-rls-b-key-001")
    denied_run = client.post(
        f"{_metric_path(tenant_a['id'])}/runs",
        json={"start_week_date": "2026-11-16", "week_count": 1},
        headers=auth_headers(owner_b),
    )
    assert denied_run.status_code == 404
    assert denied_run.json()["error"]["code"] == "tenant_not_found"

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = 'metric_runs'"
            )
        ).one()
        policies = connection.execute(
            text("SELECT policyname, cmd, roles FROM pg_policies WHERE tablename = 'metric_runs'")
        ).all()
        privileges = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'metric_runs', 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', 'metric_runs', 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', 'metric_runs', 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', 'metric_runs', 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', 'metric_runs', 'TRUNCATE') AS can_truncate"
            )
        ).one()
    assert security.relrowsecurity is True
    assert security.relforcerowsecurity is True
    assert security.owner == "bf_migrator"
    assert len(policies) == 1
    assert policies[0].cmd == "ALL"
    assert privileges.can_select and privileges.can_insert
    assert not privileges.can_update and not privileges.can_delete and not privileges.can_truncate

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM metric_runs")) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        visible = session.execute(text("SELECT id FROM metric_runs")).scalars().all()
        assert [str(run_id) for run_id in visible] == [run["id"]]

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM metric_runs")) == 0

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text(
                "INSERT INTO metric_runs (id, tenant_id, company_id, requested_by_user_id, "
                "start_week_date, end_week_date, week_count, metric_catalog_version, "
                "calculation_version, reliability_status, input_hash, result_hash, "
                "input_snapshot, result_snapshot) VALUES (:id, :tenant_id, :company_id, "
                ":user_id, '2026-11-16', '2026-11-22', 1, 'businessflow.metrics.v1', "
                "'businessflow.capacity-weekly.v1', 'unavailable', :hash, :hash, "
                "CAST(:input_snapshot AS jsonb), CAST(:result_snapshot AS jsonb))"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_a["company"]["id"],
                "user_id": owner_a_id,
                "hash": "a" * 64,
                "input_snapshot": json.dumps({}),
                "result_snapshot": json.dumps({}),
            },
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        session.execute(
            text("UPDATE metric_runs SET result_hash = :hash WHERE id = :run_id"),
            {"hash": "b" * 64, "run_id": run["id"]},
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        session.execute(text("DELETE FROM metric_runs WHERE id = :run_id"), {"run_id": run["id"]})

    hidden_from_other_tenant = client.get(
        f"{_metric_path(tenant_b['id'])}/runs/{run['id']}", headers=auth_headers(owner_b)
    )
    assert hidden_from_other_tenant.status_code == 404
