from __future__ import annotations

import json
import logging
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.config import Settings
from businessflow.direct_cash_forecasts.catalog import DIRECT_CASH_FORECAST_FORMULA_HASH
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
    return f"/api/v1/tenants/{tenant_id}/direct-cash-forecasts"


def _source_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/finance-sources"


def _run_url(tenant_id: str, intake_id: str) -> str:
    return f"{_base(tenant_id)}/finance-source-intakes/{intake_id}/runs"


def _create_cash_intake(
    client,
    tenant_id: str,
    headers: dict[str, str],
    *,
    source_as_of: datetime | None = None,
    expected_freshness: str = "fresh",
) -> dict[str, object]:
    source_as_of = source_as_of or datetime.now(UTC)
    period_start = date(2026, 11, 1)
    period_end = date(2026, 11, 30)
    records = [("opening-cash", "100.50")]
    for week in range(13):
        for direction in ("receipt", "disbursement"):
            for basis in ("actual", "forecast"):
                records.append((f"week-{week + 1:02d}-{direction}-{basis}", "0"))

    lines = ["record,bucket,from,to,amount,currency"]
    lines.extend(
        f"{record},m16-cash-group,{period_start.isoformat()},{period_end.isoformat()},{amount},USD"
        for record, amount in records
    )
    csv_bytes = ("\n".join(lines) + "\n").encode("utf-8")
    metadata = {
        "source_key": "m16-synthetic-manual-source",
        "source_as_of_at": source_as_of.isoformat(),
        "freshness_max_age_hours": 24,
        "field_mapping": FIELD_MAPPING,
        "ignored_columns": [],
        "controls": [
            {
                "control_id": "m16-independent-synthetic-control",
                "reconciliation_key": "m16-cash-group",
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
                "currency": "USD",
                "expected_amount": "100.50",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "synthetic-independent-control",
                    "source_hash": "e" * 64,
                    "record_locator": "synthetic-control:m16:cash-group",
                },
            }
        ],
    }
    response = client.post(
        f"{_source_base(tenant_id)}/intakes",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("synthetic-m16-cash.csv", csv_bytes, "text/csv")},
        headers={**headers, "Idempotency-Key": f"m16-cash-intake-{uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["quality_status"] == "passed"
    assert result["freshness_status"] == expected_freshness
    assert result["reconciliation_status"] == "passed"
    assert result["total_rows"] == 53
    return result


def _complete_request(intake: dict[str, object], as_of_date: date) -> dict[str, object]:
    group = intake["result_snapshot"]["reconciliation_groups"][0]  # type: ignore[index]
    movements = []
    row_number = 3
    for week in range(13):
        cash_date = as_of_date + timedelta(days=7 * week)
        for direction in ("receipt", "disbursement"):
            for basis in ("actual", "forecast"):
                movements.append(
                    {
                        "source_row_number": row_number,
                        "amount_text": "0",
                        "cash_flow_type": direction,
                        "basis": basis,
                        "cash_date": cash_date.isoformat(),
                    }
                )
                row_number += 1
    return {
        "snapshot_id": str(uuid4()),
        "reconciliation_group_hash": group["group_hash"],  # type: ignore[index]
        "as_of_date": as_of_date.isoformat(),
        "opening_cash": {"source_row_number": 2, "amount_text": "100.50"},
        "movements": movements,
    }


def test_direct_cash_forecast_api_creates_reads_and_replays_redacted_run(
    client, admin_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    owner = register_user(client, f"m16-api-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M16 API Tenant",
        company_name="M16 API Company",
        key=f"m16-api-tenant-{uuid4().hex}",
    )
    intake = _create_cash_intake(client, tenant["id"], headers)
    body = _complete_request(intake, datetime.now(UTC).date())

    contract = client.get(f"{_base(tenant['id'])}/contract", headers=headers)
    assert contract.status_code == 200, contract.text
    assert contract.json()["formula_id"] == "businessflow.finance.direct-cash-forecast"
    assert contract.json()["definition_hash"] == DIRECT_CASH_FORECAST_FORMULA_HASH

    created = client.post(_run_url(tenant["id"], intake["id"]), json=body, headers=headers)
    assert created.status_code == 201, created.text
    payload = created.json()
    run = payload["run"]
    trace = payload["trace"]
    assert run["calculation_state"] == "calculated"
    assert run["forecast_completeness_status"] == "not_established_by_m14"
    assert trace["calculation_state"] == "calculated"
    assert trace["result_snapshot"]["weeks"][0]["closing_cash_known_item_rollforward"] == "100.5"
    assert all(
        week["closing_cash_known_item_rollforward"] == "100.5"
        for week in trace["result_snapshot"]["weeks"]
    )
    assert trace["result_hash"] == run["result_hash"]

    replay = client.post(
        f"{_run_url(tenant['id'], intake['id'])}/{run['id']}/verify",
        json=body,
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["passed"] is True
    assert replay.json()["recorded_trace_hash"] == run["trace_hash"]
    assert replay.json()["replayed_result_hash"] == run["result_hash"]

    detail = client.get(f"{_run_url(tenant['id'], intake['id'])}/{run['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["result_hash"] == run["result_hash"]
    assert "amount_text" not in json.dumps(detail.json()["input_snapshot"])
    assert "100.50" not in json.dumps(detail.json()["input_snapshot"])

    persisted = admin_engine.connect()
    try:
        row = persisted.execute(
            text(
                "SELECT input_snapshot::text AS input_snapshot, "
                "result_snapshot::text AS result_snapshot, input_hash, result_hash "
                "FROM direct_cash_forecast_runs WHERE id = :run_id"
            ),
            {"run_id": UUID(run["id"])},
        ).one()
        audit_metadata = persisted.scalar(
            text(
                "SELECT metadata::text FROM audit_events "
                "WHERE event_type = 'direct_cash_forecast.run_created' "
                "AND target_id = :run_id"
            ),
            {"run_id": UUID(run["id"])},
        )
    finally:
        persisted.close()
    assert "amount_text" not in row.input_snapshot
    assert "100.50" not in row.input_snapshot
    assert '"100.5"' in row.result_snapshot
    assert row.input_hash == run["input_hash"]
    assert row.result_hash == run["result_hash"]
    assert audit_metadata is not None
    assert "100.50" not in audit_metadata
    assert "0.005" not in caplog.text

    altered = {
        **body,
        "opening_cash": {"source_row_number": 2, "amount_text": "100.51"},
    }
    altered_replay = client.post(
        f"{_run_url(tenant['id'], intake['id'])}/{run['id']}/verify",
        json=altered,
        headers=headers,
    )
    assert altered_replay.status_code == 200, altered_replay.text
    assert altered_replay.json()["passed"] is False
    assert "persisted_input_hash_mismatch" in altered_replay.json()["reason_codes"]
    assert "100.51" not in json.dumps(altered_replay.json())


def test_direct_cash_forecast_api_abstains_for_missing_unknown_or_stale_inputs(client):
    owner = register_user(client, f"m16-incomplete-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M16 Incomplete Tenant",
        company_name="M16 Incomplete Company",
        key=f"m16-incomplete-tenant-{uuid4().hex}",
    )
    intake = _create_cash_intake(client, tenant["id"], headers)
    body = _complete_request(intake, datetime.now(UTC).date())

    missing_opening = {
        **body,
        "opening_cash": {"source_row_number": 2, "amount_text": None},
    }
    missing_response = client.post(
        _run_url(tenant["id"], intake["id"]), json=missing_opening, headers=headers
    )
    assert missing_response.status_code == 201, missing_response.text
    missing_run = missing_response.json()["run"]
    assert missing_run["calculation_state"] == "incomplete"
    assert "opening_cash_missing_or_invalid" in missing_run["reason_codes"]
    assert all(
        week["closing_cash_known_item_rollforward"] is None
        for week in missing_run["result_snapshot"]["weeks"]
    )

    unknown_flow = {
        **body,
        "movements": [
            {
                **body["movements"][0],  # type: ignore[index]
                "cash_flow_type": "unknown",
            },
            *body["movements"][1:],  # type: ignore[index]
        ],
    }
    unknown_response = client.post(
        _run_url(tenant["id"], intake["id"]), json=unknown_flow, headers=headers
    )
    assert unknown_response.status_code == 201, unknown_response.text
    assert unknown_response.json()["run"]["calculation_state"] == "incomplete"
    assert "cash_flow_type_unknown" in unknown_response.json()["run"]["reason_codes"]

    movement_items = body["movements"]
    conflicting_rows = {
        **body,
        "movements": [
            movement_items[0],  # type: ignore[index]
            {
                **movement_items[1],  # type: ignore[index]
                "source_row_number": movement_items[0]["source_row_number"],  # type: ignore[index]
                "amount_text": "1",
            },
            *movement_items[2:],  # type: ignore[index]
        ],
    }
    conflicting_response = client.post(
        _run_url(tenant["id"], intake["id"]), json=conflicting_rows, headers=headers
    )
    assert conflicting_response.status_code == 201, conflicting_response.text
    conflicting_run = conflicting_response.json()["run"]
    assert conflicting_run["calculation_state"] == "incomplete"
    assert "conflicting_source_row_reference" in conflicting_run["reason_codes"]
    assert {
        conflicting_run["input_snapshot"]["movements"][index]["amount_commitment_status"]
        for index in (0, 1)
    } == {"conflicting"}

    stale_intake = _create_cash_intake(
        client,
        tenant["id"],
        headers,
        source_as_of=datetime.now(UTC) - timedelta(hours=72),
        expected_freshness="stale",
    )
    assert stale_intake["freshness_status"] == "stale"
    stale_body = _complete_request(stale_intake, datetime.now(UTC).date())
    stale_response = client.post(
        _run_url(tenant["id"], stale_intake["id"]), json=stale_body, headers=headers
    )
    assert stale_response.status_code == 201, stale_response.text
    stale_run = stale_response.json()["run"]
    assert stale_run["calculation_state"] == "incomplete"
    assert "source_not_fresh_at_calculation" in stale_run["reason_codes"]
    assert all(
        week["closing_cash_known_item_rollforward"] is None
        for week in stale_run["result_snapshot"]["weeks"]
    )


def test_direct_cash_forecast_default_disabled_and_finance_authorization(client, settings):
    owner = register_user(client, f"m16-auth-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M16 Authorization Tenant",
        company_name="M16 Authorization Company",
        key=f"m16-auth-tenant-{uuid4().hex}",
    )
    intake = _create_cash_intake(client, tenant["id"], owner_headers)
    body = _complete_request(intake, datetime.now(UTC).date())
    created = client.post(_run_url(tenant["id"], intake["id"]), json=body, headers=owner_headers)
    assert created.status_code == 201, created.text
    run_id = created.json()["run"]["id"]

    disabled_settings = Settings(
        _env_file=None,
        app_secret_key=settings.app_secret_key.get_secret_value(),
        app_env="test",
        database_url=settings.database_url,
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
        direct_cash_forecasts_enabled=False,
    )
    with TestClient(
        create_app(disabled_settings), raise_server_exceptions=False
    ) as disabled_client:
        responses = [
            disabled_client.get(f"{_base(tenant['id'])}/contract", headers=owner_headers),
            disabled_client.post(
                _run_url(tenant["id"], intake["id"]), json=body, headers=owner_headers
            ),
            disabled_client.get(
                f"{_run_url(tenant['id'], intake['id'])}/{run_id}", headers=owner_headers
            ),
            disabled_client.post(
                f"{_run_url(tenant['id'], intake['id'])}/{run_id}/verify",
                json=body,
                headers=owner_headers,
            ),
        ]
    assert all(response.status_code == 503 for response in responses)
    assert all(
        response.json()["error"]["code"] == "direct_cash_forecasts_disabled"
        for response in responses
    )

    member_email = f"m16-member-{uuid4().hex}@example.com"
    member = register_user(client, member_email)
    create_member(client, owner, tenant["id"], member_email)
    member_headers = auth_headers(member)
    assert client.get(f"{_base(tenant['id'])}/contract", headers=member_headers).status_code == 403
    assert (
        client.get(
            f"{_run_url(tenant['id'], intake['id'])}/{run_id}", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"{_run_url(tenant['id'], intake['id'])}/{run_id}/verify",
            json=body,
            headers=member_headers,
        ).status_code
        == 403
    )
    denied_create = client.post(
        _run_url(tenant["id"], intake["id"]), json=body, headers=member_headers
    )
    assert denied_create.status_code == 403

    other_owner = register_user(client, f"m16-other-owner-{uuid4().hex}@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="M16 Other Tenant",
        company_name="M16 Other Company",
        key=f"m16-other-tenant-{uuid4().hex}",
    )
    hidden = client.get(
        f"{_run_url(other_tenant['id'], intake['id'])}/{run_id}",
        headers=auth_headers(other_owner),
    )
    assert hidden.status_code == 404


def test_direct_cash_forecast_run_table_enforces_rls_grants_and_immutability(
    client,
    runtime_engine,
    migration_engine,
    admin_engine,
):
    owner = register_user(client, f"m16-rls-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M16 RLS Tenant",
        company_name="M16 RLS Company",
        key=f"m16-rls-tenant-{uuid4().hex}",
    )
    intake = _create_cash_intake(client, tenant["id"], headers)
    body = _complete_request(intake, datetime.now(UTC).date())
    created = client.post(_run_url(tenant["id"], intake["id"]), json=body, headers=headers)
    assert created.status_code == 201, created.text
    run = created.json()["run"]
    tenant_id = tenant["id"]
    company_id = tenant["company"]["id"]

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = 'direct_cash_forecast_runs'"
            )
        ).one()
        policies = connection.execute(
            text("SELECT cmd FROM pg_policies WHERE tablename = 'direct_cash_forecast_runs'")
        ).all()
        grants = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'direct_cash_forecast_runs', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'direct_cash_forecast_runs', 'INSERT') "
                "AS can_insert, "
                "has_table_privilege('bf_runtime', 'direct_cash_forecast_runs', 'UPDATE') "
                "AS can_update, "
                "has_table_privilege('bf_runtime', 'direct_cash_forecast_runs', 'DELETE') "
                "AS can_delete, "
                "has_table_privilege('bf_runtime', 'direct_cash_forecast_runs', 'TRUNCATE') "
                "AS can_truncate"
            )
        ).one()
    assert security.relrowsecurity and security.relforcerowsecurity
    assert security.owner == "bf_migrator"
    assert [item.cmd for item in policies] == ["ALL"]
    assert grants.can_select and grants.can_insert
    assert not grants.can_update and not grants.can_delete and not grants.can_truncate

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM direct_cash_forecast_runs")) == 0
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        assert session.scalar(text("SELECT count(*) FROM direct_cash_forecast_runs")) == 1
    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(uuid4())},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(uuid4())},
        )
        assert session.scalar(text("SELECT count(*) FROM direct_cash_forecast_runs")) == 0
        with pytest.raises(DBAPIError):
            session.execute(
                text(
                    "UPDATE direct_cash_forecast_runs SET state = 'incomplete' WHERE id = :run_id"
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
            text("DELETE FROM direct_cash_forecast_runs WHERE id = :run_id"),
            {"run_id": UUID(run["id"])},
        )
