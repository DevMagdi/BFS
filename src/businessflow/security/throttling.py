from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timedelta

from sqlalchemy import case, delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from businessflow.config import Settings
from businessflow.models import LoginThrottle


def bucket_digest(kind: str, value: str, settings: Settings) -> str:
    message = f"businessflow-login-throttle:v1:{kind}:{value}".encode()
    return hmac.new(
        settings.app_secret_key.get_secret_value().encode("utf-8"), message, hashlib.sha256
    ).hexdigest()


def get_login_bucket_digests(email: str, remote_ip: str, settings: Settings) -> tuple[str, str]:
    return (
        bucket_digest("email", email.casefold(), settings),
        bucket_digest("ip", remote_ip or "unknown", settings),
    )


def is_throttled(session: Session, bucket_hashes: tuple[str, str], now: datetime) -> bool:
    rows = session.scalars(
        select(LoginThrottle).where(LoginThrottle.bucket_hash.in_(bucket_hashes))
    )
    return any(row.locked_until is not None and row.locked_until > now for row in rows)


def record_login_failure(
    session: Session,
    bucket_hashes: tuple[str, str],
    settings: Settings,
    now: datetime,
) -> bool:
    """Atomically increment email and source-IP buckets; values contain no raw identifiers."""
    cutoff = now - timedelta(minutes=settings.login_lockout_minutes)
    locked_until = now + timedelta(minutes=settings.login_lockout_minutes)
    limits = (settings.login_failure_limit, settings.login_ip_failure_limit)
    for bucket_hash, failure_limit in zip(bucket_hashes, limits, strict=True):
        expired = LoginThrottle.window_started_at < cutoff
        next_count = case((expired, 1), else_=LoginThrottle.failure_count + 1)
        stmt = pg_insert(LoginThrottle).values(
            bucket_hash=bucket_hash,
            failure_count=1,
            window_started_at=now,
            locked_until=None,
            updated_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[LoginThrottle.bucket_hash],
            set_={
                "failure_count": next_count,
                "window_started_at": case((expired, now), else_=LoginThrottle.window_started_at),
                "locked_until": case(
                    (expired, None),
                    (LoginThrottle.failure_count + 1 >= failure_limit, locked_until),
                    else_=LoginThrottle.locked_until,
                ),
                "updated_at": now,
            },
        )
        session.execute(stmt)
    session.flush()
    return is_throttled(session, bucket_hashes, now)


def clear_login_email_failure(session: Session, email_bucket_hash: str) -> None:
    # A successful proof of account control resets the account bucket, not the shared IP bucket.
    session.execute(delete(LoginThrottle).where(LoginThrottle.bucket_hash == email_bucket_hash))
