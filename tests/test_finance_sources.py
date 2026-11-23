from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

from businessflow.metrics.snapshot import content_hash
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
    return f"/api/v1/tenants/{tenant_id}/finance-sources"


def _upload(
    client,
    tenant_id: str,
    headers: dict[str, str],
    idempotency_key: str,
    *,
    amount: str = "10.10",
    source_as_of_at: str | None = None,
    ignored_rationale: str | None = None,
):
    metadata = {
        "source_key": "manual-ledger-export",
        "source_as_of_at": source_as_of_at or datetime.now(UTC).isoformat(),
        "freshness_max_age_hours": 24,
        "field_mapping": FIELD_MAPPING,
        "ignored_columns": (
            []
            if ignored_rationale is None
            else [{"header": "memo", "rationale": ignored_rationale}]
        ),
        "controls": [
            {
                "control_id": "test-control-001",
                "reconciliation_key": "opaque-group-a",
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency": "USD",
                "expected_amount": "10.12",
                "absolute_tolerance": "0",
                "relative_tolerance": "0",
                "source": {
                    "source_id": "independent-control-register",
                    "source_hash": "f" * 64,
                    "record_locator": "control:row:1",
                },
            }
        ],
    }
    extra_header = ",memo" if ignored_rationale is not None else ""
    extra_value = ",manual note" if ignored_rationale is not None else ""
    content = (
        f"record,bucket,from,to,amount,currency{extra_header}\n"
        f"record-001,opaque-group-a,2026-11-01,2026-11-30,{amount},USD{extra_value}\n"
        f"record-002,opaque-group-a,2026-11-01,2026-11-30,0.02,USD{extra_value}\n"
    ).encode()
    return client.post(
        f"{_base(tenant_id)}/intakes",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("finance-export.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": idempotency_key},
    )


def test_finance_source_api_contract_intake_mapping_and_idempotent_readback(client):
    owner = register_user(client, "m14-api-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M14 API Tenant",
        company_name="M14 API Company",
        key="m14-api-tenant-0001",
    )
    base = _base(tenant["id"])

    contract_response = client.get(f"{base}/contracts", headers=headers)
    assert contract_response.status_code == 200, contract_response.text
    contracts = contract_response.json()["items"]
    assert len(contracts) == 1
    contract = contracts[0]
    assert contract["contract_key"] == "businessflow.finance.source-observations"
    assert contract["version"] == "1.0.0"
    assert len(contract["definition_hash"]) == 64
    assert contract["definition"]["dataset_type"] == "finance_source_observations"

    idempotency_key = f"m14-intake-{uuid4().hex}"
    source_as_of_at = datetime.now(UTC).isoformat()
    response = _upload(
        client,
        tenant["id"],
        headers,
        idempotency_key,
        source_as_of_at=source_as_of_at,
    )
    assert response.status_code == 201, response.text
    intake = response.json()
    assert intake["replayed"] is False
    assert intake["source_key"] == "manual-ledger-export"
    assert intake["contract_hash"] == contract["definition_hash"]
    assert intake["mapping_version"] == 1
    assert len(intake["mapping_hash"]) == 64
    assert intake["freshness_status"] == "fresh"
    assert intake["quality_status"] == "passed"
    assert intake["reconciliation_status"] == "passed"
    assert intake["total_rows"] == 2
    assert intake["valid_rows"] == 2
    assert intake["invalid_rows"] == 0
    assert intake["duplicate_rows"] == 0
    assert intake["input_hash"] == content_hash(intake["input_snapshot"])
    assert intake["result_hash"] == content_hash(intake["result_snapshot"])
    assert intake["input_snapshot"]["mapping"]["field_mapping"] == FIELD_MAPPING
    assert intake["result_snapshot"]["reconciliation_groups"][0]["actual_amount"] == "10.12"
    assert intake["result_snapshot"]["reconciliation_groups"][0]["state"] == "passed"
    assert "asserted by the caller" in " ".join(intake["result_snapshot"]["limitations"])

    replay = _upload(
        client,
        tenant["id"],
        headers,
        idempotency_key,
        source_as_of_at=source_as_of_at,
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["id"] == intake["id"]
    assert replay.json()["replayed"] is True

    conflict = _upload(
        client,
        tenant["id"],
        headers,
        idempotency_key,
        amount="10.11",
        source_as_of_at=source_as_of_at,
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"

    listed = client.get(f"{base}/intakes?limit=1&offset=0", headers=headers)
    assert listed.status_code == 200, listed.text
    assert listed.json()["total"] == 1
    assert listed.json()["items"][0]["id"] == intake["id"]

    detail = client.get(f"{base}/intakes/{intake['id']}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["result_hash"] == intake["result_hash"]

    row_evidence = client.get(f"{base}/intakes/{intake['id']}/rows", headers=headers)
    assert row_evidence.status_code == 200, row_evidence.text
    assert row_evidence.json()["total"] == 2
    assert [item["source_row_number"] for item in row_evidence.json()["items"]] == [2, 3]
    assert all(item["row_status"] == "valid" for item in row_evidence.json()["items"])
    assert all(len(item["source_row_hash"]) == 64 for item in row_evidence.json()["items"])


def test_mapping_versions_reuse_identical_mappings_and_bind_ignore_rationales(client):
    owner = register_user(client, "m14-mapping-owner@example.com")
    tenant = create_tenant(
        client,
        owner,
        name="M14 Mapping Tenant",
        company_name="M14 Mapping Company",
        key="m14-mapping-tenant-0001",
    )
    headers = auth_headers(owner)
    first = _upload(
        client,
        tenant["id"],
        headers,
        f"m14-map-first-{uuid4().hex}",
        ignored_rationale="This note is outside the finance contract.",
    )
    assert first.status_code == 201, first.text
    first_intake = first.json()
    assert first_intake["mapping_version"] == 1
    assert first_intake["quality_status"] == "warning"
    assert first_intake["input_snapshot"]["mapping"]["ignored_columns"] == [
        {"header": "memo", "rationale": "This note is outside the finance contract."}
    ]

    identical = _upload(
        client,
        tenant["id"],
        headers,
        f"m14-map-identical-{uuid4().hex}",
        ignored_rationale="This note is outside the finance contract.",
    )
    assert identical.status_code == 201, identical.text
    assert identical.json()["mapping_version"] == 1
    assert identical.json()["mapping_hash"] == first_intake["mapping_hash"]

    changed = _upload(
        client,
        tenant["id"],
        headers,
        f"m14-map-changed-{uuid4().hex}",
        ignored_rationale="Retain only the profile count, not this value.",
    )
    assert changed.status_code == 201, changed.text
    assert changed.json()["mapping_version"] == 2
    assert changed.json()["mapping_hash"] != first_intake["mapping_hash"]


def test_finance_source_endpoints_require_finance_permission_and_isolate_tenants(client):
    owner = register_user(client, "m14-auth-owner@example.com")
    tenant = create_tenant(
        client,
        owner,
        name="M14 Auth Tenant",
        company_name="M14 Auth Company",
        key="m14-auth-tenant-0001",
    )
    base = _base(tenant["id"])
    owner_headers = auth_headers(owner)
    created = _upload(client, tenant["id"], owner_headers, f"m14-auth-{uuid4().hex}")
    assert created.status_code == 201, created.text

    member = register_user(client, "m14-auth-member@example.com")
    create_member(client, owner, tenant["id"], "m14-auth-member@example.com")
    member_headers = auth_headers(member)
    assert client.get(f"{base}/contracts", headers=member_headers).status_code == 403
    assert client.get(f"{base}/intakes", headers=member_headers).status_code == 403
    denied_write = _upload(
        client,
        tenant["id"],
        member_headers,
        f"m14-member-write-{uuid4().hex}",
    )
    assert denied_write.status_code == 403

    other_owner = register_user(client, "m14-other-owner@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="M14 Other Tenant",
        company_name="M14 Other Company",
        key="m14-other-tenant-0001",
    )
    cross_tenant = client.get(
        f"{_base(other_tenant['id'])}/intakes/{created.json()['id']}",
        headers=auth_headers(other_owner),
    )
    assert cross_tenant.status_code == 404
    not_a_member = client.get(
        f"{base}/intakes/{created.json()['id']}",
        headers=auth_headers(other_owner),
    )
    assert not_a_member.status_code == 404
