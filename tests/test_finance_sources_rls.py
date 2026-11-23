from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_tenant, register_user

FINANCE_TABLES = (
    "finance_source_mappings",
    "finance_source_intakes",
    "finance_source_row_evidence",
)
CONTRACT_KEY = "businessflow.finance.source-observations"
CONTRACT_VERSION = "1.0.0"
COUNT_QUERIES = {
    "finance_source_mappings": text("SELECT count(*) FROM finance_source_mappings"),
    "finance_source_intakes": text("SELECT count(*) FROM finance_source_intakes"),
    "finance_source_row_evidence": text("SELECT count(*) FROM finance_source_row_evidence"),
}
FIELD_MAPPING = {
    "source_record_id": "record",
    "reconciliation_key": "bucket",
    "period_start": "from",
    "period_end": "to",
    "amount": "amount",
    "currency": "currency",
}


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/finance-sources"


def _create_intake(client, tenant_id: str, headers: dict[str, str]) -> dict[str, object]:
    metadata = {
        "source_key": "rls-fixture-source",
        "source_as_of_at": datetime.now(UTC).isoformat(),
        "freshness_max_age_hours": 24,
        "field_mapping": FIELD_MAPPING,
        "ignored_columns": [],
        "controls": [
            {
                "control_id": "rls-control-001",
                "reconciliation_key": "opaque-rls-group",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency": "USD",
                "expected_amount": "10",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "rls-independent-control",
                    "source_hash": "f" * 64,
                    "record_locator": "control:row:1",
                },
            }
        ],
    }
    response = client.post(
        f"{_base(tenant_id)}/intakes",
        data={"metadata_json": json.dumps(metadata)},
        files={
            "file": (
                "rls-finance-export.csv",
                b"record,bucket,from,to,amount,currency\n"
                b"record-001,opaque-rls-group,2026-11-01,2026-11-30,10,USD\n",
                "text/csv",
            )
        },
        headers={**headers, "Idempotency-Key": f"m14-rls-{uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_finance_source_tables_enforce_forced_rls_least_privilege_and_immutability(
    client,
    runtime_engine,
    migration_engine,
    admin_engine,
):
    owner_a = register_user(client, "m14-rls-a@example.com")
    headers_a = auth_headers(owner_a)
    tenant_a = create_tenant(
        client,
        owner_a,
        name="M14 RLS Tenant A",
        company_name="M14 RLS Company A",
        key="m14-rls-tenant-a-001",
    )
    intake = _create_intake(client, tenant_a["id"], headers_a)
    intake_id = intake["id"]
    owner_a_id = client.get("/api/v1/auth/me", headers=headers_a).json()["id"]

    owner_b = register_user(client, "m14-rls-b@example.com")
    tenant_b = create_tenant(
        client,
        owner_b,
        name="M14 RLS Tenant B",
        company_name="M14 RLS Company B",
        key="m14-rls-tenant-b-001",
    )

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(:table_names) ORDER BY c.relname"
            ),
            {"table_names": list(FINANCE_TABLES)},
        ).all()
        policies = connection.execute(
            text("SELECT tablename, cmd FROM pg_policies WHERE tablename = ANY(:table_names)"),
            {"table_names": list(FINANCE_TABLES)},
        ).all()
        privileges = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', table_name, 'TRUNCATE') AS can_truncate "
                "FROM unnest(CAST(:table_names AS text[])) AS finance_table(table_name)"
            ),
            {"table_names": list(FINANCE_TABLES)},
        ).all()
        contract_privileges = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'finance_source_contracts', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'finance_source_contracts', 'INSERT') "
                "AS can_insert, "
                "has_table_privilege('bf_runtime', 'finance_source_contracts', 'UPDATE') "
                "AS can_update, "
                "has_table_privilege('bf_runtime', 'finance_source_contracts', 'DELETE') "
                "AS can_delete, "
                "has_table_privilege('bf_runtime', 'finance_source_contracts', 'TRUNCATE') "
                "AS can_truncate"
            )
        ).one()
    assert len(security) == len(FINANCE_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert len(policies) == len(FINANCE_TABLES)
    assert all(row.cmd == "ALL" for row in policies)
    grants = {row.table_name: row for row in privileges}
    for table_name in FINANCE_TABLES:
        assert grants[table_name].can_select and grants[table_name].can_insert
        assert not grants[table_name].can_update
        assert not grants[table_name].can_delete
        assert not grants[table_name].can_truncate
    assert contract_privileges.can_select
    assert not contract_privileges.can_insert
    assert not contract_privileges.can_update
    assert not contract_privileges.can_delete
    assert not contract_privileges.can_truncate

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        for table_name in FINANCE_TABLES:
            assert session.scalar(COUNT_QUERIES[table_name]) == 0
        assert session.scalar(text("SELECT count(*) FROM finance_source_contracts")) == 1

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        for table_name in FINANCE_TABLES:
            assert session.scalar(COUNT_QUERIES[table_name]) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM finance_source_mappings")) == 1
        assert session.scalar(text("SELECT count(*) FROM finance_source_intakes")) == 1
        assert session.scalar(text("SELECT count(*) FROM finance_source_row_evidence")) == 1

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_b["company"]["id"]},
        )
        for table_name in FINANCE_TABLES:
            assert session.scalar(COUNT_QUERIES[table_name]) == 0

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
                "INSERT INTO finance_source_mappings "
                "(id, tenant_id, company_id, source_key, mapping_version, contract_key, "
                "contract_version, contract_hash, mapping_hash, field_mapping, ignored_columns, "
                "created_by_user_id) VALUES (:id, :tenant_id, :company_id, :source_key, 1, "
                ":contract_key, :contract_version, :contract_hash, :mapping_hash, "
                "CAST(:field_mapping AS jsonb), CAST(:ignored_columns AS jsonb), :user_id)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_a["id"],
                "company_id": tenant_a["company"]["id"],
                "source_key": "wrong-tenant-source",
                "contract_key": CONTRACT_KEY,
                "contract_version": CONTRACT_VERSION,
                "contract_hash": intake["contract_hash"],
                "mapping_hash": "a" * 64,
                "field_mapping": json.dumps(FIELD_MAPPING),
                "ignored_columns": "[]",
                "user_id": owner_a_id,
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
            text("UPDATE finance_source_intakes SET quality_status = 'failed' WHERE id = :id"),
            {"id": intake_id},
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
            text("DELETE FROM finance_source_row_evidence WHERE intake_id = :id"),
            {"id": intake_id},
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
            text("DELETE FROM finance_source_row_evidence WHERE intake_id = :id"),
            {"id": intake_id},
        )

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text("UPDATE finance_source_contracts SET definition_hash = :hash"),
            {"hash": "0" * 64},
        )

    hidden = client.get(
        f"{_base(tenant_b['id'])}/intakes/{intake_id}", headers=auth_headers(owner_b)
    )
    assert hidden.status_code == 404
