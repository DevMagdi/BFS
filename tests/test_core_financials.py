from __future__ import annotations

import json
from hashlib import sha256
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.core_financials.contracts import BANK_FIELDS, LEDGER_FIELDS
from businessflow.core_financials.models import (
    CoreFinancialImport,
    CoreFinancialMapping,
    CoreFinancialPolicy,
    CoreFinancialRecord,
    CoreFinancialRun,
    CoreFinancialRunEvidence,
)
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/core-financials"


def _create_revenue_policy(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
) -> dict[str, object]:
    template_response = client.get(f"{_base(tenant_id)}/template", headers=headers)
    assert template_response.status_code == 200, template_response.text
    policy = template_response.json()["illustrative_policy"]
    policy["policy_key"] = "illustrative.revenue-only"
    policy["category_coverage"] = {
        category: "complete" if category in {"revenue", "revenue_reduction"} else "unknown"
        for category in policy["category_coverage"]
    }
    response = client.post(f"{_base(tenant_id)}/policies", json=policy, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _create_mapping(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    source_key: str = "ledger-demo",
) -> dict[str, object]:
    csv_headers = (
        "record_id",
        "transaction_date",
        "account_code",
        "debit",
        "credit",
        "currency",
        "event_type",
        "reversal_of",
    )
    response = client.post(
        f"{_base(tenant_id)}/mappings",
        json={
            "source_key": source_key,
            "source_type": "general_ledger",
            "field_mapping": dict(zip(LEDGER_FIELDS, csv_headers, strict=True)),
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _import_revenue_ledger(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    policy: dict[str, object],
    mapping: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    csv_headers = (
        "record_id",
        "transaction_date",
        "account_code",
        "debit",
        "credit",
        "currency",
        "event_type",
        "reversal_of",
    )
    csv_rows = [
        "sale-001,2026-11-10,4000,0,1000,USD,normal,",
        "refund-001,2026-11-18,4090,100,0,USD,refund,",
    ]
    content = (",".join(csv_headers) + "\n" + "\n".join(csv_rows) + "\n").encode("utf-8")
    metadata = {
        "source_key": "ledger-demo",
        "source_type": "general_ledger",
        "mapping_id": mapping["id"],
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    idempotency_key = f"revenue-flow-{uuid4().hex}"
    response = client.post(
        f"{_base(tenant_id)}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("illustrative-ledger.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["quality_status"] == "passed"
    assert imported["accepted_rows"] == 2
    assert imported["rejected_rows"] == 0
    assert imported["duplicate_rows"] == 0

    replay = client.post(
        f"{_base(tenant_id)}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("illustrative-ledger.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": idempotency_key},
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == imported["id"]
    assert replay.json()["replayed"] is True
    return imported, content


def test_revenue_only_workflow_imports_calculates_reconciles_traces_and_replays(client):
    owner = register_user(client, f"core-financials-flow-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials Revenue Test",
        company_name="Synthetic Revenue Test Company",
        key=f"core-financials-flow-{uuid4().hex}",
    )

    policy = _create_revenue_policy(client, tenant["id"], headers)
    mapping = _create_mapping(client, tenant["id"], headers)
    imported, _content = _import_revenue_ledger(client, tenant["id"], headers, policy, mapping)

    record_page = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}/records?limit=100&offset=0",
        headers=headers,
    )
    assert record_page.status_code == 200, record_page.text
    assert record_page.json()["total"] == 2
    assert [item["row_status"] for item in record_page.json()["items"]] == [
        "accepted",
        "accepted",
    ]
    assert {item["category"] for item in record_page.json()["items"]} == {
        "revenue",
        "revenue_reduction",
    }

    run_request = {
        "policy_id": policy["id"],
        "measure": "revenue",
        "ledger_import_id": imported["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "currency_code": "USD",
        "controls": [
            {
                "metric": "revenue",
                "expected_value": "900",
                "currency_code": "USD",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "illustrative-close-control",
                    "source_hash": sha256(b"separate synthetic control export").hexdigest(),
                    "record_locator": "control.csv:row:2/revenue_total",
                },
            }
        ],
    }
    created = client.post(f"{_base(tenant['id'])}/runs", json=run_request, headers=headers)
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["authority"] == "illustrative_not_authoritative"
    assert run["reporting_framework"] == "management_reporting"
    assert run["state"] == "calculated"
    assert len(run["metrics"]) == 1  # The API increment deliberately exposes Revenue only.
    revenue = run["metrics"][0]
    assert revenue["metric"] == "revenue"
    assert revenue["status"] == "calculated"
    assert revenue["exact_value"] == "900"
    assert revenue["reported_value"] == "900"
    assert revenue["category_totals"] == {"revenue": "1000", "revenue_reduction": "100"}
    assert revenue["reconciliation"]["state"] == "passed"
    assert revenue["reconciliation"]["variance"] == "0"
    assert revenue["reconciliation"]["source_independence"] == "caller_asserted_not_verified"

    evidence = client.get(
        f"{_base(tenant['id'])}/runs/{run['id']}/evidence?metric=revenue&limit=100&offset=0",
        headers=headers,
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 2
    assert {item["formula_coefficient"] for item in evidence.json()["items"]} == {-1, 1}
    assert {item["contribution_amount"] for item in evidence.json()["items"]} == {
        "1000",
        "-100",
    }
    assert all(item["source_import_id"] == imported["id"] for item in evidence.json()["items"])
    assert all(
        item["source_file_hash"] == imported["file_hash"] for item in evidence.json()["items"]
    )
    assert all(len(item["source_row_hash"]) == 64 for item in evidence.json()["items"])

    replay = client.post(f"{_base(tenant['id'])}/runs/{run['id']}/verify", headers=headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["passed"] is True
    assert replay.json()["recorded_input_hash"] == run["input_hash"]
    assert replay.json()["recorded_result_hash"] == run["result_hash"]

    detail = client.get(f"{_base(tenant['id'])}/runs/{run['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["trace_hash"] == run["trace_hash"]
    import_history = client.get(f"{_base(tenant['id'])}/imports?limit=10&offset=0", headers=headers)
    run_history = client.get(f"{_base(tenant['id'])}/runs?limit=10&offset=0", headers=headers)
    assert import_history.status_code == 200, import_history.text
    assert run_history.status_code == 200, run_history.text
    assert import_history.json()["total"] == 1
    assert run_history.json()["total"] == 1


def test_core_financials_authorization_and_company_scope_are_enforced(client):
    owner = register_user(client, f"core-financials-auth-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials Authorization Test",
        company_name="Synthetic Authorization Company",
        key=f"core-financials-auth-{uuid4().hex}",
    )
    unsupported_bank_mapping = client.post(
        f"{_base(tenant['id'])}/mappings",
        json={
            "source_key": "bank-demo",
            "source_type": "bank_activity",
            "field_mapping": dict(zip(BANK_FIELDS, BANK_FIELDS, strict=True)),
            "ignored_columns": [],
        },
        headers=owner_headers,
    )
    assert unsupported_bank_mapping.status_code == 422
    assert unsupported_bank_mapping.json()["error"]["code"] == "unsupported_source_type"

    policy = _create_revenue_policy(client, tenant["id"], owner_headers)
    mapping = _create_mapping(client, tenant["id"], owner_headers)
    bank_metadata = {
        "source_key": "bank-demo",
        "source_type": "bank_activity",
        "mapping_id": str(uuid4()),
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    rejected_bank_import = client.post(
        f"{_base(tenant['id'])}/imports",
        data={"metadata_json": json.dumps(bank_metadata)},
        files={"file": ("bank.csv", (",".join(BANK_FIELDS) + "\n").encode(), "text/csv")},
        headers={**owner_headers, "Idempotency-Key": f"bank-flow-{uuid4().hex}"},
    )
    assert rejected_bank_import.status_code == 422
    assert rejected_bank_import.json()["error"]["code"] == "unsupported_source_type"

    imported, _content = _import_revenue_ledger(
        client, tenant["id"], owner_headers, policy, mapping
    )

    member_email = f"core-financials-member-{uuid4().hex}@example.com"
    member = register_user(client, member_email)
    create_member(client, owner, tenant["id"], member_email)
    denied = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}",
        headers=auth_headers(member),
    )
    assert denied.status_code == 403

    other_owner = register_user(client, f"core-financials-other-owner-{uuid4().hex}@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other Core Financials Tenant",
        company_name="Other Synthetic Company",
        key=f"core-financials-other-{uuid4().hex}",
    )
    hidden = client.get(
        f"{_base(other_tenant['id'])}/imports/{imported['id']}",
        headers=auth_headers(other_owner),
    )
    assert hidden.status_code == 404


def test_core_financial_tables_enforce_company_rls_runtime_grants_and_immutability(
    client, runtime_engine, admin_engine
):
    owner = register_user(client, f"core-financials-rls-owner-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials RLS Test",
        company_name="Synthetic RLS Company",
        key=f"core-financials-rls-{uuid4().hex}",
    )
    policy = _create_revenue_policy(client, tenant["id"], headers)
    mapping = _create_mapping(client, tenant["id"], headers)
    imported, _content = _import_revenue_ledger(client, tenant["id"], headers, policy, mapping)
    created_run = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "measure": "revenue",
            "ledger_import_id": imported["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=headers,
    )
    assert created_run.status_code == 201, created_run.text
    run = created_run.json()

    models = (
        CoreFinancialPolicy,
        CoreFinancialMapping,
        CoreFinancialImport,
        CoreFinancialRecord,
        CoreFinancialRun,
        CoreFinancialRunEvidence,
    )
    tables = tuple(model.__tablename__ for model in models)
    table_params = {"table_names": list(tables)}
    with admin_engine.connect() as connection:
        bank_import_id = connection.execute(
            text("SELECT bank_import_id FROM core_financial_runs WHERE id = :run_id"),
            {"run_id": run["id"]},
        ).scalar_one()
        assert bank_import_id is None
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
                "WHERE schemaname = 'public' AND tablename = ANY(:table_names) "
                "ORDER BY tablename"
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
    assert all(row.tgname.startswith("trg_core_financial_") for row in triggers)

    tenant_id = tenant["id"]
    company_id = tenant["company"]["id"]
    expected_counts = (1, 1, 1, 2, 1, 2)
    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        for model in models:
            assert session.scalar(select(func.count()).select_from(model)) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        for model, count in zip(models, expected_counts, strict=True):
            assert session.scalar(select(func.count()).select_from(model)) == count

        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(uuid4())},
        )
        for model in models:
            assert session.scalar(select(func.count()).select_from(model)) == 0

        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(uuid4())},
        )
        for model in models:
            assert session.scalar(select(func.count()).select_from(model)) == 0

    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE core_financial_runs SET state = 'incomplete' WHERE id = :run_id"),
            {"run_id": run["id"]},
        )
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": company_id},
        )
        session.execute(
            text("UPDATE core_financial_policies SET version = 2 WHERE id = :policy_id"),
            {"policy_id": policy["id"]},
        )
