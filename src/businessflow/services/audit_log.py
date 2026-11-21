from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.authorization import require_tenant_access
from businessflow.models import AuditEvent, UserAccount


def list_audit_events(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    limit: int,
    offset: int,
) -> tuple[list[AuditEvent], int]:
    require_tenant_access(session, user, tenant_id, "audit:read")
    total = (
        session.scalar(
            select(func.count()).select_from(AuditEvent).where(AuditEvent.tenant_id == tenant_id)
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(AuditEvent)
            .where(AuditEvent.tenant_id == tenant_id)
            .order_by(AuditEvent.created_at.desc(), AuditEvent.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total
