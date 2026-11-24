from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.config import Settings
from businessflow.database import set_user_context
from businessflow.models import AuthSession, RefreshToken, UserAccount
from businessflow.schemas import LoginRequest
from businessflow.security.passwords import hash_password, verify_password
from businessflow.security.throttling import (
    clear_login_email_failure,
    get_login_bucket_digests,
    is_throttled,
    record_login_failure,
)
from businessflow.security.tokens import (
    create_access_token,
    digest_refresh_token,
    new_refresh_token,
)


@dataclass(frozen=True, slots=True)
class AuthFailure:
    status_code: int
    code: str
    message: str
    retry_after: int | None = None


@dataclass(frozen=True, slots=True)
class IssuedTokens:
    access_token: str
    refresh_token: str
    expires_in: int
    session_expires_at: datetime
    session_id: UUID


AuthResult = AuthFailure | IssuedTokens


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password(secrets.token_urlsafe(32))


def _issue_session(
    session: Session,
    user: UserAccount,
    settings: Settings,
    now: datetime,
) -> IssuedTokens:
    session_expires_at = now + timedelta(days=settings.refresh_session_days)
    auth_session = AuthSession(
        id=uuid4(),
        user_id=user.id,
        created_at=now,
        last_seen_at=now,
        expires_at=session_expires_at,
    )
    session.add(auth_session)
    session.flush()

    raw_refresh = new_refresh_token()
    session.add(
        RefreshToken(
            id=uuid4(),
            session_id=auth_session.id,
            token_hash=digest_refresh_token(raw_refresh),
            generation=0,
            issued_at=now,
            expires_at=session_expires_at,
        )
    )
    session.flush()
    return IssuedTokens(
        access_token=create_access_token(user.id, auth_session.id, settings),
        refresh_token=raw_refresh,
        expires_in=settings.access_token_ttl_seconds,
        session_expires_at=session_expires_at,
        session_id=auth_session.id,
    )


def login(
    session: Session,
    body: LoginRequest,
    settings: Settings,
    request_id: UUID,
    remote_ip: str,
) -> AuthResult:
    email = str(body.email).strip().lower()
    now = datetime.now(UTC)
    bucket_hashes = get_login_bucket_digests(email, remote_ip, settings)
    if is_throttled(session, bucket_hashes, now):
        record_event(
            session,
            "auth.login_rate_limited",
            request_id=request_id,
            outcome="failure",
            details={"email_bucket_hash": bucket_hashes[0], "source_bucket_hash": bucket_hashes[1]},
        )
        return AuthFailure(
            429,
            "too_many_attempts",
            "Too many sign-in attempts. Try again later.",
            settings.login_lockout_minutes * 60,
        )

    user = session.scalar(select(UserAccount).where(UserAccount.email == email))
    encoded = user.password_hash if user is not None else _dummy_hash()
    password_matches = verify_password(body.password, encoded)
    if user is None or user.status != "active" or not password_matches:
        locked = record_login_failure(session, bucket_hashes, settings, now)
        record_event(
            session,
            "auth.login_failed",
            request_id=request_id,
            outcome="failure",
            details={"email_bucket_hash": bucket_hashes[0], "source_bucket_hash": bucket_hashes[1]},
        )
        if locked:
            return AuthFailure(
                429,
                "too_many_attempts",
                "Too many sign-in attempts. Try again later.",
                settings.login_lockout_minutes * 60,
            )
        return AuthFailure(401, "invalid_credentials", "Invalid email or password.")

    clear_login_email_failure(session, bucket_hashes[0])
    set_user_context(session, user.id)
    tokens = _issue_session(session, user, settings, now)
    record_event(
        session,
        "auth.login_succeeded",
        actor_user_id=user.id,
        target_type="auth_session",
        target_id=tokens.session_id,
        request_id=request_id,
    )
    return tokens


def refresh(
    session: Session,
    raw_refresh_token: str,
    settings: Settings,
    request_id: UUID,
) -> AuthResult:
    generic_failure = AuthFailure(
        401, "invalid_refresh_token", "Refresh token is invalid or expired."
    )
    if not 32 <= len(raw_refresh_token) <= 256:
        return generic_failure

    now = datetime.now(UTC)
    token_identity = session.execute(
        select(RefreshToken.id, RefreshToken.session_id).where(
            RefreshToken.token_hash == digest_refresh_token(raw_refresh_token)
        )
    ).one_or_none()
    if token_identity is None:
        return generic_failure

    # Refresh and logout use one lock order: session first, then token rows. That makes
    # rotation/revocation serialize without a session->token / token->session deadlock.
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.id == token_identity.session_id).with_for_update()
    )
    if auth_session is None or auth_session.revoked_at is not None:
        return generic_failure
    token = session.scalar(
        select(RefreshToken)
        .where(
            RefreshToken.id == token_identity.id,
            RefreshToken.token_hash == digest_refresh_token(raw_refresh_token),
        )
        .with_for_update()
    )
    if token is None or token.session_id != auth_session.id:
        return generic_failure

    if token.consumed_at is not None:
        auth_session.revoked_at = now
        auth_session.revoke_reason = "refresh_reuse"
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        set_user_context(session, auth_session.user_id)
        record_event(
            session,
            "auth.refresh_reuse_detected",
            actor_user_id=auth_session.user_id,
            target_type="auth_session",
            target_id=auth_session.id,
            request_id=request_id,
            outcome="revoked",
        )
        return generic_failure

    if token.revoked_at is not None or token.expires_at <= now or auth_session.expires_at <= now:
        return generic_failure

    user = session.scalar(select(UserAccount).where(UserAccount.id == auth_session.user_id))
    if user is None or user.status != "active":
        auth_session.revoked_at = now
        auth_session.revoke_reason = "account_inactive"
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        set_user_context(session, auth_session.user_id)
        record_event(
            session,
            "auth.session_revoked",
            actor_user_id=auth_session.user_id,
            target_type="auth_session",
            target_id=auth_session.id,
            request_id=request_id,
            outcome="revoked",
            details={"reason": "account_inactive"},
        )
        return generic_failure

    raw_replacement = new_refresh_token()
    replacement = RefreshToken(
        id=uuid4(),
        session_id=auth_session.id,
        token_hash=digest_refresh_token(raw_replacement),
        generation=token.generation + 1,
        issued_at=now,
        expires_at=auth_session.expires_at,
    )
    session.add(replacement)
    session.flush()
    token.consumed_at = now
    token.replaced_by_id = replacement.id
    auth_session.last_seen_at = now
    set_user_context(session, user.id)
    record_event(
        session,
        "auth.refresh_rotated",
        actor_user_id=user.id,
        target_type="auth_session",
        target_id=auth_session.id,
        request_id=request_id,
    )
    return IssuedTokens(
        access_token=create_access_token(user.id, auth_session.id, settings),
        refresh_token=raw_replacement,
        expires_in=settings.access_token_ttl_seconds,
        session_expires_at=auth_session.expires_at,
        session_id=auth_session.id,
    )


def logout(
    session: Session,
    request_id: UUID,
    *,
    session_id: UUID | None = None,
    user_id: UUID | None = None,
    raw_refresh_token: str | None = None,
) -> AuthFailure | None:
    """Revoke one session using a verified access or refresh credential.

    A refresh credential can identify the session after its access JWT has expired. The
    parent session is locked before any token rows, matching refresh rotation's lock order.
    """
    invalid = AuthFailure(401, "invalid_logout_credential", "Logout credentials are invalid.")
    refresh_identity = None
    refresh_digest = None
    if raw_refresh_token is not None:
        if not 32 <= len(raw_refresh_token) <= 256:
            return invalid
        refresh_digest = digest_refresh_token(raw_refresh_token)
        refresh_identity = session.execute(
            select(RefreshToken.id, RefreshToken.session_id).where(
                RefreshToken.token_hash == refresh_digest
            )
        ).one_or_none()
        if refresh_identity is None:
            return invalid
        session_id = refresh_identity.session_id
        user_id = None
    elif session_id is None or user_id is None:
        return invalid

    session_query = select(AuthSession).where(AuthSession.id == session_id)
    if user_id is not None:
        session_query = session_query.where(AuthSession.user_id == user_id)
    auth_session = session.scalar(session_query.with_for_update())
    if auth_session is None:
        return invalid

    # A repeated logout is successful only after the session is revoked and any
    # unrevoked token rows are closed; a write failure must still fail the request.
    if auth_session.revoked_at is not None:
        session.execute(
            update(RefreshToken)
            .where(
                RefreshToken.session_id == auth_session.id,
                RefreshToken.revoked_at.is_(None),
            )
            .values(revoked_at=auth_session.revoked_at)
        )
        return None

    now = datetime.now(UTC)
    if auth_session.expires_at <= now:
        return invalid

    if refresh_identity is not None and refresh_digest is not None:
        refresh_token = session.scalar(
            select(RefreshToken)
            .where(
                RefreshToken.id == refresh_identity.id,
                RefreshToken.token_hash == refresh_digest,
            )
            .with_for_update()
        )
        if (
            refresh_token is None
            or refresh_token.session_id != auth_session.id
            or refresh_token.revoked_at is not None
            or refresh_token.expires_at <= now
        ):
            return invalid

    auth_session.revoked_at = now
    auth_session.revoke_reason = "logout"
    session.execute(
        update(RefreshToken)
        .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    set_user_context(session, auth_session.user_id)
    record_event(
        session,
        "auth.session_revoked",
        actor_user_id=auth_session.user_id,
        target_type="auth_session",
        target_id=auth_session.id,
        request_id=request_id,
        details={"reason": "logout"},
    )
    return None
