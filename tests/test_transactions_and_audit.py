from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from tests.helpers import auth_headers, register_user


def test_onboarding_failure_rolls_back_tenant_company_membership_and_audit(client, admin_engine):
    tokens = register_user(client, "atomicity@example.com")
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "CREATE FUNCTION test_reject_company_created_audit() RETURNS trigger "
                "LANGUAGE plpgsql AS $$ BEGIN "
                "IF NEW.event_type = 'company.created' THEN "
                "RAISE EXCEPTION 'injected failure for atomicity test'; END IF; "
                "RETURN NEW; END; $$"
            )
        )
        connection.execute(
            text(
                "CREATE TRIGGER test_reject_company_created_audit "
                "BEFORE INSERT ON audit_events FOR EACH ROW "
                "EXECUTE FUNCTION test_reject_company_created_audit()"
            )
        )
    try:
        response = client.post(
            "/api/v1/tenants",
            json={
                "name": "Must Roll Back",
                "company": {"legal_name": "Must Roll Back Co", "base_currency": "USD"},
            },
            headers={**auth_headers(tokens), "Idempotency-Key": "atomicity-key-001"},
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"
        assert "injected failure" not in response.text
    finally:
        with admin_engine.begin() as connection:
            connection.execute(
                text("DROP TRIGGER IF EXISTS test_reject_company_created_audit ON audit_events")
            )
            connection.execute(text("DROP FUNCTION IF EXISTS test_reject_company_created_audit()"))

    with admin_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM tenants WHERE name = 'Must Roll Back'"))
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM companies WHERE legal_name = 'Must Roll Back Co'")
            )
            == 0
        )
        user_id = connection.scalar(
            text("SELECT id FROM users WHERE email = 'atomicity@example.com'")
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tenant_memberships WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT count(*) FROM tenant_onboarding_requests WHERE user_id = :user_id"),
                {"user_id": user_id},
            )
            == 0
        )


def test_audit_is_append_only_for_runtime_role(client, runtime_engine):
    tokens = register_user(client, "append-only@example.com")
    tenant = client.post(
        "/api/v1/tenants",
        json={
            "name": "Audit Tenant",
            "company": {"legal_name": "Audit Co", "base_currency": "USD"},
        },
        headers={**auth_headers(tokens), "Idempotency-Key": "audit-append-key-001"},
    )
    assert tenant.status_code == 201
    with runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :id, true)"), {"id": tenant.json()["id"]}
        )
        with pytest.raises(DBAPIError):
            connection.execute(
                text("UPDATE audit_events SET outcome = 'failure' WHERE tenant_id = :id"),
                {"id": tenant.json()["id"]},
            )
