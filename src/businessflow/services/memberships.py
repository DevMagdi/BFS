from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.authorization import (
    TenantAccess,
    lock_and_revalidate_access,
    require_tenant_access,
)
from businessflow.errors import ApiError
from businessflow.models import TenantMembership, UserAccount
from businessflow.schemas import MembershipCreateRequest, MembershipUpdateRequest


@dataclass(frozen=True, slots=True)
class MembershipRow:
    membership: TenantMembership
    user: UserAccount


def list_memberships(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[MembershipRow], int]:
    require_tenant_access(session, user, tenant_id, "membership:read")
    total = (
        session.scalar(
            select(func.count())
            .select_from(TenantMembership)
            .where(TenantMembership.tenant_id == tenant_id)
        )
        or 0
    )
    rows = session.execute(
        select(TenantMembership, UserAccount)
        .join(UserAccount, UserAccount.id == TenantMembership.user_id)
        .where(TenantMembership.tenant_id == tenant_id)
        .order_by(TenantMembership.created_at, TenantMembership.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return [MembershipRow(membership=row[0], user=row[1]) for row in rows], total


def create_membership(
    session: Session,
    access: TenantAccess,
    body: MembershipCreateRequest,
    request_id: UUID,
) -> MembershipRow:
    access = lock_and_revalidate_access(session, access, "membership:create")
    if access.membership.role == "admin" and body.role != "member":
        raise ApiError(403, "forbidden", "Administrators may add member-role accounts only.")
    email = str(body.email).strip().lower()
    target_user = session.scalar(select(UserAccount).where(UserAccount.email == email))
    if target_user is None or target_user.status != "active":
        raise ApiError(404, "account_not_found", "An active account with that email was not found.")
    existing = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == access.tenant.id,
            TenantMembership.user_id == target_user.id,
        )
    )
    if existing is not None:
        raise ApiError(409, "membership_exists", "That account already has a tenant membership.")

    membership = TenantMembership(
        id=uuid4(),
        tenant_id=access.tenant.id,
        user_id=target_user.id,
        role=body.role,
        status="active",
    )
    session.add(membership)
    session.flush()
    record_event(
        session,
        "membership.created",
        tenant_id=access.tenant.id,
        actor_user_id=access.user.id,
        target_type="tenant_membership",
        target_id=membership.id,
        request_id=request_id,
        details={"role": body.role, "user_id": target_user.id},
    )
    return MembershipRow(membership=membership, user=target_user)


def update_membership(
    session: Session,
    access: TenantAccess,
    membership_id: UUID,
    body: MembershipUpdateRequest,
    request_id: UUID,
) -> MembershipRow:
    access = lock_and_revalidate_access(session, access, "membership:manage")
    tenant_id = access.tenant.id
    # The tenant lock serializes owner-count transitions; the actor role was rechecked under it.
    target = session.scalar(
        select(TenantMembership)
        .where(TenantMembership.id == membership_id, TenantMembership.tenant_id == tenant_id)
        .with_for_update()
    )
    if target is None:
        raise ApiError(404, "membership_not_found", "Membership was not found.")
    target_user = session.scalar(select(UserAccount).where(UserAccount.id == target.user_id))
    if target_user is None:
        raise ApiError(404, "membership_not_found", "Membership was not found.")

    requested_role = body.role if body.role is not None else target.role
    requested_status = body.status if body.status is not None else target.status
    if target.user_id == access.user.id and (
        requested_role != target.role or requested_status != target.status
    ):
        raise ApiError(
            409, "self_membership_change_denied", "You cannot change your own membership."
        )

    if access.membership.role == "admin" and (
        target.role != "member" or requested_role != "member"
    ):
        raise ApiError(403, "forbidden", "Administrators may manage member-role accounts only.")

    owner_is_removed = (
        target.status == "active"
        and target.role == "owner"
        and (requested_status != "active" or requested_role != "owner")
    )
    if owner_is_removed:
        active_owners = (
            session.scalar(
                select(func.count())
                .select_from(TenantMembership)
                .where(
                    TenantMembership.tenant_id == tenant_id,
                    TenantMembership.role == "owner",
                    TenantMembership.status == "active",
                )
            )
            or 0
        )
        if active_owners <= 1:
            raise ApiError(409, "last_owner_required", "The tenant must retain an active owner.")

    old_role, old_status = target.role, target.status
    target.role = requested_role
    target.status = requested_status
    target.revoked_at = datetime.now(UTC) if requested_status == "revoked" else None
    if (old_role, old_status) != (target.role, target.status):
        if old_role != target.role:
            event_type = "membership.role_changed"
        elif target.status == "revoked":
            event_type = "membership.revoked"
        else:
            event_type = "membership.reactivated"
        record_event(
            session,
            event_type,
            tenant_id=tenant_id,
            actor_user_id=access.user.id,
            target_type="tenant_membership",
            target_id=target.id,
            request_id=request_id,
            details={
                "previous_role": old_role,
                "role": target.role,
                "previous_status": old_status,
                "status": target.status,
                "user_id": target.user_id,
            },
        )
        session.flush()
    return MembershipRow(membership=target, user=target_user)
