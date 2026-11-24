from __future__ import annotations

import os
from functools import lru_cache
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.database import set_tenant_context, set_user_context
from businessflow.models import TenantMembership, UserAccount
from businessflow.security.passwords import hash_password


@lru_cache(maxsize=1)
def _test_admin_engine():
    database_url = os.environ.get("TEST_ADMIN_DATABASE_URL")
    if not database_url:
        raise RuntimeError("TEST_ADMIN_DATABASE_URL is required to provision test accounts.")
    return create_engine(database_url, hide_parameters=True)


def register_user(
    client: TestClient,
    email: str,
    password: str = "Correct horse battery 42!",
    display_name: str = "Test User",
) -> dict[str, Any]:
    """Provision a test-only account directly, then exercise the real login endpoint.

    The historical helper name is retained for existing tests; it no longer calls a public
    registration route, which is intentionally disabled in the application.
    """
    normalized_email = email.strip().lower()
    user = UserAccount(
        id=uuid4(),
        email=normalized_email,
        display_name=display_name,
        password_hash=hash_password(password),
        status="active",
    )
    with (
        Session(_test_admin_engine(), autoflush=False, expire_on_commit=False) as session,
        session.begin(),
    ):
        session.add(user)

    response = client.post(
        "/api/v1/auth/login",
        json={"email": normalized_email, "password": password},
    )
    assert response.status_code == 200, response.text
    return response.json()


def auth_headers(tokens: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": f"Bearer {tokens['access_token']}"}


def create_tenant(
    client: TestClient,
    tokens: dict[str, Any],
    *,
    name: str = "Test Tenant",
    company_name: str = "Test Company",
    key: str = "onboard-key-0001",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/tenants",
        json={
            "name": name,
            "company": {
                "legal_name": company_name,
                "base_currency": "USD",
                "timezone": "UTC",
                "locale": "en",
            },
        },
        headers={**auth_headers(tokens), "Idempotency-Key": key},
    )
    assert response.status_code == 201, response.text
    return response.json()


def create_member(
    client: TestClient,
    owner_tokens: dict[str, Any],
    tenant_id: str,
    email: str,
    role: str = "member",
) -> dict[str, Any]:
    """Create a tenant membership as test setup, bypassing the disabled HTTP flow."""
    owner = client.get("/api/v1/auth/me", headers=auth_headers(owner_tokens))
    assert owner.status_code == 200, owner.text
    actor_user_id = UUID(owner.json()["id"])
    normalized_email = email.strip().lower()
    tenant_uuid = UUID(tenant_id)

    with client.app.state.session_factory() as session, session.begin():
        set_user_context(session, actor_user_id)
        set_tenant_context(session, tenant_uuid)
        target_user = session.scalar(
            select(UserAccount).where(UserAccount.email == normalized_email)
        )
        assert target_user is not None and target_user.status == "active"
        membership = TenantMembership(
            id=uuid4(),
            tenant_id=tenant_uuid,
            user_id=target_user.id,
            role=role,
            status="active",
        )
        session.add(membership)
        session.flush()
        record_event(
            session,
            "membership.created",
            tenant_id=tenant_uuid,
            actor_user_id=actor_user_id,
            target_type="tenant_membership",
            target_id=membership.id,
            request_id=uuid4(),
            details={"role": role, "user_id": target_user.id},
        )
        session.flush()
        session.refresh(membership)
        result = {
            "id": str(membership.id),
            "tenant_id": str(membership.tenant_id),
            "user_id": str(target_user.id),
            "email": target_user.email,
            "display_name": target_user.display_name,
            "role": membership.role,
            "status": membership.status,
            "created_at": membership.created_at.isoformat(),
            "updated_at": membership.updated_at.isoformat(),
            "revoked_at": None,
        }
    return result
