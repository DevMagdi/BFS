from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient


def register_user(
    client: TestClient,
    email: str,
    password: str = "Correct horse battery 42!",
    display_name: str = "Test User",
) -> dict[str, Any]:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "display_name": display_name, "password": password},
    )
    assert response.status_code == 201, response.text
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
    response = client.post(
        f"/api/v1/tenants/{tenant_id}/memberships",
        json={"email": email, "role": role},
        headers=auth_headers(owner_tokens),
    )
    assert response.status_code == 201, response.text
    return response.json()
