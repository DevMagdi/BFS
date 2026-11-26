"""Company-scoped Business Semantic integration coverage.

Keep this module after migration-downgrade probes: tests share one database, and these
append-only records intentionally block downgrades through the semantic migration.
"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from businessflow.business_semantic.models import BusinessSemanticEntity
from businessflow.database import set_company_context, set_tenant_context
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def _semantic_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}/business-semantic"


def _company_base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _create_entity(
    client: TestClient,
    base: str,
    headers: dict[str, str],
    *,
    entity_type: str,
    entity_key: str,
    display_name: str,
) -> dict[str, object]:
    response = client.post(
        f"{base}/entities",
        json={
            "entity_type": entity_type,
            "entity_key": entity_key,
            "display_name": display_name,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_department(
    client: TestClient, base: str, headers: dict[str, str], name: str, unit_id: str
) -> dict[str, object]:
    response = client.post(
        f"{base}/departments",
        json={"name": name, "business_unit_id": unit_id},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_employee(
    client: TestClient, base: str, headers: dict[str, str], department_id: str
) -> dict[str, object]:
    response = client.post(
        f"{base}/employees",
        json={
            "employee_code": "EMP-SEM-001",
            "full_name": "Semantic Test Employee",
            "job_title": "Operations Analyst",
            "department_id": department_id,
            "employment_status": "active",
            "employment_start_date": "2024-01-01",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_semantic_catalog_entity_rbac_and_company_isolation(client, runtime_engine):
    owner = register_user(client, "semantic-catalog-owner@example.com")
    owner_headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Semantic Catalog Tenant",
        company_name="Semantic Catalog Company",
        key="semantic-catalog-tenant-01",
    )
    tenant_id = tenant["id"]
    base = _semantic_base(tenant_id)
    openapi_paths = client.get("/openapi.json").json()["paths"]
    openapi_base = "/api/v1/tenants/{tenant_id}/business-semantic"
    assert f"{openapi_base}/catalog" in openapi_paths
    assert f"{openapi_base}/entities" in openapi_paths
    assert f"{openapi_base}/mappings" in openapi_paths
    assert f"{openapi_base}/relationships" in openapi_paths

    catalog = client.get(f"{base}/catalog", headers=owner_headers)
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["catalog_version"] == "businessflow.semantic.v1"
    assert "customer" in {item["entity_type"] for item in catalog.json()["entities"]}
    assert "customer_has_sales_transaction" in {
        item["relationship_type"] for item in catalog.json()["relationships"]
    }
    assert "No name, email, or tax identifier is automatically merged" in catalog.json()["note"]

    entity = _create_entity(
        client,
        base,
        owner_headers,
        entity_type="customer",
        entity_key="customer-acme-01",
        display_name="Acme customer identity",
    )
    assert entity["entity_type"] == "customer"
    duplicate = client.post(
        f"{base}/entities",
        json={
            "entity_type": "customer",
            "entity_key": "customer-acme-01",
            "display_name": "Another label",
        },
        headers=owner_headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "semantic_entity_key_conflict"
    blank_label = client.post(
        f"{base}/entities",
        json={"entity_type": "customer", "entity_key": "customer-blank", "display_name": "   "},
        headers=owner_headers,
    )
    assert blank_label.status_code == 422

    member = register_user(client, "semantic-catalog-member@example.com")
    create_member(client, owner, tenant_id, "semantic-catalog-member@example.com")
    member_headers = auth_headers(member)
    assert client.get(f"{base}/entities", headers=member_headers).json()["total"] == 1
    denied_write = client.post(
        f"{base}/entities",
        json={
            "entity_type": "customer",
            "entity_key": "member-write",
            "display_name": "Member write attempt",
        },
        headers=member_headers,
    )
    assert denied_write.status_code == 403

    other_owner = register_user(client, "semantic-catalog-other@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Semantic Other Tenant",
        company_name="Semantic Other Company",
        key="semantic-catalog-other-01",
    )
    other_base = _semantic_base(other_tenant["id"])
    other_entity = _create_entity(
        client,
        other_base,
        auth_headers(other_owner),
        entity_type="customer",
        entity_key="customer-acme-01",
        display_name="Other Company's separate customer",
    )
    assert other_entity["id"] != entity["id"]
    assert (
        client.get(f"{other_base}/entities", headers=auth_headers(other_owner)).json()["total"] == 1
    )

    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(tenant["company"]["id"]))
        visible = session.scalars(select(BusinessSemanticEntity)).all()
        assert [item.id for item in visible] == [UUID(entity["id"])]
    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(tenant_id))
        set_company_context(session, UUID(other_tenant["company"]["id"]))
        assert session.scalars(select(BusinessSemanticEntity)).all() == []


def test_semantic_mappings_version_and_relationships_revalidate_company_context(client):
    owner = register_user(client, "semantic-relationship-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Semantic Relationship Tenant",
        company_name="Semantic Relationship Company",
        key="semantic-relationship-tenant-01",
    )
    tenant_id = tenant["id"]
    base = _company_base(tenant_id)
    semantic_base = _semantic_base(tenant_id)

    unit_response = client.post(
        f"{base}/business-units", json={"name": "Operations"}, headers=headers
    )
    assert unit_response.status_code == 201, unit_response.text
    unit = unit_response.json()
    department_a = _create_department(client, base, headers, "Operations A", unit["id"])
    department_b = _create_department(client, base, headers, "Operations B", unit["id"])
    employee = _create_employee(client, base, headers, department_a["id"])

    employee_identity = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="employee",
        entity_key="staff-identity-a",
        display_name="Reviewed staff identity A",
    )
    department_a_identity = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="department",
        entity_key="department-identity-a",
        display_name="Reviewed department A",
    )
    department_b_identity = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="department",
        entity_key="department-identity-b",
        display_name="Reviewed department B",
    )

    employee_mapping_response = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "employee",
            "reference_id": employee["id"],
            "semantic_entity_id": employee_identity["id"],
            "expected_version": 0,
            "review_note": "Verified against the employee profile",
        },
        headers=headers,
    )
    assert employee_mapping_response.status_code == 201, employee_mapping_response.text
    employee_mapping_v1 = employee_mapping_response.json()
    assert employee_mapping_v1["version"] == 1
    assert employee_mapping_v1["source_revision_current"] is None

    department_a_mapping = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "department",
            "reference_id": department_a["id"],
            "semantic_entity_id": department_a_identity["id"],
            "expected_version": 0,
            "review_note": "Verified against the current department profile",
        },
        headers=headers,
    )
    assert department_a_mapping.status_code == 201, department_a_mapping.text
    department_a_mapping_data = department_a_mapping.json()

    replay = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "employee",
            "reference_id": employee["id"],
            "semantic_entity_id": employee_identity["id"],
            "expected_version": 1,
            "review_note": "Already reviewed",
        },
        headers=headers,
    )
    assert replay.status_code == 201
    assert replay.json()["id"] == employee_mapping_v1["id"]
    stale_version = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "employee",
            "reference_id": employee["id"],
            "semantic_entity_id": employee_identity["id"],
            "expected_version": 0,
            "review_note": "Stale mapping attempt",
        },
        headers=headers,
    )
    assert stale_version.status_code == 409
    assert stale_version.json()["error"]["code"] == "semantic_mapping_version_conflict"

    relationship = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": employee_identity["id"],
            "relationship_type": "employee_belongs_to_department",
            "object_entity_id": department_a_identity["id"],
            "expected_version": 0,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": employee_mapping_v1["id"],
            "object_mapping_version_id": department_a_mapping_data["id"],
            "evidence_note": "The current employee department_id matches the reviewed department",
        },
        headers=headers,
    )
    assert relationship.status_code == 201, relationship.text
    assert relationship.json()["analytically_usable"] is True

    changed_department = client.patch(
        f"{base}/employees/{employee['id']}",
        json={"department_id": department_b["id"]},
        headers=headers,
    )
    assert changed_department.status_code == 200, changed_department.text
    relationships = client.get(f"{semantic_base}/relationships", headers=headers)
    assert relationships.status_code == 200, relationships.text
    assert relationships.json()["items"][0]["analytically_usable"] is False

    department_b_mapping = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "department",
            "reference_id": department_b["id"],
            "semantic_entity_id": department_b_identity["id"],
            "expected_version": 0,
            "review_note": "Verified against current department profile",
        },
        headers=headers,
    )
    assert department_b_mapping.status_code == 201, department_b_mapping.text
    employee_identity_v2 = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="employee",
        entity_key="staff-identity-b",
        display_name="Reviewed staff identity B",
    )
    corrected_mapping = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "employee",
            "reference_id": employee["id"],
            "semantic_entity_id": employee_identity_v2["id"],
            "expected_version": 1,
            "review_note": "Re-reviewed after discovering the earlier identity was incorrect",
            "correction_reason": "Human review confirmed the prior semantic target was wrong",
        },
        headers=headers,
    )
    assert corrected_mapping.status_code == 201, corrected_mapping.text
    assert corrected_mapping.json()["version"] == 2
    assert corrected_mapping.json()["supersedes_mapping_version_id"] == employee_mapping_v1["id"]

    mapping_history = client.get(f"{semantic_base}/mappings?include_history=true", headers=headers)
    assert mapping_history.status_code == 200, mapping_history.text
    employee_history = [
        item for item in mapping_history.json()["items"] if item["reference_id"] == employee["id"]
    ]
    assert [item["version"] for item in employee_history] == [2, 1]
    assert [item["is_current"] for item in employee_history] == [True, False]

    current_relationship = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": employee_identity_v2["id"],
            "relationship_type": "employee_belongs_to_department",
            "object_entity_id": department_b_identity["id"],
            "expected_version": 0,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": corrected_mapping.json()["id"],
            "object_mapping_version_id": department_b_mapping.json()["id"],
            "evidence_note": "Current Company Context department reference was rechecked",
        },
        headers=headers,
    )
    assert current_relationship.status_code == 201, current_relationship.text
    assert current_relationship.json()["analytically_usable"] is True
    revised_relationship = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": employee_identity_v2["id"],
            "relationship_type": "employee_belongs_to_department",
            "object_entity_id": department_b_identity["id"],
            "expected_version": 1,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": corrected_mapping.json()["id"],
            "object_mapping_version_id": department_b_mapping.json()["id"],
            "evidence_note": "The same source-backed relationship was reviewed again",
        },
        headers=headers,
    )
    assert revised_relationship.status_code == 201, revised_relationship.text
    assert revised_relationship.json()["version"] == 2
    assert revised_relationship.json()["is_current"] is True
    relationship_history = client.get(
        f"{semantic_base}/relationships?include_history=true", headers=headers
    )
    assert relationship_history.status_code == 200, relationship_history.text
    edge_versions = [
        item
        for item in relationship_history.json()["items"]
        if item["subject_entity_id"] == employee_identity_v2["id"]
        and item["object_entity_id"] == department_b_identity["id"]
    ]
    assert [item["version"] for item in edge_versions] == [2, 1]
    assert [item["is_current"] for item in edge_versions] == [True, False]
    assert [item["analytically_usable"] for item in edge_versions] == [True, False]

    manual_assertion = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": employee_identity_v2["id"],
            "relationship_type": "employee_belongs_to_department",
            "object_entity_id": department_a_identity["id"],
            "expected_version": 0,
            "evidence_kind": "manual_assertion",
            "evidence_note": "Recorded as a human assertion only; not a source-verified link",
        },
        headers=headers,
    )
    assert manual_assertion.status_code == 201, manual_assertion.text
    assert manual_assertion.json()["analytically_usable"] is False

    other_owner = register_user(client, "semantic-relationship-other@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other Semantic Company",
        company_name="Other Semantic Company Ltd",
        key="semantic-relationship-other-01",
    )
    foreign_mapping = client.post(
        f"{_semantic_base(other_tenant['id'])}/mappings",
        json={
            "reference_kind": "employee",
            "reference_id": employee["id"],
            "semantic_entity_id": employee_identity_v2["id"],
            "expected_version": 0,
            "review_note": "Cross-Company mapping must not resolve",
        },
        headers=auth_headers(other_owner),
    )
    assert foreign_mapping.status_code == 404
    assert foreign_mapping.json()["error"]["code"] == "semantic_source_record_not_found"


def test_universal_data_mapping_pins_phase_two_revision_and_requires_refresh(client):
    owner = register_user(client, "semantic-universal-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Semantic Universal Tenant",
        company_name="Semantic Universal Company",
        key="semantic-universal-tenant-01",
    )
    tenant_id = tenant["id"]
    semantic_base = _semantic_base(tenant_id)
    universal_base = f"/api/v1/tenants/{tenant_id}/company-data/universal"

    source_response = client.post(
        f"{universal_base}/sources",
        json={
            "source_key": "crm.primary",
            "display_name": "Primary CRM",
            "source_type": "csv",
            "authority_classification": "owner_asserted_primary",
        },
        headers=headers,
    )
    assert source_response.status_code == 201, source_response.text
    source_id = source_response.json()["source_id"]
    first_content = b"customer_code,name,email\nCUS-SEM-1,Semantic Company,sem@example.test\n"
    first_stage = client.post(
        f"{universal_base}/sources/{source_id}/ingestions",
        files={"file": ("customers.csv", first_content, "text/csv")},
        headers={**headers, "Idempotency-Key": "semantic-universal-upload-01"},
    )
    assert first_stage.status_code == 201, first_stage.text
    first_ingestion_id = first_stage.json()["ingestion"]["id"]
    mapping_body = {
        "profile_key": "crm.customer",
        "display_name": "CRM customer mapping",
        "entity_type": "customer",
        "worksheet_name": "CSV",
        "field_mapping": {
            "customer_code": "customer_code",
            "name": "name",
            "email": "email",
        },
        "ignored_columns": [],
        "field_options": {},
    }
    mapping_response = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{first_ingestion_id}/mapping",
        json=mapping_body,
        headers=headers,
    )
    assert mapping_response.status_code == 200, mapping_response.text
    validation = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{first_ingestion_id}/validate",
        headers=headers,
    )
    assert validation.status_code == 200, validation.text
    assert validation.json()["ingestion"]["status"] == "validated"
    promotion = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{first_ingestion_id}/promote",
        json={},
        headers=headers,
    )
    assert promotion.status_code == 200, promotion.text
    source_records = client.get(f"{universal_base}/records?entity_type=customer", headers=headers)
    assert source_records.status_code == 200, source_records.text
    source_record = source_records.json()["items"][0]
    assert source_record["revision"] == 1

    identity = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="customer",
        entity_key="customer-semantic-company",
        display_name="Reviewed customer identity",
    )
    semantic_mapping_response = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "universal_record",
            "reference_id": source_record["id"],
            "semantic_entity_id": identity["id"],
            "expected_version": 0,
            "review_note": "Reviewed source record and mapping profile version",
        },
        headers=headers,
    )
    assert semantic_mapping_response.status_code == 201, semantic_mapping_response.text
    semantic_mapping_v1 = semantic_mapping_response.json()
    assert semantic_mapping_v1["source_revision"] == 1
    assert semantic_mapping_v1["source_revision_current"] is True
    assert semantic_mapping_v1["source_mapping_profile_version_id"] == mapping_response.json()["id"]

    sales_content = (
        b"transaction_reference,transaction_date,customer_code,currency_code,amount\n"
        b"INV-SEM-1,2025-01-15,CUS-SEM-1,USD,120.00\n"
    )
    sales_stage = client.post(
        f"{universal_base}/sources/{source_id}/ingestions",
        files={"file": ("sales.csv", sales_content, "text/csv")},
        headers={**headers, "Idempotency-Key": "semantic-universal-sales-upload-01"},
    )
    assert sales_stage.status_code == 201, sales_stage.text
    sales_ingestion_id = sales_stage.json()["ingestion"]["id"]
    sales_mapping_body = {
        "profile_key": "crm.sales",
        "display_name": "CRM sales transaction mapping",
        "entity_type": "sales_transaction",
        "worksheet_name": "CSV",
        "field_mapping": {
            "transaction_reference": "transaction_reference",
            "transaction_date": "transaction_date",
            "customer_code": "customer_code",
            "currency_code": "currency_code",
            "amount": "amount",
        },
        "ignored_columns": [],
        "field_options": {"amount": {"decimal_separator": "."}},
    }
    sales_profile = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{sales_ingestion_id}/mapping",
        json=sales_mapping_body,
        headers=headers,
    )
    assert sales_profile.status_code == 200, sales_profile.text
    sales_validation = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{sales_ingestion_id}/validate",
        headers=headers,
    )
    assert sales_validation.status_code == 200, sales_validation.text
    assert sales_validation.json()["ingestion"]["status"] == "validated"
    sales_promotion = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{sales_ingestion_id}/promote",
        json={},
        headers=headers,
    )
    assert sales_promotion.status_code == 200, sales_promotion.text
    sales_records = client.get(
        f"{universal_base}/records?entity_type=sales_transaction", headers=headers
    )
    assert sales_records.status_code == 200, sales_records.text
    sales_record = sales_records.json()["items"][0]
    sales_identity = _create_entity(
        client,
        semantic_base,
        headers,
        entity_type="sales_transaction",
        entity_key="sales-transaction-semantic-1",
        display_name="Reviewed sales transaction identity",
    )
    sales_semantic_mapping = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "universal_record",
            "reference_id": sales_record["id"],
            "semantic_entity_id": sales_identity["id"],
            "expected_version": 0,
            "review_note": "Reviewed transaction source record and mapping profile",
        },
        headers=headers,
    )
    assert sales_semantic_mapping.status_code == 201, sales_semantic_mapping.text
    sales_semantic_mapping_v1 = sales_semantic_mapping.json()
    relationship_v1 = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": identity["id"],
            "relationship_type": "customer_has_sales_transaction",
            "object_entity_id": sales_identity["id"],
            "expected_version": 0,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": semantic_mapping_v1["id"],
            "object_mapping_version_id": sales_semantic_mapping_v1["id"],
            "evidence_note": "The source customer code matches on the reviewed transaction",
        },
        headers=headers,
    )
    assert relationship_v1.status_code == 201, relationship_v1.text
    assert relationship_v1.json()["analytically_usable"] is True

    second_content = (
        b"customer_code,name,email\nCUS-SEM-1,Semantic Company Updated,updated@example.test\n"
    )
    second_stage = client.post(
        f"{universal_base}/sources/{source_id}/ingestions",
        files={"file": ("customers.csv", second_content, "text/csv")},
        headers={**headers, "Idempotency-Key": "semantic-universal-upload-02"},
    )
    assert second_stage.status_code == 201, second_stage.text
    second_ingestion_id = second_stage.json()["ingestion"]["id"]
    applied = client.put(
        f"{universal_base}/sources/{source_id}/ingestions/{second_ingestion_id}/mapping",
        json={"mapping_profile_version_id": mapping_response.json()["id"]},
        headers=headers,
    )
    assert applied.status_code == 200, applied.text
    second_validation = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{second_ingestion_id}/validate",
        headers=headers,
    )
    assert second_validation.status_code == 200, second_validation.text
    assert second_validation.json()["ingestion"]["status"] == "review_required"
    second_promotion = client.post(
        f"{universal_base}/sources/{source_id}/ingestions/{second_ingestion_id}/promote",
        json={
            "acknowledge_warnings": True,
            "correction_reason": "Approved correction from the source owner",
        },
        headers=headers,
    )
    assert second_promotion.status_code == 200, second_promotion.text

    stale_relationships = client.get(f"{semantic_base}/relationships", headers=headers)
    assert stale_relationships.status_code == 200, stale_relationships.text
    assert stale_relationships.json()["items"][0]["analytically_usable"] is False
    stale_reverification = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": identity["id"],
            "relationship_type": "customer_has_sales_transaction",
            "object_entity_id": sales_identity["id"],
            "expected_version": 1,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": semantic_mapping_v1["id"],
            "object_mapping_version_id": sales_semantic_mapping_v1["id"],
            "evidence_note": "A stale customer source revision must not be reverified",
        },
        headers=headers,
    )
    assert stale_reverification.status_code == 409
    assert stale_reverification.json()["error"]["code"] == (
        "semantic_relationship_stale_source_revision"
    )

    stale_mappings = client.get(f"{semantic_base}/mappings", headers=headers)
    assert stale_mappings.status_code == 200, stale_mappings.text
    stale_customer_mapping = next(
        item
        for item in stale_mappings.json()["items"]
        if item["reference_id"] == source_record["id"]
    )
    assert stale_customer_mapping["source_revision_current"] is False
    refreshed = client.post(
        f"{semantic_base}/mappings",
        json={
            "reference_kind": "universal_record",
            "reference_id": source_record["id"],
            "semantic_entity_id": identity["id"],
            "expected_version": 1,
            "review_note": "Re-reviewed after the source record advanced to revision two",
        },
        headers=headers,
    )
    assert refreshed.status_code == 201, refreshed.text
    assert refreshed.json()["version"] == 2
    assert refreshed.json()["source_revision"] == 2
    assert refreshed.json()["source_revision_current"] is True
    assert refreshed.json()["correction_reason"] is None
    relationship_v2 = client.post(
        f"{semantic_base}/relationships",
        json={
            "subject_entity_id": identity["id"],
            "relationship_type": "customer_has_sales_transaction",
            "object_entity_id": sales_identity["id"],
            "expected_version": 1,
            "evidence_kind": "source_verified",
            "subject_mapping_version_id": refreshed.json()["id"],
            "object_mapping_version_id": sales_semantic_mapping_v1["id"],
            "evidence_note": "The customer and transaction source codes were reverified",
        },
        headers=headers,
    )
    assert relationship_v2.status_code == 201, relationship_v2.text
    assert relationship_v2.json()["version"] == 2
    assert relationship_v2.json()["analytically_usable"] is True
    relationship_history = client.get(
        f"{semantic_base}/relationships?include_history=true", headers=headers
    )
    assert relationship_history.status_code == 200, relationship_history.text
    transaction_relationship_versions = relationship_history.json()["items"]
    assert [item["version"] for item in transaction_relationship_versions] == [2, 1]
    assert [item["is_current"] for item in transaction_relationship_versions] == [True, False]
    assert [item["analytically_usable"] for item in transaction_relationship_versions] == [
        True,
        False,
    ]

    history = client.get(f"{semantic_base}/mappings?include_history=true", headers=headers)
    assert history.status_code == 200, history.text
    versions = [
        item for item in history.json()["items"] if item["reference_id"] == source_record["id"]
    ]
    assert [item["version"] for item in versions] == [2, 1]
    assert [item["source_revision_current"] for item in versions] == [True, False]
    assert [item["is_current"] for item in versions] == [True, False]


def test_semantic_tables_force_company_rls_and_are_append_only(
    client, admin_engine, runtime_engine
):
    owner = register_user(client, "semantic-rls-owner@example.com")
    headers = auth_headers(owner)
    tenant = create_tenant(
        client,
        owner,
        name="Semantic RLS Tenant",
        company_name="Semantic RLS Company",
        key="semantic-rls-tenant-01",
    )
    entity = _create_entity(
        client,
        _semantic_base(tenant["id"]),
        headers,
        entity_type="operational_record",
        entity_key="ops-record-1",
        display_name="Operations record identity",
    )

    tables = (
        "business_semantic_entities",
        "business_semantic_mapping_versions",
        "business_semantic_relationship_versions",
    )
    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT relname, relrowsecurity, relforcerowsecurity "
                "FROM pg_class WHERE relname = ANY(CAST(:names AS text[]))"
            ),
            {"names": list(tables)},
        ).all()
        assert {row.relname for row in rows} == set(tables)
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)

    with Session(runtime_engine) as session, session.begin():
        set_tenant_context(session, UUID(tenant["id"]))
        set_company_context(session, UUID(tenant["company"]["id"]))
        visible_ids = {item.id for item in session.scalars(select(BusinessSemanticEntity)).all()}
        assert visible_ids == {UUID(entity["id"])}

    entity_id = UUID(entity["id"])
    for statement in (
        "UPDATE business_semantic_entities SET display_name = 'Mutated' WHERE id = :entity_id",
        "DELETE FROM business_semantic_entities WHERE id = :entity_id",
    ):
        with pytest.raises(DBAPIError), admin_engine.begin() as connection:
            connection.execute(text(statement), {"entity_id": entity_id})


def test_semantic_migration_downgrade_is_rejected_when_data_is_retained(
    client, migration_engine, monkeypatch
):
    owner = register_user(client, "semantic-downgrade-owner@example.com")
    tenant = create_tenant(
        client,
        owner,
        name="Semantic Downgrade Tenant",
        company_name="Semantic Downgrade Company",
        key="semantic-downgrade-tenant-01",
    )
    _create_entity(
        client,
        _semantic_base(tenant["id"]),
        auth_headers(owner),
        entity_type="customer",
        entity_key="customer-downgrade-guard",
        display_name="Retained semantic identity",
    )

    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    migration_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
    monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url)
    config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
    with pytest.raises(DBAPIError, match="Cannot downgrade while semantic data is retained"):
        command.downgrade(config, "0019_universal_data_foundation")

    with migration_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0022_bi_config_analysis"
        )
        assert (
            connection.scalar(
                text(
                    "SELECT relforcerowsecurity FROM pg_class "
                    "WHERE relname = 'business_semantic_entities'"
                )
            )
            is True
        )
