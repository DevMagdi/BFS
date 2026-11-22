from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.database import set_tenant_context
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.models import Tenant, TenantMembership, UserAccount

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "owner": frozenset(
        {
            "tenant:read",
            "tenant:update",
            "company:read",
            "company:update",
            "membership:read",
            "membership:create",
            "membership:manage",
            "audit:read",
            "company_context:read",
            "company_context:write",
        }
    ),
    "admin": frozenset(
        {
            "tenant:read",
            "company:read",
            "company:update",
            "membership:read",
            "membership:create",
            "membership:manage",
            "audit:read",
            "company_context:read",
            "company_context:write",
        }
    ),
    "member": frozenset({"tenant:read", "company:read", "company_context:read"}),
}


@dataclass(frozen=True, slots=True)
class TenantAccess:
    tenant: Tenant
    membership: TenantMembership
    user: UserAccount


def role_allows(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, frozenset())


def require_tenant_access(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    permission: str,
    *,
    request_id: UUID | None = None,
) -> TenantAccess:
    membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
        )
    )
    if membership is None or membership.status != "active":
        record_event(
            session,
            "authz.tenant_access_denied",
            actor_user_id=user.id,
            request_id=request_id,
            outcome="denied",
            details={
                "requested_tenant_hash": sha256(tenant_id.bytes).hexdigest(),
                "reason": "inactive_or_missing_membership",
            },
        )
        raise CommitAndRaise(ApiError(404, "tenant_not_found", "Tenant was not found."))

    set_tenant_context(session, tenant_id)
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    if tenant is None or tenant.status != "active":
        raise CommitAndRaise(ApiError(404, "tenant_not_found", "Tenant was not found."))

    if not role_allows(membership.role, permission):
        record_event(
            session,
            "authz.permission_denied",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            request_id=request_id,
            outcome="denied",
            details={"permission": permission, "role": membership.role},
        )
        raise CommitAndRaise(
            ApiError(403, "forbidden", "You are not allowed to perform this action.")
        )

    return TenantAccess(tenant=tenant, membership=membership, user=user)


def lock_and_revalidate_access(
    session: Session,
    access: TenantAccess,
    permission: str,
) -> TenantAccess:
    """Recheck role/status under the tenant lock before privileged writes."""
    tenant = session.scalar(select(Tenant).where(Tenant.id == access.tenant.id).with_for_update())
    membership = session.scalar(
        select(TenantMembership)
        .where(
            TenantMembership.id == access.membership.id,
            TenantMembership.tenant_id == access.tenant.id,
            TenantMembership.user_id == access.user.id,
        )
        .with_for_update()
    )
    if (
        tenant is None
        or tenant.status != "active"
        or membership is None
        or membership.status != "active"
    ):
        raise ApiError(404, "tenant_not_found", "Tenant was not found.")
    if not role_allows(membership.role, permission):
        raise ApiError(403, "forbidden", "You are not allowed to perform this action.")
    return TenantAccess(tenant=tenant, membership=membership, user=access.user)


def list_active_memberships(session: Session, user_id: UUID) -> list[TenantMembership]:
    return list(
        session.scalars(
            select(TenantMembership)
            .where(TenantMembership.user_id == user_id, TenantMembership.status == "active")
            .order_by(TenantMembership.created_at, TenantMembership.id)
        )
    )
