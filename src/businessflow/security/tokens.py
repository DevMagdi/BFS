from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import jwt
from jwt import InvalidTokenError

from businessflow.config import Settings

TOKEN_ISSUER = "businessflow-ai"
TOKEN_AUDIENCE = "businessflow-api"
TOKEN_ALGORITHM = "HS256"


@dataclass(frozen=True, slots=True)
class AccessClaims:
    user_id: UUID
    session_id: UUID
    token_id: str


def new_refresh_token() -> str:
    return secrets.token_urlsafe(48)


def digest_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(user_id: UUID, session_id: UUID, settings: Settings) -> str:
    now = datetime.now(UTC)
    claims = {
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": str(uuid4()),
        "iss": TOKEN_ISSUER,
        "aud": TOKEN_AUDIENCE,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
        "token_use": "access",
    }
    return jwt.encode(
        claims,
        settings.app_secret_key.get_secret_value(),
        algorithm=TOKEN_ALGORITHM,
        headers={"typ": "JWT"},
    )


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    claims = jwt.decode(
        token,
        settings.app_secret_key.get_secret_value(),
        algorithms=[TOKEN_ALGORITHM],
        issuer=TOKEN_ISSUER,
        audience=TOKEN_AUDIENCE,
        options={
            "require": ["sub", "sid", "jti", "iss", "aud", "iat", "exp", "token_use"],
        },
    )
    if claims.get("token_use") != "access":
        raise InvalidTokenError("wrong token type")
    try:
        user_id = UUID(claims["sub"])
        session_id = UUID(claims["sid"])
        token_id = str(UUID(claims["jti"]))
    except (TypeError, ValueError, KeyError) as exc:
        raise InvalidTokenError("invalid token identifiers") from exc
    return AccessClaims(user_id=user_id, session_id=session_id, token_id=token_id)
