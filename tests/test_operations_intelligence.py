from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from time import perf_counter

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from businessflow.operations_intelligence import service as operations_service
from tests.helpers import auth_headers, create_member, create_tenant, register_user
from tests.migration_helpers import invoke_downgrade_function


def _operations_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/operations"


def _universal_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/company-data/universal"


def _register_source(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    source_key: str,
    *,
    source_type: str = "manual_entry",
    authority_classification: str = "manual_assertion",
) -> dict[str, object]:
    response = client.post(
        f"{_universal_base(tenant_id)}/sources",
        json={
            "source_key": source_key,
            "display_name": f"{source_key} source",
            "source_type": source_type,
            "authority_classification": authority_classification,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _event_body(
    source_id: str,
    event_key: str,
    *,
    event_type: str = "recorded",
    occurred_at: str = "2026-11-20T12:00:00Z",
    state_before: str | None = None,
    state_after: str | None = None,
) -> dict[str, object]:
    return {
        "source_id": source_id,
        "source_event_key": event_key,
        "occurred_at": occurred_at,
        "event_type": event_type,
        "source_subject_reference": "work-item-17",
        "process_reference": "source-defined-process-x",
        "state_before": state_before,
        "state_after": state_after,
        "description": "Synthetic test observation only",
        "evidence_kind": "manual_assertion",
    }


def _assert_operations_append_only(connection, statement: str, parameters: dict[str, str]) -> None:
    with (
        pytest.raises(DBAPIError, match="operations evidence is append-only"),
        connection.begin_nested(),
    ):
        connection.execute(text(statement), parameters)


def test_operations_event_versions_idempotency_access_and_replay(
    client: TestClient, runtime_engine, admin_engine
):
    owner = register_user(client, "operations-core-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Core Tenant",
        company_name="Operations Core Company",
        key="operations-core-tenant-0001",
    )
    tenant_id = tenant["id"]
    second_tenant = create_tenant(
        client,
        owner,
        name="Operations Core Tenant B",
        company_name="Operations Core Company B",
        key="operations-core-tenant-b-0001",
    )
    base = _operations_base(tenant_id)
    source = _register_source(client, tenant_id, owner_headers, "operations.manual")
    source_id = str(source["source_id"])

    body = _event_body(
        source_id,
        "work-item-17-event-1",
        event_type="opened",
        state_before="not-started",
        state_after="in-progress",
    )
    event_url = f"{base}/events"
    created = client.post(
        event_url,
        json=body,
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0001"},
    )
    assert created.status_code == 201, created.text
    first = created.json()
    assert first["is_current"] is True
    assert first["replayed"] is False
    assert first["version"]["version"] == 1
    assert first["version"]["evidence_kind"] == "manual_assertion"
    assert first["version"]["canonical_record_id"] is None

    cross_tenant_detail = client.get(
        f"{_operations_base(second_tenant['id'])}/events/{first['id']}",
        headers=owner_headers,
    )
    assert cross_tenant_detail.status_code == 404

    with runtime_engine.begin() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM operations_event_streams "
                    "WHERE id = CAST(:event_id AS uuid)"
                ),
                {"event_id": first["id"]},
            )
            == 0
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :value, true)"),
            {"value": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :value, true)"),
            {"value": tenant["company"]["id"]},
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM operations_event_streams "
                    "WHERE id = CAST(:event_id AS uuid)"
                ),
                {"event_id": first["id"]},
            )
            == 1
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :value, true)"),
            {"value": second_tenant["id"]},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :value, true)"),
            {"value": second_tenant["company"]["id"]},
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM operations_event_streams "
                    "WHERE id = CAST(:event_id AS uuid)"
                ),
                {"event_id": first["id"]},
            )
            == 0
        )

    replayed_create = client.post(
        event_url,
        json=body,
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0001"},
    )
    assert replayed_create.status_code == 201, replayed_create.text
    assert replayed_create.json()["id"] == first["id"]
    assert replayed_create.json()["replayed"] is True

    changed_request = client.post(
        event_url,
        json={**body, "event_type": "different"},
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0001"},
    )
    assert changed_request.status_code == 409
    assert changed_request.json()["error"]["code"] == "idempotency_key_reused"

    duplicate_source_key = client.post(
        event_url,
        json=body,
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0002"},
    )
    assert duplicate_source_key.status_code == 409
    assert duplicate_source_key.json()["error"]["code"] == "operations_source_event_key_exists"

    end_boundary_event = client.post(
        event_url,
        json=_event_body(
            source_id,
            "work-item-18-at-end-boundary",
            event_type="excluded-at-end",
            occurred_at="2026-11-21T00:00:00Z",
        ),
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0003"},
    )
    assert end_boundary_event.status_code == 201, end_boundary_event.text

    correction_body = {
        **_event_body(
            source_id,
            "unused-for-correction-body",
            event_type="completed",
            occurred_at="2026-11-20T18:00:00Z",
            state_before="in-progress",
            state_after="complete",
        ),
        "expected_version": 1,
        "correction_reason": "Corrected the source event type after review.",
    }
    correction_body.pop("source_id")
    correction_body.pop("source_event_key")
    correction_url = f"{base}/events/{first['id']}/corrections"
    corrected = client.post(
        correction_url,
        json=correction_body,
        headers={**owner_headers, "Idempotency-Key": "operations-correction-idem-0001"},
    )
    assert corrected.status_code == 201, corrected.text
    second = corrected.json()
    assert second["version"]["version"] == 2
    assert second["version"]["previous_version_id"] == first["version"]["id"]
    assert second["version"]["previous_event_hash"] == first["version"]["event_hash"]
    assert second["version"]["event_hash"] != first["version"]["event_hash"]

    with runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :value, true)"),
            {"value": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :value, true)"),
            {"value": tenant["company"]["id"]},
        )
        current_version_id = connection.scalar(
            text(
                "SELECT event_version_id FROM operations_event_current "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND company_id = CAST(:company_id AS uuid) "
                "AND source_id = CAST(:source_id AS uuid) "
                "AND event_id = CAST(:event_id AS uuid)"
            ),
            {
                "tenant_id": tenant_id,
                "company_id": tenant["company"]["id"],
                "source_id": source_id,
                "event_id": first["id"],
            },
        )
        assert str(current_version_id) == second["version"]["id"]
        with (
            pytest.raises(
                DBAPIError,
                match="operations current pointer must reference the latest event version",
            ),
            connection.begin_nested(),
        ):
            connection.execute(
                text(
                    "UPDATE operations_event_current SET event_version_id = "
                    "CAST(:old_version_id AS uuid) "
                    "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                    "AND company_id = CAST(:company_id AS uuid) "
                    "AND source_id = CAST(:source_id AS uuid) "
                    "AND event_id = CAST(:event_id AS uuid)"
                ),
                {
                    "old_version_id": first["version"]["id"],
                    "tenant_id": tenant_id,
                    "company_id": tenant["company"]["id"],
                    "source_id": source_id,
                    "event_id": first["id"],
                },
            )
        current_after_rejected_update = connection.scalar(
            text(
                "SELECT event_version_id FROM operations_event_current "
                "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                "AND company_id = CAST(:company_id AS uuid) "
                "AND source_id = CAST(:source_id AS uuid) "
                "AND event_id = CAST(:event_id AS uuid)"
            ),
            {
                "tenant_id": tenant_id,
                "company_id": tenant["company"]["id"],
                "source_id": source_id,
                "event_id": first["id"],
            },
        )
        assert str(current_after_rejected_update) == second["version"]["id"]

    narrow_analysis = client.post(
        f"{base}/analyses",
        json={
            "source_id": source_id,
            "start_at": "2026-11-20T12:00:00Z",
            "end_at": "2026-11-20T13:00:00Z",
        },
        headers={**owner_headers, "Idempotency-Key": "operations-narrow-analysis-0001"},
    )
    assert narrow_analysis.status_code == 201, narrow_analysis.text
    assert narrow_analysis.json()["input_count"] == 0

    create_replay_after_correction = client.post(
        event_url,
        json=body,
        headers={**owner_headers, "Idempotency-Key": "operations-create-idem-0001"},
    )
    assert create_replay_after_correction.status_code == 201
    assert create_replay_after_correction.json()["replayed"] is True
    assert create_replay_after_correction.json()["version"]["id"] == first["version"]["id"]
    assert create_replay_after_correction.json()["is_current"] is False

    correction_replay = client.post(
        correction_url,
        json=correction_body,
        headers={**owner_headers, "Idempotency-Key": "operations-correction-idem-0001"},
    )
    assert correction_replay.status_code == 201, correction_replay.text
    assert correction_replay.json()["replayed"] is True
    assert correction_replay.json()["version"]["id"] == second["version"]["id"]

    stale_correction = client.post(
        correction_url,
        json={**correction_body, "expected_version": 1, "correction_reason": "Another stale edit."},
        headers={**owner_headers, "Idempotency-Key": "operations-correction-idem-0002"},
    )
    assert stale_correction.status_code == 409
    assert stale_correction.json()["error"]["code"] == "operations_event_version_conflict"

    history = client.get(f"{base}/events/{first['id']}/versions", headers=owner_headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert {item["version"] for item in history.json()["items"]} == {1, 2}

    listed_events = client.get(f"{base}/events?source_id={source_id}", headers=owner_headers)
    assert listed_events.status_code == 200, listed_events.text
    assert listed_events.json()["total"] == 2
    current_listed_event = next(
        item for item in listed_events.json()["items"] if item["id"] == first["id"]
    )
    assert current_listed_event["is_current"] is True
    assert current_listed_event["version"]["id"] == second["version"]["id"]
    assert current_listed_event["version"]["occurred_at"].startswith("2026-11-20T18:00:00")

    analysis_body = {
        "source_id": source_id,
        "start_at": "2026-11-20T00:00:00Z",
        "end_at": "2026-11-21T00:00:00Z",
    }
    analysis_url = f"{base}/analyses"
    analysis = client.post(
        analysis_url,
        json=analysis_body,
        headers={**owner_headers, "Idempotency-Key": "operations-analysis-idem-0001"},
    )
    assert analysis.status_code == 201, analysis.text
    result = analysis.json()
    assert result["input_count"] == 1
    assert result["result"]["status"] == "observations_available"
    assert result["result"]["observed_event_count"] == 1
    assert result["result"]["event_type_counts"] == [{"label": "completed", "count": 1}]
    assert result["result"]["state_before_counts"] == [{"label": "in-progress", "count": 1}]
    assert result["result"]["state_after_counts"] == [{"label": "complete", "count": 1}]
    assert result["result"]["source_authority"] == "not_assessed"
    assert result["result"]["source_completeness"] == "not_assessed"
    assert result["result"]["source_freshness"] == "not_assessed"
    assert result["result"]["hypothesis"] == "not_produced"
    assert result["result"]["forecast"] == "not_produced"
    assert result["result"]["recommendation"] == "not_produced"
    assert result["result"]["human_decision"] == "not_produced"

    replayed_analysis = client.post(
        analysis_url,
        json=analysis_body,
        headers={**owner_headers, "Idempotency-Key": "operations-analysis-idem-0001"},
    )
    assert replayed_analysis.status_code == 201, replayed_analysis.text
    assert replayed_analysis.json()["id"] == result["id"]
    assert replayed_analysis.json()["replayed"] is True

    detail = client.get(f"{base}/analyses/{result['id']}", headers=owner_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["integrity_verified"] is True
    assert detail.json()["result_hash"] == result["result_hash"]
    evidence = client.get(f"{base}/analyses/{result['id']}/evidence", headers=owner_headers)
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 1
    assert evidence.json()["items"][0]["event_id"] == first["id"]
    assert evidence.json()["items"][0]["event_version_id"] == second["version"]["id"]
    assert evidence.json()["items"][0]["version"] == 2

    with admin_engine.begin() as connection:
        _assert_operations_append_only(
            connection,
            "UPDATE operations_event_streams SET source_event_key = 'tampered' "
            "WHERE id = CAST(:row_id AS uuid)",
            {"row_id": first["id"]},
        )
        _assert_operations_append_only(
            connection,
            "UPDATE operations_event_versions SET event_type = 'tampered' "
            "WHERE id = CAST(:row_id AS uuid)",
            {"row_id": second["version"]["id"]},
        )
        _assert_operations_append_only(
            connection,
            "DELETE FROM operations_event_versions WHERE id = CAST(:row_id AS uuid)",
            {"row_id": second["version"]["id"]},
        )
        _assert_operations_append_only(
            connection,
            "UPDATE operations_analysis_runs SET result_snapshot = '{}'::jsonb "
            "WHERE id = CAST(:row_id AS uuid)",
            {"row_id": result["id"]},
        )
        _assert_operations_append_only(
            connection,
            "DELETE FROM operations_analysis_runs WHERE id = CAST(:row_id AS uuid)",
            {"row_id": result["id"]},
        )

    empty = client.post(
        analysis_url,
        json={
            "source_id": source_id,
            "start_at": "2026-11-22T00:00:00Z",
            "end_at": "2026-11-23T00:00:00Z",
        },
        headers={**owner_headers, "Idempotency-Key": "operations-analysis-idem-0002"},
    )
    assert empty.status_code == 201, empty.text
    assert empty.json()["result"]["status"] == "no_matching_evidence"
    assert empty.json()["result"]["observed_event_count"] == 0
    assert empty.json()["result"]["event_type_counts"] == []

    member_email = "operations-core-member@example.com"
    member = register_user(client, member_email)
    create_member(client, owner, tenant_id, member_email, role="member")
    member_headers = auth_headers(member)
    assert client.get(f"{base}/events", headers=member_headers).status_code == 403
    denied_write = client.post(
        event_url,
        json=_event_body(source_id, "member-event-1"),
        headers={**member_headers, "Idempotency-Key": "operations-member-idem-0001"},
    )
    assert denied_write.status_code == 403

    admin_email = "operations-core-admin@example.com"
    admin = register_user(client, admin_email)
    create_member(client, owner, tenant_id, admin_email, role="admin")
    admin_headers = auth_headers(admin)
    assert client.get(f"{base}/events", headers=admin_headers).status_code == 200
    admin_write = client.post(
        event_url,
        json=_event_body(source_id, "admin-event-1"),
        headers={**admin_headers, "Idempotency-Key": "operations-admin-idem-0001"},
    )
    assert admin_write.status_code == 201, admin_write.text


def test_operations_can_link_only_an_exact_same_source_operational_revision(
    client: TestClient, admin_engine
):
    owner = register_user(client, "operations-link-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Link Tenant",
        company_name="Operations Link Company",
        key="operations-link-tenant-0001",
    )
    tenant_id = tenant["id"]
    universal_base = _universal_base(tenant_id)
    source_one = _register_source(
        client,
        tenant_id,
        owner_headers,
        "operations.csv.primary",
        source_type="csv",
        authority_classification="unclassified",
    )
    source_two = _register_source(
        client,
        tenant_id,
        owner_headers,
        "operations.csv.secondary",
        source_type="csv",
        authority_classification="unclassified",
    )
    source_one_id = str(source_one["source_id"])
    source_two_id = str(source_two["source_id"])
    invalid_manual_assertion = client.post(
        f"{_operations_base(tenant_id)}/events",
        json=_event_body(source_two_id, "operations-csv-manual-event-1"),
        headers={**owner_headers, "Idempotency-Key": "operations-csv-manual-0001"},
    )
    assert invalid_manual_assertion.status_code == 422
    assert invalid_manual_assertion.json()["error"]["code"] == "manual_assertion_source_required"

    staged = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions",
        files={
            "file": (
                "activities.csv",
                b"activity_reference,activity_date,activity_type,description,status\n"
                b"ACT-17,2026-11-20,dispatch_recorded,Synthetic test row,recorded\n",
                "text/csv",
            )
        },
        headers={**owner_headers, "Idempotency-Key": "operations-link-ingestion-0001"},
    )
    assert staged.status_code == 201, staged.text
    ingestion_id = staged.json()["ingestion"]["id"]
    mapping = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_id}/mapping",
        json={
            "profile_key": "operations.activity.mapping",
            "display_name": "Operations activity mapping",
            "entity_type": "business_activity",
            "worksheet_name": "CSV",
            "field_mapping": {
                "activity_reference": "activity_reference",
                "activity_date": "activity_date",
                "activity_type": "activity_type",
                "description": "description",
                "status": "status",
            },
            "ignored_columns": [],
            "field_options": {},
        },
        headers=owner_headers,
    )
    assert mapping.status_code == 200, mapping.text
    validation = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validation.status_code == 200, validation.text
    promoted = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    records = client.get(
        f"{universal_base}/records?entity_type=business_activity&source_id={source_one_id}",
        headers=owner_headers,
    )
    assert records.status_code == 200, records.text
    record = records.json()["items"][0]

    linked_body = {
        "source_id": source_one_id,
        "source_event_key": "operations-source-event-17",
        "occurred_at": "2026-11-20T12:30:00Z",
        "event_type": "dispatch_observed",
        "source_subject_reference": "ACT-17",
        "evidence_kind": "canonical_record_revision",
        "canonical_record_id": record["id"],
        "canonical_record_revision": record["revision"],
    }
    linked = client.post(
        f"{_operations_base(tenant_id)}/events",
        json=linked_body,
        headers={**owner_headers, "Idempotency-Key": "operations-link-event-0001"},
    )
    assert linked.status_code == 201, linked.text
    linked_version = linked.json()["version"]
    assert linked_version["evidence_kind"] == "canonical_record_revision"
    assert linked_version["canonical_record_id"] == record["id"]
    assert linked_version["canonical_record_revision"] == record["revision"]
    assert linked_version["canonical_attributes_hash"] == record["attributes_hash"]

    changed_content = (
        b"activity_reference,activity_date,activity_type,description,status\n"
        b"ACT-17,2026-11-20,dispatch_recorded,Reviewed revision two,recorded\n"
    )
    staged_revision_two = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions",
        files={"file": ("activities-v2.csv", changed_content, "text/csv")},
        headers={**owner_headers, "Idempotency-Key": "operations-link-ingestion-0002"},
    )
    assert staged_revision_two.status_code == 201, staged_revision_two.text
    ingestion_two_id = staged_revision_two.json()["ingestion"]["id"]
    applied_mapping = client.put(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_two_id}/mapping",
        json={"mapping_profile_version_id": mapping.json()["id"]},
        headers=owner_headers,
    )
    assert applied_mapping.status_code == 200, applied_mapping.text
    validation_two = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_two_id}/validate",
        headers=owner_headers,
    )
    assert validation_two.status_code == 200, validation_two.text
    assert validation_two.json()["ingestion"]["status"] == "review_required"
    promoted_two = client.post(
        f"{universal_base}/sources/{source_one_id}/ingestions/{ingestion_two_id}/promote",
        json={
            "acknowledge_warnings": True,
            "correction_reason": "Reviewed synthetic operational record update.",
        },
        headers=owner_headers,
    )
    assert promoted_two.status_code == 200, promoted_two.text
    assert promoted_two.json()["revised_records"] == 1

    revision_delete_error: DBAPIError | None = None
    with admin_engine.begin() as connection:
        # Bypass the append-only guard and an unrelated Universal Data link FK so
        # this check specifically exercises the Operations evidence FK.
        connection.exec_driver_sql(
            "ALTER TABLE canonical_business_record_revisions "
            "DISABLE TRIGGER trg_canonical_business_record_revisions_append_only"
        )
        unrelated_delete_trigger = connection.scalar(
            text(
                "SELECT fk_trigger.tgname FROM pg_trigger AS fk_trigger "
                "JOIN pg_constraint AS fk ON fk.oid = fk_trigger.tgconstraint "
                "WHERE fk.conname = 'fk_udf_record_link_revision' "
                "AND fk_trigger.tgrelid = "
                "'public.canonical_business_record_revisions'::regclass "
                "AND (fk_trigger.tgtype & 8) <> 0"
            )
        )
        assert unrelated_delete_trigger is not None
        quoted_trigger = connection.dialect.identifier_preparer.quote(unrelated_delete_trigger)
        connection.exec_driver_sql(
            "ALTER TABLE public.canonical_business_record_revisions "
            f"DISABLE TRIGGER {quoted_trigger}"
        )
        try:
            with connection.begin_nested():
                connection.execute(
                    text(
                        "DELETE FROM canonical_business_record_revisions "
                        "WHERE tenant_id = CAST(:tenant_id AS uuid) "
                        "AND company_id = CAST(:company_id AS uuid) "
                        "AND record_id = CAST(:record_id AS uuid) AND revision = 1"
                    ),
                    {
                        "tenant_id": tenant_id,
                        "company_id": tenant["company"]["id"],
                        "record_id": record["id"],
                    },
                )
        except DBAPIError as error:
            revision_delete_error = error
        else:
            raise AssertionError("The linked canonical revision must be protected from deletion.")
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE public.canonical_business_record_revisions "
                f"ENABLE TRIGGER {quoted_trigger}"
            )
            connection.exec_driver_sql(
                "ALTER TABLE canonical_business_record_revisions "
                "ENABLE TRIGGER trg_canonical_business_record_revisions_append_only"
            )
    assert revision_delete_error is not None
    assert getattr(revision_delete_error.orig.diag, "constraint_name", None) == (
        "fk_operations_event_version_canonical_revision"
    )

    cross_source = client.post(
        f"{_operations_base(tenant_id)}/events",
        json={
            **linked_body,
            "source_id": source_two["source_id"],
            "source_event_key": "operations-cross-source-event-1",
        },
        headers={**owner_headers, "Idempotency-Key": "operations-link-event-0002"},
    )
    assert cross_source.status_code == 422
    assert cross_source.json()["error"]["code"] == "canonical_revision_not_eligible"

    retention_admin_email = "operations-link-retention-admin@example.com"
    retention_admin = register_user(client, retention_admin_email)
    create_member(client, owner, tenant_id, retention_admin_email, role="admin")
    retention_admin_headers = auth_headers(retention_admin)
    # Synthetic policy solely to exercise Universal Data's existing dependency gate; it is not an
    # Operations retention duration or a business-policy decision.
    configured_policy = client.post(
        f"{universal_base}/retention/policies",
        json={"retention_days": 1},
        headers=owner_headers,
    )
    assert configured_policy.status_code == 201, configured_policy.text
    approved_policy = client.post(
        f"{universal_base}/retention/policies/"
        f"{configured_policy.json()['policy_version_id']}/approve",
        headers=retention_admin_headers,
    )
    assert approved_policy.status_code == 200, approved_policy.text
    revoked_source = client.post(
        f"{universal_base}/sources/{source_one_id}/revoke",
        json={"reason_code": "source_replaced"},
        headers=owner_headers,
    )
    assert revoked_source.status_code == 200, revoked_source.text

    aged_at = datetime.now(UTC) - timedelta(days=2)
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE public.universal_ingestions DISABLE TRIGGER trg_universal_ingestions_guard"
        )
        try:
            connection.execute(
                text(
                    "UPDATE public.universal_ingestions "
                    "SET created_at = :created_at, updated_at = :created_at "
                    "WHERE id IN (CAST(:first_id AS uuid), CAST(:second_id AS uuid))"
                ),
                {
                    "created_at": aged_at,
                    "first_id": ingestion_id,
                    "second_id": ingestion_two_id,
                },
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE public.universal_ingestions "
                "ENABLE TRIGGER trg_universal_ingestions_guard"
            )

    purge_plan = client.get(
        f"{universal_base}/sources/{source_one_id}/purge-plan?limit=100&offset=0",
        headers=owner_headers,
    )
    assert purge_plan.status_code == 200, purge_plan.text
    assert purge_plan.json()["purge_enabled"] is True
    assert purge_plan.json()["total_eligible"] == 0
    assert purge_plan.json()["total_blocked"] == 2
    assert all(
        "canonical_record_revisions" in item["blockers"]
        for item in purge_plan.json()["blocked_ingestions"]
    )
    refused_purge = client.post(
        f"{universal_base}/sources/{source_one_id}/purge",
        json={
            "policy_version_id": configured_policy.json()["policy_version_id"],
            "plan_limit": 100,
            "plan_offset": 0,
            "ingestion_ids": [ingestion_id, ingestion_two_id],
        },
        headers=owner_headers,
    )
    assert refused_purge.status_code == 409
    assert refused_purge.json()["error"]["code"] == "universal_purge_plan_stale"
    retained_event = client.get(
        f"{_operations_base(tenant_id)}/events/{linked.json()['id']}",
        headers=owner_headers,
    )
    assert retained_event.status_code == 200, retained_event.text
    assert retained_event.json()["version"]["canonical_record_revision"] == record["revision"]


def test_operations_tables_have_forced_rls_and_restricted_runtime_grants(admin_engine):
    table_names = [
        "operations_event_streams",
        "operations_event_versions",
        "operations_event_current",
        "operations_analysis_runs",
    ]
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT relation.relname, relation.relrowsecurity, relation.relforcerowsecurity, "
                "owner.rolname AS owner "
                "FROM pg_class AS relation "
                "JOIN pg_roles AS owner ON owner.oid = relation.relowner "
                "WHERE relation.relname = ANY(CAST(:table_names AS text[])) "
                "ORDER BY relation.relname"
            ),
            {"table_names": table_names},
        ).all()
        grants = connection.execute(
            text(
                "SELECT table_name, "
                "has_table_privilege('bf_runtime', table_name, 'SELECT') AS can_select, "
                "has_table_privilege('bf_runtime', table_name, 'INSERT') AS can_insert, "
                "has_table_privilege('bf_runtime', table_name, 'UPDATE') AS can_update, "
                "has_table_privilege('bf_runtime', table_name, 'DELETE') AS can_delete, "
                "has_table_privilege('bf_runtime', table_name, 'TRUNCATE') AS can_truncate "
                "FROM unnest(CAST(:table_names AS text[])) AS scoped_table(table_name)"
            ),
            {"table_names": table_names},
        ).all()
        current_column_updates = connection.execute(
            text(
                "SELECT has_column_privilege('bf_runtime', "
                "'public.operations_event_current', 'event_version_id', 'UPDATE') "
                "AS can_update_version, "
                "has_column_privilege('bf_runtime', "
                "'public.operations_event_current', 'occurred_at', 'UPDATE') "
                "AS can_update_time, "
                "has_column_privilege('bf_runtime', "
                "'public.operations_event_current', 'event_id', 'UPDATE') "
                "AS can_update_identity"
            )
        ).one()
        canonical_fk = connection.scalar(
            text(
                "SELECT confdeltype FROM pg_constraint "
                "WHERE conname = 'fk_operations_event_version_canonical_revision'"
            )
        )

    assert len(security) == len(table_names)
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert len(grants) == len(table_names)
    assert all(row.can_select and row.can_insert for row in grants)
    assert all(not row.can_update and not row.can_delete and not row.can_truncate for row in grants)
    assert current_column_updates.can_update_version is True
    assert current_column_updates.can_update_time is True
    assert current_column_updates.can_update_identity is False
    # Linked event evidence intentionally prevents Universal Data purge from deleting its revision.
    assert canonical_fk == "r"


def test_0029_downgrade_refuses_retained_operations_evidence_under_forced_rls(
    client: TestClient, migration_engine, admin_engine
):
    owner = register_user(client, "operations-downgrade-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Downgrade Tenant",
        company_name="Operations Downgrade Company",
        key="operations-downgrade-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.downgrade-retained")
    created = client.post(
        f"{_operations_base(tenant['id'])}/events",
        json=_event_body(str(source["source_id"]), "retained-evidence-event-0001"),
        headers={**owner_headers, "Idempotency-Key": "operations-downgrade-idem-0001"},
    )
    assert created.status_code == 201, created.text

    with pytest.raises(
        DBAPIError,
        match="refusing to discard Operations Intelligence evidence during downgrade",
    ):
        invoke_downgrade_function(migration_engine, "0029_operations_intelligence.py")

    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0029_operations_intelligence"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM operations_event_streams "
                    "WHERE id = CAST(:event_id AS uuid)"
                ),
                {"event_id": created.json()["id"]},
            )
            == 1
        )
        for table_name in (
            "operations_event_streams",
            "operations_event_versions",
            "operations_event_current",
            "operations_analysis_runs",
        ):
            assert (
                connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity "
                        "FROM pg_class WHERE oid = to_regclass(:table_name)"
                    ),
                    {"table_name": f"public.{table_name}"},
                )
                is True
            )


def test_operations_event_verification_rejects_a_tampered_ancestor(
    client: TestClient, admin_engine
):
    owner = register_user(client, "operations-chain-integrity-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Chain Integrity Tenant",
        company_name="Operations Chain Integrity Company",
        key="operations-chain-integrity-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.chain-integrity")
    base = _operations_base(tenant["id"])
    created = client.post(
        f"{base}/events",
        json=_event_body(str(source["source_id"]), "chain-root-event-0001"),
        headers={**owner_headers, "Idempotency-Key": "operations-chain-root-idem-0001"},
    )
    assert created.status_code == 201, created.text

    for version in (2, 3):
        correction_body = _event_body(
            str(source["source_id"]),
            f"unused-correction-key-{version}",
            event_type=f"corrected-{version}",
        )
        correction_body.pop("source_id")
        correction_body.pop("source_event_key")
        correction_body["expected_version"] = version - 1
        correction_body["correction_reason"] = f"Synthetic correction test version {version}."
        correction = client.post(
            f"{base}/events/{created.json()['id']}/corrections",
            json=correction_body,
            headers={
                **owner_headers,
                "Idempotency-Key": f"operations-chain-correction-{version:04d}",
            },
        )
        assert correction.status_code == 201, correction.text
        assert correction.json()["version"]["version"] == version

    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE public.operations_event_versions "
            "DISABLE TRIGGER trg_operations_event_versions_append_only"
        )
        try:
            connection.execute(
                text(
                    "UPDATE public.operations_event_versions SET event_type = 'tampered' "
                    "WHERE id = CAST(:version_id AS uuid) "
                    "AND event_id = CAST(:event_id AS uuid)"
                ),
                {
                    "version_id": created.json()["version"]["id"],
                    "event_id": created.json()["id"],
                },
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE public.operations_event_versions "
                "ENABLE TRIGGER trg_operations_event_versions_append_only"
            )

    detail = client.get(
        f"{base}/events/{created.json()['id']}",
        headers=owner_headers,
    )
    assert detail.status_code == 409
    assert detail.json()["error"]["code"] == "operations_event_integrity_mismatch"


def test_operations_event_verification_rejects_a_tampered_current_pointer_timestamp(
    client: TestClient, admin_engine
):
    owner = register_user(client, "operations-pointer-integrity-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Pointer Integrity Tenant",
        company_name="Operations Pointer Integrity Company",
        key="operations-pointer-integrity-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.pointer-integrity")
    base = _operations_base(tenant["id"])
    created = client.post(
        f"{base}/events",
        json=_event_body(str(source["source_id"]), "pointer-time-event-0001"),
        headers={**owner_headers, "Idempotency-Key": "operations-pointer-time-idem-0001"},
    )
    assert created.status_code == 201, created.text

    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE public.operations_event_current "
            "DISABLE TRIGGER trg_operations_event_current_guard"
        )
        try:
            connection.execute(
                text(
                    "UPDATE public.operations_event_current "
                    "SET occurred_at = occurred_at + INTERVAL '1 hour' "
                    "WHERE event_id = CAST(:event_id AS uuid)"
                ),
                {"event_id": created.json()["id"]},
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE public.operations_event_current "
                "ENABLE TRIGGER trg_operations_event_current_guard"
            )

    detail = client.get(f"{base}/events/{created.json()['id']}", headers=owner_headers)
    assert detail.status_code == 409
    assert detail.json()["error"]["code"] == "operations_event_integrity_mismatch"
    analysis = client.post(
        f"{base}/analyses",
        json={
            "source_id": source["source_id"],
            "start_at": "2026-11-20T00:00:00Z",
            "end_at": "2026-11-21T00:00:00Z",
        },
        headers={**owner_headers, "Idempotency-Key": "operations-pointer-time-analysis-0001"},
    )
    assert analysis.status_code == 409
    assert analysis.json()["error"]["code"] == "operations_event_integrity_mismatch"


def test_concurrent_operations_corrections_serialize_on_the_source_lock(client: TestClient):
    owner = register_user(client, "operations-concurrency-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Concurrency Tenant",
        company_name="Operations Concurrency Company",
        key="operations-concurrency-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.concurrent")
    base = _operations_base(tenant["id"])
    created = client.post(
        f"{base}/events",
        json=_event_body(str(source["source_id"]), "concurrent-event-0001"),
        headers={**owner_headers, "Idempotency-Key": "operations-concurrent-create-0001"},
    )
    assert created.status_code == 201, created.text
    event_id = created.json()["id"]
    barrier = Barrier(2)

    def submit_correction(label: str):
        correction_body = _event_body(
            str(source["source_id"]),
            f"unused-concurrent-correction-{label}",
            event_type=f"completed-{label}",
        )
        correction_body.pop("source_id")
        correction_body.pop("source_event_key")
        correction_body["expected_version"] = 1
        correction_body["correction_reason"] = f"Synthetic concurrent correction {label}."
        barrier.wait(timeout=10)
        return client.post(
            f"{base}/events/{event_id}/corrections",
            json=correction_body,
            headers={
                **owner_headers,
                "Idempotency-Key": f"operations-concurrent-correction-{label}",
            },
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit_correction, ("alpha", "bravo")))

    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "operations_event_version_conflict"
    history = client.get(f"{base}/events/{event_id}/versions", headers=owner_headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert [item["version"] for item in reversed(history.json()["items"])] == [1, 2]


def test_operations_event_history_locks_current_pointer_before_version_reads(
    client: TestClient,
):
    owner = register_user(client, "operations-history-lock-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations History Lock Tenant",
        company_name="Operations History Lock Company",
        key="operations-history-lock-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.history-lock")
    base = _operations_base(tenant["id"])
    created = client.post(
        f"{base}/events",
        json=_event_body(str(source["source_id"]), "history-lock-event-0001"),
        headers={**owner_headers, "Idempotency-Key": "operations-history-lock-create-0001"},
    )
    assert created.status_code == 201, created.text

    version_selects: list[str] = []

    def capture_version_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        normalized = statement.lstrip().lower()
        if normalized.startswith("select") and "operations_event_versions" in normalized:
            version_selects.append(normalized)

    engine = client.app.state.engine
    sqlalchemy_event.listen(engine, "before_cursor_execute", capture_version_selects)
    try:
        history = client.get(
            f"{base}/events/{created.json()['id']}/versions", headers=owner_headers
        )
    finally:
        sqlalchemy_event.remove(engine, "before_cursor_execute", capture_version_selects)

    assert history.status_code == 200, history.text
    assert version_selects
    assert "for update" in version_selects[0]
    assert "operations_event_current" in version_selects[0]
    assert any("for update" not in statement for statement in version_selects[1:])


def test_operations_analysis_uses_bounded_queries_for_corrected_event_batch(
    client: TestClient,
):
    owner = register_user(client, "operations-performance-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Performance Tenant",
        company_name="Operations Performance Company",
        key="operations-performance-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.performance")
    source_id = str(source["source_id"])
    base = _operations_base(tenant["id"])
    event_count = 12

    for index in range(event_count):
        created = client.post(
            f"{base}/events",
            json=_event_body(source_id, f"performance-event-{index:03d}"),
            headers={
                **owner_headers,
                "Idempotency-Key": f"operations-performance-create-{index:03d}",
            },
        )
        assert created.status_code == 201, created.text
        correction_body = _event_body(
            source_id,
            f"unused-performance-correction-{index:03d}",
            event_type=f"observed-{index:03d}",
        )
        correction_body.pop("source_id")
        correction_body.pop("source_event_key")
        correction_body["expected_version"] = 1
        correction_body["correction_reason"] = "Synthetic performance-chain correction."
        correction = client.post(
            f"{base}/events/{created.json()['id']}/corrections",
            json=correction_body,
            headers={
                **owner_headers,
                "Idempotency-Key": f"operations-performance-correction-{index:03d}",
            },
        )
        assert correction.status_code == 201, correction.text

    version_selects: list[str] = []

    def count_version_selects(
        _connection, _cursor, statement, _parameters, _context, _executemany
    ) -> None:
        normalized = statement.lstrip().lower()
        if normalized.startswith("select") and "operations_event_versions" in normalized:
            version_selects.append(statement)

    engine = client.app.state.engine
    sqlalchemy_event.listen(engine, "before_cursor_execute", count_version_selects)
    started_at = perf_counter()
    try:
        analysis = client.post(
            f"{base}/analyses",
            json={
                "source_id": source_id,
                "start_at": "2026-11-20T00:00:00Z",
                "end_at": "2026-11-21T00:00:00Z",
            },
            headers={
                **owner_headers,
                "Idempotency-Key": "operations-performance-analysis-0001",
            },
        )
    finally:
        elapsed_ms = (perf_counter() - started_at) * 1000
        sqlalchemy_event.remove(engine, "before_cursor_execute", count_version_selects)

    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["input_count"] == event_count
    assert len(version_selects) <= 2
    assert elapsed_ms > 0


def test_operations_analysis_fails_closed_above_its_configured_input_bound(
    client: TestClient, admin_engine, monkeypatch
):
    # Lower only the service bound so the production fail-closed branch is exercised with two
    # synthetic events instead of creating 501 fixture rows.
    monkeypatch.setattr(operations_service, "MAX_ANALYSIS_EVENTS", 1)
    owner = register_user(client, "operations-input-limit-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Input Limit Tenant",
        company_name="Operations Input Limit Company",
        key="operations-input-limit-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.input-limit")
    source_id = str(source["source_id"])
    base = _operations_base(tenant["id"])
    for index in range(2):
        created = client.post(
            f"{base}/events",
            json=_event_body(source_id, f"input-limit-event-{index:04d}"),
            headers={
                **owner_headers,
                "Idempotency-Key": f"operations-input-limit-event-{index:04d}",
            },
        )
        assert created.status_code == 201, created.text

    analysis = client.post(
        f"{base}/analyses",
        json={
            "source_id": source_id,
            "start_at": "2026-11-20T00:00:00Z",
            "end_at": "2026-11-21T00:00:00Z",
        },
        headers={
            **owner_headers,
            "Idempotency-Key": "operations-input-limit-analysis-0001",
        },
    )
    assert analysis.status_code == 422
    assert analysis.json()["error"]["code"] == "operations_analysis_input_limit_exceeded"
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM public.operations_analysis_runs "
                    "WHERE source_id = CAST(:source_id AS uuid)"
                ),
                {"source_id": source_id},
            )
            == 0
        )


def test_operations_analysis_detects_tampered_saved_input_and_result(
    client: TestClient, admin_engine
):
    owner = register_user(client, "operations-analysis-integrity-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Analysis Integrity Tenant",
        company_name="Operations Analysis Integrity Company",
        key="operations-analysis-integrity-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.analysis-integrity")
    base = _operations_base(tenant["id"])
    request_body = {
        "source_id": source["source_id"],
        "start_at": "2026-11-20T00:00:00Z",
        "end_at": "2026-11-21T00:00:00Z",
    }
    input_run = client.post(
        f"{base}/analyses",
        json=request_body,
        headers={
            **owner_headers,
            "Idempotency-Key": "operations-analysis-input-hash-0001",
        },
    )
    assert input_run.status_code == 201, input_run.text
    result_run = client.post(
        f"{base}/analyses",
        json=request_body,
        headers={
            **owner_headers,
            "Idempotency-Key": "operations-analysis-result-hash-0001",
        },
    )
    assert result_run.status_code == 201, result_run.text

    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE public.operations_analysis_runs "
            "DISABLE TRIGGER trg_operations_analysis_runs_append_only"
        )
        try:
            connection.execute(
                text(
                    "UPDATE public.operations_analysis_runs "
                    "SET input_snapshot = jsonb_set(input_snapshot, "
                    "'{period,interval}', to_jsonb('tampered'::text)) "
                    "WHERE id = CAST(:run_id AS uuid)"
                ),
                {"run_id": input_run.json()["id"]},
            )
            connection.execute(
                text(
                    "UPDATE public.operations_analysis_runs "
                    "SET result_snapshot = jsonb_set(result_snapshot, "
                    "'{interpretation}', to_jsonb('unsupported'::text)) "
                    "WHERE id = CAST(:run_id AS uuid)"
                ),
                {"run_id": result_run.json()["id"]},
            )
        finally:
            connection.exec_driver_sql(
                "ALTER TABLE public.operations_analysis_runs "
                "ENABLE TRIGGER trg_operations_analysis_runs_append_only"
            )

    for run in (input_run, result_run):
        detail = client.get(f"{base}/analyses/{run.json()['id']}", headers=owner_headers)
        assert detail.status_code == 409
        assert detail.json()["error"]["code"] == "operations_analysis_integrity_mismatch"


def test_0029_analysis_only_retained_evidence_blocks_downgrade(
    client: TestClient, migration_engine, admin_engine
):
    # This destructive cleanup is limited to the uniquely named disposable test database. It
    # isolates the analysis-run row so the downgrade guard cannot pass because of event evidence.
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            "TRUNCATE TABLE public.operations_event_current, "
            "public.operations_event_versions, public.operations_event_streams, "
            "public.operations_analysis_runs"
        )

    owner = register_user(client, "operations-analysis-retention-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Operations Analysis Retention Tenant",
        company_name="Operations Analysis Retention Company",
        key="operations-analysis-retention-tenant-0001",
    )
    source = _register_source(client, tenant["id"], owner_headers, "operations.analysis-retention")
    base = _operations_base(tenant["id"])
    analysis = client.post(
        f"{base}/analyses",
        json={
            "source_id": source["source_id"],
            "start_at": "2026-11-20T00:00:00Z",
            "end_at": "2026-11-21T00:00:00Z",
        },
        headers={
            **owner_headers,
            "Idempotency-Key": "operations-analysis-retention-0001",
        },
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["input_count"] == 0

    with pytest.raises(
        DBAPIError,
        match="refusing to discard Operations Intelligence evidence during downgrade",
    ):
        invoke_downgrade_function(migration_engine, "0029_operations_intelligence.py")

    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0029_operations_intelligence"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM public.operations_analysis_runs "
                    "WHERE id = CAST(:run_id AS uuid)"
                ),
                {"run_id": analysis.json()["id"]},
            )
            == 1
        )
        for table_name in (
            "operations_event_streams",
            "operations_event_versions",
            "operations_event_current",
            "operations_analysis_runs",
        ):
            assert (
                connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity "
                        "FROM pg_class WHERE oid = to_regclass(:table_name)"
                    ),
                    {"table_name": f"public.{table_name}"},
                )
                is True
            )
        assert connection.scalar(text("SELECT count(*) FROM public.operations_event_streams")) == 0
        assert connection.scalar(text("SELECT count(*) FROM public.operations_event_versions")) == 0
        assert connection.scalar(text("SELECT count(*) FROM public.operations_event_current")) == 0
