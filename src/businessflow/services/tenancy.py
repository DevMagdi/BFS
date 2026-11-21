from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.authorization import list_active_memberships, lock_and_revalidate_access
from businessflow.database import set_tenant_context, set_user_context
from businessflow.errors import ApiError
from businessflow.models import (
    Company,
    Tenant,
    TenantMembership,
    TenantOnboardingRequest,
    UserAccount,
)
from businessflow.schemas import CompanyProfileUpdate, TenantCreateRequest


@dataclass(frozen=True, slots=True)
class TenantBundle:
    tenant: Tenant
    company: Company
    membership: TenantMembership
    created: bool = True


def _bundle_for_existing_request(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
) -> TenantBundle:
    set_tenant_context(session, tenant_id)
    membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
    )
    tenant = session.scalar(select(Tenant).where(Tenant.id == tenant_id))
    company = session.scalar(select(Company).where(Company.tenant_id == tenant_id))
    if membership is None or tenant is None or company is None or tenant.status != "active":
        raise ApiError(404, "tenant_not_found", "Tenant was not found.")
    return TenantBundle(tenant=tenant, company=company, membership=membership, created=False)


def create_tenant(
    session: Session,
    user: UserAccount,
    body: TenantCreateRequest,
    idempotency_key: str,
    request_id: UUID,
) -> TenantBundle:
    set_user_context(session, user.id)
    key_hash = hashlib.sha256(idempotency_key.encode()).hexdigest()
    payload_bytes = json.dumps(
        body.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    ).encode()
    request_hash = hashlib.sha256(payload_bytes).hexdigest()
    tenant_id = uuid4()
    company_id = uuid4()
    set_tenant_context(session, tenant_id)

    inserted_id = session.scalar(
        pg_insert(TenantOnboardingRequest)
        .values(
            user_id=user.id,
            key_hash=key_hash,
            request_hash=request_hash,
            tenant_id=tenant_id,
        )
        .on_conflict_do_nothing(
            index_elements=[TenantOnboardingRequest.user_id, TenantOnboardingRequest.key_hash]
        )
        .returning(TenantOnboardingRequest.tenant_id)
    )
    if inserted_id is None:
        existing = session.execute(
            select(
                TenantOnboardingRequest.tenant_id,
                TenantOnboardingRequest.request_hash,
            ).where(
                TenantOnboardingRequest.user_id == user.id,
                TenantOnboardingRequest.key_hash == key_hash,
            )
        ).one_or_none()
        if existing is None:
            raise ApiError(
                409,
                "idempotency_conflict",
                "The onboarding request could not be reused.",
            )
        if existing.request_hash != request_hash:
            raise ApiError(
                409,
                "idempotency_key_reused",
                "The idempotency key was already used for different request data.",
            )
        return _bundle_for_existing_request(session, user, existing.tenant_id)

    tenant = Tenant(
        id=tenant_id,
        name=body.name,
        primary_company_id=company_id,
        status="active",
    )
    company = Company(
        id=company_id,
        tenant_id=tenant_id,
        legal_name=body.company.legal_name,
        base_currency=body.company.base_currency,
        timezone=body.company.timezone,
        locale=body.company.locale,
        status="active",
    )
    membership = TenantMembership(
        id=uuid4(), tenant_id=tenant_id, user_id=user.id, role="owner", status="active"
    )
    # The tenant-to-primary-company FK is deferrable, so stage the tenant row first;
    # the company-to-tenant FK is immediate and must see the tenant before it is inserted.
    session.add(tenant)
    session.flush()
    session.add_all([company, membership])
    session.flush()

    record_event(
        session,
        "tenant.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="tenant",
        target_id=tenant_id,
        request_id=request_id,
    )
    record_event(
        session,
        "company.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="company",
        target_id=company_id,
        request_id=request_id,
    )
    record_event(
        session,
        "membership.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="tenant_membership",
        target_id=membership.id,
        request_id=request_id,
        details={"role": "owner", "user_id": user.id},
    )
    session.flush()
    return TenantBundle(tenant=tenant, company=company, membership=membership)


def list_user_tenants(session: Session, user: UserAccount) -> list[TenantBundle]:
    set_user_context(session, user.id)
    memberships = list_active_memberships(session, user.id)
    bundles: list[TenantBundle] = []
    for membership in memberships:
        set_tenant_context(session, membership.tenant_id)
        tenant = session.scalar(
            select(Tenant).where(Tenant.id == membership.tenant_id, Tenant.status == "active")
        )
        company = session.scalar(
            select(Company).where(
                Company.tenant_id == membership.tenant_id,
                Company.status == "active",
            )
        )
        if tenant is not None and company is not None:
            bundles.append(TenantBundle(tenant=tenant, company=company, membership=membership))
    return bundles


def get_tenant_bundle(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
) -> TenantBundle:
    from businessflow.authorization import require_tenant_access

    access = require_tenant_access(session, user, tenant_id, "tenant:read")
    company = session.scalar(
        select(Company).where(Company.tenant_id == tenant_id, Company.status == "active")
    )
    if company is None:
        raise ApiError(404, "tenant_not_found", "Tenant was not found.")
    return TenantBundle(tenant=access.tenant, company=company, membership=access.membership)


def update_tenant_name(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    name: str,
    request_id: UUID,
) -> Tenant:
    from businessflow.authorization import require_tenant_access

    access = require_tenant_access(session, user, tenant_id, "tenant:update", request_id=request_id)
    access = lock_and_revalidate_access(session, access, "tenant:update")
    if access.tenant.name != name:
        old_name = access.tenant.name
        access.tenant.name = name
        record_event(
            session,
            "tenant.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="tenant",
            target_id=tenant_id,
            request_id=request_id,
            details={"changed_fields": ["name"], "previous_name": old_name},
        )
        session.flush()
    return access.tenant


def update_company_profile(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    changes: CompanyProfileUpdate,
    request_id: UUID,
) -> Company:
    from businessflow.authorization import require_tenant_access

    access = require_tenant_access(
        session, user, tenant_id, "company:update", request_id=request_id
    )
    lock_and_revalidate_access(session, access, "company:update")
    company = session.scalar(
        select(Company).where(Company.tenant_id == tenant_id, Company.status == "active")
    )
    if company is None:
        raise ApiError(404, "company_not_found", "Company was not found.")
    patch = changes.model_dump(exclude_unset=True, exclude_none=True)
    changed: list[str] = []
    for field, value in patch.items():
        if getattr(company, field) != value:
            setattr(company, field, value)
            changed.append(field)
    if changed:
        record_event(
            session,
            "company.updated",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="company",
            target_id=company.id,
            request_id=request_id,
            details={"changed_fields": changed},
        )
        session.flush()
    return company
