from __future__ import annotations

import hashlib
import os
from io import BytesIO
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from pydantic import ValidationError
from sqlalchemy import text

from businessflow.config import Settings
from businessflow.main import create_app
from businessflow.universal_data.connectors import ConnectorError, LocalDirectoryConnector
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _universal_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/company-data/universal"


def _register_source(client, base: str, headers: dict[str, str], source_key: str, **overrides):
    body = {
        "source_key": source_key,
        "display_name": f"{source_key} source",
        "source_type": "csv",
        "authority_classification": "owner_asserted_primary",
        **overrides,
    }
    response = client.post(f"{base}/sources", json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _stage_file(
    client,
    base: str,
    source_id: str,
    headers: dict[str, str],
    idempotency_key: str,
    content: bytes,
    *,
    file_name: str = "customers.csv",
    content_type: str = "text/csv",
    form: dict[str, str] | None = None,
):
    return client.post(
        f"{base}/sources/{source_id}/ingestions",
        files={"file": (file_name, content, content_type)},
        data=form,
        headers={**headers, "Idempotency-Key": idempotency_key},
    )


def _customer_mapping(
    *,
    profile_key: str = "crm.customer",
    display_name: str = "CRM customer mapping",
    worksheet_name: str = "CSV",
    headers: tuple[str, ...] = ("customer_code", "name", "email"),
    field_options: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "profile_key": profile_key,
        "display_name": display_name,
        "entity_type": "customer",
        "worksheet_name": worksheet_name,
        "field_mapping": {header: header for header in headers},
        "ignored_columns": [],
        "field_options": field_options or {},
    }


def test_settings_require_an_absolute_universal_local_connector_root(settings, tmp_path):
    settings_args = {
        "_env_file": None,
        "app_secret_key": settings.app_secret_key,
        "app_env": settings.app_env,
        "database_url": settings.database_url,
    }
    with pytest.raises(ValidationError, match="must be an absolute path"):
        Settings(**settings_args, universal_local_connector_root="relative/approved")

    unset = Settings(**settings_args, universal_local_connector_root="")
    assert unset.universal_local_connector_root is None
    configured = Settings(**settings_args, universal_local_connector_root=str(tmp_path))
    assert configured.universal_local_connector_root == str(tmp_path)


def test_local_directory_connector_rejects_traversal_symlinks_and_oversized_files(tmp_path):
    root = tmp_path / "approved"
    company_scope_id = UUID("00000000-0000-0000-0000-000000000001")
    other_company_id = UUID("00000000-0000-0000-0000-000000000002")
    incoming = root / str(company_scope_id) / "incoming"
    incoming.mkdir(parents=True)
    (incoming / "customers.csv").write_bytes(b"a,b\n")
    outside = tmp_path / "outside.csv"
    outside.write_bytes(b"secret\n")
    (incoming / "linked.csv").symlink_to(outside)
    (incoming / "large.csv").write_bytes(b"0123456789")
    if hasattr(os, "mkfifo"):
        os.mkfifo(incoming / "pipe.csv")
    (root / str(other_company_id)).mkdir()
    (root / str(other_company_id) / "private.csv").write_bytes(b"other company\n")

    with pytest.raises(ConnectorError) as relative_root_error:
        LocalDirectoryConnector(
            "relative-root", company_scope_id=company_scope_id, max_file_bytes=8
        )
    assert relative_root_error.value.code == "connector_not_configured"

    connector = LocalDirectoryConnector(root, company_scope_id=company_scope_id, max_file_bytes=8)
    selected = connector.read_file("incoming/customers.csv")
    assert selected.file_name == "customers.csv"
    assert selected.content == b"a,b\n"
    assert selected.content_type == "text/csv"

    for invalid_path in ("../outside.csv", str(outside), r"incoming\..\outside.csv"):
        with pytest.raises(ConnectorError) as error:
            connector.read_file(invalid_path)
        assert error.value.code == "invalid_connector_path"

    with pytest.raises(ConnectorError) as cross_company_error:
        connector.read_file(f"../{other_company_id}/private.csv")
    assert cross_company_error.value.code == "invalid_connector_path"

    with pytest.raises(ConnectorError) as symlink_error:
        connector.read_file("incoming/linked.csv")
    assert symlink_error.value.code == "invalid_connector_file"
    if hasattr(os, "mkfifo"):
        with pytest.raises(ConnectorError) as fifo_error:
            connector.read_file("incoming/pipe.csv")
        assert fifo_error.value.code == "invalid_connector_file"

    with pytest.raises(ConnectorError) as size_error:
        connector.read_file("incoming/large.csv")
    assert size_error.value.status_code == 413
    assert size_error.value.code == "file_too_large"

    symlinked_root = tmp_path / "symlinked-approved"
    symlinked_root.mkdir()
    (symlinked_root / str(company_scope_id)).symlink_to(incoming.parent, target_is_directory=True)
    scoped_connector = LocalDirectoryConnector(
        symlinked_root, company_scope_id=company_scope_id, max_file_bytes=8
    )
    with pytest.raises(ConnectorError) as scope_symlink_error:
        scoped_connector.read_file("incoming/customers.csv")
    assert scope_symlink_error.value.code == "invalid_connector_file"


def test_local_connector_sync_isolated_to_authorized_company_directory(settings, tmp_path):
    root = tmp_path / "approved"
    first_owner_email = "universal-local-owner@example.com"
    second_owner_email = "universal-local-other-owner@example.com"
    content = b"Export report\ncustomer_code;name\nCUS-1;First Company\n"
    app = create_app(settings.model_copy(update={"universal_local_connector_root": str(root)}))
    with TestClient(app, raise_server_exceptions=False) as local_client:
        first_owner = register_user(local_client, first_owner_email)
        first_tenant = create_tenant(
            local_client,
            first_owner,
            name="Local Connector One",
            company_name="Local Company One",
            key="universal-local-tenant-0001",
        )
        first_company_id = first_tenant["company"]["id"]
        first_source = _register_source(
            local_client,
            _universal_base(first_tenant["id"]),
            auth_headers(first_owner),
            "local.primary",
            source_type="local_directory",
            connection_reference="config://universal/local-directory",
        )
        first_path = root / first_company_id / "incoming" / "customers.csv"
        first_path.parent.mkdir(parents=True)
        first_path.write_bytes(content)

        first_sync = local_client.post(
            f"{_universal_base(first_tenant['id'])}/sources/{first_source['source_id']}/sync",
            json={
                "relative_path": "incoming/customers.csv",
                "csv_delimiter": ";",
                "csv_encoding": "utf-8-sig",
                "header_row": 2,
            },
            headers={
                **auth_headers(first_owner),
                "Idempotency-Key": "universal-local-sync-0001",
            },
        )
        assert first_sync.status_code == 201, first_sync.text
        assert first_sync.json()["ingestion"]["file_format"] == "csv"
        assert first_sync.json()["ingestion"]["counts"]["total_rows"] == 1
        assert first_sync.json()["inspection"]["header_row"] == 2
        assert (
            first_sync.json()["ingestion"]["input_fingerprint"]
            == hashlib.sha256(content).hexdigest()
        )

        second_owner = register_user(local_client, second_owner_email)
        second_tenant = create_tenant(
            local_client,
            second_owner,
            name="Local Connector Two",
            company_name="Local Company Two",
            key="universal-local-tenant-0002",
        )
        second_source = _register_source(
            local_client,
            _universal_base(second_tenant["id"]),
            auth_headers(second_owner),
            "local.primary",
            source_type="local_directory",
            connection_reference="config://universal/local-directory",
        )
        second_sync = local_client.post(
            f"{_universal_base(second_tenant['id'])}/sources/{second_source['source_id']}/sync",
            json={"relative_path": "incoming/customers.csv"},
            headers={
                **auth_headers(second_owner),
                "Idempotency-Key": "universal-local-sync-0002",
            },
        )
        assert second_sync.status_code == 503
        assert second_sync.json()["error"]["code"] == "connector_scope_not_configured"


def test_local_sync_accepts_exact_json_body_limit_with_padded_valid_json(settings, tmp_path):
    from businessflow.api.finance_source_upload_limit import MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES

    root = tmp_path / "approved"
    content = b"customer_code,name\nCUS-2,Boundary\n"
    app = create_app(settings.model_copy(update={"universal_local_connector_root": str(root)}))
    with TestClient(app, raise_server_exceptions=False) as local_client:
        owner = register_user(local_client, "universal-sync-body-boundary@example.com")
        tenant = create_tenant(
            local_client,
            owner,
            name="Universal Sync Boundary Tenant",
            company_name="Universal Sync Boundary Company",
            key="universal-sync-boundary-tenant-01",
        )
        base = _universal_base(tenant["id"])
        source = _register_source(
            local_client,
            base,
            auth_headers(owner),
            "local.boundary",
            source_type="local_directory",
            connection_reference="config://universal/local-directory",
        )
        path = root / tenant["company"]["id"] / "incoming" / "customers.csv"
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        json_body = b'{"relative_path":"incoming/customers.csv"}'
        padded_body = json_body + b" " * (MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES - len(json_body))
        assert len(padded_body) == MAX_UNIVERSAL_SYNC_REQUEST_BODY_BYTES

        response = local_client.post(
            f"{base}/sources/{source['source_id']}/sync",
            content=padded_body,
            headers={
                **auth_headers(owner),
                "Content-Type": "application/json",
                "Idempotency-Key": "universal-sync-body-boundary-01",
            },
        )
        assert response.status_code == 201, response.text
        assert response.json()["ingestion"]["counts"]["total_rows"] == 1


def test_legacy_universal_source_keeps_its_contract_version_through_bi_lineage(
    client, migration_engine
):
    owner = register_user(client, "universal-legacy-contract-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Universal Legacy Contract Tenant",
        company_name="Universal Legacy Contract Company",
        key="universal-legacy-contract-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    company_id = str(tenant["company"]["id"])
    owner_response = client.get("/api/v1/auth/me", headers=owner_headers)
    assert owner_response.status_code == 200, owner_response.text
    owner_id = UUID(owner_response.json()["id"])
    source_id = uuid4()
    profile_id = uuid4()

    with migration_engine.begin() as connection:
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
                "INSERT INTO company_data_sources "
                "(id, tenant_id, company_id, source_key, display_name, dataset_type, "
                "contract_key, contract_version, owner_user_id, registered_by_user_id) "
                "VALUES (:id, :tenant_id, :company_id, 'sales.legacy', "
                "'Legacy sales export', 'universal_records', "
                "'businessflow.universal.business_records', '1.0.0', :owner_id, :owner_id)"
            ),
            {
                "id": str(source_id),
                "tenant_id": tenant_id,
                "company_id": company_id,
                "owner_id": str(owner_id),
            },
        )
        connection.execute(
            text(
                "INSERT INTO company_data_source_profile_versions "
                "(id, tenant_id, company_id, source_id, version, source_type, display_name, "
                "business_domain, owner_user_id, source_status, authority_classification, "
                "freshness_max_age_hours, created_by_user_id) "
                "VALUES (:id, :tenant_id, :company_id, :source_id, 1, 'csv', "
                "'Legacy sales export', 'sales', :owner_id, 'enabled', "
                "'owner_asserted_primary', 24, :owner_id)"
            ),
            {
                "id": str(profile_id),
                "tenant_id": tenant_id,
                "company_id": company_id,
                "source_id": str(source_id),
                "owner_id": str(owner_id),
            },
        )

    base = _universal_base(tenant_id)
    legacy_source = client.get(f"{base}/sources/{source_id}", headers=owner_headers)
    assert legacy_source.status_code == 200, legacy_source.text
    assert legacy_source.json()["contract_version"] == "1.0.0"
    csv = (
        b"transaction_reference,transaction_date,currency_code,amount,status\n"
        b"TX-LEGACY-001,2025-01-06,USD,125.50,posted\n"
    )
    staged = _stage_file(
        client,
        base,
        str(source_id),
        owner_headers,
        "universal-legacy-sales-upload-0001",
        csv,
        file_name="legacy-sales.csv",
    )
    assert staged.status_code == 201, staged.text
    ingestion_id = staged.json()["ingestion"]["id"]
    mapping = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={
            "profile_key": "legacy.sales.transaction",
            "display_name": "Legacy sales mapping",
            "entity_type": "sales_transaction",
            "worksheet_name": "CSV",
            "field_mapping": {
                "transaction_reference": "transaction_reference",
                "transaction_date": "transaction_date",
                "currency_code": "currency_code",
                "amount": "amount",
                "status": "status",
            },
            "field_options": {"amount": {"decimal_separator": "."}},
            "ignored_columns": [],
        },
        headers=owner_headers,
    )
    assert mapping.status_code == 200, mapping.text
    assert mapping.json()["contract_hash"] == legacy_source.json()["contract_hash"]
    validated = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["validation_snapshot"]["contract_version"] == "1.0.0"
    promoted = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text

    records = client.get(f"{base}/records?entity_type=sales_transaction", headers=owner_headers)
    assert records.status_code == 200, records.text
    record = records.json()["items"][0]
    lineage = client.get(f"{base}/records/{record['id']}/lineage", headers=owner_headers)
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["items"][0]["contract_version"] == "1.0.0"

    bi_base = f"/api/v1/tenants/{tenant_id}/business-intelligence"
    configuration = client.post(
        f"{bi_base}/configurations",
        json={
            "expected_version": 0,
            "source_precedence": [
                {"dataset_type": "universal_records", "source_id": str(source_id)}
            ],
        },
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    readiness = client.get(
        f"{bi_base}/metrics/sales.source_transaction_record_count_by_product/readiness",
        headers=owner_headers,
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["readiness_status"] == "blocked"
    assert "required_field_not_supported_by_source_contract" in readiness.json()["blockers"]


def test_universal_data_isolation_mapping_history_evidence_and_governed_promotion(
    client, admin_engine
):
    owner = register_user(client, "universal-hub-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Universal Hub Tenant",
        company_name="Universal Hub Company",
        key="universal-hub-tenant-0001",
    )
    tenant_id = tenant["id"]
    base = _universal_base(tenant_id)

    plaintext_credential = "https://user:plain-password@example.test/api"
    rejected_source = client.post(
        f"{base}/sources",
        json={
            "source_key": "crm.unsafe",
            "display_name": "Unsafe CRM reference",
            "source_type": "rest_api",
            "connection_reference": plaintext_credential,
        },
        headers=owner_headers,
    )
    assert rejected_source.status_code == 422
    assert plaintext_credential not in rejected_source.text

    secret_reference = "vault://approved/crm-token"
    source = _register_source(
        client,
        base,
        owner_headers,
        "crm.primary",
        credential_reference=secret_reference,
    )
    assert source["credential_reference_configured"] is True
    assert secret_reference not in str(source)
    source_id = source["source_id"]

    legacy_source_list = client.get(
        f"/api/v1/tenants/{tenant_id}/company-data/sources", headers=owner_headers
    )
    assert legacy_source_list.status_code == 200, legacy_source_list.text
    assert legacy_source_list.json()["total"] == 0
    legacy_source_detail = client.get(
        f"/api/v1/tenants/{tenant_id}/company-data/sources/{source_id}",
        headers=owner_headers,
    )
    assert legacy_source_detail.status_code == 404
    legacy_ingestion = client.post(
        f"/api/v1/tenants/{tenant_id}/company-data/sources/{source_id}/ingestions",
        json={"import_id": str(UUID(int=0))},
        headers=owner_headers,
    )
    assert legacy_ingestion.status_code == 409
    assert legacy_ingestion.json()["error"]["code"] == "universal_source_workflow_required"

    patched = client.patch(
        f"{base}/sources/{source_id}/profile",
        json={"authority_classification": "supporting"},
        headers=owner_headers,
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["version"] == 2
    disabled = client.patch(
        f"{base}/sources/{source_id}/profile",
        json={"source_status": "disabled"},
        headers=owner_headers,
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["version"] == 3
    blocked_upload = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-disabled-upload-0001",
        b"customer_code,name,email\nCUS-1,Acme,one@example.test\n",
    )
    assert blocked_upload.status_code == 409
    assert blocked_upload.json()["error"]["code"] == "universal_source_disabled"
    enabled = client.patch(
        f"{base}/sources/{source_id}/profile",
        json={"source_status": "enabled"},
        headers=owner_headers,
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["version"] == 4
    history = client.get(
        f"{base}/sources/{source_id}/profile-history?limit=10",
        headers=owner_headers,
    )
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 4
    assert [item["version"] for item in history.json()["items"]] == [4, 3, 2, 1]
    assert all(secret_reference not in str(item) for item in history.json()["items"])

    member = register_user(client, "universal-hub-member@example.com")
    create_member(client, owner, tenant_id, "universal-hub-member@example.com")
    member_headers = auth_headers(member)
    member_catalog = client.get(f"{base}/entities", headers=member_headers)
    assert member_catalog.status_code == 200, member_catalog.text
    member_sources = client.get(f"{base}/sources", headers=member_headers)
    assert member_sources.status_code == 403
    member_source = client.get(f"{base}/sources/{source_id}", headers=member_headers)
    assert member_source.status_code == 403
    member_profile_history = client.get(
        f"{base}/sources/{source_id}/profile-history?limit=10", headers=member_headers
    )
    assert member_profile_history.status_code == 403

    other_owner = register_user(client, "universal-hub-other-owner@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Universal Other Tenant",
        company_name="Universal Other Company",
        key="universal-other-tenant-0001",
    )
    other_base = _universal_base(other_tenant["id"])
    cross_tenant_source = client.get(
        f"{other_base}/sources/{source_id}", headers=auth_headers(other_owner)
    )
    assert cross_tenant_source.status_code == 404

    original = b"customer_code,name,email\nCUS-1,Acme Ltd,one@example.test\n"
    staged_response = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original,
        content_type="text/html",
    )
    assert staged_response.status_code == 201, staged_response.text
    staged = staged_response.json()
    ingestion_id = staged["ingestion"]["id"]
    assert staged["ingestion"]["counts"]["total_rows"] == 1
    assert staged["replayed"] is False

    for protected_path in (
        f"{base}/sources/{source_id}/ingestions",
        f"{base}/sources/{source_id}/mapping-profiles",
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}",
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/rows",
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/evidence",
    ):
        assert client.get(protected_path, headers=member_headers).status_code == 403

    replay = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original,
        content_type="application/octet-stream",
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["ingestion"]["id"] == ingestion_id
    assert replay.json()["replayed"] is True
    parser_conflict = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original,
        form={
            "csv_delimiter": ";",
            "csv_encoding": "utf-8-sig",
            "header_row": "1",
        },
    )
    assert parser_conflict.status_code == 409
    assert parser_conflict.json()["error"]["code"] == "idempotency_key_reused"
    format_conflict = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original,
        file_name="customers.xlsx",
    )
    assert format_conflict.status_code == 409
    assert format_conflict.json()["error"]["code"] == "idempotency_key_reused"
    conflict = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original + b"\n",
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_reused"

    owner_evidence = client.get(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/evidence",
        headers=owner_headers,
    )
    assert owner_evidence.status_code == 200, owner_evidence.text
    assert owner_evidence.content == original
    assert owner_evidence.headers["content-type"].startswith("text/csv")
    assert owner_evidence.headers["x-content-type-options"] == "nosniff"
    assert (
        hashlib.sha256(owner_evidence.content).hexdigest()
        == staged["ingestion"]["input_fingerprint"]
    )
    cross_tenant_evidence = client.get(
        f"{other_base}/sources/{source_id}/ingestions/{ingestion_id}/evidence",
        headers=auth_headers(other_owner),
    )
    assert cross_tenant_evidence.status_code == 404

    mapping_body = _customer_mapping()
    mapping_v1_response = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json=mapping_body,
        headers=owner_headers,
    )
    assert mapping_v1_response.status_code == 200, mapping_v1_response.text
    mapping_v1 = mapping_v1_response.json()
    assert mapping_v1["version"] == 1

    validation = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validation.status_code == 200, validation.text
    validation_data = validation.json()
    assert validation_data["ingestion"]["status"] == "validated"
    assert validation_data["ingestion"]["counts"]["valid_rows"] == 1
    validation_hash_v1 = validation_data["ingestion"]["validation_hash"]

    renamed_mapping = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json=_customer_mapping(display_name="Renamed profile"),
        headers=owner_headers,
    )
    assert renamed_mapping.status_code == 409
    assert renamed_mapping.json()["error"]["code"] == "universal_mapping_profile_name_conflict"

    member_promotion = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=member_headers,
    )
    assert member_promotion.status_code == 403

    reapply_current_mapping = client.put(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={"mapping_profile_version_id": mapping_v1["id"]},
        headers=owner_headers,
    )
    assert reapply_current_mapping.status_code == 200, reapply_current_mapping.text
    revalidated = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert revalidated.status_code == 200, revalidated.text
    assert revalidated.json()["ingestion"]["validation_hash"] == validation_hash_v1

    mapping_v2_response = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json=_customer_mapping(field_options={"name": {"text_case": "upper"}}),
        headers=owner_headers,
    )
    assert mapping_v2_response.status_code == 200, mapping_v2_response.text
    mapping_v2 = mapping_v2_response.json()
    assert mapping_v2["profile_id"] == mapping_v1["profile_id"]
    assert mapping_v2["version"] == 2
    validation_v2 = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validation_v2.status_code == 200, validation_v2.text
    assert validation_v2.json()["ingestion"]["status"] == "validated"

    stale_mapping_reapply = client.put(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={"mapping_profile_version_id": mapping_v1["id"]},
        headers=owner_headers,
    )
    assert stale_mapping_reapply.status_code == 409
    assert stale_mapping_reapply.json()["error"]["code"] == (
        "universal_mapping_version_already_validated"
    )

    promoted = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["created_records"] == 1
    assert promoted.json()["revised_records"] == 0
    assert promoted.json()["replayed"] is False

    promoted_replay = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert promoted_replay.status_code == 200, promoted_replay.text
    assert promoted_replay.json()["replayed"] is True
    assert promoted_replay.json()["created_records"] == 1

    records = client.get(f"{base}/records?entity_type=customer", headers=owner_headers)
    assert records.status_code == 200, records.text
    assert records.json()["total"] == 1
    first_record = records.json()["items"][0]
    assert first_record["source_record_id"] == "CUS-1"
    assert first_record["revision"] == 1
    assert first_record["attributes"]["name"] == "ACME LTD"
    assert (
        first_record["source_profile_version_id"]
        == staged["ingestion"]["source_profile_version_id"]
    )
    lineage = client.get(f"{base}/records/{first_record['id']}/lineage", headers=owner_headers)
    assert lineage.status_code == 200, lineage.text
    assert lineage.json()["total"] == 1
    assert lineage.json()["items"][0]["row_number"] == 2
    assert (
        lineage.json()["items"][0]["input_fingerprint"] == staged["ingestion"]["input_fingerprint"]
    )
    member_records = client.get(f"{base}/records?entity_type=customer", headers=member_headers)
    assert member_records.status_code == 403
    member_lineage = client.get(
        f"{base}/records/{first_record['id']}/lineage", headers=member_headers
    )
    assert member_lineage.status_code == 403

    changed_content = b"customer_code,name,email\nCUS-1,Acme International,changed@example.test\n"
    staged_update = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-update-0001",
        changed_content,
    )
    assert staged_update.status_code == 201, staged_update.text
    update_id = staged_update.json()["ingestion"]["id"]
    apply_v2 = client.put(
        f"{base}/sources/{source_id}/ingestions/{update_id}/mapping",
        json={"mapping_profile_version_id": mapping_v2["id"]},
        headers=owner_headers,
    )
    assert apply_v2.status_code == 200, apply_v2.text
    changed_validation = client.post(
        f"{base}/sources/{source_id}/ingestions/{update_id}/validate",
        headers=owner_headers,
    )
    assert changed_validation.status_code == 200, changed_validation.text
    assert changed_validation.json()["ingestion"]["status"] == "review_required"
    update_rows = client.get(
        f"{base}/sources/{source_id}/ingestions/{update_id}/rows?limit=10",
        headers=owner_headers,
    )
    assert update_rows.status_code == 200, update_rows.text
    assert any(
        finding["code"] == "existing_source_record_changed"
        for finding in update_rows.json()["items"][0]["findings"]
    )

    no_acknowledgement = client.post(
        f"{base}/sources/{source_id}/ingestions/{update_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert no_acknowledgement.status_code == 409
    assert no_acknowledgement.json()["error"]["code"] == (
        "universal_review_acknowledgement_required"
    )
    blank_reason = client.post(
        f"{base}/sources/{source_id}/ingestions/{update_id}/promote",
        json={"acknowledge_warnings": True, "correction_reason": "   "},
        headers=owner_headers,
    )
    assert blank_reason.status_code == 422
    assert blank_reason.json()["error"]["code"] == "invalid_request"
    revised = client.post(
        f"{base}/sources/{source_id}/ingestions/{update_id}/promote",
        json={
            "acknowledge_warnings": True,
            "correction_reason": "  Approved customer correction  ",
        },
        headers=owner_headers,
    )
    assert revised.status_code == 200, revised.text
    assert revised.json()["revised_records"] == 1
    latest_records = client.get(f"{base}/records?entity_type=customer", headers=owner_headers)
    latest_record = latest_records.json()["items"][0]
    assert latest_record["revision"] == 2
    assert latest_record["attributes"]["name"] == "ACME INTERNATIONAL"
    revision_lineage = client.get(
        f"{base}/records/{latest_record['id']}/lineage", headers=owner_headers
    )
    assert revision_lineage.status_code == 200, revision_lineage.text
    assert [item["revision"] for item in revision_lineage.json()["items"]] == [2, 1]
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT correction_note FROM canonical_business_record_revisions "
                    "WHERE tenant_id = :tenant_id AND record_id = :record_id AND revision = 2"
                ),
                {"tenant_id": UUID(tenant_id), "record_id": UUID(latest_record["id"])},
            )
            == "Approved customer correction"
        )

    second_source = _register_source(
        client, base, owner_headers, "crm.secondary", display_name="Secondary CRM"
    )
    cross_source_content = b"customer_code,name,email\nCUS-2,Other Company,changed@example.test\n"
    staged_secondary = _stage_file(
        client,
        base,
        second_source["source_id"],
        owner_headers,
        "universal-cross-source-upload-0001",
        cross_source_content,
    )
    assert staged_secondary.status_code == 201, staged_secondary.text
    secondary_id = staged_secondary.json()["ingestion"]["id"]
    secondary_mapping = client.post(
        f"{base}/sources/{second_source['source_id']}/ingestions/{secondary_id}/mapping",
        json=_customer_mapping(profile_key="secondary.customer"),
        headers=owner_headers,
    )
    assert secondary_mapping.status_code == 200, secondary_mapping.text
    secondary_validation = client.post(
        f"{base}/sources/{second_source['source_id']}/ingestions/{secondary_id}/validate",
        headers=owner_headers,
    )
    assert secondary_validation.status_code == 200, secondary_validation.text
    assert secondary_validation.json()["ingestion"]["status"] == "review_required"
    secondary_rows = client.get(
        f"{base}/sources/{second_source['source_id']}/ingestions/{secondary_id}/rows?limit=10",
        headers=owner_headers,
    )
    findings = secondary_rows.json()["items"][0]["findings"]
    cross_source_finding = next(
        finding for finding in findings if finding["code"] == "possible_cross_source_duplicate"
    )
    assert cross_source_finding["candidates"][0]["source_id"] == source_id
    secondary_promotion = client.post(
        f"{base}/sources/{second_source['source_id']}/ingestions/{secondary_id}/promote",
        json={"acknowledge_warnings": True},
        headers=owner_headers,
    )
    assert secondary_promotion.status_code == 200, secondary_promotion.text
    assert secondary_promotion.json()["created_records"] == 1
    separate_records = client.get(f"{base}/records?entity_type=customer", headers=owner_headers)
    assert separate_records.json()["total"] == 2
    assert len({item["source_id"] for item in separate_records.json()["items"]}) == 2

    replay_after_promotion = _stage_file(
        client,
        base,
        source_id,
        owner_headers,
        "universal-customer-upload-0001",
        original,
    )
    assert replay_after_promotion.status_code == 201, replay_after_promotion.text
    assert replay_after_promotion.json()["replayed"] is True
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM core_financial_records WHERE tenant_id = :tenant_id"),
                {"tenant_id": UUID(tenant_id)},
            )
            == 0
        )


def test_universal_data_empty_files_and_dataset_level_blockers(client):
    owner = register_user(client, "universal-empty-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Universal Empty Tenant",
        company_name="Universal Empty Company",
        key="universal-empty-tenant-0001",
    )
    base = _universal_base(tenant["id"])
    source = _register_source(client, base, headers, "empty.source")

    header_only = _stage_file(
        client,
        base,
        source["source_id"],
        headers,
        "universal-header-only-upload-0001",
        b"customer_code,name,email\n",
    )
    assert header_only.status_code == 422
    assert header_only.json()["error"]["code"] == "empty_dataset"

    workbook = Workbook()
    populated = workbook.active
    populated.title = "Data"
    populated.append(["customer_code", "name", "email"])
    populated.append(["CUS-1", "Example Company", "example@example.test"])
    empty_sheet = workbook.create_sheet("Empty")
    empty_sheet.append(["email"])
    buffer = BytesIO()
    workbook.save(buffer)
    workbook.close()

    staged = _stage_file(
        client,
        base,
        source["source_id"],
        headers,
        "universal-empty-sheet-upload-0001",
        buffer.getvalue(),
        file_name="multi-sheet.xlsx",
        content_type="text/html",
    )
    assert staged.status_code == 201, staged.text
    ingestion_id = staged.json()["ingestion"]["id"]
    mapping = client.post(
        f"{base}/sources/{source['source_id']}/ingestions/{ingestion_id}/mapping",
        json=_customer_mapping(
            profile_key="empty-sheet.customer",
            worksheet_name="Empty",
            headers=("email",),
        ),
        headers=headers,
    )
    assert mapping.status_code == 200, mapping.text
    validation = client.post(
        f"{base}/sources/{source['source_id']}/ingestions/{ingestion_id}/validate",
        headers=headers,
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["ingestion"]["status"] == "blocked"
    assert validation.json()["ingestion"]["failure_code"] == "dataset_validation_failed"
    codes = {item["code"] for item in validation.json()["dataset_findings"]}
    assert {"empty_dataset", "required_fields_unmapped"}.issubset(codes)
    promotion = client.post(
        f"{base}/sources/{source['source_id']}/ingestions/{ingestion_id}/promote",
        json={},
        headers=headers,
    )
    assert promotion.status_code == 409
    assert promotion.json()["error"]["code"] == "universal_ingestion_not_promotable"
