"""Business Intelligence API integration tests.

Keep these append-only records after migration-downgrade probes in the suite.
"""

from __future__ import annotations

import json
import os
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from businessflow.database import set_company_context, set_tenant_context, set_user_context
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _bi_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/business-intelligence"


def _create_and_publish_metric(
    client: TestClient,
    base: str,
    owner_headers: dict[str, str],
    reviewer_headers: dict[str, str],
    body: dict[str, object],
) -> dict[str, object]:
    created = client.post(f"{base}/metrics", json=body, headers=owner_headers)
    assert created.status_code == 201, created.text
    metric_key = str(created.json()["metric_key"])
    version = int(created.json()["version"])
    submitted = client.post(
        f"{base}/metrics/{metric_key}/versions/{version}/lifecycle",
        json={
            "expected_version": version,
            "target_status": "review",
            "note": "Submit this immutable version for independent review.",
        },
        headers=owner_headers,
    )
    assert submitted.status_code == 200, submitted.text
    published = client.post(
        f"{base}/metrics/{metric_key}/versions/{version}/lifecycle",
        json={
            "expected_version": version,
            "target_status": "published",
            "note": "Independently reviewed for this bounded test case.",
        },
        headers=reviewer_headers,
    )
    assert published.status_code == 200, published.text
    assert published.json()["status"] == "published"
    return published.json()


def _source_amount_definition(
    *, metric_key: str = "sales.source_amount_observed", expected_version: int = 0
) -> dict[str, object]:
    return {
        "expected_version": expected_version,
        "metric_key": metric_key,
        "name": "Source-observed sales amount",
        "description": "Sums a source's reported sales transaction amount strings.",
        "domain": "sales",
        "category": "source_amount",
        "business_meaning": "A source-scoped amount sum with no accounting recognition.",
        "calculation": {
            "kind": "universal_source_amount_sum",
            "entity_type": "sales_transaction",
        },
        "aggregation_method": "sum",
        "required_source_datasets": [{"dataset_type": "universal_records", "required": True}],
        "unit": "source currency amount",
        "period_boundary": "transaction_date",
        "policy": {"currency_behavior": "single_currency_only"},
        "limitations": ["This source amount is not recognized revenue or a Core Financials fact."],
    }


def _register_and_promote_sales_source(
    client: TestClient,
    tenant_id: str,
    owner_headers: dict[str, str],
    *,
    product_code: str | None = None,
) -> str:
    base = f"/api/v1/tenants/{tenant_id}/company-data/universal"
    registered = client.post(
        f"{base}/sources",
        json={
            "source_key": "sales.primary",
            "display_name": "Primary sales export",
            "source_type": "csv",
            "authority_classification": "owner_asserted_primary",
            "freshness_max_age_hours": 24,
        },
        headers=owner_headers,
    )
    assert registered.status_code == 201, registered.text
    source_id = str(registered.json()["source_id"])
    if product_code is None:
        csv = (
            b"transaction_reference,transaction_date,currency_code,amount,status\n"
            b"TX-001,2025-01-06,USD,125.50,posted\n"
        )
        field_mapping = {
            "transaction_reference": "transaction_reference",
            "transaction_date": "transaction_date",
            "currency_code": "currency_code",
            "amount": "amount",
            "status": "status",
        }
    else:
        csv = (
            "transaction_reference,transaction_date,product_code,currency_code,amount,status\n"
            f"TX-001,2025-01-06,{product_code},USD,125.50,posted\n"
        ).encode()
        field_mapping = {
            "transaction_reference": "transaction_reference",
            "transaction_date": "transaction_date",
            "product_code": "product_code",
            "currency_code": "currency_code",
            "amount": "amount",
            "status": "status",
        }
    staged = client.post(
        f"{base}/sources/{source_id}/ingestions",
        files={"file": ("sales.csv", csv, "text/csv")},
        headers={**owner_headers, "Idempotency-Key": "bi-sales-source-upload-0001"},
    )
    assert staged.status_code == 201, staged.text
    ingestion_id = str(staged.json()["ingestion"]["id"])
    mapping = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/mapping",
        json={
            "profile_key": "sales.transaction",
            "display_name": "Sales transaction export",
            "entity_type": "sales_transaction",
            "worksheet_name": "CSV",
            "field_mapping": field_mapping,
            "field_options": {"amount": {"decimal_separator": "."}},
            "ignored_columns": [],
        },
        headers=owner_headers,
    )
    assert mapping.status_code == 200, mapping.text
    validated = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/validate",
        headers=owner_headers,
    )
    assert validated.status_code == 200, validated.text
    assert validated.json()["ingestion"]["status"] == "validated"
    assert (
        validated.json()["validation_snapshot"]["contract_version"]
        == registered.json()["contract_version"]
    )
    promoted = client.post(
        f"{base}/sources/{source_id}/ingestions/{ingestion_id}/promote",
        json={},
        headers=owner_headers,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["created_records"] == 1
    return source_id


def _create_core_financial_revenue_run(
    client: TestClient, tenant_id: str, owner_headers: dict[str, str]
) -> dict[str, object]:
    base = f"/api/v1/tenants/{tenant_id}/core-financials"
    template = client.get(f"{base}/template", headers=owner_headers)
    assert template.status_code == 200, template.text
    policy_body = template.json()["illustrative_policy"]
    policy_body["policy_key"] = "illustrative.bi-revenue-only"
    policy_body["category_coverage"] = {
        category: "complete" if category in {"revenue", "revenue_reduction"} else "unknown"
        for category in policy_body["category_coverage"]
    }
    policy = client.post(f"{base}/policies", json=policy_body, headers=owner_headers)
    assert policy.status_code == 201, policy.text

    mapping = client.post(
        f"{base}/mappings",
        json={
            "source_key": "bi-ledger",
            "source_type": "general_ledger",
            "field_mapping": {
                "source_record_id": "record_id",
                "transaction_date": "transaction_date",
                "account_code": "account_code",
                "debit": "debit",
                "credit": "credit",
                "currency": "currency",
                "event_type": "event_type",
                "reversal_of_record_id": "reversal_of",
            },
            "ignored_columns": [],
        },
        headers=owner_headers,
    )
    assert mapping.status_code == 201, mapping.text
    csv = (
        b"record_id,transaction_date,account_code,debit,credit,currency,event_type,reversal_of\n"
        b"sale-001,2026-11-10,4000,0,1000,USD,normal,\n"
        b"refund-001,2026-11-18,4090,100,0,USD,refund,\n"
    )
    metadata = {
        "source_key": "bi-ledger",
        "source_type": "general_ledger",
        "mapping_id": mapping.json()["id"],
        "policy_id": policy.json()["id"],
        "period_start": "2026-11-01",
        "period_end": "2026-11-30",
        "period_completeness": "complete",
    }
    imported = client.post(
        f"{base}/imports",
        data={"metadata_json": json.dumps(metadata)},
        files={"file": ("bi-revenue.csv", csv, "text/csv")},
        headers={**owner_headers, "Idempotency-Key": "bi-core-revenue-import-0001"},
    )
    assert imported.status_code == 201, imported.text
    run = client.post(
        f"{base}/runs",
        json={
            "policy_id": policy.json()["id"],
            "measure": "revenue",
            "ledger_import_id": imported.json()["id"],
            "period_start": "2026-11-01",
            "period_end": "2026-11-30",
            "currency_code": "USD",
        },
        headers={**owner_headers, "Idempotency-Key": f"bi-core-run-{uuid4().hex}"},
    )
    assert run.status_code == 201, run.text
    return run.json()


def test_business_intelligence_metric_migration_downgrade_refuses_retained_definition(
    client, migration_engine
):
    owner = register_user(client, "bi-phase3-downgrade-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Metric Downgrade Tenant",
        company_name="BI Metric Downgrade Company",
        key="bi-phase3-metric-downgrade-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    metric = client.post(
        f"{base}/metrics",
        json=_source_amount_definition(),
        headers=owner_headers,
    )
    assert metric.status_code == 201, metric.text

    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    with pytest.raises(
        DBAPIError,
        match=(
            r"archived source bytes or idempotent runs remain|"
            r"business metric definition data is retained|"
            r"project-manager semantic relationship evidence is retained|"
            r"workforce-sensitive analysis evidence is retained"
        ),
    ):
        command.downgrade(config, "0020_business_semantic_layer")

    with migration_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0025_domain_intelligence"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'business_metric_definition_versions'"
                )
            )
            is True
        )
        assert (
            connection.scalar(
                text("SELECT to_regclass('public.business_company_semantic_config_versions')")
            )
            == "business_company_semantic_config_versions"
        )


def test_bi_readiness_blocks_a_source_over_the_bounded_record_limit(client, monkeypatch):
    owner = register_user(client, "bi-phase3-limit-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Source Limit Tenant",
        company_name="BI Source Limit Company",
        key="bi-phase3-source-limit-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    reviewer = register_user(client, "bi-phase3-limit-reviewer@example.com")
    create_member(client, owner, tenant_id, "bi-phase3-limit-reviewer@example.com", role="admin")
    reviewer_headers = auth_headers(reviewer)

    source_id = _register_and_promote_sales_source(client, tenant_id, owner_headers)
    configuration = client.post(
        f"{base}/configurations",
        json={
            "expected_version": 0,
            "source_precedence": [{"dataset_type": "universal_records", "source_id": source_id}],
        },
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    metric = _create_and_publish_metric(
        client,
        base,
        owner_headers,
        reviewer_headers,
        _source_amount_definition(metric_key="sales.source_limit_probe"),
    )

    monkeypatch.setattr(
        "businessflow.business_intelligence.analysis_service.MAX_BI_SOURCE_RECORDS", 0
    )
    readiness = client.get(
        f"{base}/metrics/{metric['metric_key']}/readiness", headers=owner_headers
    )

    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["readiness_status"] == "blocked"
    assert "source_record_limit_exceeded" in readiness.json()["blockers"]
    source_check = next(
        item for item in readiness.json()["checks"] if item["check_key"].startswith("source:")
    )
    assert source_check["status"] == "blocked"
    assert source_check["evidence"]["record_count"] == 1


def test_manual_commitment_metric_uses_accepted_private_evidence_and_customer_permission(client):
    owner = register_user(client, "bi-commitment-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Commitment Tenant",
        company_name="BI Commitment Company",
        key="bi-commitment-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    member = register_user(client, "bi-commitment-member@example.com")
    create_member(client, owner, tenant_id, "bi-commitment-member@example.com")
    member_headers = auth_headers(member)
    delivery_date = date.today() + timedelta(days=14)
    commitment_base = f"/api/v1/tenants/{tenant_id}/company-data/customer-commitments"
    created = client.post(
        commitment_base,
        json={
            "customer_name": "Confidential BI Customer",
            "customer_reference": "PRIVATE-ORDER-01",
            "order_date": date.today().isoformat(),
            "requested_delivery_date": delivery_date.isoformat(),
            "summary": "Private operational commitment details",
            "notes": "Do not copy these notes into analytics.",
        },
        headers=member_headers,
    )
    assert created.status_code == 201, created.text
    commitment = created.json()["commitment"]
    commitment_id = commitment["id"]
    source_id = commitment["source_id"]
    submitted = client.post(
        f"{commitment_base}/{commitment_id}/submit",
        json={"expected_revision": commitment["current_revision"], "acknowledge_warnings": True},
        headers=member_headers,
    )
    assert submitted.status_code == 200, submitted.text
    accepted = client.post(
        f"{commitment_base}/{commitment_id}/review",
        json={
            "expected_revision": submitted.json()["commitment"]["current_revision"],
            "decision": "accept",
            "criteria_confirmed": True,
            "acknowledge_warnings": True,
        },
        headers=owner_headers,
    )
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["commitment"]["status"] == "accepted"

    configuration = client.post(
        f"{base}/configurations",
        json={
            "expected_version": 0,
            "source_precedence": [{"dataset_type": "customer_commitments", "source_id": source_id}],
        },
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    metric_key = "operations.accepted_customer_commitment_count"
    readiness = client.get(
        f"{base}/metrics/{metric_key}/readiness"
        f"?period_start={delivery_date.isoformat()}&period_end={delivery_date.isoformat()}",
        headers=owner_headers,
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["readiness_status"] == "conditional"
    assert readiness.json()["calculation_supported"] is True
    assert readiness.json()["period_start"] == delivery_date.isoformat()
    assert "manual_commitment_source_is_a_human_assertion" in readiness.json()["warnings"]

    analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": metric_key,
            "configuration_version": 1,
            "period_start": delivery_date.isoformat(),
            "period_end": delivery_date.isoformat(),
        },
        headers=owner_headers,
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["result_snapshot"]["status"] == "partial"
    assert analysis.json()["result_snapshot"]["groups"][0]["value"] == "1"
    evidence = client.get(
        f"{base}/analyses/{analysis.json()['id']}/evidence?limit=10",
        headers=owner_headers,
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 1
    evidence_item = evidence.json()["items"][0]
    assert evidence_item["evidence_kind"] == "manual_customer_commitment"
    assert evidence_item["commitment_status"] == "accepted"
    assert evidence_item["analysis_date"] == delivery_date.isoformat()
    assert evidence_item["analysis_date_field"] == "requested_delivery_date"
    assert "Confidential BI Customer" not in str(evidence.json())
    assert "PRIVATE-ORDER-01" not in str(evidence.json())
    assert "Private operational commitment details" not in str(evidence.json())

    saved_readiness = client.get(
        f"{base}/analyses/{analysis.json()['id']}/readiness", headers=member_headers
    )
    assert saved_readiness.status_code == 200, saved_readiness.text
    assert saved_readiness.json()["analysis_id"] == analysis.json()["id"]


def test_system_product_metrics_are_readiness_checked_and_keep_currency_separate(client):
    owner = register_user(client, "bi-product-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Product Tenant",
        company_name="BI Product Company",
        key="bi-product-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    source_id = _register_and_promote_sales_source(
        client, tenant_id, owner_headers, product_code="SKU-BI-001"
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

    count_metric = "sales.source_transaction_record_count_by_product"
    readiness = client.get(f"{base}/metrics/{count_metric}/readiness", headers=owner_headers)
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["readiness_status"] == "ready"
    assert readiness.json()["dimensions"] == ["product"]
    assert readiness.json()["warnings"] == []

    count_analysis = client.post(
        f"{base}/analyses",
        json={"metric_key": count_metric, "configuration_version": 1},
        headers=owner_headers,
    )
    assert count_analysis.status_code == 201, count_analysis.text
    count_result = count_analysis.json()["result_snapshot"]
    assert count_result["status"] == "complete"
    assert count_result["groups"][0]["dimensions"] == {"product": "SKU-BI-001"}
    assert count_result["groups"][0]["value"] == "1"
    count_evidence = client.get(
        f"{base}/analyses/{count_analysis.json()['id']}/evidence?limit=10",
        headers=owner_headers,
    )
    assert count_evidence.status_code == 200, count_evidence.text
    assert count_evidence.json()["total"] == 1
    assert count_evidence.json()["items"][0]["evidence_kind"] == "universal_record"
    saved_readiness = client.get(
        f"{base}/analyses/{count_analysis.json()['id']}/readiness", headers=owner_headers
    )
    assert saved_readiness.status_code == 200, saved_readiness.text
    assert saved_readiness.json()["readiness_status"] == "ready"

    amount_metric = "sales.source_amount_observed_by_product"
    amount_analysis = client.post(
        f"{base}/analyses",
        json={"metric_key": amount_metric, "configuration_version": 1},
        headers=owner_headers,
    )
    assert amount_analysis.status_code == 201, amount_analysis.text
    amount_result = amount_analysis.json()["result_snapshot"]
    assert amount_result["status"] == "complete"
    assert amount_result["groups"][0]["dimensions"] == {"product": "SKU-BI-001"}
    assert amount_result["groups"][0]["currency_code"] == "USD"
    assert amount_result["groups"][0]["value"] == "125.50"


def test_bi_registry_analysis_replay_evidence_and_exact_formula_dependencies(
    client, runtime_engine, migration_engine
):
    owner = register_user(client, "bi-phase3-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Phase Three Tenant",
        company_name="BI Phase Three Company",
        key="bi-phase3-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    base = _bi_base(tenant_id)
    reviewer = register_user(client, "bi-phase3-reviewer@example.com")
    create_member(client, owner, tenant_id, "bi-phase3-reviewer@example.com", role="admin")
    reviewer_headers = auth_headers(reviewer)

    customer_key = "customers.source_customer_record_count"
    customer_readiness = client.get(
        f"{base}/metrics/{customer_key}/readiness", headers=owner_headers
    )
    assert customer_readiness.status_code == 200, customer_readiness.text
    assert customer_readiness.json()["readiness_status"] == "blocked"
    assert (
        "customer_data_processing_authorization_required" in customer_readiness.json()["blockers"]
    )
    blocked_customer_analysis = client.post(
        f"{base}/analyses",
        json={"metric_key": customer_key, "configuration_version": 1},
        headers=owner_headers,
    )
    assert blocked_customer_analysis.status_code == 409
    assert blocked_customer_analysis.json()["error"]["code"] == (
        "customer_data_processing_authorization_required"
    )

    source_id = _register_and_promote_sales_source(client, tenant_id, owner_headers)
    configuration = client.post(
        f"{base}/configurations",
        json={
            "expected_version": 0,
            "source_precedence": [{"dataset_type": "universal_records", "source_id": source_id}],
        },
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    assert configuration.json()["version"] == 1
    configuration_id = configuration.json()["id"]
    current_configuration = client.get(f"{base}/configuration", headers=owner_headers)
    configuration_history = client.get(f"{base}/configurations", headers=owner_headers)
    assert current_configuration.status_code == configuration_history.status_code == 200
    assert (
        current_configuration.json()["configuration_hash"]
        == configuration.json()["configuration_hash"]
    )
    assert configuration_history.json()["total"] == 1

    metric_key = "sales.source_amount_observed"
    amount_definition = _source_amount_definition()
    amount_definition["dimensions"] = ["month"]
    metric_v1 = _create_and_publish_metric(
        client,
        base,
        owner_headers,
        reviewer_headers,
        amount_definition,
    )
    assert metric_v1["version"] == 1
    readiness = client.get(
        f"{base}/metrics/{metric_key}/readiness"
        "?configuration_version=1&domain=sales&period_start=2025-01-01"
        "&period_end=2025-01-31&dimensions=month",
        headers=owner_headers,
    )
    assert readiness.status_code == 200, readiness.text
    readiness_data = readiness.json()
    assert readiness_data["readiness_status"] == "ready"
    assert readiness_data["calculation_supported"] is True
    assert readiness_data["tenant_id"] == tenant_id
    assert readiness_data["company_id"] == tenant["company"]["id"]
    assert readiness_data["domain"] == "sales"
    assert readiness_data["analysis_id"] is None
    assert readiness_data["period_start"] == "2025-01-01"
    assert readiness_data["period_end"] == "2025-01-31"
    assert readiness_data["dimensions"] == ["month"]
    assert readiness_data["retention_status"] == "unresolved"
    assert readiness_data["governance_blockers"]
    assert readiness_data["warnings"] == []

    customer_grouping_readiness = client.get(
        f"{base}/metrics/sales.source_transaction_record_count/readiness?dimensions=customer",
        headers=owner_headers,
    )
    assert customer_grouping_readiness.status_code == 200, customer_grouping_readiness.text
    assert customer_grouping_readiness.json()["readiness_status"] == "blocked"
    assert (
        "customer_data_processing_authorization_required"
        in customer_grouping_readiness.json()["blockers"]
    )
    blocked_customer_grouping = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "sales.source_transaction_record_count",
            "configuration_version": 1,
            "dimensions": ["customer"],
        },
        headers=owner_headers,
    )
    assert blocked_customer_grouping.status_code == 409
    assert blocked_customer_grouping.json()["error"]["code"] == (
        "customer_data_processing_authorization_required"
    )
    blocked_customer_filter = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "sales.source_transaction_record_count",
            "configuration_version": 1,
            "filters": [
                {
                    "entity_type": "sales_transaction",
                    "field": "customer_code",
                    "operation": "equals",
                    "value": "CUST-1",
                }
            ],
        },
        headers=owner_headers,
    )
    assert blocked_customer_filter.status_code == 409
    assert blocked_customer_filter.json()["error"]["code"] == (
        "customer_data_processing_authorization_required"
    )

    wrong_domain = client.get(
        f"{base}/metrics/{metric_key}/readiness?domain=finance", headers=owner_headers
    )
    assert wrong_domain.status_code == 422
    assert wrong_domain.json()["error"]["code"] == "metric_readiness_domain_mismatch"
    missing_period_bound = client.get(
        f"{base}/metrics/{metric_key}/readiness?period_start=2025-01-01",
        headers=owner_headers,
    )
    assert missing_period_bound.status_code == 422
    assert missing_period_bound.json()["error"]["code"] == "metric_readiness_period_invalid"
    reversed_period = client.get(
        f"{base}/metrics/{metric_key}/readiness?period_start=2025-02-01&period_end=2025-01-31",
        headers=owner_headers,
    )
    assert reversed_period.status_code == 422
    assert reversed_period.json()["error"]["code"] == "metric_readiness_period_invalid"
    duplicate_dimensions = client.get(
        f"{base}/metrics/{metric_key}/readiness?dimensions=month&dimensions=month",
        headers=owner_headers,
    )
    assert duplicate_dimensions.status_code == 422
    assert duplicate_dimensions.json()["error"]["code"] == "metric_readiness_dimensions_invalid"
    excess_dimensions = client.get(
        f"{base}/metrics/sales.source_transaction_record_count/readiness"
        "?dimensions=currency_code&dimensions=status&dimensions=month",
        headers=owner_headers,
    )
    assert excess_dimensions.status_code == 200, excess_dimensions.text
    assert excess_dimensions.json()["readiness_status"] == "blocked"
    assert "metric_dimension_limit_exceeded" in excess_dimensions.json()["blockers"]

    draft_v2_body = _source_amount_definition(expected_version=1)
    draft_v2_body["description"] = "Unpublished candidate definition."
    draft_v2 = client.post(f"{base}/metrics", json=draft_v2_body, headers=owner_headers)
    assert draft_v2.status_code == 201, draft_v2.text
    assert draft_v2.json()["version"] == 2
    assert draft_v2.json()["status"] == "draft"
    current_definition = client.get(f"{base}/metrics/{metric_key}", headers=owner_headers)
    assert current_definition.status_code == 200, current_definition.text
    assert current_definition.json()["version"] == 1
    exact_draft = client.get(f"{base}/metrics/{metric_key}/versions/2", headers=owner_headers)
    assert exact_draft.status_code == 200, exact_draft.text
    assert exact_draft.json()["status"] == "draft"
    submitted_v2 = client.post(
        f"{base}/metrics/{metric_key}/versions/2/lifecycle",
        json={
            "expected_version": 2,
            "target_status": "review",
            "note": "Submit the candidate for independent review.",
        },
        headers=owner_headers,
    )
    assert submitted_v2.status_code == 200, submitted_v2.text
    self_approval = client.post(
        f"{base}/metrics/{metric_key}/versions/2/lifecycle",
        json={
            "expected_version": 2,
            "target_status": "published",
            "note": "Attempt to approve my own candidate.",
        },
        headers=owner_headers,
    )
    assert self_approval.status_code == 403
    assert self_approval.json()["error"]["code"] == (
        "metric_definition_independent_review_required"
    )
    still_current = client.get(f"{base}/metrics/{metric_key}", headers=owner_headers)
    assert still_current.status_code == 200, still_current.text
    assert still_current.json()["version"] == 1

    owner_id = UUID(client.get("/api/v1/auth/me", headers=owner_headers).json()["id"])
    reviewer_id = UUID(client.get("/api/v1/auth/me", headers=reviewer_headers).json()["id"])
    with pytest.raises(DBAPIError), Session(runtime_engine) as session, session.begin():
        set_user_context(session, owner_id)
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(tenant["company"]["id"]))
        session.execute(
            text(
                "UPDATE business_metric_definition_versions "
                "SET status = 'published', reviewed_by_user_id = :reviewer_id, "
                "review_note = 'spoofed reviewer attribution' "
                "WHERE tenant_id = :tenant_id AND company_id = :company_id "
                "AND metric_key = :metric_key AND version = 2"
            ),
            {
                "reviewer_id": reviewer_id,
                "tenant_id": UUID(tenant_id),
                "company_id": UUID(tenant["company"]["id"]),
                "metric_key": metric_key,
            },
        )
    still_in_review = client.get(f"{base}/metrics/{metric_key}/versions/2", headers=owner_headers)
    assert still_in_review.status_code == 200, still_in_review.text
    assert still_in_review.json()["status"] == "review"

    analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": metric_key,
            "configuration_version": 1,
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
        },
        headers=owner_headers,
    )
    assert analysis.status_code == 201, analysis.text
    analysis_data = analysis.json()
    assert analysis_data["metric_version"] == 1
    assert analysis_data["tenant_id"] == tenant_id
    assert analysis_data["company_id"] == tenant["company"]["id"]
    assert analysis_data["domain"] == "sales"
    assert analysis_data["retention_status"] == "unresolved"
    assert analysis_data["governance_blockers"]
    assert analysis_data["status"] == "complete"
    assert analysis_data["result_snapshot"]["groups"][0]["value"] == "125.50"
    assert analysis_data["result_snapshot"]["groups"][0]["currency_code"] == "USD"

    saved_readiness = client.get(
        f"{base}/analyses/{analysis_data['id']}/readiness", headers=owner_headers
    )
    assert saved_readiness.status_code == 200, saved_readiness.text
    assert saved_readiness.json()["analysis_id"] == analysis_data["id"]
    assert saved_readiness.json()["period_start"] == "2025-01-01"
    assert saved_readiness.json()["period_end"] == "2025-01-31"
    assert saved_readiness.json()["dimensions"] == []

    replay = client.get(f"{base}/analyses/{analysis_data['id']}/replay", headers=owner_headers)
    assert replay.status_code == 200, replay.text
    assert replay.json()["replay_status"] == "verified"
    assert replay.json()["tenant_id"] == tenant_id
    assert replay.json()["company_id"] == tenant["company"]["id"]
    assert replay.json()["domain"] == "sales"
    assert replay.json()["retention_status"] == "unresolved"
    assert replay.json()["governance_blockers"]
    assert replay.json()["replayed_result_hash"] == analysis_data["result_hash"]

    evidence = client.get(
        f"{base}/analyses/{analysis_data['id']}/evidence?limit=1&offset=0",
        headers=owner_headers,
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["tenant_id"] == tenant_id
    assert evidence.json()["company_id"] == tenant["company"]["id"]
    assert evidence.json()["metric_key"] == metric_key
    assert evidence.json()["domain"] == "sales"
    assert evidence.json()["period_start"] == "2025-01-01"
    assert evidence.json()["period_end"] == "2025-01-31"
    assert evidence.json()["retention_status"] == "unresolved"
    assert evidence.json()["governance_blockers"]
    assert evidence.json()["total"] == 1
    assert evidence.json()["items"][0]["evidence_kind"] == "universal_record"
    assert evidence.json()["items"][0]["source_id"] == source_id

    record_count_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "sales.source_transaction_record_count",
            "configuration_version": 1,
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
        },
        headers=owner_headers,
    )
    assert record_count_analysis.status_code == 201, record_count_analysis.text

    formula_key = "sales.source_amount_passthrough"
    formula = _create_and_publish_metric(
        client,
        base,
        owner_headers,
        reviewer_headers,
        {
            "expected_version": 0,
            "metric_key": formula_key,
            "name": "Saved source-amount formula",
            "description": "Passes through an exact saved source-observation analysis.",
            "domain": "sales",
            "category": "source_amount_formula",
            "business_meaning": "The exact pinned source amount result, without reclassification.",
            "calculation": {"kind": "custom_formula"},
            "formula": {"op": "metric_ref", "metric_key": metric_key},
            "aggregation_method": "source_defined",
            "unit": "source currency amount",
            "limitations": ["This formula is not independent evidence or accounting recognition."],
        },
    )
    assert formula["dependencies"][0]["version"] == 1
    formula_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": formula_key,
            "metric_version": 1,
            "configuration_version": 1,
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
            "dependency_analyses": [{"metric_key": metric_key, "analysis_id": analysis_data["id"]}],
        },
        headers=owner_headers,
    )
    assert formula_analysis.status_code == 201, formula_analysis.text
    formula_result = formula_analysis.json()["result_snapshot"]
    assert formula_result["status"] == "complete"
    assert formula_result["groups"][0]["value"] == "125.50"
    assert formula_result["groups"][0]["currency_code"] == "USD"
    formula_evidence = client.get(
        f"{base}/analyses/{formula_analysis.json()['id']}/evidence?limit=10",
        headers=owner_headers,
    )
    assert formula_evidence.status_code == 200, formula_evidence.text
    assert formula_evidence.json()["total"] == 1
    assert formula_evidence.json()["items"][0]["evidence_kind"] == "dependency_analysis"
    assert formula_evidence.json()["items"][0]["analysis_id"] == analysis_data["id"]

    member = register_user(client, "bi-phase3-member@example.com")
    create_member(client, owner, tenant_id, "bi-phase3-member@example.com")
    member_headers = auth_headers(member)
    denied_m5_readiness = client.get(
        f"{base}/metrics/m5.available_capacity_hours/readiness", headers=member_headers
    )
    assert denied_m5_readiness.status_code == 403
    denied_m5_analysis = client.post(
        f"{base}/analyses",
        json={"metric_key": "m5.available_capacity_hours", "configuration_version": 1},
        headers=member_headers,
    )
    assert denied_m5_analysis.status_code == 403
    denied_universal_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "sales.source_transaction_record_count",
            "configuration_version": 1,
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
        },
        headers=member_headers,
    )
    assert denied_universal_analysis.status_code == 403
    readable = client.get(f"{base}/analyses/{analysis_data['id']}", headers=member_headers)
    assert readable.status_code == 403, readable.text
    assert (
        client.get(
            f"{base}/analyses/{record_count_analysis.json()['id']}", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/analyses/{record_count_analysis.json()['id']}/replay",
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/analyses/{record_count_analysis.json()['id']}/evidence",
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/analyses/{record_count_analysis.json()['id']}/readiness",
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/metrics/sales.source_transaction_record_count/readiness",
            headers=member_headers,
        ).status_code
        == 403
    )
    assert client.get(f"{base}/configuration", headers=member_headers).status_code == 403
    member_configurations = client.get(f"{base}/configurations", headers=member_headers)
    assert member_configurations.status_code == 200, member_configurations.text
    assert member_configurations.json()["total"] == 0
    assert member_configurations.json()["items"] == []
    member_analysis_page = client.get(f"{base}/analyses", headers=member_headers)
    assert member_analysis_page.status_code == 200, member_analysis_page.text
    assert member_analysis_page.json()["total"] == 0
    assert member_analysis_page.json()["items"] == []
    member_metric_page = client.get(
        f"{base}/metrics?metric_key={metric_key}", headers=member_headers
    )
    assert member_metric_page.status_code == 200, member_metric_page.text
    assert member_metric_page.json()["total"] == 0
    assert member_metric_page.json()["items"] == []
    denied_metric_read = client.get(f"{base}/metrics/{metric_key}", headers=member_headers)
    assert denied_metric_read.status_code == 403, denied_metric_read.text
    denied_formula_read = client.get(f"{base}/metrics/{formula_key}", headers=member_headers)
    assert denied_formula_read.status_code == 403, denied_formula_read.text
    member_catalog = client.get(f"{base}/registry", headers=member_headers)
    assert member_catalog.status_code == 200, member_catalog.text
    assert member_catalog.json()["governance_blockers"]
    assert not any(
        item["calculation"].get("kind", "").startswith("universal_")
        for item in member_catalog.json()["system_metrics"]
    )
    assert not any(
        item["metric_key"].startswith("core_financials.")
        for item in member_catalog.json()["system_metrics"]
    )
    assert not {
        "universal_record_count",
        "universal_source_amount_sum",
        "universal_distinct_source_values",
    } & {item["calculation_key"] for item in member_catalog.json()["calculations"]}
    member_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": metric_key,
            "configuration_version": 1,
            "period_start": "2025-01-01",
            "period_end": "2025-01-31",
        },
        headers=member_headers,
    )
    assert member_analysis.status_code == 403, member_analysis.text
    denied_write = client.post(
        f"{base}/configurations",
        json={"expected_version": 1},
        headers=member_headers,
    )
    assert denied_write.status_code == 403

    other_owner = register_user(client, "bi-phase3-other-owner@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="BI Phase Three Other Tenant",
        company_name="BI Phase Three Other Company",
        key="bi-phase3-other-tenant-0001",
    )
    other_headers = auth_headers(other_owner)
    cross_tenant_read = client.get(
        f"{_bi_base(str(other_tenant['id']))}/analyses/{analysis_data['id']}",
        headers=other_headers,
    )
    assert cross_tenant_read.status_code == 404
    assert cross_tenant_read.json()["error"]["code"] == "business_intelligence_analysis_not_found"

    bi_table_count_queries = (
        text("SELECT count(*) FROM business_metric_definition_versions"),
        text("SELECT count(*) FROM business_company_semantic_config_versions"),
        text("SELECT count(*) FROM business_company_semantic_source_precedence"),
        text("SELECT count(*) FROM business_intelligence_analysis_runs"),
    )
    with Session(runtime_engine) as session, session.begin():
        for query in bi_table_count_queries:
            assert session.scalar(query) == 0
    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(tenant["company"]["id"]))
        for query in bi_table_count_queries:
            assert session.scalar(query) > 0
    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(str(other_tenant["id"])))
        set_company_context(session, UUID(str(other_tenant["company"]["id"])))
        for query in bi_table_count_queries:
            assert session.scalar(query) == 0
    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(str(other_tenant["company"]["id"])))
        for query in bi_table_count_queries:
            assert session.scalar(query) == 0

    with pytest.raises(DBAPIError), Session(runtime_engine) as session, session.begin():
        set_user_context(session, owner_id)
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(tenant["company"]["id"]))
        session.execute(
            text(
                "UPDATE business_company_semantic_config_versions "
                "SET configuration_hash = :hash WHERE id = :configuration_id"
            ),
            {"hash": "f" * 64, "configuration_id": configuration_id},
        )

    with pytest.raises(DBAPIError), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(owner_id)},
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(tenant["company"]["id"])},
        )
        connection.execute(
            text(
                "DELETE FROM business_company_semantic_source_precedence "
                "WHERE configuration_version_id = :configuration_id AND source_id = :source_id"
            ),
            {"configuration_id": configuration_id, "source_id": source_id},
        )

    with pytest.raises(DBAPIError), migration_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": str(owner_id)},
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(tenant["company"]["id"])},
        )
        connection.execute(
            text("DELETE FROM business_intelligence_analysis_runs WHERE id = :analysis_id"),
            {"analysis_id": analysis_data["id"]},
        )

    deprecated = client.post(
        f"{base}/metrics/{formula_key}/versions/1/lifecycle",
        json={
            "expected_version": 1,
            "target_status": "deprecated",
            "note": "Retire this test definition without erasing its review provenance.",
        },
        headers=owner_headers,
    )
    assert deprecated.status_code == 200, deprecated.text
    assert deprecated.json()["status"] == "deprecated"
    assert deprecated.json()["reviewed_by_user_id"] == str(reviewer_id)
    assert deprecated.json()["review_note"] == (
        "Independently reviewed for this bounded test case."
    )


def test_business_intelligence_writes_are_blocked_for_seeded_m10_company(client):
    owner = register_user(client, "bi-seeded-demo-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="M10 Demo BI Guard Tenant",
        company_name="M10 Synthetic BI Guard Company",
        key="bi-seeded-demo-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    seeded = client.post(
        f"/api/v1/tenants/{tenant_id}/position-reviews/demo/seed",
        headers=owner_headers,
    )
    assert seeded.status_code == 201, seeded.text

    base = _bi_base(tenant_id)
    read_catalog = client.get(f"{base}/registry", headers=owner_headers)
    assert read_catalog.status_code == 200, read_catalog.text
    blocked_write = client.post(
        f"{base}/configurations",
        json={"expected_version": 0},
        headers=owner_headers,
    )
    assert blocked_write.status_code == 409, blocked_write.text
    assert blocked_write.json()["error"]["code"] == "synthetic_demo_operational_api_read_only"

    member = register_user(client, "bi-seeded-demo-member@example.com")
    create_member(client, owner, tenant_id, "bi-seeded-demo-member@example.com")
    blocked_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": "m5.available_capacity_hours",
            "configuration_version": 1,
            "source_run_id": "00000000-0000-0000-0000-000000000001",
        },
        headers=auth_headers(member),
    )
    assert blocked_analysis.status_code == 409, blocked_analysis.text
    assert blocked_analysis.json()["error"]["code"] == "synthetic_demo_operational_api_read_only"


def test_business_intelligence_m5_metric_uses_exact_saved_run_and_evidence(client):
    owner = register_user(client, "bi-phase3-m5-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Phase Three M5 Tenant",
        company_name="BI Phase Three M5 Company",
        key="bi-phase3-m5-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    m5_base = f"/api/v1/tenants/{tenant_id}"
    saved_run = client.post(
        f"{m5_base}/metrics/runs",
        json={"start_week_date": "2026-11-16", "week_count": 1},
        headers=owner_headers,
    )
    assert saved_run.status_code == 201, saved_run.text
    assert saved_run.json()["result_snapshot"]["metric_versions"]["available_capacity_hours"] == (
        "1.0.0"
    )

    base = _bi_base(tenant_id)
    configuration = client.post(
        f"{base}/configurations",
        json={"expected_version": 0, "source_precedence": []},
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    reviewer = register_user(client, "bi-phase3-m5-reviewer@example.com")
    create_member(
        client,
        owner,
        tenant_id,
        "bi-phase3-m5-reviewer@example.com",
        role="admin",
    )
    reviewer_headers = auth_headers(reviewer)
    metric_key = "operations.bi_m5_capacity_observed"
    _create_and_publish_metric(
        client,
        base,
        owner_headers,
        reviewer_headers,
        {
            "expected_version": 0,
            "metric_key": metric_key,
            "name": "Saved M5 available capacity",
            "description": "References one exact saved M5 capacity observation.",
            "domain": "operations",
            "category": "workforce_operations",
            "business_meaning": "An observational saved M5 capacity result.",
            "calculation": {"kind": "m5_metric", "metric_id": "available_capacity_hours"},
            "aggregation_method": "source_defined",
            "dimensions": ["week"],
            "required_source_datasets": [{"dataset_type": "m5", "required": True}],
            "unit": "hours per employee-week",
            "time_granularity": "week",
            "limitations": ["M5 remains observational and implementation-draft."],
        },
    )
    analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": metric_key,
            "metric_version": 1,
            "configuration_version": configuration.json()["version"],
            "period_start": "2026-11-16",
            "period_end": "2026-11-22",
            "source_run_id": saved_run.json()["id"],
        },
        headers=owner_headers,
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["status"] == "partial"
    assert analysis.json()["result_snapshot"]["calculation_authority"] == (
        "m5_implementation_draft_observational"
    )
    evidence = client.get(
        f"{base}/analyses/{analysis.json()['id']}/evidence?limit=10",
        headers=owner_headers,
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 1
    assert evidence.json()["items"][0]["evidence_kind"] == "m5_metric_run"
    assert evidence.json()["items"][0]["run_id"] == saved_run.json()["id"]

    member = register_user(client, "bi-phase3-m5-member@example.com")
    create_member(client, owner, tenant_id, "bi-phase3-m5-member@example.com")
    member_headers = auth_headers(member)
    member_page = client.get(f"{base}/analyses", headers=member_headers)
    assert member_page.status_code == 200, member_page.text
    assert member_page.json()["total"] == 0
    assert member_page.json()["items"] == []
    member_metric_page = client.get(
        f"{base}/metrics?metric_key={metric_key}", headers=member_headers
    )
    assert member_metric_page.status_code == 200, member_metric_page.text
    assert member_metric_page.json()["total"] == 0
    assert member_metric_page.json()["items"] == []
    assert client.get(f"{base}/metrics/{metric_key}", headers=member_headers).status_code == 403
    assert (
        client.get(f"{base}/analyses/{analysis.json()['id']}", headers=member_headers).status_code
        == 403
    )
    assert (
        client.get(
            f"{base}/analyses/{analysis.json()['id']}/readiness", headers=member_headers
        ).status_code
        == 403
    )
    assert (
        client.get(f"{base}/metrics/{metric_key}/readiness", headers=member_headers).status_code
        == 403
    )
    assert (
        client.get(
            f"{m5_base}/metrics/runs/{saved_run.json()['id']}", headers=member_headers
        ).status_code
        == 403
    )
    member_registry = client.get(f"{base}/registry", headers=member_headers)
    assert member_registry.status_code == 200, member_registry.text
    assert not any(
        item["metric_key"].startswith("m5.") for item in member_registry.json()["system_metrics"]
    )


def test_business_intelligence_core_financial_metric_requires_matching_verified_run(client):
    owner = register_user(client, "bi-phase3-core-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="BI Phase Three Core Financials Tenant",
        company_name="BI Phase Three Core Financials Company",
        key="bi-phase3-core-tenant-0001",
    )
    tenant_id = str(tenant["id"])
    core_run = _create_core_financial_revenue_run(client, tenant_id, owner_headers)

    base = _bi_base(tenant_id)
    configuration = client.post(
        f"{base}/configurations",
        json={"expected_version": 0, "source_precedence": []},
        headers=owner_headers,
    )
    assert configuration.status_code == 201, configuration.text
    reviewer_email = "bi-phase3-core-reviewer@example.com"
    reviewer = register_user(client, reviewer_email)
    create_member(client, owner, tenant_id, reviewer_email, role="admin")
    reviewer_headers = auth_headers(reviewer)

    def publish_core_financial_metric(metric_key: str, measure: str) -> dict[str, object]:
        return _create_and_publish_metric(
            client,
            base,
            owner_headers,
            reviewer_headers,
            {
                "expected_version": 0,
                "metric_key": metric_key,
                "name": f"Saved {measure} observation",
                "description": "References one exact verified Core Financials measure.",
                "domain": "finance",
                "category": "financial_performance",
                "business_meaning": "A saved illustrative Core Financials run result.",
                "calculation": {"kind": "core_financial_measure", "measure": measure},
                "aggregation_method": "source_defined",
                "required_source_datasets": [{"dataset_type": "core_financials", "required": True}],
                "unit": "source currency amount",
                "policy": {"currency_behavior": "single_currency_only"},
                "limitations": ["Core Financials remains illustrative and not authoritative."],
            },
        )

    revenue_key = "finance.bi_revenue_snapshot"
    expenses_key = "finance.bi_expenses_snapshot"
    publish_core_financial_metric(revenue_key, "revenue")
    publish_core_financial_metric(expenses_key, "expenses")

    mismatched = client.post(
        f"{base}/analyses",
        json={
            "metric_key": expenses_key,
            "metric_version": 1,
            "configuration_version": configuration.json()["version"],
            "core_financial_run_id": core_run["id"],
        },
        headers=owner_headers,
    )
    assert mismatched.status_code == 409
    assert mismatched.json()["error"]["code"] == "core_financial_measure_mismatch"

    analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": revenue_key,
            "metric_version": 1,
            "configuration_version": configuration.json()["version"],
            "core_financial_run_id": core_run["id"],
        },
        headers=owner_headers,
    )
    assert analysis.status_code == 201, analysis.text
    assert analysis.json()["status"] == "complete"
    assert analysis.json()["result_snapshot"]["groups"][0]["value"] == "900"
    assert analysis.json()["result_snapshot"]["groups"][0]["currency_code"] == "USD"
    evidence = client.get(
        f"{base}/analyses/{analysis.json()['id']}/evidence?limit=10",
        headers=owner_headers,
    )
    assert evidence.status_code == 200, evidence.text
    assert evidence.json()["total"] == 1
    assert evidence.json()["items"][0]["evidence_kind"] == "core_financial_run"
    assert evidence.json()["items"][0]["run_id"] == core_run["id"]

    member_email = "bi-phase3-finance-member@example.com"
    member = register_user(client, member_email)
    create_member(client, owner, tenant_id, member_email)
    member_headers = auth_headers(member)
    assert client.get(f"{base}/registry", headers=member_headers).status_code == 200
    assert client.get(f"{base}/metrics/{revenue_key}", headers=member_headers).status_code == 403
    assert (
        client.get(f"{base}/metrics/{revenue_key}/readiness", headers=member_headers).status_code
        == 403
    )
    denied_analysis = client.post(
        f"{base}/analyses",
        json={
            "metric_key": revenue_key,
            "metric_version": 1,
            "configuration_version": configuration.json()["version"],
            "core_financial_run_id": core_run["id"],
        },
        headers=member_headers,
    )
    assert denied_analysis.status_code == 403
    assert (
        client.get(f"{base}/analyses/{analysis.json()['id']}", headers=member_headers).status_code
        == 403
    )
    member_analyses = client.get(f"{base}/analyses", headers=member_headers)
    assert member_analyses.status_code == 200, member_analyses.text
    assert member_analyses.json()["items"] == []
    assert member_analyses.json()["total"] == 0


def test_business_intelligence_tables_force_company_rls_and_restrict_runtime_grants(
    admin_engine,
):
    tables = {
        "business_metric_definition_versions",
        "business_company_semantic_config_versions",
        "business_company_semantic_source_precedence",
        "business_intelligence_analysis_runs",
    }
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname = ANY(CAST(:names AS text[]))"
            ),
            {"names": sorted(tables)},
        ).all()
        policies = connection.execute(
            text(
                "SELECT tablename, qual, with_check FROM pg_policies "
                "WHERE schemaname = 'public' AND tablename = ANY(CAST(:names AS text[]))"
            ),
            {"names": sorted(tables)},
        ).all()
        grants = {
            table: connection.execute(
                text(
                    "SELECT "
                    "has_table_privilege('bf_runtime', :name, 'SELECT') AS can_select, "
                    "has_table_privilege('bf_runtime', :name, 'INSERT') AS can_insert, "
                    "has_table_privilege('bf_runtime', :name, 'UPDATE') AS can_update, "
                    "has_table_privilege('bf_runtime', :name, 'DELETE') AS can_delete, "
                    "has_table_privilege('bf_runtime', :name, 'TRUNCATE') AS can_truncate"
                ),
                {"name": f"public.{table}"},
            ).one()
            for table in tables
        }

    assert {row.relname for row in security} == tables
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert {row.tablename for row in policies} == tables
    assert all(
        row.qual is not None
        and row.with_check is not None
        and "app.tenant_id" in row.qual
        and "app.company_id" in row.qual
        and "app.tenant_id" in row.with_check
        and "app.company_id" in row.with_check
        for row in policies
    )
    assert all(item.can_select and item.can_insert for item in grants.values())
    assert not any(item.can_delete or item.can_truncate for item in grants.values())
    for table, item in grants.items():
        assert item.can_update == (
            table
            in {
                "business_metric_definition_versions",
                "business_company_semantic_config_versions",
            }
        )


def test_business_intelligence_migration_downgrade_refuses_retained_evidence(
    migration_engine,
):
    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))

    with pytest.raises(
        DBAPIError,
        match=(
            r"archived source bytes or idempotent runs remain|"
            r"Company configuration or BI evidence is retained|"
            r"project-manager semantic relationship evidence is retained|"
            r"workforce-sensitive analysis evidence is retained"
        ),
    ):
        command.downgrade(config, "0021_bi_registry")

    with migration_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0025_domain_intelligence"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'business_intelligence_analysis_runs'"
                )
            )
            is True
        )
