from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from tests.helpers import auth_headers, create_member, create_tenant, register_user
from tests.migration_helpers import invoke_downgrade_function


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/company-data/universal"


RETENTION_TABLES = (
    "universal_retention_policy_versions",
    "universal_retention_policy_approvals",
    "universal_retention_policy_revocations",
    "universal_source_lifecycle_events",
    "universal_ingestion_purge_tombstones",
)
RETENTION_COUNT_QUERIES = {
    "universal_retention_policy_versions": text(
        "SELECT count(*) FROM universal_retention_policy_versions"
    ),
    "universal_retention_policy_approvals": text(
        "SELECT count(*) FROM universal_retention_policy_approvals"
    ),
    "universal_retention_policy_revocations": text(
        "SELECT count(*) FROM universal_retention_policy_revocations"
    ),
    "universal_source_lifecycle_events": text(
        "SELECT count(*) FROM universal_source_lifecycle_events"
    ),
    "universal_ingestion_purge_tombstones": text(
        "SELECT count(*) FROM universal_ingestion_purge_tombstones"
    ),
}


def _source(client: TestClient, base: str, headers: dict[str, str], key: str) -> dict[str, object]:
    response = client.post(
        f"{base}/sources",
        json={
            "source_key": key,
            "display_name": "Retention test source",
            "source_type": "csv",
            "authority_classification": "unclassified",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _stage(
    client: TestClient,
    base: str,
    source_id: str,
    headers: dict[str, str],
    key: str,
    content: bytes,
):
    return client.post(
        f"{base}/sources/{source_id}/ingestions",
        files={"file": ("customers.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": key},
    )


def _age_ingestion(admin_engine, ingestion_id: str) -> None:
    """Test-only time travel; production ingestion identity remains immutable."""
    created_at = datetime.now(UTC) - timedelta(days=2)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE universal_ingestions DISABLE TRIGGER trg_universal_ingestions_guard"
        )
        connection.execute(
            text(
                "UPDATE universal_ingestions SET created_at = :created_at, "
                "updated_at = :created_at "
                "WHERE id = :ingestion_id"
            ),
            {"created_at": created_at, "ingestion_id": UUID(ingestion_id)},
        )
        connection.exec_driver_sql(
            "ALTER TABLE universal_ingestions ENABLE TRIGGER trg_universal_ingestions_guard"
        )


def _cleanup_retention_rows(admin_engine, tenant_id: str) -> None:
    tables = (
        "universal_retention_policy_versions",
        "universal_retention_policy_approvals",
        "universal_retention_policy_revocations",
        "universal_source_lifecycle_events",
        "universal_ingestion_purge_tombstones",
    )
    with admin_engine.begin() as connection:
        for table in tables:
            connection.exec_driver_sql(f"ALTER TABLE {table} DISABLE TRIGGER USER")
        connection.execute(
            text("DELETE FROM universal_ingestion_purge_tombstones WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant_id)},
        )
        connection.execute(
            text("DELETE FROM universal_source_lifecycle_events WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant_id)},
        )
        connection.execute(
            text("DELETE FROM universal_retention_policy_revocations WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant_id)},
        )
        connection.execute(
            text("DELETE FROM universal_retention_policy_approvals WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant_id)},
        )
        connection.execute(
            text("DELETE FROM universal_retention_policy_versions WHERE tenant_id = :tenant_id"),
            {"tenant_id": UUID(tenant_id)},
        )
        for table in tables:
            connection.exec_driver_sql(f"ALTER TABLE {table} ENABLE TRIGGER USER")


@pytest.fixture
def retention_row_cleanup(admin_engine):
    tenant_ids: list[str] = []

    def track(tenant_id: str) -> None:
        tenant_ids.append(tenant_id)

    yield track
    for tenant_id in tenant_ids:
        _cleanup_retention_rows(admin_engine, tenant_id)


def _make_owner_and_approver(client: TestClient, tenant_key: str):
    owner = register_user(client, f"{tenant_key}-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name=f"{tenant_key} Tenant",
        company_name=f"{tenant_key} Company",
        key=f"{tenant_key}-tenant-onboarding-key",
    )
    tenant_id = str(tenant["id"])
    approver = register_user(client, f"{tenant_key}-approver@example.com")
    create_member(client, owner, tenant_id, f"{tenant_key}-approver@example.com", role="admin")
    return owner, owner_headers, tenant, approver, auth_headers(approver)


def _configure_and_approve(
    client: TestClient,
    base: str,
    owner_headers: dict[str, str],
    approver_headers: dict[str, str],
    retention_days: int = 1,
) -> dict[str, object]:
    configured = client.post(
        f"{base}/retention/policies",
        json={"retention_days": retention_days},
        headers=owner_headers,
    )
    assert configured.status_code == 201, configured.text
    approval = client.post(
        f"{base}/retention/policies/{configured.json()['policy_version_id']}/approve",
        headers=approver_headers,
    )
    assert approval.status_code == 200, approval.text
    assert approval.json()["active"] is True
    return configured.json()


def test_retention_tables_enforce_company_rls_and_tenant_api_scope(
    client, runtime_engine, admin_engine, retention_row_cleanup
):
    _owner_a, owner_headers, tenant_a, _approver_a, approver_headers = _make_owner_and_approver(
        client, "universal-retention-rls-a"
    )
    tenant_a_id = str(tenant_a["id"])
    retention_row_cleanup(tenant_a_id)
    base_a = _base(tenant_a_id)
    source = _source(client, base_a, owner_headers, "retention.rls-source")
    source_id = str(source["source_id"])
    policy = _configure_and_approve(client, base_a, owner_headers, approver_headers)
    revoked_source = client.post(
        f"{base_a}/sources/{source_id}/revoke",
        json={"reason_code": "owner_request"},
        headers=owner_headers,
    )
    assert revoked_source.status_code == 200, revoked_source.text
    revoked_policy = client.post(
        f"{base_a}/retention/policies/{policy['policy_version_id']}/revoke",
        headers=owner_headers,
    )
    assert revoked_policy.status_code == 200, revoked_policy.text

    owner_b = register_user(client, "universal-retention-rls-b-owner@example.com")
    owner_b_headers = auth_headers(owner_b)
    tenant_b = create_tenant(
        client,
        owner_b,
        name="Universal Retention RLS Tenant B",
        company_name="Universal Retention RLS Company B",
        key="universal-retention-rls-b-tenant",
    )
    tenant_b_id = str(tenant_b["id"])
    retention_row_cleanup(tenant_b_id)
    for protected_path in (
        f"{base_a}/retention/policies",
        f"{base_a}/sources/{source_id}/purge-plan",
    ):
        response = client.get(protected_path, headers=owner_b_headers)
        assert response.status_code == 404, response.text

    table_names = list(RETENTION_TABLES)
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, "
                "r.rolname AS owner FROM pg_class AS c "
                "JOIN pg_roles AS r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(CAST(:table_names AS text[])) ORDER BY c.relname"
            ),
            {"table_names": table_names},
        ).all()
        policies = connection.execute(
            text(
                "SELECT tablename, cmd FROM pg_policies "
                "WHERE tablename = ANY(CAST(:table_names AS text[]))"
            ),
            {"table_names": table_names},
        ).all()
        privileges = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', table_name, 'TRUNCATE') AS can_truncate "
                "FROM unnest(CAST(:table_names AS text[])) AS retention_table(table_name)"
            ),
            {"table_names": table_names},
        ).all()
    assert len(security) == len(RETENTION_TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert len(policies) == len(RETENTION_TABLES)
    assert all(row.cmd == "ALL" for row in policies)
    grants = {row.table_name: row for row in privileges}
    for table_name in RETENTION_TABLES:
        assert grants[table_name].can_select and grants[table_name].can_insert
        assert not grants[table_name].can_update
        assert not grants[table_name].can_delete
        assert not grants[table_name].can_truncate

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert all(
            session.scalar(RETENTION_COUNT_QUERIES[table]) == 0 for table in RETENTION_TABLES
        )

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_a_id},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant_a["company"]["id"]},
        )
        visible = {
            table: session.scalar(RETENTION_COUNT_QUERIES[table]) for table in RETENTION_TABLES
        }
        assert visible == {
            "universal_retention_policy_versions": 1,
            "universal_retention_policy_approvals": 1,
            "universal_retention_policy_revocations": 1,
            "universal_source_lifecycle_events": 1,
            "universal_ingestion_purge_tombstones": 0,
        }

    for tenant_id, company_id in (
        (tenant_a_id, tenant_b["company"]["id"]),
        (tenant_b_id, tenant_b["company"]["id"]),
    ):
        with factory.begin() as session:
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": company_id},
            )
            assert all(
                session.scalar(RETENTION_COUNT_QUERIES[table]) == 0 for table in RETENTION_TABLES
            )


def test_company_scoped_retention_policy_and_source_purge_remove_evidence(
    client, runtime_engine, admin_engine, retention_row_cleanup
):
    owner, owner_headers, tenant, _approver, approver_headers = _make_owner_and_approver(
        client, "universal-retention-purge"
    )
    tenant_id = str(tenant["id"])
    retention_row_cleanup(tenant_id)
    company_id = str(tenant["company"]["id"])
    base = _base(tenant_id)
    source = _source(client, base, owner_headers, "retention.primary")
    source_id = str(source["source_id"])
    key = "universal-retention-idempotency-key-01"
    contents = b"customer_code,name,email\nCUS-RT-1,Private Customer,private@example.test\n"
    staged = _stage(client, base, source_id, owner_headers, key, contents)
    assert staged.status_code == 201, staged.text
    ingestion_id = staged.json()["ingestion"]["id"]

    member = register_user(client, "universal-retention-member@example.com")
    create_member(client, owner, tenant_id, "universal-retention-member@example.com")
    denied = client.post(
        f"{base}/retention/policies",
        json={"retention_days": 1},
        headers=auth_headers(member),
    )
    assert denied.status_code == 403

    no_policy = client.get(f"{base}/retention/policies", headers=owner_headers)
    assert no_policy.status_code == 200, no_policy.text
    assert no_policy.json()["items"] == []
    assert no_policy.json()["purge_enabled"] is False

    before_revocation = client.get(f"{base}/sources/{source_id}/purge-plan", headers=owner_headers)
    assert before_revocation.status_code == 200, before_revocation.text
    assert before_revocation.json()["purge_enabled"] is False
    assert before_revocation.json()["reason"] == "source_not_revoked"

    policy_one = client.post(
        f"{base}/retention/policies",
        json={"retention_days": 30},
        headers=owner_headers,
    )
    assert policy_one.status_code == 201, policy_one.text
    assert policy_one.json()["active"] is False
    self_approval = client.post(
        f"{base}/retention/policies/{policy_one.json()['policy_version_id']}/approve",
        headers=owner_headers,
    )
    assert self_approval.status_code == 409
    approved_one = client.post(
        f"{base}/retention/policies/{policy_one.json()['policy_version_id']}/approve",
        headers=approver_headers,
    )
    assert approved_one.status_code == 200, approved_one.text
    assert approved_one.json()["retention_days"] == 30
    assert approved_one.json()["active"] is True

    # A newer, explicit but unapproved version suspends purging; it cannot silently inherit
    # the previously approved duration.
    policy_two = client.post(
        f"{base}/retention/policies",
        json={"retention_days": 1},
        headers=owner_headers,
    )
    assert policy_two.status_code == 201, policy_two.text
    assert policy_two.json()["version"] == 2
    assert policy_two.json()["active"] is False
    policies_pending_approval = client.get(f"{base}/retention/policies", headers=owner_headers)
    assert policies_pending_approval.json()["purge_enabled"] is False
    approved_two = client.post(
        f"{base}/retention/policies/{policy_two.json()['policy_version_id']}/approve",
        headers=approver_headers,
    )
    assert approved_two.status_code == 200, approved_two.text
    assert approved_two.json()["active"] is True

    revoked = client.post(
        f"{base}/sources/{source_id}/revoke",
        json={"reason_code": "owner_request"},
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text
    source_profile = client.get(f"{base}/sources/{source_id}", headers=owner_headers)
    assert source_profile.status_code == 200
    assert source_profile.json()["source_status"] == "disabled"

    rejected_new_upload = _stage(
        client,
        base,
        source_id,
        owner_headers,
        "universal-retention-new-upload-key-01",
        contents,
    )
    assert rejected_new_upload.status_code == 409
    assert rejected_new_upload.json()["error"]["code"] == "universal_source_revoked"
    revoked_mapping = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={
            "profile_key": "retention.after-revocation",
            "display_name": "Revoked source mapping",
            "entity_type": "customer",
            "worksheet_name": "CSV",
            "field_mapping": {
                "customer_code": "customer_code",
                "name": "name",
                "email": "email",
            },
            "ignored_columns": [],
            "field_options": {},
        },
        headers=owner_headers,
    )
    revoked_validation = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    revoked_promotion = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={"acknowledge_warnings": True},
        headers=owner_headers,
    )
    for rejected_operation in (revoked_mapping, revoked_validation, revoked_promotion):
        assert rejected_operation.status_code == 409, rejected_operation.text
        assert rejected_operation.json()["error"]["code"] == "universal_source_revoked"

    evidence_before = client.get(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/evidence",
        headers=owner_headers,
    )
    assert evidence_before.status_code == 200
    assert evidence_before.content == contents
    _age_ingestion(admin_engine, ingestion_id)

    try:
        plan_response = client.get(
            f"{base}/sources/{source_id}/purge-plan?limit=1&offset=0",
            headers=owner_headers,
        )
        assert plan_response.status_code == 200, plan_response.text
        plan = plan_response.json()
        assert plan["purge_enabled"] is True
        assert plan["total_eligible"] == 1
        assert plan["total_blocked"] == 0
        assert plan["limit"] == 1
        assert plan["eligible_ingestions"][0]["ingestion_id"] == ingestion_id
        assert plan["eligible_ingestions"][0]["row_count"] == 1
        assert plan["snapshots"][0]["file_name"] == "customers.csv"
        assert plan["snapshots"][0]["content_hash"] == hashlib.sha256(contents).hexdigest()
        assert plan["snapshots"][0]["will_be_deleted_if_all_eligible_ingestions_are_purged"] is True
        assert "Private Customer" not in plan_response.text
        assert "private@example.test" not in plan_response.text
        assert key not in plan_response.text

        plan_policy_id = plan["policy"]["policy_version_id"]
        assert plan_policy_id == policy_two.json()["policy_version_id"]
        purge = client.post(
            f"{base}/sources/{source_id}/purge",
            json={
                "policy_version_id": plan_policy_id,
                "plan_limit": 1,
                "plan_offset": 0,
                "ingestion_ids": [ingestion_id],
            },
            headers=owner_headers,
        )
        assert purge.status_code == 200, purge.text
        assert purge.json()["purged_ingestion_count"] == 1
        assert purge.json()["purged_row_count"] == 1
        assert purge.json()["purged_event_count"] == 1
        assert purge.json()["purged_snapshot_count"] == 1
        assert purge.json()["purged_snapshot_bytes"] == len(contents)

        reused_purged_key = _stage(client, base, source_id, owner_headers, key, contents)
        assert reused_purged_key.status_code == 409
        assert reused_purged_key.json()["error"]["code"] == "idempotency_key_purged"

        assert (
            client.get(f"{base}/sources/{source_id}/ingestions", headers=owner_headers).json()[
                "total"
            ]
            == 0
        )
        for suffix in ("", "/rows", "/evidence"):
            response = client.get(
                f"{base}/sources/{source_id}/ingestions/{ingestion_id}{suffix}",
                headers=owner_headers,
            )
            assert response.status_code == 404, response.text
        assert client.get(f"{base}/sources/{source_id}", headers=owner_headers).status_code == 200

        with admin_engine.connect() as connection:
            source_scope = {
                "tenant_id": UUID(tenant_id),
                "company_id": UUID(company_id),
                "source_id": UUID(source_id),
            }
            ingestion_scope = {
                "tenant_id": UUID(tenant_id),
                "company_id": UUID(company_id),
                "ingestion_id": UUID(ingestion_id),
            }
            remaining_counts = {
                "universal_ingestions": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_ingestions "
                        "WHERE tenant_id = :tenant_id "
                        "AND company_id = :company_id AND source_id = :source_id"
                    ),
                    source_scope,
                ),
                "universal_source_snapshots": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_source_snapshots "
                        "WHERE tenant_id = :tenant_id "
                        "AND company_id = :company_id AND source_id = :source_id"
                    ),
                    source_scope,
                ),
                "universal_ingestion_rows": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_ingestion_rows "
                        "WHERE tenant_id = :tenant_id "
                        "AND company_id = :company_id AND source_id = :source_id"
                    ),
                    source_scope,
                ),
                "universal_row_validations": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_row_validations "
                        "WHERE tenant_id = :tenant_id "
                        "AND company_id = :company_id AND source_id = :source_id"
                    ),
                    source_scope,
                ),
                "universal_ingestion_events": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_ingestion_events "
                        "WHERE tenant_id = :tenant_id "
                        "AND company_id = :company_id AND ingestion_id = :ingestion_id"
                    ),
                    ingestion_scope,
                ),
                "universal_ingestion_record_links": connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_ingestion_record_links "
                        "WHERE tenant_id = :tenant_id AND company_id = :company_id "
                        "AND ingestion_id = :ingestion_id"
                    ),
                    ingestion_scope,
                ),
            }
            assert all(count == 0 for count in remaining_counts.values()), remaining_counts
            tombstone = connection.execute(
                text(
                    "SELECT idempotency_key_hash FROM universal_ingestion_purge_tombstones "
                    "WHERE tenant_id = :tenant_id AND source_id = :source_id"
                ),
                {"tenant_id": UUID(tenant_id), "source_id": UUID(source_id)},
            ).scalar_one()
            assert tombstone == hashlib.sha256(key.encode("utf-8")).hexdigest()

            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_source_lifecycle_events "
                        "WHERE tenant_id = :tenant_id AND company_id = :company_id "
                        "AND source_id = :source_id AND event_type IN ('revoked', 'purged')"
                    ),
                    {
                        "tenant_id": UUID(tenant_id),
                        "company_id": UUID(company_id),
                        "source_id": UUID(source_id),
                    },
                )
                == 2
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_table_privilege('bf_runtime', "
                        "'public.universal_source_snapshots', 'DELETE')"
                    )
                )
                is False
            )
            audit_details = connection.scalar(
                text(
                    "SELECT COALESCE(jsonb_agg(metadata), '[]'::jsonb) FROM audit_events "
                    "WHERE tenant_id = :tenant_id AND event_type LIKE 'universal_retention.%'"
                ),
                {"tenant_id": UUID(tenant_id)},
            )
            audit_text = str(audit_details)
            assert "Private Customer" not in audit_text
            assert "private@example.test" not in audit_text
            assert key not in audit_text

        factory = sessionmaker(runtime_engine, expire_on_commit=False)
        with factory.begin() as session:
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": tenant_id},
            )
            session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": company_id},
            )
            tombstone_count = session.scalar(
                RETENTION_COUNT_QUERIES["universal_ingestion_purge_tombstones"]
            )
            assert tombstone_count == 1
        with factory.begin() as session:
            session.execute(
                text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
                {"tenant_id": str(uuid4())},
            )
            session.execute(
                text("SELECT set_config('app.company_id', :company_id, true)"),
                {"company_id": str(uuid4())},
            )
            tombstone_count = session.scalar(
                RETENTION_COUNT_QUERIES["universal_ingestion_purge_tombstones"]
            )
            assert tombstone_count == 0
    finally:
        _cleanup_retention_rows(admin_engine, tenant_id)


def test_retention_policy_revocation_disables_purge_until_new_version_approval(
    client, retention_row_cleanup
):
    _owner, owner_headers, tenant, _approver, approver_headers = _make_owner_and_approver(
        client, "universal-retention-policy-revocation"
    )
    tenant_id = str(tenant["id"])
    retention_row_cleanup(tenant_id)
    base = _base(tenant_id)
    source = _source(client, base, owner_headers, "retention.policy-revocation")
    source_id = str(source["source_id"])
    policy_one = _configure_and_approve(client, base, owner_headers, approver_headers)
    revoked_source = client.post(
        f"{base}/sources/{source_id}/revoke",
        json={"reason_code": "owner_request"},
        headers=owner_headers,
    )
    assert revoked_source.status_code == 200, revoked_source.text

    enabled_plan = client.get(f"{base}/sources/{source_id}/purge-plan", headers=owner_headers)
    assert enabled_plan.status_code == 200, enabled_plan.text
    assert enabled_plan.json()["purge_enabled"] is True

    revoked_policy = client.post(
        f"{base}/retention/policies/{policy_one['policy_version_id']}/revoke",
        headers=owner_headers,
    )
    assert revoked_policy.status_code == 200, revoked_policy.text
    assert revoked_policy.json()["active"] is False
    disabled_plan = client.get(f"{base}/sources/{source_id}/purge-plan", headers=owner_headers)
    assert disabled_plan.status_code == 200, disabled_plan.text
    assert disabled_plan.json()["purge_enabled"] is False
    assert disabled_plan.json()["reason"] == "no_configured_approved_active_retention_policy"

    policy_two = client.post(
        f"{base}/retention/policies",
        json={"retention_days": 7},
        headers=owner_headers,
    )
    assert policy_two.status_code == 201, policy_two.text
    assert policy_two.json()["version"] == 2
    approved = client.post(
        f"{base}/retention/policies/{policy_two.json()['policy_version_id']}/approve",
        headers=approver_headers,
    )
    assert approved.status_code == 200, approved.text
    restored_plan = client.get(f"{base}/sources/{source_id}/purge-plan", headers=owner_headers)
    assert restored_plan.status_code == 200, restored_plan.text
    assert restored_plan.json()["purge_enabled"] is True
    assert (
        restored_plan.json()["policy"]["policy_version_id"]
        == policy_two.json()["policy_version_id"]
    )


def test_retention_purge_plan_blocks_promoted_canonical_dependencies(
    client, admin_engine, retention_row_cleanup
):
    _owner, owner_headers, tenant, _approver, approver_headers = _make_owner_and_approver(
        client, "universal-retention-dependency"
    )
    tenant_id = str(tenant["id"])
    retention_row_cleanup(tenant_id)
    base = _base(tenant_id)
    source = _source(client, base, owner_headers, "retention.promoted")
    source_id = str(source["source_id"])
    contents = b"customer_code,name,email\nCUS-DEP-1,Retained Customer,retained@example.test\n"
    staged = _stage(
        client,
        base,
        source_id,
        owner_headers,
        "universal-retention-dependency-idem-01",
        contents,
    )
    assert staged.status_code == 201, staged.text
    ingestion_id = staged.json()["ingestion"]["id"]
    page_eligible = _stage(
        client,
        base,
        source_id,
        owner_headers,
        "universal-retention-dependency-page-eligible-01",
        b"customer_code,name\nCUS-PAGE-2,Eligible\n",
    )
    assert page_eligible.status_code == 201, page_eligible.text
    page_eligible_ingestion_id = page_eligible.json()["ingestion"]["id"]
    mapping = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={
            "profile_key": "retention.customer",
            "display_name": "Retention customer mapping",
            "entity_type": "customer",
            "worksheet_name": "CSV",
            "field_mapping": {
                "customer_code": "customer_code",
                "name": "name",
                "email": "email",
            },
            "ignored_columns": [],
            "field_options": {},
        },
        headers=owner_headers,
    )
    assert mapping.status_code == 200, mapping.text
    validation = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validation.status_code == 200, validation.text
    promoted = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={"acknowledge_warnings": True},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    policy = _configure_and_approve(client, base, owner_headers, approver_headers, retention_days=1)
    revoked = client.post(
        f"{base}/sources/{source_id}/revoke",
        json={"reason_code": "source_replaced"},
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text
    _age_ingestion(admin_engine, ingestion_id)
    _age_ingestion(admin_engine, page_eligible_ingestion_id)

    try:
        pages = []
        for offset in (0, 1):
            plan_response = client.get(
                f"{base}/sources/{source_id}/purge-plan?limit=1&offset={offset}",
                headers=owner_headers,
            )
            assert plan_response.status_code == 200, plan_response.text
            pages.append(plan_response.json())

        for page in pages:
            assert page["purge_enabled"] is True
            assert page["total_eligible"] == 1
            assert page["total_blocked"] == 1
        assert pages[0]["has_more"] is True
        assert pages[1]["has_more"] is False
        assert all(
            len(page["eligible_ingestions"]) + len(page["blocked_ingestions"]) == 1
            for page in pages
        )
        blocked_page = next(page for page in pages if page["blocked_ingestions"])
        eligible_page = next(page for page in pages if page["eligible_ingestions"])
        blocked = blocked_page["blocked_ingestions"][0]
        eligible = eligible_page["eligible_ingestions"][0]
        assert blocked["ingestion_id"] == ingestion_id
        assert "canonical_record_revisions" in blocked["blockers"]
        assert blocked["dependency_counts"]["canonical_record_revisions"] > 0
        assert eligible["ingestion_id"] == page_eligible_ingestion_id

        refused = client.post(
            f"{base}/sources/{source_id}/purge",
            json={
                "policy_version_id": policy["policy_version_id"],
                "plan_limit": 1,
                "plan_offset": blocked_page["offset"],
                "ingestion_ids": [ingestion_id],
            },
            headers=owner_headers,
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "universal_purge_plan_stale"

        purged = client.post(
            f"{base}/sources/{source_id}/purge",
            json={
                "policy_version_id": policy["policy_version_id"],
                "plan_limit": 1,
                "plan_offset": eligible_page["offset"],
                "ingestion_ids": [page_eligible_ingestion_id],
            },
            headers=owner_headers,
        )
        assert purged.status_code == 200, purged.text
        assert purged.json()["purged_ingestion_count"] == 1

        blocked_evidence = client.get(
            f"{base}/sources/{source_id}/ingestions/{ingestion_id}/evidence",
            headers=owner_headers,
        )
        assert blocked_evidence.status_code == 200
        assert blocked_evidence.content == contents
        eligible_evidence = client.get(
            f"{base}/sources/{source_id}/ingestions/{page_eligible_ingestion_id}/evidence",
            headers=owner_headers,
        )
        assert eligible_evidence.status_code == 404
        with admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM universal_ingestions WHERE id = :id"),
                    {"id": UUID(ingestion_id)},
                )
                == 1
            )
            assert (
                connection.scalar(
                    text("SELECT count(*) FROM universal_ingestions WHERE id = :id"),
                    {"id": UUID(page_eligible_ingestion_id)},
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM universal_source_snapshots "
                        "WHERE source_id = :source_id"
                    ),
                    {"source_id": UUID(source_id)},
                )
                == 1
            )
    finally:
        _cleanup_retention_rows(admin_engine, tenant_id)


def test_0028_downgrade_directly_refuses_retained_policy_and_lifecycle_evidence(
    client, migration_engine, admin_engine, retention_row_cleanup
):
    tenant_key = f"universal-migration-guard-{uuid4().hex[:12]}"
    _owner, owner_headers, tenant, _approver, approver_headers = _make_owner_and_approver(
        client, tenant_key
    )
    tenant_id = str(tenant["id"])
    retention_row_cleanup(tenant_id)
    base = _base(tenant_id)
    source = _source(client, base, owner_headers, "retention.migration-guard")
    source_id = str(source["source_id"])
    policy = _configure_and_approve(
        client, base, owner_headers, approver_headers, retention_days=30
    )
    revoked = client.post(
        f"{base}/sources/{source_id}/revoke",
        json={"reason_code": "owner_request"},
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text

    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM universal_retention_policy_versions "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": UUID(tenant_id)},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM universal_retention_policy_approvals "
                    "WHERE tenant_id = :tenant_id"
                ),
                {"tenant_id": UUID(tenant_id)},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM universal_source_lifecycle_events "
                    "WHERE tenant_id = :tenant_id AND source_id = :source_id "
                    "AND event_type = 'revoked'"
                ),
                {"tenant_id": UUID(tenant_id), "source_id": UUID(source_id)},
            )
            == 1
        )

    with pytest.raises(
        DBAPIError,
        match="cannot downgrade while retention or lifecycle evidence exists",
    ):
        invoke_downgrade_function(migration_engine, "0028_universal_data_retention.py")

    with migration_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0028_universal_data_retention"
        )
        assert connection.scalar(text("SELECT current_user")) == "bf_migrator"
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM universal_retention_policy_versions "
                    "WHERE tenant_id = :tenant_id AND id = :policy_id"
                ),
                {"tenant_id": UUID(tenant_id), "policy_id": UUID(policy["policy_version_id"])},
            )
            == 1
        )
        assert (
            connection.scalar(
                text(
                    "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'universal_source_lifecycle_events'"
                )
            )
            is True
        )
