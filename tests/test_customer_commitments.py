from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.metrics.snapshot import content_hash
from tests.helpers import auth_headers, create_member, create_tenant, register_user

TABLES = (
    "manual_customer_commitments",
    "manual_customer_commitment_events",
    "manual_customer_commitment_evidence",
)


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/company-data/customer-commitments"


def _valid_fields(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "customer_name": "Nile Logistics",
        "customer_reference": "NL-ORDER-2026-041",
        "order_date": date.today().isoformat(),
        "requested_delivery_date": (date.today() + timedelta(days=14)).isoformat(),
        "summary": "Deliver the agreed warehouse onboarding workshop.",
        "notes": "Operational intake only.",
    }
    values.update(overrides)
    return values


def test_manual_customer_commitment_lifecycle_correction_evidence_history_and_company_scope(
    client, runtime_engine, admin_engine
):
    owner = register_user(client, "commitments-owner@example.com")
    owner_headers = auth_headers(owner)
    owner_user_id = client.get("/api/v1/auth/me", headers=owner_headers).json()["id"]
    tenant = create_tenant(
        client,
        owner,
        name="Customer Commitments Tenant",
        company_name="Customer Commitments Company",
        key="commitments-tenant-key-001",
    )
    tenant_id = tenant["id"]
    base = _base(tenant_id)

    member = register_user(client, "commitments-member@example.com")
    create_member(client, owner, tenant_id, "commitments-member@example.com")
    member_headers = auth_headers(member)

    contracts = client.get(
        f"/api/v1/tenants/{tenant_id}/company-data/contracts", headers=member_headers
    )
    assert contracts.status_code == 200, contracts.text
    contract = next(
        item for item in contracts.json()["items"] if item["dataset_type"] == "customer_commitments"
    )
    assert contract["contract_key"] == "businessflow.manual.customer_commitments"
    assert contract["definition"]["source_kind"] == "manual_entry"
    assert contract["definition_hash"]

    created = client.post(
        base,
        json={
            "customer_name": "Nile Logistics",
            "order_date": date.today().isoformat(),
            "summary": "Deliver the agreed warehouse onboarding workshop.",
        },
        headers=member_headers,
    )
    assert created.status_code == 201, created.text
    response = created.json()
    record = response["commitment"]
    commitment_id = record["id"]
    assert record["status"] == "draft"
    assert record["origin"] == "manual"
    assert record["source_key"] == "manual.customer-commitments"
    assert record["contract_hash"] == contract["definition_hash"]
    assert response["validation"]["errors"] == []
    assert {item["code"] for item in response["validation"]["warnings"]} == {
        "missing_customer_reference",
        "missing_requested_delivery_date",
        "no_supporting_evidence",
    }

    source_list = client.get(
        f"/api/v1/tenants/{tenant_id}/company-data/sources", headers=member_headers
    )
    assert source_list.status_code == 200, source_list.text
    assert source_list.json()["total"] == 1
    source = source_list.json()["items"][0]
    assert source["id"] == record["source_id"]
    assert source["dataset_type"] == "customer_commitments"
    assert source["source_key"] == "manual.customer-commitments"
    assert source["latest_ingestion_id"] is None

    coverage = client.get(
        f"/api/v1/tenants/{tenant_id}/company-data/coverage", headers=member_headers
    )
    assert coverage.status_code == 200, coverage.text
    assert len(coverage.json()["datasets"]) == 6
    assert all(item["registered_source_count"] == 0 for item in coverage.json()["datasets"])

    unsupported_m4 = client.post(
        f"/api/v1/tenants/{tenant_id}/company-data/sources/{source['id']}/ingestions",
        json={"import_id": str(uuid4())},
        headers=owner_headers,
    )
    assert unsupported_m4.status_code == 409
    assert unsupported_m4.json()["error"]["code"] == "manual_source_m4_ingestion_not_supported"
    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with (
        pytest.raises(DBAPIError, match="manual customer commitments cannot be recorded"),
        factory.begin() as session,
    ):
        session.execute(text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_id})
        session.execute(
            text("SELECT set_config('app.company_id', :id, true)"),
            {"id": tenant["company"]["id"]},
        )
        session.execute(
            text(
                "INSERT INTO company_data_ingestions "
                "(id, tenant_id, company_id, source_id) "
                "VALUES (:id, :tenant_id, :company_id, :source_id)"
            ),
            {
                "id": str(uuid4()),
                "tenant_id": tenant_id,
                "company_id": tenant["company"]["id"],
                "source_id": source["id"],
            },
        )
    generic_source = client.post(
        f"/api/v1/tenants/{tenant_id}/company-data/sources",
        json={
            "source_key": "manual-other-source",
            "display_name": "Unmanaged manual source",
            "dataset_type": "customer_commitments",
            "contract_version": "1.0.0",
        },
        headers=owner_headers,
    )
    assert generic_source.status_code == 409
    assert generic_source.json()["error"]["code"] == "manual_customer_commitment_source_managed"

    initial_revision = record["current_revision"]
    invalid_dates = client.put(
        f"{base}/{commitment_id}",
        json={
            **_valid_fields(requested_delivery_date=(date.today() - timedelta(days=1)).isoformat()),
            "expected_revision": initial_revision,
        },
        headers=member_headers,
    )
    assert invalid_dates.status_code == 200, invalid_dates.text
    invalid_record = invalid_dates.json()
    assert "delivery_date_before_order_date" in {
        item["code"] for item in invalid_record["validation"]["errors"]
    }
    stale_revision = client.put(
        f"{base}/{commitment_id}",
        json={**_valid_fields(), "expected_revision": initial_revision},
        headers=member_headers,
    )
    assert stale_revision.status_code == 409
    assert stale_revision.json()["error"]["code"] == "manual_customer_commitment_revision_conflict"
    repaired_dates = client.put(
        f"{base}/{commitment_id}",
        json={
            **_valid_fields(customer_reference=None, requested_delivery_date=None),
            "expected_revision": invalid_record["commitment"]["current_revision"],
        },
        headers=member_headers,
    )
    assert repaired_dates.status_code == 200, repaired_dates.text
    assert repaired_dates.json()["validation"]["errors"] == []

    evidence_bytes = b"%PDF-1.7\n% User-provided order confirmation evidence\n"
    uploaded = client.post(
        f"{base}/{commitment_id}/evidence",
        files={"file": ("customer-confirmation.pdf", evidence_bytes, "application/pdf")},
        headers=member_headers,
    )
    assert uploaded.status_code == 201, uploaded.text
    evidence = uploaded.json()["evidence"]
    assert evidence["file_size_bytes"] == len(evidence_bytes)
    assert len(evidence["file_hash"]) == 64

    current = client.get(f"{base}/{commitment_id}", headers=member_headers)
    assert current.status_code == 200, current.text
    record = current.json()["commitment"]
    assert current.json()["validation"]["errors"] == []
    assert {item["code"] for item in current.json()["validation"]["warnings"]} == {
        "missing_customer_reference",
        "missing_requested_delivery_date",
    }

    not_acknowledged = client.post(
        f"{base}/{commitment_id}/submit",
        json={"expected_revision": record["current_revision"], "acknowledge_warnings": False},
        headers=member_headers,
    )
    assert not_acknowledged.status_code == 422
    assert not_acknowledged.json()["error"]["code"] == (
        "manual_customer_commitment_warnings_unacknowledged"
    )

    submitted = client.post(
        f"{base}/{commitment_id}/submit",
        json={"expected_revision": record["current_revision"], "acknowledge_warnings": True},
        headers=member_headers,
    )
    assert submitted.status_code == 200, submitted.text
    assert submitted.json()["commitment"]["status"] == "submitted"
    member_user_id = client.get("/api/v1/auth/me", headers=member_headers).json()["id"]
    assert submitted.json()["commitment"]["submitted_by_user_id"] == member_user_id
    assert submitted.json()["commitment"]["reviewed_by_user_id"] is None

    member_review = client.post(
        f"{base}/{commitment_id}/review",
        json={
            "expected_revision": submitted.json()["commitment"]["current_revision"],
            "decision": "accept",
            "criteria_confirmed": True,
            "acknowledge_warnings": True,
        },
        headers=member_headers,
    )
    assert member_review.status_code == 403

    correction = client.post(
        f"{base}/{commitment_id}/review",
        json={
            "expected_revision": submitted.json()["commitment"]["current_revision"],
            "decision": "request_correction",
            "comment": "Please add the customer's reference and requested delivery date.",
        },
        headers=owner_headers,
    )
    assert correction.status_code == 200, correction.text
    assert correction.json()["commitment"]["status"] == "changes_requested"
    assert correction.json()["commitment"]["review_comment"].startswith("Please add")

    corrected = client.put(
        f"{base}/{commitment_id}",
        json={
            **_valid_fields(),
            "expected_revision": correction.json()["commitment"]["current_revision"],
        },
        headers=member_headers,
    )
    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["commitment"]["status"] == "changes_requested"
    assert corrected.json()["validation"]["warnings"] == []
    assert corrected.json()["commitment"]["customer_reference"] == "NL-ORDER-2026-041"

    resubmitted = client.post(
        f"{base}/{commitment_id}/submit",
        json={
            "expected_revision": corrected.json()["commitment"]["current_revision"],
            "acknowledge_warnings": False,
        },
        headers=member_headers,
    )
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["commitment"]["status"] == "submitted"

    missing_criteria = client.post(
        f"{base}/{commitment_id}/review",
        json={
            "expected_revision": resubmitted.json()["commitment"]["current_revision"],
            "decision": "accept",
            "criteria_confirmed": False,
        },
        headers=owner_headers,
    )
    assert missing_criteria.status_code == 422
    assert missing_criteria.json()["error"]["code"] == (
        "manual_customer_commitment_review_criteria_required"
    )

    accepted = client.post(
        f"{base}/{commitment_id}/review",
        json={
            "expected_revision": resubmitted.json()["commitment"]["current_revision"],
            "decision": "accept",
            "criteria_confirmed": True,
        },
        headers=owner_headers,
    )
    assert accepted.status_code == 200, accepted.text
    accepted_record = accepted.json()["commitment"]
    assert accepted_record["status"] == "accepted"
    assert accepted_record["reviewed_by_user_id"] == owner_user_id
    assert accepted_record["source_id"] == source["id"]

    immutable_update = client.put(
        f"{base}/{commitment_id}",
        json={
            **_valid_fields(summary="Silent accepted-record overwrite"),
            "expected_revision": accepted_record["current_revision"],
        },
        headers=owner_headers,
    )
    assert immutable_update.status_code == 409
    assert immutable_update.json()["error"]["code"] == "manual_customer_commitment_not_editable"

    immutable_evidence = client.post(
        f"{base}/{commitment_id}/evidence",
        files={"file": ("late.txt", b"late", "text/plain")},
        headers=owner_headers,
    )
    assert immutable_evidence.status_code == 409

    download = client.get(
        f"{base}/{commitment_id}/evidence/{evidence['id']}", headers=member_headers
    )
    assert download.status_code == 200, download.text
    assert download.content == evidence_bytes
    assert download.headers["x-evidence-sha256"] == evidence["file_hash"]
    assert download.headers["content-disposition"].endswith("customer-confirmation.pdf")
    assert download.headers["x-content-type-options"] == "nosniff"

    history = client.get(f"{base}/{commitment_id}/history?limit=100", headers=member_headers)
    assert history.status_code == 200, history.text
    events = history.json()["items"]
    assert [event["event_type"] for event in events] == [
        "accepted",
        "submitted",
        "corrected",
        "correction_requested",
        "submitted",
        "evidence_added",
        "draft_saved",
        "draft_saved",
        "draft_created",
    ]
    chronological = list(reversed(events))
    assert [event["current_revision"] for event in chronological] == list(
        range(1, len(chronological) + 1)
    )
    for index, event in enumerate(chronological):
        assert content_hash(event["snapshot"]) == event["snapshot_hash"]
        assert len(event["event_hash"]) == 64
        if index == 0:
            assert event["previous_event_hash"] is None
        else:
            assert event["previous_event_hash"] == chronological[index - 1]["event_hash"]
    acceptance_event = events[0]
    assert acceptance_event["snapshot"]["origin"] == "manual"
    assert acceptance_event["snapshot"]["source"]["source_key"] == "manual.customer-commitments"
    assert (
        acceptance_event["snapshot"]["action_context"][
            "review_confirms_intake_criteria_not_underlying_event_truth"
        ]
        is True
    )
    assert acceptance_event["snapshot"]["action_context"]["process_criteria_confirmed"] is True

    other_owner = register_user(client, "commitments-other-owner@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other Commitments Tenant",
        company_name="Other Commitments Company",
        key="commitments-other-tenant-001",
    )
    cross_company_read = client.get(
        f"{_base(other_tenant['id'])}/{commitment_id}",
        headers=auth_headers(other_owner),
    )
    assert cross_company_read.status_code == 404
    cross_company_download = client.get(
        f"{_base(other_tenant['id'])}/{commitment_id}/evidence/{evidence['id']}",
        headers=auth_headers(other_owner),
    )
    assert cross_company_download.status_code == 404

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_id})
        session.execute(
            text("SELECT set_config('app.company_id', :id, true)"),
            {"id": tenant["company"]["id"]},
        )
        session.execute(
            text("SELECT set_config('app.user_id', :id, true)"),
            {"id": owner_user_id},
        )
        session.execute(
            text(
                "UPDATE manual_customer_commitments SET summary = 'direct overwrite' "
                "WHERE tenant_id = :tenant_id AND company_id = :company_id AND id = :id"
            ),
            {"tenant_id": tenant_id, "company_id": tenant["company"]["id"], "id": commitment_id},
        )

    with admin_engine.connect() as connection:
        table_security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class AS c JOIN pg_roles AS r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(:names) ORDER BY c.relname"
            ),
            {"names": list(TABLES)},
        ).all()
        policies = connection.execute(
            text("SELECT tablename FROM pg_policies WHERE tablename = ANY(:names)"),
            {"names": list(TABLES)},
        ).all()
        privileges = {
            table: connection.execute(
                text(
                    "SELECT "
                    "has_table_privilege('bf_runtime', :table_name, 'SELECT') AS can_select, "
                    "has_table_privilege('bf_runtime', :table_name, 'INSERT') AS can_insert, "
                    "has_table_privilege('bf_runtime', :table_name, 'UPDATE') AS can_update, "
                    "has_table_privilege('bf_runtime', :table_name, 'DELETE') AS can_delete, "
                    "has_table_privilege('bf_runtime', :table_name, 'TRUNCATE') AS can_truncate"
                ),
                {"table_name": f"public.{table}"},
            ).one()
            for table in TABLES
        }
    assert len(table_security) == len(TABLES)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in table_security)
    assert all(row.owner == "bf_migrator" for row in table_security)
    assert len(policies) == len(TABLES)
    assert len(privileges) == len(TABLES)
    for table, grants in privileges.items():
        assert grants.can_select and grants.can_insert
        assert not grants.can_delete and not grants.can_truncate
        if table == "manual_customer_commitments":
            assert grants.can_update
        else:
            assert not grants.can_update

    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    with pytest.raises(
        DBAPIError,
        match="cannot downgrade while manual customer-commitment source or record history exists",
    ):
        command.downgrade(config, "0017_core_financial_transfers")
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0022_bi_config_analysis"
        )


def test_customer_commitment_permission_separation_and_draft_validation(client):
    owner = register_user(client, "commitments-permissions-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Commitments Permissions Tenant",
        company_name="Commitments Permissions Company",
        key="commitments-permissions-tenant-001",
    )
    tenant_id = tenant["id"]
    base = _base(tenant_id)

    member = register_user(client, "commitments-permissions-member@example.com")
    create_member(client, owner, tenant_id, "commitments-permissions-member@example.com")
    member_headers = auth_headers(member)

    incomplete = client.post(base, json={"customer_name": "  "}, headers=member_headers)
    assert incomplete.status_code == 201, incomplete.text
    draft = incomplete.json()
    assert draft["validation"]["errors"]
    draft_id = draft["commitment"]["id"]

    forbidden_edit = client.put(
        f"{base}/{draft_id}",
        json={**_valid_fields(), "expected_revision": draft["commitment"]["current_revision"]},
        headers=owner_headers,
    )
    assert forbidden_edit.status_code == 200, forbidden_edit.text
    assert forbidden_edit.json()["commitment"]["status"] == "draft"

    another_member = register_user(client, "commitments-permissions-other-member@example.com")
    create_member(client, owner, tenant_id, "commitments-permissions-other-member@example.com")
    wrong_editor = client.put(
        f"{base}/{draft_id}",
        json={
            **_valid_fields(summary="Unauthorized edit"),
            "expected_revision": forbidden_edit.json()["commitment"]["current_revision"],
        },
        headers=auth_headers(another_member),
    )
    assert wrong_editor.status_code == 403
    assert wrong_editor.json()["error"]["code"] == "manual_customer_commitment_edit_forbidden"

    submitted = client.post(
        f"{base}/{draft_id}/submit",
        json={
            "expected_revision": forbidden_edit.json()["commitment"]["current_revision"],
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert submitted.status_code == 200, submitted.text
    self_review = client.post(
        f"{base}/{draft_id}/review",
        json={
            "expected_revision": submitted.json()["commitment"]["current_revision"],
            "decision": "reject",
            "comment": "Cannot approve my own submission.",
        },
        headers=owner_headers,
    )
    assert self_review.status_code == 403
    assert self_review.json()["error"]["code"] == "manual_customer_commitment_self_review_forbidden"

    invalid = client.post(
        f"{base}/{draft_id}/submit",
        json={
            "expected_revision": submitted.json()["commitment"]["current_revision"],
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert invalid.status_code == 409
    assert invalid.json()["error"]["code"] == "manual_customer_commitment_not_editable"

    unauthenticated = client.get(f"{base}/{draft_id}")
    assert unauthenticated.status_code == 401


def test_customer_commitment_rls_fails_closed_without_context_and_hides_cross_company_records(
    client, runtime_engine
):
    owner_a = register_user(client, "commitments-rls-a@example.com")
    tenant_a = create_tenant(
        client,
        owner_a,
        name="Commitments RLS A",
        company_name="Commitments RLS Company A",
        key="commitments-rls-a-key-001",
    )
    created = client.post(
        _base(tenant_a["id"]),
        json=_valid_fields(),
        headers=auth_headers(owner_a),
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["commitment"]["id"]

    owner_b = register_user(client, "commitments-rls-b@example.com")
    tenant_b = create_tenant(
        client,
        owner_b,
        name="Commitments RLS B",
        company_name="Commitments RLS Company B",
        key="commitments-rls-b-key-001",
    )

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitments")) == 0
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitment_events")) == 0
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitment_evidence")) == 0

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_a["id"]}
        )
        session.execute(
            text("SELECT set_config('app.company_id', :id, true)"),
            {"id": tenant_a["company"]["id"]},
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM manual_customer_commitments WHERE id = :id"),
                {"id": record_id},
            )
            == 1
        )
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitment_events")) == 1

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant_b["id"]}
        )
        session.execute(
            text("SELECT set_config('app.company_id', :id, true)"),
            {"id": tenant_b["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitments")) == 0
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitment_events")) == 0
        assert session.scalar(text("SELECT count(*) FROM manual_customer_commitment_evidence")) == 0
