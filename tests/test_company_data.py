from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_member, create_tenant, register_user

COMPANY_DATA_TABLES = ("company_data_sources", "company_data_ingestions")


def _company_data_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/company-data"


def _apply_employee_import(
    client, tenant_id: str, headers: dict[str, str], *, employee_code: str = "M12-EMP-001"
) -> str:
    base = f"/api/v1/tenants/{tenant_id}/imports"
    content = (
        "employee_code,full_name,job_title,standard_weekly_capacity_hours,employment_status\n"
        f"{employee_code},Company Data Test Employee,Analyst,40.00,active\n"
    ).encode()
    staged = client.post(
        base,
        files={"file": ("employees.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"m12-employee-import-{uuid4().hex}"},
    )
    assert staged.status_code == 201, staged.text
    import_id = staged.json()["import"]["id"]
    mapping = client.put(
        f"{base}/{import_id}/mapping",
        json={
            "dataset_type": "employees",
            "worksheet_name": "CSV",
            "field_mapping": {
                "employee_code": "employee_code",
                "full_name": "full_name",
                "job_title": "job_title",
                "standard_weekly_capacity_hours": "standard_weekly_capacity_hours",
                "employment_status": "employment_status",
            },
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert mapping.status_code == 200, mapping.text
    validated = client.post(f"{base}/{import_id}/validate", headers=headers)
    assert validated.status_code == 200, validated.text
    report = validated.json()
    assert report["import"]["counts"]["invalid_rows"] == 0
    assert report["import"]["counts"]["warning_rows"] == 0
    confirmed = client.post(
        f"{base}/{import_id}/confirm",
        json={
            "validation_hash": report["import"]["validation_hash"],
            "allow_partial": False,
            "acknowledge_warnings": False,
        },
        headers=headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    applied = client.post(f"{base}/{import_id}/apply", headers=headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["import"]["status"] == "applied"
    return import_id


def test_company_data_registry_binds_m4_quality_freshness_lineage_and_tenant_scope(
    client, runtime_engine, migration_engine, admin_engine
):
    owner = register_user(client, "m12-company-data-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M12 Company Data Tenant",
        company_name="M12 Company Data Company",
        key="m12-company-data-tenant-001",
    )
    tenant_id = tenant["id"]
    base = _company_data_path(tenant_id)
    current_user = client.get("/api/v1/auth/me", headers=headers)
    assert current_user.status_code == 200, current_user.text

    contracts = client.get(f"{base}/contracts", headers=headers)
    assert contracts.status_code == 200, contracts.text
    assert len(contracts.json()["items"]) == 8
    employee_contract = next(
        item for item in contracts.json()["items"] if item["dataset_type"] == "employees"
    )
    assert employee_contract["contract_key"] == "businessflow.m4.employees"
    assert employee_contract["definition"]["required"] == [
        "employee_code",
        "full_name",
        "job_title",
        "standard_weekly_capacity_hours",
    ]

    member = register_user(client, "m12-company-data-member@example.com")
    create_member(client, owner, tenant_id, "m12-company-data-member@example.com")
    member_source = client.post(
        f"{base}/sources",
        json={
            "source_key": "hr-export",
            "display_name": "HR file export",
            "dataset_type": "employees",
            "contract_version": employee_contract["version"],
            "freshness_max_age_hours": 24,
        },
        headers=auth_headers(member),
    )
    assert member_source.status_code == 403

    source_response = client.post(
        f"{base}/sources",
        json={
            "source_key": "hr-export",
            "display_name": "HR file export",
            "dataset_type": "employees",
            "contract_version": employee_contract["version"],
            "freshness_max_age_hours": 24,
        },
        headers=headers,
    )
    assert source_response.status_code == 201, source_response.text
    source = source_response.json()
    assert source["owner_user_id"] == current_user.json()["id"]
    assert source["contract_key"] == employee_contract["contract_key"]
    member_headers = auth_headers(member)
    member_sources = client.get(f"{base}/sources", headers=member_headers)
    assert member_sources.status_code == 403
    assert client.get(f"{base}/coverage", headers=member_headers).status_code == 403

    import_id = _apply_employee_import(client, tenant_id, headers)
    source_as_of_at = datetime.now(UTC).isoformat()
    ingestion_response = client.post(
        f"{base}/sources/{source['id']}/ingestions",
        json={"import_id": import_id, "source_as_of_at": source_as_of_at},
        headers=headers,
    )
    assert ingestion_response.status_code == 201, ingestion_response.text
    ingestion = ingestion_response.json()
    assert ingestion["ingestion"]["ingestion_status"] == "succeeded"
    assert ingestion["ingestion"]["quality_status"] == "passed"
    assert ingestion["ingestion"]["freshness_status"] == "fresh"
    assert ingestion["ingestion"]["current_freshness_status"] == "fresh"
    assert ingestion["ingestion"]["contract_hash"] == employee_contract["definition_hash"]
    assert ingestion["ingestion"]["file_hash"]
    assert ingestion["ingestion"]["validation_hash"]
    assert ingestion["snapshot"]["m4_counts"]["total_rows"] == 1
    assert (
        ingestion["snapshot"]["contract_snapshot"]["definition"] == employee_contract["definition"]
    )
    assert ingestion["replayed"] is False

    replay = client.post(
        f"{base}/sources/{source['id']}/ingestions",
        json={"import_id": import_id, "source_as_of_at": source_as_of_at},
        headers=headers,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["ingestion"]["id"] == ingestion["ingestion"]["id"]
    assert replay.json()["replayed"] is True

    conflicting_replay = client.post(
        f"{base}/sources/{source['id']}/ingestions",
        json={
            "import_id": import_id,
            "source_as_of_at": (datetime.now(UTC) - timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    assert conflicting_replay.status_code == 409
    assert conflicting_replay.json()["error"]["code"] == "company_data_import_already_bound"

    ingestion_id = ingestion["ingestion"]["id"]
    lineage = client.get(
        f"{base}/sources/{source['id']}/ingestions/{ingestion_id}/lineage",
        headers=headers,
    )
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["total"] == 1
    lineage_row = lineage.json()["items"][0]
    assert lineage_row["import_id"] == import_id
    assert lineage_row["row_number"] == 2
    assert lineage_row["row_hash"]
    assert lineage_row["canonical_type"] == "employee"
    assert lineage_row["canonical_id"]
    assert lineage_row["canonical_fingerprint"]
    assert client.get(f"{base}/sources/{source['id']}", headers=member_headers).status_code == 403
    assert (
        client.get(f"{base}/sources/{source['id']}/ingestions", headers=member_headers).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/sources/{source['id']}/ingestions/{ingestion_id}",
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/sources/{source['id']}/ingestions/{ingestion_id}/lineage",
            headers=member_headers,
        ).status_code
        == 403
    )

    source_list = client.get(f"{base}/sources", headers=headers)
    assert source_list.status_code == 200, source_list.text
    source_summary = source_list.json()["items"][0]
    assert source_summary["latest_ingestion_id"] == ingestion_id
    assert source_summary["last_successful_ingestion_id"] == ingestion_id
    assert source_summary["latest_quality_status"] == "passed"
    assert source_summary["latest_freshness_status"] == "fresh"
    assert source_summary["latest_recorded_freshness_status"] == "fresh"

    owner_b = register_user(client, "m12-company-data-other-owner@example.com")
    tenant_b = create_tenant(
        client,
        owner_b,
        name="M12 Other Tenant",
        company_name="M12 Other Company",
        key="m12-other-tenant-001",
    )
    cross_tenant = client.get(
        f"{_company_data_path(tenant_b['id'])}/sources/{source['id']}",
        headers=auth_headers(owner_b),
    )
    assert cross_tenant.status_code == 404
    unauthorized_tenant = client.get(f"{base}/sources", headers=auth_headers(owner_b))
    assert unauthorized_tenant.status_code == 404

    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(:table_names) ORDER BY c.relname"
            ),
            {"table_names": list(COMPANY_DATA_TABLES)},
        ).all()
        policies = connection.execute(
            text("SELECT tablename, cmd FROM pg_policies WHERE tablename = ANY(:table_names)"),
            {"table_names": list(COMPANY_DATA_TABLES)},
        ).all()
        privileges = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', table_name, 'TRUNCATE') AS can_truncate "
                "FROM unnest(CAST(:table_names AS text[])) AS grant_table(table_name)"
            ),
            {"table_names": list(COMPANY_DATA_TABLES)},
        ).all()
        contract_privileges = connection.execute(
            text(
                "SELECT has_table_privilege('bf_runtime', 'company_data_contracts', 'SELECT') "
                "AS can_select, "
                "has_table_privilege('bf_runtime', 'company_data_contracts', 'INSERT') "
                "AS can_insert, "
                "has_table_privilege('bf_runtime', 'company_data_contracts', 'UPDATE') "
                "AS can_update"
            )
        ).one()
    assert len(security) == len(COMPANY_DATA_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert len(policies) == len(COMPANY_DATA_TABLES)
    assert all(row.cmd == "ALL" for row in policies)
    grants = {row.table_name: row for row in privileges}
    for table in COMPANY_DATA_TABLES:
        assert grants[table].can_select and grants[table].can_insert
        assert not grants[table].can_update
        assert not grants[table].can_delete
        assert not grants[table].can_truncate
    assert contract_privileges.can_select
    assert not contract_privileges.can_insert and not contract_privileges.can_update

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM company_data_sources")) == 0
        assert session.scalar(text("SELECT count(*) FROM company_data_ingestions")) == 0
        assert session.scalar(text("SELECT count(*) FROM company_data_contracts")) == 8

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM company_data_sources")) == 1
        assert session.scalar(text("SELECT count(*) FROM company_data_ingestions")) == 1

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_b["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_b["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM company_data_sources")) == 0
        assert session.scalar(text("SELECT count(*) FROM company_data_ingestions")) == 0

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text("UPDATE company_data_ingestions SET quality_status = 'failed' WHERE id = :id"),
            {"id": ingestion_id},
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text(
                "INSERT INTO company_data_sources "
                "(id, tenant_id, company_id, source_key, display_name, dataset_type, "
                "contract_key, contract_version, owner_user_id, registered_by_user_id, "
                "freshness_max_age_hours) VALUES "
                "(:id, :tenant_id, :company_id, 'mismatch', 'Wrong dataset contract', 'skills', "
                ":contract_key, '1.0.0', :owner_id, :owner_id, 24)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant_id,
                "company_id": tenant["company"]["id"],
                "contract_key": employee_contract["contract_key"],
                "owner_id": current_user.json()["id"],
            },
        )

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        connection.execute(
            text("DELETE FROM company_data_sources WHERE id = :id"),
            {"id": source["id"]},
        )

    with pytest.raises(DBAPIError, match="immutable"), migration_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE company_data_contracts SET definition_hash = :hash "
                "WHERE contract_key = :key AND version = :version"
            ),
            {"hash": "0" * 64, "key": employee_contract["contract_key"], "version": "1.0.0"},
        )


def test_company_data_coverage_counts_each_sources_latest_snapshot_and_checks_company_access(
    client,
):
    owner = register_user(client, "m12-coverage-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M12 Coverage Tenant",
        company_name="M12 Coverage Company",
        key="m12-coverage-tenant-001",
    )
    tenant_id = tenant["id"]
    base = _company_data_path(tenant_id)

    empty_coverage = client.get(f"{base}/coverage", headers=headers)
    assert empty_coverage.status_code == 200, empty_coverage.text
    assert len(empty_coverage.json()["datasets"]) == 6
    assert "not proof that the business has no data" in empty_coverage.json()["note"]
    assert all(item["registered_source_count"] == 0 for item in empty_coverage.json()["datasets"])

    contract = next(
        item
        for item in client.get(f"{base}/contracts", headers=headers).json()["items"]
        if item["dataset_type"] == "employees"
    )
    source = client.post(
        f"{base}/sources",
        json={
            "source_key": "hr-monthly",
            "display_name": "Monthly HR export",
            "dataset_type": "employees",
            "contract_version": contract["version"],
            "freshness_max_age_hours": 24,
        },
        headers=headers,
    )
    assert source.status_code == 201, source.text

    older_import_id = _apply_employee_import(client, tenant_id, headers)
    stale_as_of = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    older_ingestion = client.post(
        f"{base}/sources/{source.json()['id']}/ingestions",
        json={"import_id": older_import_id, "source_as_of_at": stale_as_of},
        headers=headers,
    )
    assert older_ingestion.status_code == 201, older_ingestion.text
    assert older_ingestion.json()["ingestion"]["current_freshness_status"] == "stale"

    newer_import_id = _apply_employee_import(
        client, tenant_id, headers, employee_code="M12-EMP-002"
    )
    newer_ingestion = client.post(
        f"{base}/sources/{source.json()['id']}/ingestions",
        json={"import_id": newer_import_id, "source_as_of_at": datetime.now(UTC).isoformat()},
        headers=headers,
    )
    assert newer_ingestion.status_code == 201, newer_ingestion.text
    assert newer_ingestion.json()["ingestion"]["current_freshness_status"] == "fresh"

    pending_source = client.post(
        f"{base}/sources",
        json={
            "source_key": "hr-next-cycle",
            "display_name": "Next HR cycle",
            "dataset_type": "employees",
            "contract_version": contract["version"],
        },
        headers=headers,
    )
    assert pending_source.status_code == 201, pending_source.text

    coverage = client.get(f"{base}/coverage", headers=headers)
    assert coverage.status_code == 200, coverage.text
    datasets = {item["dataset_type"]: item for item in coverage.json()["datasets"]}
    employee_coverage = datasets["employees"]
    assert employee_coverage["registered_source_count"] == 2
    assert employee_coverage["sources_without_ingestion_count"] == 1
    assert employee_coverage["latest_ingestion_counts"] == {
        "succeeded": 1,
        "succeeded_with_warnings": 0,
        "blocked": 0,
    }
    assert employee_coverage["latest_quality_counts"] == {
        "passed": 1,
        "warning": 0,
        "failed": 0,
    }
    assert employee_coverage["current_freshness_counts"]["fresh"] == 1
    assert employee_coverage["current_freshness_counts"]["stale"] == 0
    assert (
        employee_coverage["latest_ingestion_at"]
        == newer_ingestion.json()["ingestion"]["created_at"]
    )
    assert len(employee_coverage["contract_versions"]) == 1
    assert datasets["skills"]["registered_source_count"] == 0

    member = register_user(client, "m12-coverage-member@example.com")
    create_member(client, owner, tenant_id, "m12-coverage-member@example.com")
    member_coverage = client.get(f"{base}/coverage", headers=auth_headers(member))
    assert member_coverage.status_code == 403

    other_owner = register_user(client, "m12-coverage-other@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="M12 Other Coverage Tenant",
        company_name="M12 Other Coverage Company",
        key="m12-other-coverage-tenant-001",
    )
    other_owner_coverage = client.get(
        f"{_company_data_path(other_tenant['id'])}/coverage",
        headers=auth_headers(other_owner),
    )
    assert other_owner_coverage.status_code == 200, other_owner_coverage.text
    cross_tenant = client.get(
        f"{_company_data_path(other_tenant['id'])}/coverage",
        headers=auth_headers(owner),
    )
    assert cross_tenant.status_code == 404
