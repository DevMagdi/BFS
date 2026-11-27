from __future__ import annotations

import io
import logging
import os
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from sqlalchemy import delete, func, select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from businessflow.business_reports.models import (
    BusinessReportArtifactContent,
    BusinessReportArtifactRevocation,
    BusinessReportExport,
)
from businessflow.database import set_company_context, set_tenant_context, set_user_context
from tests.helpers import auth_headers, create_member, create_tenant, register_user
from tests.test_direct_cash_forecasts import (
    _complete_request as _complete_direct_cash_request,
)
from tests.test_direct_cash_forecasts import (
    _create_cash_intake as _create_direct_cash_intake,
)
from tests.test_direct_cash_forecasts import (
    _run_url as _direct_cash_run_url,
)
from tests.test_financial_calculations import (
    _calculation_request,
)
from tests.test_financial_calculations import (
    _create_intake as _create_financial_intake,
)
from tests.test_financial_calculations import (
    _run_url as _financial_calculation_run_url,
)
from tests.test_z_business_intelligence import (
    _bi_base,
    _register_and_promote_sales_source,
)


def _analysis_for_report(
    client: TestClient, email_prefix: str
) -> tuple[dict[str, object], str, dict[str, str]]:
    owner = register_user(client, f"{email_prefix}-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name=f"{email_prefix} Tenant",
        company_name=f"{email_prefix} Company",
        key=f"{email_prefix}-onboard-key-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    source_id = _register_and_promote_sales_source(
        client,
        tenant_id,
        owner_headers,
        records=[
            {
                "transaction_reference": f"{email_prefix}-TX-001",
                "transaction_date": "2026-01-06",
                "product_code": "SKU-REPORT-A",
                "currency_code": "USD",
                "amount": "100.00",
                "status": "posted",
            },
            {
                "transaction_reference": f"{email_prefix}-TX-002",
                "transaction_date": "2026-01-07",
                "product_code": "SKU-REPORT-A",
                "currency_code": "USD",
                "amount": "300.00",
                "status": "posted",
            },
        ],
    )
    configuration = client.post(
        f"{base}/configurations",
        json={
            "expected_version": 0,
            "source_precedence": [{"dataset_type": "universal_records", "source_id": source_id}],
        },
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "sales.average_source_transaction_amount",
            "configuration_version": configuration.json()["version"],
            "dimensions": ["product"],
        },
        headers=owner_headers,
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["status"] == "complete"
    return tenant, str(analysis.json()["id"]), owner_headers


def _report_body(analysis_id: str, report_format: str, *, title: str = "M18 prototype report"):
    return {
        "title": title,
        "format": report_format,
        "sources": [
            {
                "kind": "business_intelligence_analysis",
                "source_id": analysis_id,
            }
        ],
        "retention_days": 7,
    }


def test_m18_exports_verified_bi_analysis_to_json_csv_and_xlsx(client):
    tenant, analysis_id, owner_headers = _analysis_for_report(client, "m18-formats")
    base = f"/api/v1/tenants/{tenant['id']}/reports"
    formula_title = '=HYPERLINK("https://example.invalid","unsafe")'

    json_export = client.post(
        base,
        json=_report_body(analysis_id, "json", title=formula_title),
        headers=owner_headers,
    )
    assert json_export.status_code == 201, json_export.text
    json_metadata = json_export.json()
    assert json_metadata["mode"] == "nonproduction_prototype"
    assert json_metadata["artifact_status"] == "available"
    assert json_metadata["manifest"]["governance"]["production_approved"] is False
    assert json_metadata["manifest"]["scope"]["currency_codes"] == ["USD"]
    assert json_metadata["manifest"]["sources"][0]["lineage"]["input_hash"]
    assert json_metadata["manifest"]["sources"][0]["evidence_count"] == 2
    assert (
        json_metadata["manifest"]["sources"][0]["quality"]["source_freshness"]["status"]
        == "within_policy_at_analysis_time"
    )
    json_download = client.get(f"{base}/{json_metadata['id']}/download", headers=owner_headers)
    assert json_download.status_code == 200, json_download.text
    assert json_download.headers["x-report-mode"] == "nonproduction_prototype"
    assert json_download.headers["cache-control"] == "private, no-store, max-age=0"
    assert json_download.headers["x-content-type-options"] == "nosniff"
    json_document = json_download.json()
    assert json_document["manifest"]["title"] == formula_title
    assert json_document["sources"][0]["result"]["groups"][0]["value"] == "200.00"

    csv_export = client.post(
        base,
        json=_report_body(analysis_id, "csv", title=formula_title),
        headers=owner_headers,
    )
    assert csv_export.status_code == 201, csv_export.text
    csv_download = client.get(f"{base}/{csv_export.json()['id']}/download", headers=owner_headers)
    assert csv_download.status_code == 200, csv_download.text
    assert b"'=HYPERLINK" in csv_download.content

    xlsx_export = client.post(
        base,
        json=_report_body(analysis_id, "xlsx", title=formula_title),
        headers=owner_headers,
    )
    assert xlsx_export.status_code == 201, xlsx_export.text
    xlsx_download = client.get(f"{base}/{xlsx_export.json()['id']}/download", headers=owner_headers)
    assert xlsx_download.status_code == 200, xlsx_download.text
    workbook = load_workbook(io.BytesIO(xlsx_download.content), data_only=False)
    assert workbook.sheetnames == ["Report", "Data", "Lineage"]
    assert all(
        cell.data_type != "f"
        for sheet in workbook.worksheets
        for row in sheet.iter_rows()
        for cell in row
    )
    assert workbook["Report"]["B3"].value == "'" + formula_title
    assert workbook["Data"]["M2"].value == "200.00"
    assert workbook["Lineage"].max_row >= 4

    history = client.get(base, headers=owner_headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] >= 3


def test_m18_m15_export_replays_authoritative_bfs_calculation_without_raw_inputs(
    client, admin_engine, caplog
):
    caplog.set_level(logging.DEBUG)
    owner = register_user(client, f"m18-m15-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M18 M15 Tenant",
        company_name="M18 M15 Company",
        key=f"m18-m15-tenant-{uuid4().hex}",
    )
    intake = _create_financial_intake(client, tenant["id"], owner_headers)
    replay_request = _calculation_request(intake)
    run_response = client.post(
        _financial_calculation_run_url(tenant["id"], intake["id"]),
        json=replay_request,
        headers=owner_headers,
    )
    assert run_response.status_code == 201, run_response.text
    run_payload = run_response.json()
    run = run_payload["run"]

    base = f"/api/v1/tenants/{tenant['id']}/reports"
    exported = client.post(
        base,
        json={
            "title": "M18 M15 mechanical subtotal export",
            "format": "json",
            "sources": [
                {
                    "kind": "financial_calculation_run",
                    "source_id": run["id"],
                    "intake_id": intake["id"],
                    "replay_request": replay_request,
                }
            ],
        },
        headers=owner_headers,
    )
    assert exported.status_code == 201, exported.text
    download = client.get(f"{base}/{exported.json()['id']}/download", headers=owner_headers)
    assert download.status_code == 200, download.text

    document = download.json()
    source = document["sources"][0]
    assert source["kind"] == "financial_calculation_run"
    assert source["quality"]["replay_status"] == "verified"
    assert source["quality"]["source_freshness_status"] == "fresh"
    assert source["quality"]["accounting_policy_status"] == "mechanical_only_not_accounting_policy"
    assert source["lineage"]["trace_hash"] == run["trace_hash"]
    assert source["lineage"]["replayed_trace_hash"] == run["trace_hash"]
    assert source["result"]["output"] == run_payload["trace"]["output"]
    assert source["data_points"][0]["value"] == "1.005"
    serialized = download.content.decode("utf-8")
    with admin_engine.connect() as connection:
        saved_manifest = connection.scalar(
            text("SELECT manifest::text FROM business_report_exports WHERE id = :report_id"),
            {"report_id": UUID(exported.json()["id"])},
        )
        audit_metadata = connection.scalar(
            text(
                "SELECT metadata::text FROM audit_events "
                "WHERE event_type = 'business_report.export_created' AND target_id = :report_id"
            ),
            {"report_id": UUID(exported.json()["id"])},
        )
    assert saved_manifest is not None
    assert audit_metadata is not None
    for raw_amount in ("1.25", "-0.25", "0.005"):
        assert raw_amount not in serialized
        assert raw_amount not in saved_manifest
        assert raw_amount not in audit_metadata
        assert raw_amount not in caplog.text


def test_m18_m16_export_carries_freshness_assumptions_and_lineage(client):
    owner = register_user(client, f"m18-m16-owner-{uuid4().hex}@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M18 M16 Tenant",
        company_name="M18 M16 Company",
        key=f"m18-m16-tenant-{uuid4().hex}",
    )
    intake = _create_direct_cash_intake(client, tenant["id"], owner_headers)
    run_request = _complete_direct_cash_request(intake, datetime.now(UTC).date())
    run_response = client.post(
        _direct_cash_run_url(tenant["id"], intake["id"]),
        json=run_request,
        headers=owner_headers,
    )
    assert run_response.status_code == 201, run_response.text
    run_payload = run_response.json()
    run = run_payload["run"]

    base = f"/api/v1/tenants/{tenant['id']}/reports"
    exported = client.post(
        base,
        json={
            "title": "M18 M16 known-item cash prototype export",
            "format": "json",
            "sources": [
                {
                    "kind": "direct_cash_forecast_run",
                    "source_id": run["id"],
                    "intake_id": intake["id"],
                }
            ],
        },
        headers=owner_headers,
    )
    assert exported.status_code == 201, exported.text
    download = client.get(f"{base}/{exported.json()['id']}/download", headers=owner_headers)
    assert download.status_code == 200, download.text

    document = download.json()
    source = document["sources"][0]
    assert source["quality"]["source_freshness_status"] == "fresh"
    assert source["quality"]["forecast_completeness_status"] == "not_established_by_m14"
    assert source["lineage"]["formula_hash"] == run["formula_hash"]
    assert source["result"] == run_payload["trace"]["result_snapshot"]
    assert source["data_points"][0]["value"] == "100.5"
    assert "caller assertions" in " ".join(source["assumptions"])
    assert "amount_text" not in str(source["input_snapshot"])
    assert "100.50" not in str(source["input_snapshot"])


def test_m18_report_permissions_tenant_scope_and_production_gate(client):
    tenant, analysis_id, owner_headers = _analysis_for_report(client, "m18-permissions")
    tenant_id = str(tenant["id"])
    base = f"/api/v1/tenants/{tenant_id}/reports"
    created = client.post(base, json=_report_body(analysis_id, "json"), headers=owner_headers)
    assert created.status_code == 201, created.text
    report_id = created.json()["id"]

    member = register_user(client, "m18-permissions-member@example.com")
    create_member(
        client,
        {"access_token": owner_headers["Authorization"].split(" ", 1)[1]},
        tenant_id,
        "m18-permissions-member@example.com",
    )
    member_headers = auth_headers(member)
    assert client.get(base, headers=member_headers).status_code == 403
    assert client.get(f"{base}/{report_id}", headers=member_headers).status_code == 403
    assert client.get(f"{base}/{report_id}/download", headers=member_headers).status_code == 403

    other_owner = register_user(client, "m18-other-tenant-owner@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="M18 Other Tenant",
        company_name="M18 Other Company",
        key="m18-other-tenant-key-0001",
    )
    other_base = f"/api/v1/tenants/{other_tenant['id']}/reports"
    assert (
        client.get(f"{other_base}/{report_id}", headers=auth_headers(other_owner)).status_code
        == 404
    )

    original_settings = client.app.state.settings
    client.app.state.settings = original_settings.model_copy(
        update={"report_exports_enabled": False}
    )
    try:
        disabled = client.post(
            base,
            json=_report_body(analysis_id, "json"),
            headers=owner_headers,
        )
        disabled_list = client.get(base, headers=owner_headers)
        disabled_detail = client.get(f"{base}/{report_id}", headers=owner_headers)
        disabled_download = client.get(f"{base}/{report_id}/download", headers=owner_headers)
    finally:
        client.app.state.settings = original_settings
    assert disabled.status_code == 503
    assert disabled.json()["error"]["code"] == "business_reports_disabled"
    assert disabled_list.status_code == 503
    assert disabled_detail.status_code == 503
    assert disabled_download.status_code == 503

    client.app.state.settings = original_settings.model_copy(update={"app_env": "production"})
    try:
        blocked = client.post(
            base,
            json=_report_body(analysis_id, "json"),
            headers=owner_headers,
        )
        blocked_list = client.get(base, headers=owner_headers)
        blocked_detail = client.get(f"{base}/{report_id}", headers=owner_headers)
        blocked_download = client.get(f"{base}/{report_id}/download", headers=owner_headers)
    finally:
        client.app.state.settings = original_settings
    assert blocked.status_code == 503
    assert blocked.json()["error"]["code"] == "production_report_exports_blocked"
    assert blocked_list.status_code == 503
    assert blocked_detail.status_code == 503
    assert blocked_download.status_code == 503


def test_m18_retention_revocation_purges_bytes_and_database_guards_are_enforced(
    client, runtime_engine, migration_engine, admin_engine
):
    tenant, analysis_id, owner_headers = _analysis_for_report(client, "m18-retention")
    tenant_id = UUID(str(tenant["id"]))
    company_id = UUID(str(tenant["company"]["id"]))
    owner_response = client.get("/api/v1/auth/me", headers=owner_headers)
    assert owner_response.status_code == 200, owner_response.text
    owner_id = UUID(owner_response.json()["id"])
    base = f"/api/v1/tenants/{tenant_id}/reports"
    created = client.post(base, json=_report_body(analysis_id, "json"), headers=owner_headers)
    assert created.status_code == 201, created.text
    report_id = UUID(created.json()["id"])

    table_names = [
        "business_report_exports",
        "business_report_artifact_contents",
        "business_report_artifact_revocations",
    ]
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relname = ANY(:table_names) "
                "ORDER BY c.relname"
            ),
            {"table_names": table_names},
        ).all()
        assert len(security) == 3
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
        assert all(row.rolname == "bf_migrator" for row in security)
        grants = connection.execute(
            text(
                "SELECT "
                "has_table_privilege('bf_runtime', 'business_report_exports', 'SELECT'), "
                "has_table_privilege('bf_runtime', 'business_report_exports', 'INSERT'), "
                "has_table_privilege('bf_runtime', 'business_report_exports', 'UPDATE'), "
                "has_table_privilege('bf_runtime', 'business_report_exports', 'DELETE'), "
                "has_table_privilege('bf_runtime', 'business_report_artifact_contents', 'DELETE'), "
                "has_table_privilege('bf_runtime', 'business_report_artifact_contents', 'UPDATE'), "
                "has_table_privilege('bf_runtime', "
                "'business_report_artifact_revocations', 'UPDATE')"
            )
        ).one()
        assert grants == (True, True, False, False, True, False, False)

    with Session(runtime_engine) as session, session.begin():
        assert session.scalar(select(func.count()).select_from(BusinessReportExport)) == 0
        set_user_context(session, owner_id)
        set_tenant_context(session, tenant_id)
        set_company_context(session, company_id)
        assert (
            session.scalar(
                select(func.count())
                .select_from(BusinessReportExport)
                .where(BusinessReportExport.id == report_id)
            )
            == 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(BusinessReportArtifactContent)
                .where(BusinessReportArtifactContent.report_id == report_id)
            )
            == 1
        )

    with (
        pytest.raises(DBAPIError, match="business report artifact is retained"),
        Session(runtime_engine) as session,
        session.begin(),
    ):
        set_user_context(session, owner_id)
        set_tenant_context(session, tenant_id)
        set_company_context(session, company_id)
        session.execute(
            delete(BusinessReportArtifactContent).where(
                BusinessReportArtifactContent.report_id == report_id
            )
        )

    with (
        pytest.raises(DBAPIError, match="business report evidence is append-only"),
        migration_engine.begin() as connection,
    ):
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(owner_id)},
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(tenant_id)},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(company_id)},
        )
        connection.execute(
            update(BusinessReportExport)
            .where(BusinessReportExport.id == report_id)
            .values(title=BusinessReportExport.title)
        )

    revoked = client.post(
        f"{base}/{report_id}/revoke",
        json={"reason": "The requester withdrew the need for this report."},
        headers=owner_headers,
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["artifact_status"] == "revoked"
    assert client.get(f"{base}/{report_id}/download", headers=owner_headers).status_code == 410

    purged = client.post(f"{base}/retention/purge", headers=owner_headers)
    assert purged.status_code == 200, purged.text
    assert purged.json()["purged_artifact_count"] == 1
    detail = client.get(f"{base}/{report_id}", headers=owner_headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["artifact_status"] == "revoked"
    assert detail.json()["download_path"] is None

    with Session(runtime_engine) as session, session.begin():
        set_user_context(session, owner_id)
        set_tenant_context(session, tenant_id)
        set_company_context(session, company_id)
        assert (
            session.scalar(
                select(func.count())
                .select_from(BusinessReportArtifactContent)
                .where(BusinessReportArtifactContent.report_id == report_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(BusinessReportArtifactRevocation)
                .where(BusinessReportArtifactRevocation.report_id == report_id)
            )
            == 1
        )


def test_m18_downgrade_is_guarded_when_report_evidence_is_retained(client, migration_engine):
    tenant, analysis_id, owner_headers = _analysis_for_report(client, "m18-migration-guard")
    base = f"/api/v1/tenants/{tenant['id']}/reports"
    created = client.post(base, json=_report_body(analysis_id, "json"), headers=owner_headers)
    assert created.status_code == 201, created.text

    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    with pytest.raises(
        DBAPIError,
        match="cannot downgrade while immutable report export or retention evidence is retained",
    ):
        command.downgrade(config, "0026_forecasting_engine")

    with migration_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0027_business_reports"
        )
