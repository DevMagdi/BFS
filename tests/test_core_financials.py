from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.core_financials.calculations import calculate_financial_metrics
from businessflow.core_financials.catalog import LEGACY_CORE_FINANCIAL_ENGINE_VERSION
from businessflow.core_financials.contracts import (
    BANK_FIELDS,
    LEDGER_FIELDS,
    CoreFinancialRunCreateRequest,
)
from businessflow.core_financials.models import (
    CoreFinancialImport,
    CoreFinancialMapping,
    CoreFinancialPolicy,
    CoreFinancialRecord,
    CoreFinancialRun,
    CoreFinancialRunEvidence,
)
from businessflow.core_financials.service import (
    _build_import_input_snapshot,
    _fact_from_record,
    _import_result_snapshot,
    _line_payload,
    _record_snapshot,
    _run_input_snapshot,
    _run_result_snapshot,
    _source_import_state,
    _trace_hash,
    _verify_import_integrity,
    _verify_policy,
)
from businessflow.metrics.snapshot import content_hash
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/core-financials"


def _create_revenue_policy(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    *,
    legacy_v2: bool = False,
) -> dict[str, object]:
    template_response = client.get(f"{_base(tenant_id)}/template", headers=headers)
    assert template_response.status_code == 200, template_response.text
    policy = template_response.json()["illustrative_policy"]
    policy["policy_key"] = "illustrative.revenue-only"
    if legacy_v2:
        policy.pop("cash_flow_policy", None)
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


def _create_bank_mapping(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    source_key: str = "bank-demo",
) -> dict[str, object]:
    csv_headers = (
        "record_id",
        "transaction_date",
        "bank_account_id",
        "cash_in",
        "cash_out",
        "currency",
        "event_type",
        "reversal_of",
    )
    response = client.post(
        f"{_base(tenant_id)}/mappings",
        json={
            "source_key": source_key,
            "source_type": "bank_activity",
            "field_mapping": dict(zip(BANK_FIELDS, csv_headers, strict=True)),
            "ignored_columns": [],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_complete_policy(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
) -> dict[str, object]:
    template_response = client.get(f"{_base(tenant_id)}/template", headers=headers)
    assert template_response.status_code == 200, template_response.text
    policy = template_response.json()["illustrative_policy"]
    policy["policy_key"] = "illustrative.six-measure-golden"
    policy["category_coverage"] = {category: "complete" for category in policy["category_coverage"]}
    response = client.post(f"{_base(tenant_id)}/policies", json=policy, headers=headers)
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


def _import_bank_activity(
    client: TestClient,
    tenant_id: str,
    headers: dict[str, str],
    policy: dict[str, object],
    mapping: dict[str, object],
) -> tuple[dict[str, object], bytes]:
    csv_headers = (
        "record_id",
        "transaction_date",
        "bank_account_id",
        "cash_in",
        "cash_out",
        "currency",
        "event_type",
        "reversal_of",
    )
    csv_rows = [
        "bank-in-001,2026-11-10,operating-1,750,0,USD,normal,",
        "bank-out-001,2026-11-12,operating-1,0,100,USD,normal,",
        "bank-transfer-001,2026-11-15,operating-1,0,50,USD,transfer,",
    ]
    content = (",".join(csv_headers) + "\n" + "\n".join(csv_rows) + "\n").encode("utf-8")
    metadata = {
        "source_key": "bank-demo",
        "source_type": "bank_activity",
        "mapping_id": mapping["id"],
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    response = client.post(
        f"{_base(tenant_id)}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("illustrative-bank.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"bank-flow-{uuid4().hex}"},
    )
    assert response.status_code == 201, response.text
    imported = response.json()
    assert imported["source_type"] == "bank_activity"
    assert imported["quality_status"] == "passed"
    assert imported["accepted_rows"] == 3
    return imported, content


def _persist_legacy_v2_run(session, import_id: UUID) -> tuple[CoreFinancialRun, str, str]:
    source_import = session.get(CoreFinancialImport, import_id)
    assert source_import is not None
    mapping, _current_policy, records = _verify_import_integrity(session, source_import)
    policy_row = session.get(CoreFinancialPolicy, source_import.policy_id)
    assert policy_row is not None

    # Simulate the immutable v2 policy/import snapshot shape: those definitions predate
    # cash_flow_policy, and the stored snapshot engine identifier remains v2.
    policy_definition = dict(policy_row.definition)
    policy_definition.pop("cash_flow_policy", None)
    session.execute(
        text(
            "ALTER TABLE core_financial_policies "
            "DISABLE TRIGGER trg_core_financial_policies_append_only"
        )
    )
    session.execute(
        text(
            "ALTER TABLE core_financial_imports "
            "DISABLE TRIGGER trg_core_financial_imports_append_only"
        )
    )
    policy_row.definition = policy_definition
    legacy_policy = _verify_policy(policy_row)
    blank_rows_ignored = source_import.input_snapshot["file"]["blank_rows_ignored"]
    source_row_hashes = [
        {"row_number": record.source_row_number, "row_hash": record.source_row_hash}
        for record in records
    ]
    legacy_import_input = _build_import_input_snapshot(
        tenant_id=source_import.tenant_id,
        company_id=source_import.company_id,
        source_key=source_import.source_key,
        source_type=source_import.source_type,
        period_start=source_import.period_start,
        period_end=source_import.period_end,
        period_completeness=source_import.period_completeness,
        file_name=source_import.file_name,
        file_hash=source_import.file_hash,
        file_size_bytes=source_import.file_size_bytes,
        row_count=source_import.total_rows,
        blank_rows_ignored=blank_rows_ignored,
        mapping=mapping,
        policy=policy_row,
        source_row_hashes=source_row_hashes,
        engine_version=LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    row_summaries = [_record_snapshot(record) for record in records]
    legacy_import_result = _import_result_snapshot(
        row_summaries,
        quality_status=source_import.quality_status,
        engine_version=LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    source_import.input_snapshot = legacy_import_input
    source_import.result_snapshot = legacy_import_result
    source_import.input_hash = content_hash(legacy_import_input)
    source_import.result_hash = content_hash(legacy_import_result)
    session.flush()
    session.execute(
        text(
            "ALTER TABLE core_financial_imports "
            "ENABLE TRIGGER trg_core_financial_imports_append_only"
        )
    )
    session.execute(
        text(
            "ALTER TABLE core_financial_policies "
            "ENABLE TRIGGER trg_core_financial_policies_append_only"
        )
    )
    session.flush()

    # Prove that old imported evidence is still independently recoverable before replaying a run.
    _verify_import_integrity(session, source_import)
    run_request = CoreFinancialRunCreateRequest(
        policy_id=policy_row.id,
        measure="revenue",
        ledger_import_id=source_import.id,
        period_start=date(2026, 11, 1),
        period_end=date(2026, 11, 30),
        currency_code="USD",
    )
    facts = tuple(
        _fact_from_record(record, source_import)
        for record in records
        if record.row_status == "accepted"
    )
    import_pairs = ((source_import, records), None)
    metrics, analysis, lines, state = calculate_financial_metrics(
        policy=legacy_policy,
        policy_version=policy_row.version,
        request_period_start=run_request.period_start,
        request_period_end=run_request.period_end,
        currency_code=run_request.currency_code,
        ledger_import=_source_import_state(source_import),
        bank_import=None,
        facts=facts,
        controls=run_request.controls,
        requested_metrics=("revenue",),
        legacy_v2_replay=True,
    )
    assert state == "calculated"
    assert metrics[0].exact_value == "900"
    assert metrics[0].excluded_other_currency_count == 1

    run_id = uuid4()
    created_at = datetime.now(UTC)
    input_snapshot = _run_input_snapshot(
        tenant_id=source_import.tenant_id,
        company_id=source_import.company_id,
        policy_row=policy_row,
        policy=legacy_policy,
        request=run_request,
        imports=import_pairs,
        facts=facts,
        engine_version=LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    result_snapshot = _run_result_snapshot(
        state=state,
        metrics=metrics,
        analysis=analysis,
        engine_version=LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    input_hash = content_hash(input_snapshot)
    result_hash = content_hash(result_snapshot)
    evidence_payload = sorted(
        (_line_payload(line) for line in lines),
        key=lambda item: (str(item["metric"]), str(item["record_id"])),
    )
    trace_hash = _trace_hash(
        run_id=run_id,
        input_hash=input_hash,
        result_hash=result_hash,
        evidence_hash=content_hash(evidence_payload),
        created_at=created_at,
        engine_version=LEGACY_CORE_FINANCIAL_ENGINE_VERSION,
    )
    run = CoreFinancialRun(
        id=run_id,
        tenant_id=source_import.tenant_id,
        company_id=source_import.company_id,
        requested_by_user_id=policy_row.created_by_user_id,
        policy_id=policy_row.id,
        policy_version=policy_row.version,
        policy_hash=policy_row.definition_hash,
        ledger_import_id=source_import.id,
        bank_import_id=None,
        period_start=run_request.period_start,
        period_end=run_request.period_end,
        currency_code=run_request.currency_code,
        state=state,
        input_hash=input_hash,
        result_hash=result_hash,
        trace_hash=trace_hash,
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=created_at,
    )
    session.add(run)
    session.flush()
    for line in lines:
        session.add(
            CoreFinancialRunEvidence(
                id=uuid4(),
                tenant_id=source_import.tenant_id,
                company_id=source_import.company_id,
                run_id=run_id,
                metric=line.metric,
                record_id=line.fact.id,
                formula_coefficient=line.coefficient,
                contribution_amount=line.contribution_amount,
                created_at=created_at,
            )
        )
    session.flush()
    return run, input_hash, result_hash


def test_core_financial_transfer_migration_downgrade_is_safe(
    client,
    admin_engine,
    monkeypatch,
):
    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

    command.downgrade(config, "0016_core_financials")
    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0016_core_financials"
        )
    command.upgrade(config, "head")

    owner = register_user(client, f"core-financials-migration-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financial Migration Test",
        company_name="Synthetic Migration Company",
        key=f"core-financials-migration-{uuid4().hex}",
    )
    policy = _create_revenue_policy(client, tenant["id"], headers)
    bank_mapping = _create_bank_mapping(client, tenant["id"], headers)
    bank_import, _content = _import_bank_activity(
        client,
        tenant["id"],
        headers,
        policy,
        bank_mapping,
    )

    try:
        with pytest.raises(
            Exception,
            match="cannot downgrade Core Financials while bank transfer evidence exists",
        ):
            command.downgrade(config, "0016_core_financials")
    finally:
        command.upgrade(config, "head")

    with admin_engine.connect() as connection:
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "0017_core_financial_transfers"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM core_financial_records "
                    "WHERE import_id = :import_id AND event_type = 'transfer'"
                ),
                {"import_id": bank_import["id"]},
            )
            == 1
        )
        security = connection.execute(
            text(
                "SELECT relrowsecurity, relforcerowsecurity FROM pg_class "
                "WHERE oid = 'public.core_financial_records'::regclass"
            )
        ).one()
        assert security.relrowsecurity and security.relforcerowsecurity


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
    imported, content = _import_revenue_ledger(client, tenant["id"], headers, policy, mapping)

    repeated_import = client.post(
        f"{_base(tenant['id'])}/imports",
        data={
            "metadata_json": json.dumps(
                {
                    "source_key": "ledger-demo",
                    "source_type": "general_ledger",
                    "mapping_id": mapping["id"],
                    "policy_id": policy["id"],
                    "period_start": "2026-11-01",
                    "period_end": "2026-11-30",
                    "period_completeness": "complete",
                }
            )
        },
        files={"file": ("renamed-ledger.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"new-browser-session-{uuid4().hex}"},
    )
    assert repeated_import.status_code == 201, repeated_import.text
    repeated = repeated_import.json()
    assert repeated["id"] == imported["id"]
    assert repeated["replayed"] is True
    assert repeated["file_name"] == imported["file_name"]
    assert repeated["mapping_id"] == imported["mapping_id"]
    assert repeated["mapping_version"] == imported["mapping_version"]
    assert repeated["mapping_hash"] == imported["mapping_hash"]
    assert repeated["policy_id"] == imported["policy_id"]
    assert repeated["policy_version"] == imported["policy_version"]
    assert repeated["policy_hash"] == imported["policy_hash"]

    persisted_history = client.get(
        f"{_base(tenant['id'])}/imports?limit=100&offset=0",
        headers=headers,
    )
    assert persisted_history.status_code == 200, persisted_history.text
    assert persisted_history.json()["total"] == 1
    assert persisted_history.json()["items"][0]["id"] == imported["id"]
    recovered_response = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}",
        headers=headers,
    )
    assert recovered_response.status_code == 200, recovered_response.text
    recovered_import = recovered_response.json()
    assert recovered_import["mapping_id"] == imported["mapping_id"]
    assert recovered_import["mapping_version"] == imported["mapping_version"]
    assert recovered_import["mapping_hash"] == imported["mapping_hash"]
    assert recovered_import["policy_id"] == imported["policy_id"]
    assert recovered_import["policy_version"] == imported["policy_version"]
    assert recovered_import["policy_hash"] == imported["policy_hash"]
    assert recovered_import["input_hash"] == imported["input_hash"]
    assert recovered_import["result_hash"] == imported["result_hash"]
    recovered_policy_response = client.get(
        f"{_base(tenant['id'])}/policies/{imported['policy_id']}",
        headers=headers,
    )
    assert recovered_policy_response.status_code == 200, recovered_policy_response.text
    recovered_policy = recovered_policy_response.json()
    assert recovered_policy["id"] == imported["policy_id"]
    assert recovered_policy["version"] == imported["policy_version"]
    assert recovered_policy["definition_hash"] == imported["policy_hash"]

    recovered_records = client.get(
        f"{_base(tenant['id'])}/imports/{recovered_import['id']}/records?limit=100&offset=0",
        headers=headers,
    )
    assert recovered_records.status_code == 200, recovered_records.text
    assert recovered_records.json()["total"] == 2
    assert [item["row_status"] for item in recovered_records.json()["items"]] == [
        "accepted",
        "accepted",
    ]
    assert {item["category"] for item in recovered_records.json()["items"]} == {
        "revenue",
        "revenue_reduction",
    }

    run_request = {
        "policy_id": recovered_policy["id"],
        "measure": "revenue",
        "ledger_import_id": recovered_import["id"],
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
    assert len(run["metrics"]) == 1  # Each persisted run deliberately selects one measure.
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


def test_six_measure_workflow_binds_ledger_and_bank_lineage_and_replays(client):
    owner = register_user(client, f"core-financials-six-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials Six-Measure Test",
        company_name="Synthetic Six-Measure Company",
        key=f"core-financials-six-{uuid4().hex}",
    )
    policy = _create_complete_policy(client, tenant["id"], headers)
    ledger_mapping = _create_mapping(client, tenant["id"], headers)
    bank_mapping = _create_bank_mapping(client, tenant["id"], headers)

    ledger_headers = (
        "record_id",
        "transaction_date",
        "account_code",
        "debit",
        "credit",
        "currency",
        "event_type",
        "reversal_of",
    )
    ledger_rows = [
        "sale-001,2026-11-01,4000,0,1000,USD,normal,",
        "refund-001,2026-11-30,4090,100,0,USD,refund,",
        "cost-001,2026-11-30,5000,300,0,USD,normal,",
        "opex-001,2026-11-30,6000,200,0,USD,normal,",
        "nonop-income-001,2026-11-30,7000,0,50,USD,normal,",
        "nonop-expense-001,2026-11-30,7100,10,0,USD,normal,",
        "tax-001,2026-11-30,8000,20,0,USD,normal,",
    ]
    ledger_content = (",".join(ledger_headers) + "\n" + "\n".join(ledger_rows) + "\n").encode(
        "utf-8"
    )
    ledger_metadata = {
        "source_key": "ledger-demo",
        "source_type": "general_ledger",
        "mapping_id": ledger_mapping["id"],
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    ledger_response = client.post(
        f"{_base(tenant['id'])}/imports",
        data={"metadata_json": json.dumps(ledger_metadata)},
        files={"file": ("six-measure-ledger.csv", ledger_content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"six-ledger-{uuid4().hex}"},
    )
    assert ledger_response.status_code == 201, ledger_response.text
    ledger_import = ledger_response.json()
    assert ledger_import["quality_status"] == "passed"
    assert ledger_import["accepted_rows"] == 7

    bank_import, _bank_content = _import_bank_activity(
        client, tenant["id"], headers, policy, bank_mapping
    )
    wrong_ledger_source = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "measure": "revenue",
            "ledger_import_id": bank_import["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=headers,
    )
    assert wrong_ledger_source.status_code == 409
    assert wrong_ledger_source.json()["error"]["code"] == "core_financial_run_reference_mismatch"

    wrong_bank_source = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "measure": "cash_flow",
            "bank_import_id": ledger_import["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=headers,
    )
    assert wrong_bank_source.status_code == 409
    assert wrong_bank_source.json()["error"]["code"] == "core_financial_run_reference_mismatch"

    expected = {
        "revenue": "900",
        "expenses": "530",
        "gross_profit": "600",
        "operating_profit": "400",
        "net_profit": "420",
    }
    created_runs: dict[str, dict[str, object]] = {}
    for measure, value in expected.items():
        response = client.post(
            f"{_base(tenant['id'])}/runs",
            json={
                "policy_id": policy["id"],
                "measure": measure,
                "ledger_import_id": ledger_import["id"],
                "period_start": "2026-11-01",
                "period_end": "2026-11-30",
                "currency_code": "USD",
            },
            headers=headers,
        )
        assert response.status_code == 201, response.text
        run = response.json()
        metric = run["metrics"][0]
        assert run["state"] == "calculated"
        assert run["ledger_import_id"] == ledger_import["id"]
        assert run["bank_import_id"] is None
        assert metric["metric"] == measure
        assert metric["exact_value"] == value
        created_runs[measure] = run

    cash_response = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "measure": "cash_flow",
            "bank_import_id": bank_import["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=headers,
    )
    assert cash_response.status_code == 201, cash_response.text
    cash_run = cash_response.json()
    cash_metric = cash_run["metrics"][0]
    assert cash_run["state"] == "calculated"
    assert cash_run["ledger_import_id"] is None
    assert cash_run["bank_import_id"] == bank_import["id"]
    assert cash_metric["exact_value"] == "650"
    assert cash_metric["included_record_count"] == 2
    assert cash_metric["excluded_transfer_count"] == 1
    created_runs["cash_flow"] = cash_run

    assert int(created_runs["gross_profit"]["metrics"][0]["exact_value"]) == (
        int(created_runs["revenue"]["metrics"][0]["exact_value"]) - 300
    )
    assert int(created_runs["operating_profit"]["metrics"][0]["exact_value"]) == 400
    assert int(created_runs["net_profit"]["metrics"][0]["exact_value"]) == 420
    assert int(created_runs["cash_flow"]["metrics"][0]["exact_value"]) != 420

    cash_evidence = client.get(
        f"{_base(tenant['id'])}/runs/{cash_run['id']}/evidence?metric=cash_flow&limit=100&offset=0",
        headers=headers,
    )
    assert cash_evidence.status_code == 200, cash_evidence.text
    assert cash_evidence.json()["total"] == 2
    assert all(item["source_type"] == "bank_activity" for item in cash_evidence.json()["items"])
    assert all(
        item["source_import_id"] == bank_import["id"] for item in cash_evidence.json()["items"]
    )

    for measure, run in created_runs.items():
        replay = client.post(f"{_base(tenant['id'])}/runs/{run['id']}/verify", headers=headers)
        assert replay.status_code == 200, replay.text
        assert replay.json()["passed"] is True, f"{measure}: {replay.text}"


def test_legacy_v2_mixed_currency_import_and_run_remain_replay_verifiable(
    client,
    admin_engine,
):
    owner = register_user(client, f"core-financials-v2-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials Legacy Replay Test",
        company_name="Synthetic Legacy Replay Company",
        key=f"core-financials-v2-{uuid4().hex}",
    )
    policy = _create_revenue_policy(client, tenant["id"], headers, legacy_v2=True)
    mapping = _create_mapping(client, tenant["id"], headers)
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
    content = (
        ",".join(csv_headers)
        + "\n"
        + "sale-usd,2026-11-10,4000,0,1000,USD,normal,\n"
        + "refund-usd,2026-11-18,4090,100,0,USD,refund,\n"
        + "sale-eur,2026-11-20,4000,0,75,EUR,normal,\n"
    ).encode("utf-8")
    metadata = {
        "source_key": "ledger-demo",
        "source_type": "general_ledger",
        "mapping_id": mapping["id"],
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    imported_response = client.post(
        f"{_base(tenant['id'])}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("legacy-v2-ledger.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"legacy-v2-{uuid4().hex}"},
    )
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()
    assert imported["quality_status"] == "passed"
    assert imported["accepted_rows"] == 3

    factory = sessionmaker(admin_engine, expire_on_commit=False)
    with factory.begin() as session:
        run, _input_hash, _result_hash = _persist_legacy_v2_run(
            session,
            UUID(imported["id"]),
        )
        run_id = run.id

    recovered = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}",
        headers=headers,
    )
    assert recovered.status_code == 200, recovered.text
    records = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}/records?limit=100&offset=0",
        headers=headers,
    )
    assert records.status_code == 200, records.text
    assert records.json()["total"] == 3

    detail = client.get(f"{_base(tenant['id'])}/runs/{run_id}", headers=headers)
    assert detail.status_code == 200, detail.text
    assert detail.json()["engine_version"] == LEGACY_CORE_FINANCIAL_ENGINE_VERSION
    assert detail.json()["metrics"][0]["status"] == "calculated"
    assert detail.json()["metrics"][0]["exact_value"] == "900"
    assert detail.json()["metrics"][0]["excluded_other_currency_count"] == 1

    verification = client.post(
        f"{_base(tenant['id'])}/runs/{run_id}/verify",
        headers=headers,
    )
    assert verification.status_code == 200, verification.text
    assert verification.json()["passed"] is True
    assert verification.json()["engine_version"] == LEGACY_CORE_FINANCIAL_ENGINE_VERSION


def test_fractional_and_max_precision_amounts_round_trip_through_import_run_and_evidence(client):
    owner = register_user(client, f"core-financials-decimal-{uuid4().hex}@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Core Financials Decimal Test",
        company_name="Synthetic Decimal Test Company",
        key=f"core-financials-decimal-{uuid4().hex}",
    )
    policy = _create_revenue_policy(client, tenant["id"], headers)
    mapping = _create_mapping(client, tenant["id"], headers)
    header_names = (
        "record_id",
        "transaction_date",
        "account_code",
        "debit",
        "credit",
        "currency",
        "event_type",
        "reversal_of",
    )
    maximum_amount = "9" * 64
    content = (
        ",".join(header_names)
        + "\n"
        + "sale-cent,2026-11-10,4000,0,1000.25,USD,normal,\n"
        + "refund-cent,2026-11-18,4090,0.10,0,USD,refund,\n"
        + f"large-debit,2026-11-20,4000,{maximum_amount},0,USD,normal,\n"
    ).encode("utf-8")
    metadata = {
        "source_key": "ledger-demo",
        "source_type": "general_ledger",
        "mapping_id": mapping["id"],
        "policy_id": policy["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    imported_response = client.post(
        f"{_base(tenant['id'])}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("decimal-ledger.csv", content, "text/csv")},
        headers={**headers, "Idempotency-Key": f"decimal-flow-{uuid4().hex}"},
    )
    assert imported_response.status_code == 201, imported_response.text
    imported = imported_response.json()
    assert imported["quality_status"] == "passed"
    records_response = client.get(
        f"{_base(tenant['id'])}/imports/{imported['id']}/records?limit=100&offset=0",
        headers=headers,
    )
    assert records_response.status_code == 200, records_response.text
    records = records_response.json()["items"]
    assert [item["signed_amount"] for item in records] == [
        "1000.25",
        "0.1",
        f"-{maximum_amount}",
    ]

    run_response = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "ledger_import_id": imported["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=headers,
    )
    assert run_response.status_code == 201, run_response.text
    assert run_response.json()["state"] == "calculated"
    evidence_response = client.get(
        f"{_base(tenant['id'])}/runs/{run_response.json()['id']}/evidence?metric=revenue&limit=100&offset=0",
        headers=headers,
    )
    assert evidence_response.status_code == 200, evidence_response.text
    evidence = evidence_response.json()["items"]
    assert len(evidence) == 3
    assert f"-{maximum_amount}" in {item["source_signed_amount"] for item in evidence}
    assert "-0.1" in {item["contribution_amount"] for item in evidence}


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
    policy = _create_revenue_policy(client, tenant["id"], owner_headers)
    mapping = _create_mapping(client, tenant["id"], owner_headers)
    bank_mapping = _create_bank_mapping(client, tenant["id"], owner_headers)
    bank_import, _bank_content = _import_bank_activity(
        client, tenant["id"], owner_headers, policy, bank_mapping
    )
    bank_run = client.post(
        f"{_base(tenant['id'])}/runs",
        json={
            "policy_id": policy["id"],
            "measure": "cash_flow",
            "bank_import_id": bank_import["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers=owner_headers,
    )
    assert bank_run.status_code == 201, bank_run.text
    assert bank_run.json()["metrics"][0]["exact_value"] == "650"
    assert bank_run.json()["ledger_import_id"] is None

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
    denied_bank_import = client.get(
        f"{_base(tenant['id'])}/imports/{bank_import['id']}",
        headers=auth_headers(member),
    )
    assert denied_bank_import.status_code == 403
    denied_policy = client.get(
        f"{_base(tenant['id'])}/policies/{policy['id']}",
        headers=auth_headers(member),
    )
    assert denied_policy.status_code == 403

    other_owner = register_user(client, f"core-financials-other-owner-{uuid4().hex}@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other Core Financials Tenant",
        company_name="Other Synthetic Company",
        key=f"core-financials-other-{uuid4().hex}",
    )
    other_headers = auth_headers(other_owner)
    hidden = client.get(
        f"{_base(other_tenant['id'])}/imports/{imported['id']}",
        headers=other_headers,
    )
    assert hidden.status_code == 404
    hidden_bank_import = client.get(
        f"{_base(other_tenant['id'])}/imports/{bank_import['id']}",
        headers=other_headers,
    )
    assert hidden_bank_import.status_code == 404
    hidden_history = client.get(
        f"{_base(other_tenant['id'])}/imports?limit=100&offset=0",
        headers=other_headers,
    )
    assert hidden_history.status_code == 200, hidden_history.text
    assert hidden_history.json()["total"] == 0
    hidden_policy = client.get(
        f"{_base(other_tenant['id'])}/policies/{policy['id']}",
        headers=auth_headers(other_owner),
    )
    assert hidden_policy.status_code == 404


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
