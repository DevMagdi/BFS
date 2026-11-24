from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from businessflow.authorization import TenantAccess, require_tenant_access
from businessflow.database import set_user_context
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.services.tenancy import update_tenant_name
from tests.helpers import auth_headers, create_member, create_tenant, register_user


def test_onboarding_is_atomic_and_idempotent(client, admin_engine):
    tokens = register_user(client, "onboard@example.com")
    request_body = {
        "name": "Northwind",
        "company": {
            "legal_name": "Northwind Services LLC",
            "base_currency": "USD",
            "timezone": "Africa/Cairo",
            "locale": "en",
        },
    }
    headers = {**auth_headers(tokens), "Idempotency-Key": "unique-onboard-00001"}
    created = client.post("/api/v1/tenants", json=request_body, headers=headers)
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["role"] == "owner"
    assert created_body["company"]["tenant_id"] == created_body["id"]
    assert created_body["company"]["legal_name"] == "Northwind Services LLC"

    replay = client.post("/api/v1/tenants", json=request_body, headers=headers)
    assert replay.status_code == 200
    assert replay.json()["id"] == created_body["id"]
    assert replay.json()["company"]["id"] == created_body["company"]["id"]

    mismatch = client.post(
        "/api/v1/tenants",
        json={**request_body, "name": "Different payload"},
        headers=headers,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["error"]["code"] == "idempotency_key_reused"

    tenant_id = created_body["id"]
    with admin_engine.connect() as connection:
        tenant_count = connection.scalar(
            text("SELECT count(*) FROM tenants WHERE id = :tenant_id"), {"tenant_id": tenant_id}
        )
        company_count = connection.scalar(
            text("SELECT count(*) FROM companies WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        )
        membership = connection.execute(
            text("SELECT role, status FROM tenant_memberships WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).one()
    assert tenant_count == company_count == 1
    assert membership.role == "owner" and membership.status == "active"


def test_owner_admin_member_permissions_and_membership_lifecycle(client):
    owner_tokens = register_user(client, "owner-rbac@example.com", display_name="Owner")
    tenant = create_tenant(client, owner_tokens, key="rbac-owner-key-001")
    tenant_id = tenant["id"]
    admin_tokens = register_user(client, "admin-rbac@example.com", display_name="Admin")
    create_member(client, owner_tokens, tenant_id, "admin-rbac@example.com", "admin")
    member_tokens = register_user(client, "member-rbac@example.com", display_name="Member")
    member_membership = create_member(client, owner_tokens, tenant_id, "member-rbac@example.com")
    owner_membership_page = client.get(
        f"/api/v1/tenants/{tenant_id}/memberships", headers=auth_headers(owner_tokens)
    )
    assert owner_membership_page.status_code == 200
    owner_membership_id = next(
        item["id"] for item in owner_membership_page.json()["items"] if item["role"] == "owner"
    )

    assert (
        client.get(f"/api/v1/tenants/{tenant_id}", headers=auth_headers(member_tokens)).status_code
        == 200
    )
    assert (
        client.get(
            f"/api/v1/tenants/{tenant_id}/company", headers=auth_headers(member_tokens)
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/tenants/{tenant_id}/company",
            json={"legal_name": "Unauthorized"},
            headers=auth_headers(member_tokens),
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/tenants/{tenant_id}/memberships", headers=auth_headers(member_tokens)
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/tenants/{tenant_id}/audit-events", headers=auth_headers(member_tokens)
        ).status_code
        == 403
    )

    own_role = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{member_membership['id']}",
        json={"role": "owner"},
        headers=auth_headers(member_tokens),
    )
    assert own_role.status_code == 403

    admin_update = client.patch(
        f"/api/v1/tenants/{tenant_id}/company",
        json={"legal_name": "Admin Updated Company"},
        headers=auth_headers(admin_tokens),
    )
    assert admin_update.status_code == 200
    assert admin_update.json()["legal_name"] == "Admin Updated Company"
    assert (
        client.patch(
            f"/api/v1/tenants/{tenant_id}",
            json={"name": "Admin must not rename tenant"},
            headers=auth_headers(admin_tokens),
        ).status_code
        == 403
    )

    register_user(client, "added-member@example.com")
    unavailable = client.post(
        f"/api/v1/tenants/{tenant_id}/memberships",
        json={"email": "added-member@example.com", "role": "member"},
        headers=auth_headers(admin_tokens),
    )
    assert unavailable.status_code == 503
    assert unavailable.json()["error"]["code"] == "membership_invitation_unavailable"
    denied_admin_grant = client.post(
        f"/api/v1/tenants/{tenant_id}/memberships",
        json={"email": "added-member@example.com", "role": "admin"},
        headers=auth_headers(admin_tokens),
    )
    assert denied_admin_grant.status_code == 403
    unchanged_memberships = client.get(
        f"/api/v1/tenants/{tenant_id}/memberships", headers=auth_headers(owner_tokens)
    )
    assert all(
        member["email"] != "added-member@example.com"
        for member in unchanged_memberships.json()["items"]
    )
    denied_owner_change = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{owner_membership_id}",
        json={"status": "revoked"},
        headers=auth_headers(admin_tokens),
    )
    assert denied_owner_change.status_code == 403

    self_change = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{owner_membership_id}",
        json={"role": "member"},
        headers=auth_headers(owner_tokens),
    )
    assert self_change.status_code == 409
    assert self_change.json()["error"]["code"] == "self_membership_change_denied"

    promoted = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{member_membership['id']}",
        json={"role": "admin"},
        headers=auth_headers(owner_tokens),
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    # Revocation is soft and audited; it invalidates subsequent tenant access.
    revoke = client.delete(
        f"/api/v1/tenants/{tenant_id}/memberships/{member_membership['id']}",
        headers=auth_headers(owner_tokens),
    )
    assert revoke.status_code == 204
    denied = client.get(f"/api/v1/tenants/{tenant_id}", headers=auth_headers(member_tokens))
    assert denied.status_code == 404

    audit = client.get(
        f"/api/v1/tenants/{tenant_id}/audit-events", headers=auth_headers(owner_tokens)
    )
    assert audit.status_code == 200
    types = {item["event_type"] for item in audit.json()["items"]}
    assert {"tenant.created", "company.created", "membership.created", "company.updated"} <= types
    assert "membership.role_changed" in types
    assert "membership.revoked" in types


def test_cross_tenant_read_update_delete_and_spoofing_are_rejected(client):
    owner_a = register_user(client, "tenant-a@example.com")
    tenant_a = create_tenant(
        client, owner_a, name="A", company_name="Company A", key="tenant-a-key-001"
    )
    owner_b = register_user(client, "tenant-b@example.com")
    tenant_b = create_tenant(
        client, owner_b, name="B", company_name="Company B", key="tenant-b-key-001"
    )
    member_b = register_user(client, "member-b@example.com")
    membership_b = create_member(client, owner_b, tenant_b["id"], "member-b@example.com")

    assert (
        client.get(f"/api/v1/tenants/{tenant_b['id']}", headers=auth_headers(owner_a)).status_code
        == 404
    )
    assert (
        client.get(
            f"/api/v1/tenants/{tenant_b['id']}/company", headers=auth_headers(owner_a)
        ).status_code
        == 404
    )
    assert (
        client.patch(
            f"/api/v1/tenants/{tenant_b['id']}/company",
            json={"legal_name": "Spoofed"},
            headers=auth_headers(owner_a),
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"/api/v1/tenants/{tenant_b['id']}/memberships",
            json={"email": "tenant-a@example.com", "role": "owner"},
            headers=auth_headers(owner_a),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/tenants/{tenant_b['id']}/memberships/{membership_b['id']}",
            headers=auth_headers(owner_a),
        ).status_code
        == 404
    )
    assert (
        client.delete(
            f"/api/v1/tenants/{tenant_a['id']}/memberships/{membership_b['id']}",
            headers=auth_headers(owner_a),
        ).status_code
        == 404
    )
    assert (
        client.get(f"/api/v1/tenants/{tenant_b['id']}", headers=auth_headers(member_b)).status_code
        == 200
    )

    spoofed_company = client.patch(
        f"/api/v1/tenants/{tenant_a['id']}/company",
        json={"legal_name": "Company A", "tenant_id": tenant_b["id"]},
        headers=auth_headers(owner_a),
    )
    assert spoofed_company.status_code == 422
    assert tenant_b["id"] not in spoofed_company.text

    # Tenants and companies have no physical-delete API; runtime SQL also lacks DELETE privilege.
    assert (
        client.delete(
            f"/api/v1/tenants/{tenant_b['id']}/company", headers=auth_headers(owner_a)
        ).status_code
        == 405
    )


def test_owner_constraint_is_enforced_by_application_and_postgresql(
    client, runtime_engine, admin_engine
):
    owner = register_user(client, "sole-owner@example.com")
    tenant = create_tenant(client, owner, key="sole-owner-key-001")
    tenant_id = tenant["id"]
    membership_id = client.get(
        f"/api/v1/tenants/{tenant_id}/memberships", headers=auth_headers(owner)
    ).json()["items"][0]["id"]

    response = client.patch(
        f"/api/v1/tenants/{tenant_id}/memberships/{membership_id}",
        json={"status": "revoked"},
        headers=auth_headers(owner),
    )
    assert response.status_code == 409

    owner_user_id = client.get("/api/v1/auth/me", headers=auth_headers(owner)).json()["id"]
    with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.user_id', :user_id, true)"),
            {"user_id": owner_user_id},
        )
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant_id},
        )
        connection.execute(
            text(
                "UPDATE tenant_memberships SET status = 'revoked', revoked_at = now() "
                "WHERE id = :membership_id"
            ),
            {"membership_id": membership_id},
        )

    orphan_tenant_id, orphan_company_id = uuid4(), uuid4()
    with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
        connection.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": str(orphan_tenant_id)},
        )
        connection.execute(
            text(
                "INSERT INTO tenants (id, name, primary_company_id) "
                "VALUES (:tenant_id, 'Orphan', :company_id)"
            ),
            {"tenant_id": orphan_tenant_id, "company_id": orphan_company_id},
        )
        connection.execute(
            text(
                "INSERT INTO companies (id, tenant_id, legal_name, base_currency) "
                "VALUES (:company_id, :tenant_id, 'Orphan Co', 'USD')"
            ),
            {"tenant_id": orphan_tenant_id, "company_id": orphan_company_id},
        )

    with admin_engine.connect() as connection:
        status = connection.scalar(
            text("SELECT status FROM tenant_memberships WHERE id = :id"), {"id": membership_id}
        )
    assert status == "active"


def test_concurrent_owner_changes_cannot_remove_all_owners(app):
    owner_a_client = TestClient(app, raise_server_exceptions=False)
    owner_b_client = TestClient(app, raise_server_exceptions=False)
    with owner_a_client as client_a, owner_b_client as client_b:
        owner_a = register_user(client_a, "concurrent-owner-a@example.com")
        tenant = create_tenant(client_a, owner_a, key="concurrent-owner-key-001")
        owner_b = register_user(client_b, "concurrent-owner-b@example.com")
        membership_b = create_member(
            client_a, owner_a, tenant["id"], "concurrent-owner-b@example.com", "owner"
        )
        membership_a_id = next(
            item["id"]
            for item in client_a.get(
                f"/api/v1/tenants/{tenant['id']}/memberships", headers=auth_headers(owner_a)
            ).json()["items"]
            if item["email"] == "concurrent-owner-a@example.com"
        )

        def revoke(client: TestClient, token: dict, target_id: str):
            return client.delete(
                f"/api/v1/tenants/{tenant['id']}/memberships/{target_id}",
                headers=auth_headers(token),
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(
                pool.map(
                    lambda args: revoke(*args),
                    [
                        (client_a, owner_a, membership_b["id"]),
                        (client_b, owner_b, membership_a_id),
                    ],
                )
            )
    assert 204 in statuses
    assert all(status in {204, 404, 409} for status in statuses)
    with app.state.session_factory() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        active_owner_count = session.scalar(
            text(
                "SELECT count(*) FROM tenant_memberships "
                "WHERE tenant_id = :tenant_id AND role = 'owner' AND status = 'active'"
            ),
            {"tenant_id": tenant["id"]},
        )
    assert active_owner_count >= 1


def _create_stale_access_tenant(client: TestClient, suffix: str) -> tuple[str, dict[str, str]]:
    actor_email = f"stale-access-{suffix}@example.com"
    actor = register_user(client, actor_email)
    tenant = create_tenant(client, actor, key=f"stale-access-key-{suffix}")
    second_owner_email = f"stale-second-owner-{suffix}@example.com"
    register_user(client, second_owner_email)
    create_member(client, actor, tenant["id"], second_owner_email, "owner")
    return actor_email, tenant


def _preauthorize_tenant_write(
    session, actor_email: str, tenant_id: UUID
) -> tuple[UserAccount, TenantAccess]:
    user = session.scalar(select(UserAccount).where(UserAccount.email == actor_email))
    assert user is not None
    set_user_context(session, user.id)
    access = require_tenant_access(session, user, tenant_id, "tenant:update")
    assert access.membership.role == "owner"
    assert access.membership.status == "active"
    assert access.tenant.status == "active"
    return user, access


def test_postgres_write_revalidates_committed_owner_to_member_demotion(client, app, admin_engine):
    suffix = uuid4().hex[:12]
    actor_email, tenant = _create_stale_access_tenant(client, suffix)
    tenant_id = UUID(tenant["id"])

    # Keep the request transaction open after it has loaded owner access. A separate
    # committed PostgreSQL transaction changes the row before the privileged write.
    with app.state.session_factory() as session, session.begin():
        user, stale_access = _preauthorize_tenant_write(session, actor_email, tenant_id)
        original_name = tenant["name"]
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_memberships SET role = 'member' "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user.id},
            )

        with pytest.raises(ApiError) as denied:
            update_tenant_name(session, user, tenant_id, "Must not be written", uuid4())
        assert denied.value.status_code == 403
        assert denied.value.code == "forbidden"
        assert stale_access.membership.role == "member"

    with admin_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT tenants.name, tenant_memberships.role "
                "FROM tenants JOIN tenant_memberships ON tenant_memberships.tenant_id = tenants.id "
                "WHERE tenants.id = :tenant_id AND tenant_memberships.user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user.id},
        ).one()
    assert stored.name == original_name
    assert stored.role == "member"


def test_postgres_write_revalidates_committed_membership_revocation(client, app, admin_engine):
    suffix = uuid4().hex[:12]
    actor_email, tenant = _create_stale_access_tenant(client, suffix)
    tenant_id = UUID(tenant["id"])

    with app.state.session_factory() as session, session.begin():
        user, stale_access = _preauthorize_tenant_write(session, actor_email, tenant_id)
        original_name = tenant["name"]
        with admin_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE tenant_memberships "
                    "SET status = 'revoked', revoked_at = now() "
                    "WHERE tenant_id = :tenant_id AND user_id = :user_id"
                ),
                {"tenant_id": tenant_id, "user_id": user.id},
            )

        with pytest.raises(ApiError) as denied:
            update_tenant_name(session, user, tenant_id, "Must not be written", uuid4())
        assert denied.value.status_code == 404
        assert denied.value.code == "tenant_not_found"
        assert stale_access.membership.status == "revoked"

    with admin_engine.connect() as connection:
        stored = connection.execute(
            text(
                "SELECT tenants.name, tenant_memberships.status, tenant_memberships.revoked_at "
                "FROM tenants JOIN tenant_memberships ON tenant_memberships.tenant_id = tenants.id "
                "WHERE tenants.id = :tenant_id AND tenant_memberships.user_id = :user_id"
            ),
            {"tenant_id": tenant_id, "user_id": user.id},
        ).one()
    assert stored.name == original_name
    assert stored.status == "revoked"
    assert stored.revoked_at is not None


def test_postgres_write_revalidates_committed_tenant_deactivation(client, app, admin_engine):
    suffix = uuid4().hex[:12]
    actor_email, tenant = _create_stale_access_tenant(client, suffix)
    tenant_id = UUID(tenant["id"])

    with app.state.session_factory() as session, session.begin():
        user, stale_access = _preauthorize_tenant_write(session, actor_email, tenant_id)
        original_name = tenant["name"]
        with admin_engine.begin() as connection:
            connection.execute(
                text("UPDATE tenants SET status = 'disabled', updated_at = now() WHERE id = :id"),
                {"id": tenant_id},
            )

        with pytest.raises(ApiError) as denied:
            update_tenant_name(session, user, tenant_id, "Must not be written", uuid4())
        assert denied.value.status_code == 404
        assert denied.value.code == "tenant_not_found"
        assert stale_access.tenant.status == "disabled"

    with admin_engine.connect() as connection:
        stored = connection.execute(
            text("SELECT name, status FROM tenants WHERE id = :tenant_id"),
            {"tenant_id": tenant_id},
        ).one()
    assert stored.name == original_name
    assert stored.status == "disabled"
