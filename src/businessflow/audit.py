from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from businessflow.models import AuditEvent

REDACTED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "access_token",
        "refresh_token",
        "token",
        "authorization",
        "secret",
        "client_secret",
        "credential",
        "cookie",
        "set-cookie",
    }
)


def _safe_details(value: dict[str, Any] | None) -> dict[str, Any]:
    """Allow only shallow, bounded metadata and redact credential-like keys."""
    safe: dict[str, Any] = {}
    for key, item in (value or {}).items():
        normalized = key.lower().replace("-", "_")
        if normalized in REDACTED_KEYS or any(
            word in normalized for word in ("token", "password", "secret")
        ):
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            safe[key[:64]] = item[:256] if isinstance(item, str) else item
        elif isinstance(item, UUID):
            safe[key[:64]] = str(item)
        elif isinstance(item, list) and len(item) <= 20:
            safe[key[:64]] = [str(v)[:128] for v in item]
    return safe


def record_event(
    session: Session,
    event_type: str,
    *,
    tenant_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    target_type: str | None = None,
    target_id: UUID | None = None,
    request_id: UUID | None = None,
    outcome: str = "success",
    details: dict[str, Any] | None = None,
) -> AuditEvent:
    event = AuditEvent(
        tenant_id=tenant_id,
        actor_user_id=actor_user_id,
        event_type=event_type,
        target_type=target_type,
        target_id=target_id,
        request_id=request_id,
        outcome=outcome,
        details=_safe_details(details),
        # Avoid INSERT ... RETURNING for unscoped auth events: PostgreSQL applies the
        # SELECT policy to RETURNING rows, while auth events intentionally remain hidden
        # from ordinary tenant-scoped reads.
        created_at=datetime.now(UTC),
    )
    session.add(event)
    return event
