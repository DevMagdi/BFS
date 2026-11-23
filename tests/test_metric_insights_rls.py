from __future__ import annotations

import json
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_tenant, register_user


def _metrics_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/metrics"


def test_metric_insight_runs_force_company_tenant_rls_and_are_append_only(
    client, runtime_engine, migration_engine, admin_engine
):
    owner_a = register_user(client, "m13-rls-a@example.com")
    owner_a_id = client.get("/api/v1/auth/me", headers=auth_headers(owner_a)).json()["id"]
    tenant_a = create_tenant(client, owner_a, name="M13 RLS A", key="m13-rls-a-key-0001")
    metrics_a = _metrics_path(tenant_a["id"])
    headers_a = auth_headers(owner_a)
    baseline_response = client.post(
        f"{metrics_a}/runs",
        json={"start_week_date": "2025-11-03", "week_count": 1},
        headers=headers_a,
    )
    comparison_response = client.post(
        f"{metrics_a}/runs",
        json={"start_week_date": "2025-11-10", "week_count": 1},
        headers=headers_a,
    )
    assert baseline_response.status_code == comparison_response.status_code == 201
    baseline = baseline_response.json()
    comparison = comparison_response.json()
    insight_response = client.post(
        f"{metrics_a}/insights/runs",
        json={
            "baseline_metric_run_id": baseline["id"],
            "comparison_metric_run_id": comparison["id"],
            "metric_id": "available_capacity_hours",
        },
        headers=headers_a,
    )
    assert insight_response.status_code == 201, insight_response.text
    insight = insight_response.json()
    assert insight["availability_status"] == "abstained"
    insight_id = insight["id"]

    owner_b = register_user(client, "m13-rls-b@example.com")
    tenant_b = create_tenant(client, owner_b, name="M13 RLS B", key="m13-rls-b-key-0001")
    hidden = client.get(
        f"{_metrics_path(tenant_b['id'])}/insights/runs/{insight_id}",
        headers=auth_headers(owner_b),
    )
    assert hidden.status_code == 404
    assert hidden.json()["error"]["code"] == "metric_insight_run_not_found"

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = 'metric_insight_runs'"
            )
        ).one()
        policies = connection.execute(
            text(
                "SELECT policyname, cmd, roles FROM pg_policies "
                "WHERE tablename = 'metric_insight_runs'"
            )
        ).all()
        privileges = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'metric_insight_runs', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'metric_insight_runs', 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', 'metric_insight_runs', 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', 'metric_insight_runs', 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', 'metric_insight_runs', 'TRUNCATE') "
                "AS can_truncate"
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
        assert session.scalar(text("SELECT count(*) FROM metric_insight_runs")) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM metric_insight_runs")) == 0
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        visible = session.execute(text("SELECT id FROM metric_insight_runs")).scalars().all()
        assert [str(run_id) for run_id in visible] == [insight_id]

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_b["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM metric_insight_runs")) == 0

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_b["company"]["id"]},
        )
        session.execute(
            text(
                "INSERT INTO metric_insight_runs (id, tenant_id, company_id, requested_by_user_id, "
                "baseline_metric_run_id, comparison_metric_run_id, metric_id, "
                "insight_catalog_version, engine_version, comparison_definition_version, "
                "availability_status, input_hash, result_hash, input_snapshot, result_snapshot) "
                "VALUES (:id, :tenant_id, :company_id, :user_id, :baseline_id, :comparison_id, "
                "'available_capacity_hours', 'businessflow.metric-insight-catalog.v2', "
                "'businessflow.metric-insight.v2', "
                "'businessflow.metric-comparison.completed-adjacent-weekly.v2', 'abstained', "
                ":hash, :hash, "
                "CAST(:input_snapshot AS jsonb), CAST(:result_snapshot AS jsonb))"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_a["company"]["id"],
                "user_id": owner_a_id,
                "baseline_id": baseline["id"],
                "comparison_id": comparison["id"],
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
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        session.execute(
            text("UPDATE metric_insight_runs SET result_hash = :hash WHERE id = :run_id"),
            {"hash": "b" * 64, "run_id": insight_id},
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        session.execute(
            text("DELETE FROM metric_insight_runs WHERE id = :run_id"), {"run_id": insight_id}
        )

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        connection.execute(
            text("DELETE FROM metric_insight_runs WHERE id = :run_id"), {"run_id": insight_id}
        )
