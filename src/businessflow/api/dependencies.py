from __future__ import annotations

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import UUID

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.config import Settings
from businessflow.database import set_user_context
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.models import AuthSession, RefreshToken, UserAccount
from businessflow.security.tokens import AccessClaims, decode_access_token

logger = logging.getLogger(__name__)
_bearer = HTTPBearer(auto_error=False, scheme_name="BearerAuth")


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    request_id: UUID = request.state.request_id
    with factory() as session:
        deferred_error: ApiError | None = None
        try:
            with session.begin():
                try:
                    yield session
                except CommitAndRaise as exc:
                    session.flush()
                    deferred_error = exc.error
            if deferred_error is not None:
                raise deferred_error
        except IntegrityError:
            logger.info("request %s rejected by a database constraint", request_id)
            raise ApiError(
                409, "conflict", "The requested change conflicts with current state."
            ) from None


def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UserAccount:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise ApiError(401, "authentication_required", "Authentication is required.")
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except InvalidTokenError:
        raise ApiError(401, "invalid_token", "Access token is invalid or expired.") from None

    user = session.scalar(select(UserAccount).where(UserAccount.id == claims.user_id))
    auth_session = session.scalar(
        select(AuthSession).where(
            AuthSession.id == claims.session_id,
            AuthSession.user_id == claims.user_id,
        )
    )
    if user is None or auth_session is None:
        raise ApiError(401, "invalid_token", "Access token is invalid or expired.")
    if user.status != "active" and auth_session.revoked_at is None:
        now = datetime.now(UTC)
        auth_session.revoked_at = now
        auth_session.revoke_reason = "account_disabled"
        session.execute(
            update(RefreshToken)
            .where(RefreshToken.session_id == auth_session.id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        set_user_context(session, user.id)
        record_event(
            session,
            "auth.session_revoked",
            actor_user_id=user.id,
            target_type="auth_session",
            target_id=auth_session.id,
            request_id=request.state.request_id,
            outcome="revoked",
            details={"reason": "account_disabled"},
        )
        raise CommitAndRaise(ApiError(401, "invalid_token", "Access token is invalid or expired."))
    if (
        user.status != "active"
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= request.state.now
    ):
        raise ApiError(401, "invalid_token", "Access token is invalid or expired.")
    set_user_context(session, user.id)
    request.state.access_claims = AccessClaims(
        user_id=claims.user_id,
        session_id=claims.session_id,
        token_id=claims.token_id,
    )
    return user


def request_id(request: Request) -> UUID:
    return request.state.request_id
