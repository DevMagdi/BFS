from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.config import Settings
from businessflow.main import create_app
from tests.helpers import auth_headers, create_member, create_tenant, register_user

FIELD_MAPPING = {
    "source_record_id": "record",
    "reconciliation_key": "bucket",
    "period_start": "from",
    "period_end": "to",
    "amount": "amount",
    "currency": "currency",
}


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/financial-calculations"


def _source_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/finance-sources"


def _create_intake(
    client,
    tenant_id: str,
    headers: dict[str, str],
    *,
    source_as_of_at: datetime | None = None,
) -> dict[str, object]:
    metadata = {
        "source_key": "m15-synthetic-manual-source",
        "source_as_of_at": (source_as_of_at or datetime.now(UTC)).isoformat(),
        "freshness_max_age_hours": 24,
        "field_mapping": FIELD_MAPPING,
        "ignored_columns": [],
        "controls": [
            {
                "control_id": "m15-independent-control",
                "reconciliation_key": "opaque-m15-group",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency": "USD",
                "expected_amount": "1.005",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "synthetic-control-source",
                    "source_hash": "e" * 64,
                    "record_locator": "synthetic-control:row:1",
                },
            }
        ],
    }
    response = client.post(
        f"{_source_base(tenant_id)}/intakes",
        data={"metadata_json": json.dumps(metadata)},
        files={
            "file": (
                "synthetic-m15.csv",
                b"record,bucket,from,to,amount,currency\n"
                b"row-a,opaque-m15-group,2026-11-01,2026-11-30,1.25,USD\n"
                b"row-b,opaque-m15-group,2026-11-01,2026-11-30,-0.25,USD\n"
                b"row-c,opaque-m15-group,2026-11-01,2026-11-30,0.005,USD\n",
                "text/csv",
            )
        },
        headers={**headers, "Idempotency-Key": f"m15-m14-intake-{uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _calculation_request(intake: dict[str, object]) -> dict[str, object]:
    group = intake["result_snapshot"]["reconciliation_groups"][0]  # type: ignore[index]
    return {
        "snapshot_id": str(uuid4()),
        "reconciliation_group_hash": group["group_hash"],  # type: ignore[index]
        "observations": [
            {"source_row_number": 2, "amount_text": "1.25"},
            {"source_row_number": 3, "amount_text": "-0.25"},
            {"source_row_number": 4, "amount_text": "0.005"},
        ],
    }


def _run_url(tenant_id: str, intake_id: str) -> str:
    return f"{_base(tenant_id)}/finance-source-intakes/{intake_id}/runs"


def test_financial_calculation_api_runs_exact_m14_subtotal_and_replays(
    client, admin_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    owner = register_user(client, f"m15-api-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 API Tenant",
        company_name="M15 API Company",
        key=f"m15-api-tenant-{uuid4().hex}",
    )
    intake = _create_intake(client, tenant["id"], headers)
    request_body = _calculation_request(intake)
    response = client.post(
        _run_url(tenant["id"], intake["id"]),
        json=request_body,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    run = payload["run"]
    trace = payload["trace"]
    assert run["state"] == "calculated"
    assert run["formula_id"] == "businessflow.finance.source-group-subtotal"
    assert run["formula_version"] == "1.0.0"
    assert trace["output"]["value"] == "1.005"
    assert trace["output"]["currency_code"] == "USD"
    assert trace["output"]["rounding_applied"] is False
    assert (
        trace["output"]["accounting_interpretation"]
        == "mechanical_subtotal_only_not_revenue_cost_profit_or_accounting_truth"
    )
    assert trace["core_trace"]["state"] == "calculated"
    assert trace["core_trace"]["formula"]["operation"] == "exact_sum"

    replay = client.post(
        f"{_run_url(tenant['id'], intake['id'])}/{run['id']}/verify",
        json=request_body,
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["passed"] is True
    assert replay.json()["recorded_trace_hash"] == run["trace_hash"]
    assert replay.json()["core_replay_passed"] is True
    replay_metadata = json.dumps(replay.json(), sort_keys=True)

    detail = client.get(f"{_run_url(tenant['id'], intake['id'])}/{run['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    stored_metadata = json.dumps(detail.json()["evidence_snapshot"], sort_keys=True)
    for amount_text in ("1.25", "-0.25", "0.005", "1.005"):
        assert amount_text not in stored_metadata

    with admin_engine.connect() as connection:
        commitments = connection.scalar(
            text(
                "SELECT count(*) FROM finance_source_row_evidence "
                "WHERE intake_id = :intake_id "
                "AND amount_commitment_hash IS NOT NULL "
                "AND amount_commitment_key_version = 'app-secret.v1'"
            ),
            {"intake_id": UUID(intake["id"])},
        )
        invalid_commitment_pairs = connection.scalar(
            text(
                "SELECT count(*) FROM finance_source_row_evidence "
                "WHERE intake_id = :intake_id AND ("
                "(amount_commitment_hash IS NULL) <> "
                "(amount_commitment_key_version IS NULL) OR "
                "(amount_commitment_hash IS NOT NULL AND "
                "amount_commitment_key_version <> 'app-secret.v1'))"
            ),
            {"intake_id": UUID(intake["id"])},
        )
        persisted = connection.execute(
            text(
                "SELECT evidence_snapshot::text AS snapshot, input_hash, result_hash "
                "FROM financial_calculation_runs WHERE id = :run_id"
            ),
            {"run_id": UUID(run["id"])},
        ).one()
        audit_metadata = connection.scalar(
            text(
                "SELECT metadata::text FROM audit_events "
                "WHERE event_type = 'financial_calculation.run_created' "
                "AND target_id = :run_id"
            ),
            {"run_id": UUID(run["id"])},
        )
    assert commitments == 3
    assert invalid_commitment_pairs == 0
    assert "1.25" not in persisted.snapshot
    assert "-0.25" not in persisted.snapshot
    assert "0.005" not in persisted.snapshot
    assert "1.005" not in persisted.snapshot
    assert persisted.input_hash == run["input_hash"]
    assert persisted.result_hash == run["result_hash"]
    assert audit_metadata is not None
    for amount_text in ("1.25", "-0.25", "0.005", "1.005"):
        assert amount_text not in audit_metadata
        assert amount_text not in replay_metadata
        assert amount_text not in caplog.text

    altered_request = {
        **request_body,
        "observations": [
            {"source_row_number": 2, "amount_text": "1.26"},
            *request_body["observations"][1:],  # type: ignore[index]
        ],
    }
    failed_replay = client.post(
        f"{_run_url(tenant['id'], intake['id'])}/{run['id']}/verify",
        json=altered_request,
        headers=headers,
    )
    assert failed_replay.status_code == 200, failed_replay.text
    assert failed_replay.json()["passed"] is False
    assert "persisted_input_hash_mismatch" in failed_replay.json()["reason_codes"]
    failed_replay_metadata = json.dumps(failed_replay.json(), sort_keys=True)
    for amount_text in ("1.25", "-0.25", "0.005", "1.005", "1.26"):
        assert amount_text not in failed_replay_metadata
        assert amount_text not in caplog.text


def test_financial_calculation_api_is_default_disabled_for_every_route(
    client, settings, monkeypatch
):
    monkeypatch.delenv("FINANCIAL_CALCULATIONS_ENABLED", raising=False)
    owner = register_user(client, f"m15-disabled-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 Default Disabled Tenant",
        company_name="M15 Default Disabled Company",
        key=f"m15-default-disabled-tenant-{uuid4().hex}",
    )
    intake = _create_intake(client, tenant["id"], headers)
    request_body = _calculation_request(intake)
    created = client.post(_run_url(tenant["id"], intake["id"]), json=request_body, headers=headers)
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["id"]

    m15_disabled_settings = Settings(
        _env_file=None,
        app_secret_key=settings.app_secret_key.get_secret_value(),
        app_env="test",
        database_url=settings.database_url,
        finance_source_intake_enabled=True,
    )
    m14_disabled_settings = Settings(
        _env_file=None,
        app_secret_key=settings.app_secret_key.get_secret_value(),
        app_env="test",
        database_url=settings.database_url,
        financial_calculations_enabled=True,
        finance_source_intake_enabled=False,
    )
    assert m15_disabled_settings.financial_calculations_enabled is False
    assert m15_disabled_settings.finance_source_intake_enabled is True
    assert m14_disabled_settings.financial_calculations_enabled is True
    assert m14_disabled_settings.finance_source_intake_enabled is False

    for disabled_settings in (m15_disabled_settings, m14_disabled_settings):
        with TestClient(
            create_app(disabled_settings), raise_server_exceptions=False
        ) as disabled_client:
            responses = [
                disabled_client.get(f"{_base(tenant['id'])}/formulas", headers=headers),
                disabled_client.post(
                    _run_url(tenant["id"], intake["id"]), json=request_body, headers=headers
                ),
                disabled_client.get(
                    f"{_run_url(tenant['id'], intake['id'])}/{run_id}", headers=headers
                ),
                disabled_client.post(
                    f"{_run_url(tenant['id'], intake['id'])}/{run_id}/verify",
                    json=request_body,
                    headers=headers,
                ),
            ]
            assert all(response.status_code == 503 for response in responses)
            assert all(
                response.json()["error"]["code"] == "financial_calculations_disabled"
                for response in responses
            )
            assert disabled_client.get("/api/v1/auth/me", headers=headers).status_code == 200


def test_financial_calculation_api_abstains_for_missing_and_duplicate_source_rows(client):
    owner = register_user(client, f"m15-incomplete-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 Incomplete Tenant",
        company_name="M15 Incomplete Company",
        key=f"m15-incomplete-tenant-{uuid4().hex}",
    )
    intake = _create_intake(client, tenant["id"], headers)
    url = _run_url(tenant["id"], intake["id"])
    request_body = _calculation_request(intake)

    missing = {
        **request_body,
        "observations": [
            {"source_row_number": 2, "amount_text": None},
            *request_body["observations"][1:],  # type: ignore[index]
        ],
    }
    missing_response = client.post(url, json=missing, headers=headers)
    assert missing_response.status_code == 201, missing_response.text
    assert missing_response.json()["trace"]["state"] == "incomplete"
    assert missing_response.json()["trace"]["output"] is None
    assert "amount_missing" in missing_response.json()["trace"]["reason_codes"]

    duplicate = {
        **request_body,
        "observations": [
            *request_body["observations"][:1],  # type: ignore[index]
            *request_body["observations"][:1],  # type: ignore[index]
            *request_body["observations"][1:],  # type: ignore[index]
        ],
    }
    duplicate_response = client.post(url, json=duplicate, headers=headers)
    assert duplicate_response.status_code == 201, duplicate_response.text
    assert duplicate_response.json()["trace"]["state"] == "incomplete"
    assert duplicate_response.json()["trace"]["output"] is None
    assert "duplicate_input_row" in duplicate_response.json()["trace"]["reason_codes"]


def test_financial_calculation_api_abstains_for_a_stale_m14_intake(client):
    owner = register_user(client, f"m15-stale-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 Stale Tenant",
        company_name="M15 Stale Company",
        key=f"m15-stale-tenant-{uuid4().hex}",
    )
    intake = _create_intake(
        client,
        tenant["id"],
        headers,
        source_as_of_at=datetime.now(UTC) - timedelta(hours=25),
    )
    assert intake["freshness_status"] == "stale"

    response = client.post(
        _run_url(tenant["id"], intake["id"]),
        json=_calculation_request(intake),
        headers=headers,
    )
    assert response.status_code == 201, response.text
    trace = response.json()["trace"]
    assert trace["state"] == "incomplete"
    assert trace["output"] is None
    assert "source_not_fresh_at_calculation" in trace["reason_codes"]


def test_financial_calculation_api_enforces_finance_authorization_and_tenant_scope(client):
    owner = register_user(client, f"m15-auth-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 Authorization Tenant",
        company_name="M15 Authorization Company",
        key=f"m15-auth-tenant-{uuid4().hex}",
    )
    intake = _create_intake(client, tenant["id"], owner_headers)
    request_body = _calculation_request(intake)
    created = client.post(
        _run_url(tenant["id"], intake["id"]),
        json=request_body,
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["id"]

    member_email = f"m15-auth-member-{uuid4().hex}@example.com"
    member = register_user(client, member_email)
    create_member(client, owner, tenant["id"], member_email)
    member_headers = auth_headers(member)
    assert client.get(f"{_base(tenant['id'])}/formulas", headers=member_headers).status_code == 403
    assert (
        client.get(
            f"{_run_url(tenant['id'], intake['id'])}/{run_id}", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{_run_url(tenant['id'], intake['id'])}/{run_id}/verify",
            json=request_body,
            headers=member_headers,
        ).status_code
        == 403
    )
    denied_create = client.post(
        _run_url(tenant["id"], intake["id"]),
        json=request_body,
        headers=member_headers,
    )
    assert denied_create.status_code == 403

    other_owner = register_user(client, f"m15-other-owner-{uuid4().hex}@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="M15 Other Tenant",
        company_name="M15 Other Company",
        key=f"m15-other-tenant-{uuid4().hex}",
    )
    hidden = client.get(
        f"{_run_url(other_tenant['id'], intake['id'])}/{run_id}",
        headers=auth_headers(other_owner),
    )
    assert hidden.status_code == 404


def test_financial_calculation_run_table_enforces_rls_grants_and_immutability(
    client,
    runtime_engine,
    migration_engine,
    admin_engine,
):
    owner = register_user(client, f"m15-rls-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M15 RLS Tenant A",
        company_name="M15 RLS Company A",
        key=f"m15-rls-tenant-a-{uuid4().hex}",
    )
    intake = _create_intake(client, tenant["id"], headers)
    request_body = _calculation_request(intake)
    created = client.post(_run_url(tenant["id"], intake["id"]), json=request_body, headers=headers)
    assert created.status_code == 201, created.text
    run = created.json()["run"]
    tenant_id = tenant["id"]
    company_id = tenant["company"]["id"]

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = 'financial_calculation_runs'"
            )
        ).one()
        policies = connection.execute(
            text("SELECT cmd FROM pg_policies WHERE tablename = 'financial_calculation_runs'")
        ).all()
        grants = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'financial_calculation_runs', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'financial_calculation_runs', 'INSERT') "
                "AS can_insert, "
                "has_table_privilege('bf_runtime', 'financial_calculation_runs', 'UPDATE') "
                "AS can_update, "
                "has_table_privilege('bf_runtime', 'financial_calculation_runs', 'DELETE') "
                "AS can_delete, "
                "has_table_privilege('bf_runtime', 'financial_calculation_runs', 'TRUNCATE') "
                "AS can_truncate"
            )
        ).one()
    assert security.relrowsecurity and security.relforcerowsecurity
    assert security.owner == "bf_migrator"
    assert [item.cmd for item in policies] == ["ALL"]
    assert grants.can_select and grants.can_insert
    assert not grants.can_update and not grants.can_delete and not grants.can_truncate

    for source_row_number, commitment_hash, key_version in (
        (100_003, "d" * 64, None),
        (100_004, None, "app-secret.v1"),
    ):
        with (
            pytest.raises(
                DBAPIError,
                match="ck_finance_source_row_evidence_amount_commitment",
            ),
            migration_engine.begin() as connection,
        ):
            connection.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            connection.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO finance_source_row_evidence ("
                    "id, tenant_id, company_id, intake_id, source_row_number, "
                    "source_row_hash, source_record_identity_hash, "
                    "reconciliation_group_hash, row_status, amount_commitment_hash, "
                    "amount_commitment_key_version) VALUES ("
                    ":id, :tenant_id, :company_id, :intake_id, :row_number, "
                    ":row_hash, :identity_hash, :group_hash, 'invalid', :commitment_hash, "
                    ":key_version)"
                ),
                {
                    "id": uuid4(),
                    "tenant_id": tenant_id,
                    "company_id": company_id,
                    "intake_id": intake["id"],
                    "row_number": source_row_number,
                    "row_hash": "a" * 64,
                    "identity_hash": "b" * 64,
                    "group_hash": intake["result_snapshot"]["reconciliation_groups"][0][
                        "group_hash"
                    ],
                    "commitment_hash": commitment_hash,
                    "key_version": key_version,
                },
            )

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM financial_calculation_runs")) == 0
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        assert session.scalar(text("SELECT count(*) FROM financial_calculation_runs")) == 1
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(uuid4())},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(uuid4())},
        )
        assert session.scalar(text("SELECT count(*) FROM financial_calculation_runs")) == 0
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE financial_calculation_runs SET state = 'incomplete' WHERE id = :run_id"
                ),
                {"run_id": UUID(run["id"])},
            )

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        connection.execute(
            text("DELETE FROM financial_calculation_runs WHERE id = :run_id"),
            {"run_id": UUID(run["id"])},
        )
