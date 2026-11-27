from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest
from starlette.requests import Request

from businessflow.api import company_context_routes
from businessflow.authorization import ROLE_PERMISSIONS


def test_workforce_and_universal_data_reads_are_owner_admin_only():
    for permission in ("workforce:read", "universal_data:read"):
        assert permission in ROLE_PERMISSIONS["owner"]
        assert permission in ROLE_PERMISSIONS["admin"]
        assert permission not in ROLE_PERMISSIONS["member"]
    assert {"customer_commitment:read", "customer_commitment:write"} <= ROLE_PERMISSIONS["member"]
    assert "customer_commitment:review" not in ROLE_PERMISSIONS["member"]


@pytest.mark.parametrize(
    "route_path",
    [
        "/tenants/{tenant_id}/employees",
        "/tenants/{tenant_id}/employees/{employee_id}/history",
        "/tenants/{tenant_id}/skills",
        "/tenants/{tenant_id}/projects",
        "/tenants/{tenant_id}/projects/{project_id}/assignments",
        "/tenants/{tenant_id}/resource-assignments/{assignment_id}/history",
        "/tenants/{tenant_id}/company-context",
    ],
)
def test_sensitive_company_context_routes_require_workforce_read(monkeypatch, route_path: str):
    tenant_id = uuid4()
    user = object()
    session = object()
    access_check = Mock()
    monkeypatch.setattr(company_context_routes, "require_company_context_access", access_check)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": route_path.replace("{tenant_id}", str(tenant_id)),
            "route": SimpleNamespace(path=route_path),
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )

    company_context_routes._protect_workforce_detail_access(request, tenant_id, user, session)

    access_check.assert_called_once_with(session, user, tenant_id, "workforce:read")


def test_safe_organization_structure_route_does_not_require_workforce_read(monkeypatch):
    tenant_id = uuid4()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": f"/api/v1/tenants/{tenant_id}/departments",
            "route": SimpleNamespace(path="/tenants/{tenant_id}/departments"),
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1),
            "scheme": "http",
        }
    )
    access_check = Mock()
    monkeypatch.setattr(company_context_routes, "require_company_context_access", access_check)

    company_context_routes._protect_workforce_detail_access(request, tenant_id, object(), object())

    access_check.assert_not_called()
