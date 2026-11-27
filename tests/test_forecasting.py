from __future__ import annotations

import hashlib
from datetime import date, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import MetaData, Table, delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.main import create_app
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/forecasts"


@pytest.fixture
def _clear_forecast_evidence(admin_engine):
    try:
        yield
    finally:
        # This integration test shares the disposable DB with migration downgrade tests.
        # Clear only M17 records so those tests can reach the older guards they exercise.
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "TRUNCATE TABLE forecast_promotion_decisions, forecast_runs, "
                "forecast_evaluation_model_versions, forecast_evaluations, "
                "forecast_evaluation_protocol_versions, forecast_model_versions, "
                "forecast_dataset_versions"
            )


def _dataset_body() -> dict[str, object]:
    observations = []
    for index in range(8):
        period = date(2026, 1, 1) + timedelta(days=index)
        observations.append(
            {
                "period_start": period.isoformat(),
                "dimension_values": {},
                "value": str(index + 1),
                "source_reference": {
                    "evidence_type": "caller_assertion",
                    "source_id": "test.source",
                    "source_hash": "a" * 64,
                    "record_locator": f"source-row:{index + 1}",
                    "record_hash": hashlib.sha256(f"record-{index + 1}".encode()).hexdigest(),
                },
            }
        )
    return {
        "dataset_key": "m17.daily.revenue",
        "display_name": "Caller-asserted daily revenue test series",
        "data_origin": "caller_assertion",
        "target_key": "revenue",
        "target_label": "Revenue",
        "unit": "USD",
        "currency_code": "USD",
        "frequency": "daily",
        "observations": observations,
    }


def _assert_forecast_storage_controls(
    *,
    tenant_id: UUID,
    company_id: UUID,
    dataset_id: UUID,
    model_id: UUID,
    protocol_id: UUID,
    validation_id: UUID,
    holdout_id: UUID,
    run_id: UUID,
    promotion_id: UUID,
    runtime_engine,
    migration_engine,
    admin_engine,
) -> None:
    tables = (
        "forecast_dataset_versions",
        "forecast_model_versions",
        "forecast_evaluation_protocol_versions",
        "forecast_evaluations",
        "forecast_evaluation_model_versions",
        "forecast_runs",
        "forecast_promotion_decisions",
    )
    table_params = {"table_names": list(tables)}
    metadata = MetaData()
    table_map = {
        table_name: Table(table_name, metadata, autoload_with=runtime_engine)
        for table_name in tables
    }
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:table_names) "
                "ORDER BY c.relname"
            ),
            table_params,
        ).all()
        policies = connection.execute(
            text(
                "SELECT tablename, cmd FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = ANY(:table_names)"
            ),
            table_params,
        ).all()
        grants = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', table_name, 'TRUNCATE') AS can_truncate "
                "FROM unnest(CAST(:table_names AS text[])) AS t(table_name) "
                "ORDER BY table_name"
            ),
            table_params,
        ).all()
        triggers = connection.execute(
            text(
                "SELECT c.relname, t.tgname FROM pg_trigger t "
                "JOIN pg_class c ON c.oid = t.tgrelid "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND NOT t.tgisinternal "
                "AND c.relname = ANY(:table_names)"
            ),
            table_params,
        ).all()

    assert {row.relname for row in security} == set(tables)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert {row.tablename for row in policies} == set(tables)
    assert all(row.cmd == "ALL" for row in policies)
    assert {row.table_name for row in grants} == set(tables)
    assert all(row.can_select and row.can_insert for row in grants)
    assert all(not row.can_update and not row.can_delete and not row.can_truncate for row in grants)
    assert {row.relname for row in triggers} == set(tables)
    assert {row.tgname for row in triggers} == {f"trg_{table}_append_only" for table in tables}

    expected_counts = {
        "forecast_dataset_versions": 1,
        "forecast_model_versions": 1,
        "forecast_evaluation_protocol_versions": 1,
        "forecast_evaluations": 2,
        "forecast_evaluation_model_versions": 2,
        "forecast_runs": 1,
        "forecast_promotion_decisions": 1,
    }
    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        for table_name in tables:
            assert session.scalar(select(func.count()).select_from(table_map[table_name])) == 0
    for selected_tenant_id, selected_company_id, expected_visible in (
        (tenant_id, company_id, True),
        (tenant_id, uuid4(), False),
        (uuid4(), company_id, False),
    ):
        with factory.begin() as session:
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(selected_tenant_id)},
            )
            session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": str(selected_company_id)},
            )
            for table_name in tables:
                expected = expected_counts[table_name] if expected_visible else 0
                assert (
                    session.scalar(select(func.count()).select_from(table_map[table_name]))
                    == expected
                )

    update_specs = (
        ("forecast_dataset_versions", "display_name", {"id": dataset_id}),
        ("forecast_model_versions", "display_name", {"id": model_id}),
        ("forecast_evaluation_protocol_versions", "display_name", {"id": protocol_id}),
        ("forecast_evaluations", "evaluation_status", {"id": holdout_id}),
        (
            "forecast_evaluation_model_versions",
            "model_index",
            {
                "tenant_id": tenant_id,
                "company_id": company_id,
                "evaluation_id": validation_id,
                "model_version_id": model_id,
            },
        ),
        ("forecast_runs", "run_status", {"id": run_id}),
        ("forecast_promotion_decisions", "decision", {"id": promotion_id}),
    )
    for table_name, column_name, key_values in update_specs:
        table = table_map[table_name]
        where_clause = [table.c[key] == value for key, value in key_values.items()]
        update_statement = (
            update(table).values({column_name: table.c[column_name]}).where(*where_clause)
        )
        delete_statement = delete(table).where(*where_clause)
        for statement in (update_statement, delete_statement):
            with (
                pytest.raises(DBAPIError, match="forecast evidence is append-only"),
                migration_engine.begin() as connection,
            ):
                connection.execute(
                    text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                    {"tenant_id": str(tenant_id)},
                )
                connection.execute(
                    text("SELECT set_config('app.company_id', :company_id, true)"),
                    {"company_id": str(company_id)},
                )
                connection.execute(statement)


def test_forecasting_api_versions_replays_and_requires_independent_reviewer(
    _clear_forecast_evidence, client, settings, runtime_engine, migration_engine, admin_engine
) -> None:
    owner_tokens = register_user(client, f"m17-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner_tokens)
    tenant = create_tenant(
        client,
        owner_tokens,
        name="M17 Test Tenant",
        company_name="M17 Test Company",
        key=f"m17-tenant-{uuid4().hex}",
    )
    base = _base(tenant["id"])
    assert client.get(f"{base}/catalog", headers=owner_headers).status_code == 503

    reviewer_email = f"m17-reviewer-{uuid4().hex}@example.com"
    reviewer_tokens = register_user(client, reviewer_email)
    create_member(client, owner_tokens, tenant["id"], reviewer_email, role="admin")
    enabled_settings = settings.model_copy(
        update={"forecasting_enabled": True, "forecast_promotion_enabled": True}
    )
    with TestClient(create_app(enabled_settings), raise_server_exceptions=False) as forecast_client:
        catalog = forecast_client.get(f"{base}/catalog", headers=owner_headers)
        assert catalog.status_code == 200, catalog.text
        assert catalog.json()["maximum_observations_per_dataset"] == 20_000
        assert catalog.json()["policy_defaults"]["production_activation"] == (
            "blocked_pending_governance_and_approval"
        )

        member_email = f"m17-member-{uuid4().hex}@example.com"
        member_tokens = register_user(client, member_email)
        create_member(client, owner_tokens, tenant["id"], member_email, role="member")
        denied_member_read = forecast_client.get(
            f"{base}/catalog", headers=auth_headers(member_tokens)
        )
        assert denied_member_read.status_code == 403

        created_dataset = forecast_client.post(
            f"{base}/datasets", json=_dataset_body(), headers=owner_headers
        )
        assert created_dataset.status_code == 201, created_dataset.text
        dataset_response = created_dataset.json()
        dataset_id = dataset_response["dataset"]["id"]
        assert dataset_response["eligibility"]["eligibility_for_backtesting"] == "candidate"
        assert dataset_response["eligibility"]["scope"] == (
            "technical_input_assessment_not_business_approval"
        )
        assert (
            "caller_asserted_source_not_independently_verified"
            in dataset_response["eligibility"]["reason_codes"]
        )

        created_model = forecast_client.post(
            f"{base}/models",
            json={
                "model_key": "m17.naive-last",
                "display_name": "Naive last-value baseline",
                "definition": {"kind": "naive_last_value"},
            },
            headers=owner_headers,
        )
        assert created_model.status_code == 201, created_model.text
        model = created_model.json()

        created_protocol = forecast_client.post(
            f"{base}/protocols",
            json={
                "protocol_key": "m17.daily.protocol",
                "display_name": "Daily rolling-origin evaluation",
                "target_key": "revenue",
                "frequency": "daily",
                "horizon_periods": 1,
                "validation_periods": 2,
                "holdout_periods": 2,
                "metrics": ["mae", "rmse", "mape", "smape"],
                "interval": {
                    "method": "validation_absolute_error_nearest_rank",
                    "coverage_level": "0.9",
                    "minimum_calibration_points": 1,
                },
            },
            headers=owner_headers,
        )
        assert created_protocol.status_code == 201, created_protocol.text
        protocol_id = created_protocol.json()["id"]

        created_validation = forecast_client.post(
            f"{base}/evaluations",
            json={
                "dataset_version_id": dataset_id,
                "protocol_version_id": protocol_id,
                "model_version_ids": [model["id"]],
                "stage": "validation",
            },
            headers=owner_headers,
        )
        assert created_validation.status_code == 201, created_validation.text
        validation = created_validation.json()
        assert validation["evaluation"]["evaluation_status"] == "evaluated"
        validation_id = validation["evaluation"]["id"]
        validation_replay = forecast_client.post(
            f"{base}/evaluations/{validation_id}/verify", headers=owner_headers
        )
        assert validation_replay.status_code == 200, validation_replay.text
        assert validation_replay.json()["replay_status"] == "verified"

        created_holdout = forecast_client.post(
            f"{base}/evaluations",
            json={
                "dataset_version_id": dataset_id,
                "protocol_version_id": protocol_id,
                "model_version_ids": [model["id"]],
                "stage": "holdout",
                "calibration_evaluation_id": validation_id,
            },
            headers=owner_headers,
        )
        assert created_holdout.status_code == 201, created_holdout.text
        holdout = created_holdout.json()
        assert holdout["evaluation"]["evaluation_status"] == "evaluated"
        assert holdout["result_snapshot"]["split"]["holdout_uses_one_fixed_forecast_origin"]

        second_holdout = forecast_client.post(
            f"{base}/evaluations",
            json={
                "dataset_version_id": dataset_id,
                "protocol_version_id": protocol_id,
                "model_version_ids": [model["id"]],
                "stage": "holdout",
                "calibration_evaluation_id": validation_id,
            },
            headers=owner_headers,
        )
        assert second_holdout.status_code == 409
        assert second_holdout.json()["error"]["code"] == "forecast_holdout_already_consumed"

        created_run = forecast_client.post(
            f"{base}/runs",
            json={
                "dataset_version_id": dataset_id,
                "protocol_version_id": protocol_id,
                "model_version_id": model["id"],
                "calibration_evaluation_id": validation_id,
            },
            headers=owner_headers,
        )
        assert created_run.status_code == 201, created_run.text
        run = created_run.json()
        assert run["run"]["run_status"] == "forecasted"
        assert len(run["run"]["run_hash"]) == 64
        replayed_run = forecast_client.post(
            f"{base}/runs/{run['run']['id']}/verify", headers=owner_headers
        )
        assert replayed_run.status_code == 200, replayed_run.text
        assert replayed_run.json()["replay_status"] == "verified"

        promotion_body = {
            "evaluation_id": holdout["evaluation"]["id"],
            "model_version_id": model["id"],
            "expected_evaluation_hash": holdout["evaluation"]["evaluation_hash"],
            "expected_model_hash": model["model_hash"],
            "decision": "approve",
            "rationale": "Independent review of the complete holdout evidence.",
        }
        self_approved = forecast_client.post(
            f"{base}/promotions", json=promotion_body, headers=owner_headers
        )
        assert self_approved.status_code == 403
        assert self_approved.json()["error"]["code"] == "forecast_promotion_separation_of_duties"

        reviewed = forecast_client.post(
            f"{base}/promotions",
            json=promotion_body,
            headers=auth_headers(reviewer_tokens),
        )
        assert reviewed.status_code == 201, reviewed.text
        decision = reviewed.json()["promotion"]
        assert decision["promotion_scope"] == "nonproduction_only"
        assert decision["production_activation_status"] == "blocked_pending_governance"
        promotions = forecast_client.get(f"{base}/promotions", headers=owner_headers)
        assert promotions.status_code == 200, promotions.text
        assert promotions.json()["items"][0]["promotion_hash"] == decision["promotion_hash"]

    _assert_forecast_storage_controls(
        tenant_id=UUID(tenant["id"]),
        company_id=UUID(tenant["company"]["id"]),
        dataset_id=UUID(dataset_id),
        model_id=UUID(model["id"]),
        protocol_id=UUID(protocol_id),
        validation_id=UUID(validation_id),
        holdout_id=UUID(holdout["evaluation"]["id"]),
        run_id=UUID(run["run"]["id"]),
        promotion_id=UUID(decision["id"]),
        runtime_engine=runtime_engine,
        migration_engine=migration_engine,
        admin_engine=admin_engine,
    )
