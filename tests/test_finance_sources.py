from __future__ import annotations

import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import event, text

from businessflow.api.finance_source_limits import finance_source_request_body_limit
from businessflow.config import Settings
from businessflow.main import create_app
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


def _oversized_multipart(boundary: bytes, request_limit: int, chunk_size: int) -> bytes:
    prefix = (
        b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="metadata_json"\r\n\r\n{}\r\n'
        + b"--"
        + boundary
        + b"\r\n"
        + b'Content-Disposition: form-data; name="file"; filename="large.csv"\r\n'
        + b"Content-Type: text/csv\r\n\r\n"
    )
    return prefix + (b"x" * (request_limit + 4 * chunk_size)) + b"\r\n--" + boundary + b"--\r\n"


def _raw_asgi_post(
    app,
    path: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes,
    chunk_size: int = 32 * 1024,
):
    async def invoke():
        chunks = [body[index : index + chunk_size] for index in range(0, len(body), chunk_size)]
        received_bytes = 0
        next_chunk = 0
        sent = []

        async def receive():
            nonlocal next_chunk, received_bytes
            if next_chunk >= len(chunks):
                return {"type": "http.disconnect"}
            chunk = chunks[next_chunk]
            next_chunk += 1
            received_bytes += len(chunk)
            return {
                "type": "http.request",
                "body": chunk,
                "more_body": next_chunk < len(chunks),
            }

        async def send(message):
            sent.append(message.copy())

        scope = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "state": {},
            "extensions": {},
        }
        await app(scope, receive, send)
        status = next(
            message["status"] for message in sent if message["type"] == "http.response.start"
        )
        response_body = b"".join(
            message.get("body", b"") for message in sent if message["type"] == "http.response.body"
        )
        return status, json.loads(response_body), received_bytes

    return asyncio.run(invoke())


def test_finance_source_api_contract_intake_mapping_and_idempotent_readback(client, settings):
    assert settings.finance_source_intake_enabled is True
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
    assert intake["reconciliation_method"] == "caller_supplied_decimal_arithmetic_only"
    assert intake["governance_status"] == {
        "finance_source_identity": "caller_asserted_not_independently_authenticated",
        "control_source_identity": "caller_asserted_not_independently_authenticated",
        "control_origin": "caller_supplied",
        "control_independence": "not_independently_verified",
        "finance_owner_approval": "not_established",
        "authoritative_source_access_approval": "not_established",
        "freshness_policy_approval": "not_established",
        "correction_policy_approval": "not_established",
        "retention_policy_approval": "not_established",
        "accounting_correctness": "not_established",
        "production_readiness": "not_established",
        "production_customer_data_authorization": "not_authorized",
    }
    assert intake["total_rows"] == 2
    assert intake["valid_rows"] == 2
    assert intake["invalid_rows"] == 0
    assert intake["duplicate_rows"] == 0
    assert intake["input_hash"] == content_hash(intake["input_snapshot"])
    assert intake["result_hash"] == content_hash(intake["result_snapshot"])
    assert intake["input_snapshot"]["mapping"]["field_mapping"] == FIELD_MAPPING
    comparison = intake["result_snapshot"]["reconciliation_groups"][0]
    assert comparison["actual_amount"] == "10.12"
    assert comparison["state"] == "passed"
    assert comparison["comparison_method"] == "caller_supplied_decimal_arithmetic_only"
    assert comparison["control_origin"] == "caller_supplied"
    assert intake["result_snapshot"]["governance_status"] == intake["governance_status"]
    assert "caller-supplied control amounts" in " ".join(intake["result_snapshot"]["limitations"])

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


def test_finance_source_api_is_default_disabled_for_every_route(client, settings, monkeypatch):
    monkeypatch.delenv("FINANCE_SOURCE_INTAKE_ENABLED", raising=False)
    disabled_settings = Settings(
        _env_file=None,
        app_secret_key=settings.app_secret_key.get_secret_value(),
        app_env="test",
        database_url=settings.database_url,
    )
    assert disabled_settings.finance_source_intake_enabled is False

    owner = register_user(client, "m14-default-closed-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M14 Default Closed Tenant",
        company_name="M14 Default Closed Company",
        key="m14-default-closed-tenant-0001",
    )
    created = _upload(client, tenant["id"], owner_headers, f"m14-enabled-fixture-{uuid4().hex}")
    assert created.status_code == 201, created.text
    intake_id = created.json()["id"]
    base = _base(tenant["id"])

    with TestClient(
        create_app(disabled_settings), raise_server_exceptions=False
    ) as disabled_client:
        responses = [
            disabled_client.get(f"{base}/contracts", headers=owner_headers),
            disabled_client.get(f"{base}/intakes", headers=owner_headers),
            disabled_client.get(f"{base}/intakes/{intake_id}", headers=owner_headers),
            disabled_client.get(f"{base}/intakes/{intake_id}/rows", headers=owner_headers),
            _upload(
                disabled_client,
                tenant["id"],
                owner_headers,
                f"m14-disabled-write-{uuid4().hex}",
            ),
        ]
        assert all(response.status_code == 503 for response in responses)
        assert all(
            response.json()["error"]["code"] == "finance_source_intake_disabled"
            for response in responses
        )
        assert disabled_client.get("/api/v1/auth/me", headers=owner_headers).status_code == 200


def test_failed_arithmetic_comparison_remains_caller_supplied_and_unapproved(client):
    owner = register_user(client, "m14-arithmetic-owner@example.com")
    tenant = create_tenant(
        client,
        owner,
        name="M14 Arithmetic Tenant",
        company_name="M14 Arithmetic Company",
        key="m14-arithmetic-tenant-0001",
    )
    response = _upload(
        client,
        tenant["id"],
        auth_headers(owner),
        f"m14-arithmetic-{uuid4().hex}",
        amount="10.11",
    )
    assert response.status_code == 201, response.text
    intake = response.json()
    assert intake["reconciliation_status"] == "failed"
    comparison = intake["result_snapshot"]["reconciliation_groups"][0]
    assert comparison["state"] == "failed"
    assert comparison["actual_amount"] == "10.13"
    assert comparison["comparison_method"] == "caller_supplied_decimal_arithmetic_only"
    assert comparison["control_origin"] == "caller_supplied"
    assert intake["governance_status"]["control_independence"] == "not_independently_verified"
    assert intake["governance_status"]["finance_owner_approval"] == "not_established"
    assert intake["governance_status"]["accounting_correctness"] == "not_established"
    assert intake["governance_status"]["production_customer_data_authorization"] == "not_authorized"


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


def test_concurrent_identical_finance_source_retries_are_idempotent(
    client, app, admin_engine, caplog
):
    owner = register_user(client, f"m14-race-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M14 Concurrent Retry Tenant",
        company_name="M14 Concurrent Retry Company",
        key=f"m14-race-tenant-{uuid4().hex}",
    )
    idempotency_key = f"m14-concurrent-{uuid4().hex}"
    source_as_of_at = datetime.now(UTC).isoformat()
    lock_barrier = Barrier(2)
    barrier_passes = []

    def synchronize_at_idempotency_lock(_conn, _cursor, statement, _parameters, _context, _many):
        if "pg_advisory_xact_lock" in statement.lower():
            barrier_passes.append(lock_barrier.wait(timeout=15))

    event.listen(app.state.engine, "before_cursor_execute", synchronize_at_idempotency_lock)
    try:

        def submit_identical_request():
            request_client = TestClient(app, raise_server_exceptions=False)
            try:
                return _upload(
                    request_client,
                    tenant["id"],
                    headers,
                    idempotency_key,
                    source_as_of_at=source_as_of_at,
                )
            finally:
                request_client.close()

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit_identical_request) for _ in range(2)]
            responses = [future.result(timeout=30) for future in futures]
    finally:
        event.remove(app.state.engine, "before_cursor_execute", synchronize_at_idempotency_lock)
        lock_barrier.abort()

    assert [response.status_code for response in responses] == [201, 201], (
        f"responses={[response.text for response in responses]!r}; "
        f"lock barrier passes={barrier_passes!r}; logs={caplog.text!r}"
    )
    payloads = [response.json() for response in responses]
    assert len({payload["id"] for payload in payloads}) == 1
    assert len({payload["input_hash"] for payload in payloads}) == 1
    assert len({payload["result_hash"] for payload in payloads}) == 1
    assert sorted(payload["replayed"] for payload in payloads) == [False, True]
    first_result = {key: value for key, value in payloads[0].items() if key != "replayed"}
    replayed_result = {key: value for key, value in payloads[1].items() if key != "replayed"}
    assert first_result == replayed_result

    idempotency_key_hash = hashlib.sha256(idempotency_key.encode("ascii")).hexdigest()
    tenant_uuid = UUID(tenant["id"])
    intake_id = UUID(payloads[0]["id"])
    with admin_engine.connect() as connection:
        intakes = (
            connection.execute(
                text(
                    "SELECT id, request_fingerprint, result_hash "
                    "FROM finance_source_intakes "
                    "WHERE tenant_id = :tenant_id AND idempotency_key_hash = :key_hash"
                ),
                {"tenant_id": tenant_uuid, "key_hash": idempotency_key_hash},
            )
            .mappings()
            .all()
        )
        evidence_count = connection.scalar(
            text(
                "SELECT count(*) FROM finance_source_row_evidence "
                "WHERE tenant_id = :tenant_id AND intake_id = :intake_id"
            ),
            {"tenant_id": tenant_uuid, "intake_id": intake_id},
        )
        mapping_count = connection.scalar(
            text(
                "SELECT count(*) FROM finance_source_mappings "
                "WHERE tenant_id = :tenant_id AND source_key = :source_key"
            ),
            {"tenant_id": tenant_uuid, "source_key": "manual-ledger-export"},
        )

    assert len(intakes) == 1
    assert intakes[0]["id"] == intake_id
    assert intakes[0]["request_fingerprint"]
    assert intakes[0]["result_hash"] == payloads[0]["result_hash"]
    assert evidence_count == 2
    assert mapping_count == 1


def test_finance_source_http_upload_limit_allows_upload_and_rejects_oversized_before_parse(
    settings, monkeypatch
):
    constrained_settings = settings.model_copy(update={"import_max_file_bytes": 1024})
    app = create_app(constrained_settings)
    request_limit = finance_source_request_body_limit(1024)

    with TestClient(app, raise_server_exceptions=False) as limited_client:
        owner = register_user(limited_client, f"m14-size-{uuid4().hex}@example.com")
        headers = auth_headers(owner)
        tenant = create_tenant(
            limited_client,
            owner,
            name="M14 Upload Limit Tenant",
            company_name="M14 Upload Limit Company",
            key=f"m14-size-tenant-{uuid4().hex}",
        )
        allowed = _upload(
            limited_client,
            tenant["id"],
            headers,
            f"m14-size-allowed-{uuid4().hex}",
        )
        assert allowed.status_code == 201, allowed.text

        from starlette.formparsers import MultiPartParser

        async def multipart_parse_must_not_run(_parser):
            raise AssertionError("oversized multipart request reached the multipart parser")

        monkeypatch.setattr(MultiPartParser, "parse", multipart_parse_must_not_run)
        oversized = limited_client.post(
            f"{_base(tenant['id'])}/intakes",
            data={"metadata_json": "{}"},
            files={"file": ("large.csv", b"x" * (request_limit + 1), "text/csv")},
            headers={**headers, "Idempotency-Key": f"m14-size-large-{uuid4().hex}"},
        )
        assert oversized.status_code == 413, oversized.text
        assert oversized.json()["error"]["code"] == "request_body_too_large"
        assert oversized.headers["X-Request-ID"] == oversized.json()["error"]["request_id"]

        unrelated = limited_client.post(
            "/api/v1/auth/register",
            content=b"{}" + b" " * (request_limit + 1),
            headers={"Content-Type": "application/json"},
        )
        assert unrelated.status_code == 422
        assert unrelated.json()["error"]["code"] == "invalid_request"


def test_finance_source_http_upload_limit_counts_actual_body_without_reliable_content_length(
    settings,
):
    constrained_settings = settings.model_copy(update={"import_max_file_bytes": 1024})
    app = create_app(constrained_settings)
    request_limit = finance_source_request_body_limit(1024)
    chunk_size = 32 * 1024
    boundary = b"m14-upload-size-boundary"
    body = _oversized_multipart(boundary, request_limit, chunk_size)
    tenant_id = str(uuid4())
    path = f"{_base(tenant_id)}/intakes"

    try:
        for declared_length in (None, b"1", b"not-a-length", b"999999999999999999999"):
            headers = [
                (b"host", b"testserver"),
                (b"content-type", b"multipart/form-data; boundary=" + boundary),
                (b"idempotency-key", b"m14-missing-length-0001"),
            ]
            if declared_length is not None:
                headers.append((b"content-length", declared_length))

            status, payload, received_bytes = _raw_asgi_post(app, path, headers, body, chunk_size)
            assert status == 413
            assert payload["error"]["code"] == "request_body_too_large"
            if (
                declared_length is not None
                and declared_length.isdigit()
                and int(declared_length) > request_limit
            ):
                assert received_bytes == 0
            else:
                assert received_bytes > request_limit
                assert received_bytes < len(body)
    finally:
        app.state.engine.dispose()
