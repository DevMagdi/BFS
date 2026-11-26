from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from businessflow.authorization import lock_and_revalidate_access, require_tenant_access
from businessflow.database import set_company_context
from businessflow.errors import ApiError
from businessflow.models import Company, Tenant, UserAccount


@dataclass(frozen=True, slots=True)
class CompanyContextAccess:
    tenant: Tenant
    company: Company
    user: UserAccount


def require_company_context_access(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    permission: str,
    *,
    request_id: UUID | None = None,
) -> CompanyContextAccess:
    """Resolve the tenant's canonical Company only after verifying membership and permission."""
    tenant_access = require_tenant_access(
        session, user, tenant_id, permission, request_id=request_id
    )
    if permission.endswith((":write", ":review")) or permission == "decision:approve":
        tenant_access = lock_and_revalidate_access(session, tenant_access, permission)
    company = session.scalar(
        select(Company).where(
            Company.id == tenant_access.tenant.primary_company_id,
            Company.tenant_id == tenant_id,
            Company.status == "active",
        )
    )
    if company is None:
        raise ApiError(404, "company_not_found", "Company was not found.")
    set_company_context(session, company.id)
    return CompanyContextAccess(tenant=tenant_access.tenant, company=company, user=user)
