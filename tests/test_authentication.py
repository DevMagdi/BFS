from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import jwt
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from businessflow.config import Settings
from businessflow.security.tokens import TOKEN_ALGORITHM, TOKEN_AUDIENCE, TOKEN_ISSUER
from tests.helpers import auth_headers, register_user


def test_production_rejects_development_and_test_signing_keys():
    for prefix in ("dev-", "test-", "ci-"):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                app_secret_key=prefix + "x" * 48,
                app_env="production",
                database_url="postgresql+psycopg://bf_runtime:local-only@localhost/businessflow",
            )


def test_health_openapi_registration_duplicate_and_login(client, admin_engine, settings):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}
    assert "/api/v1/auth/register" in client.get("/openapi.json").json()["paths"]

    email = "  CEO@Example.COM "
    raw_password = "Correct horse battery 42!"
    tokens = register_user(client, email, raw_password, "Example Owner")
    me = client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == "ceo@example.com"

    duplicate = client.post(
        "/api/v1/auth/register",
        json={"email": "ceo@example.com", "display_name": "Someone", "password": raw_password},
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "registration_unavailable"
    assert raw_password not in duplicate.text

    login = client.post(
        "/api/v1/auth/login", json={"email": "CEO@example.com", "password": raw_password}
    )
    assert login.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth_headers(login.json())).status_code == 200

    wrong = client.post(
        "/api/v1/auth/login", json={"email": "ceo@example.com", "password": "wrong-password"}
    )
    missing = client.post(
        "/api/v1/auth/login",
        json={"email": "unknown@example.com", "password": "wrong-password"},
    )
    assert wrong.status_code == missing.status_code == 401
    assert wrong.json()["error"]["code"] == missing.json()["error"]["code"]
    assert wrong.json()["error"]["message"] == missing.json()["error"]["message"]
    assert "wrong-password" not in wrong.text

    with admin_engine.connect() as connection:
        stored = connection.execute(
            text("SELECT email, password_hash FROM users WHERE email = :email"),
            {"email": "ceo@example.com"},
        ).one()
    assert stored.email == "ceo@example.com"
    assert stored.password_hash.startswith("$argon2id$")
    assert raw_password not in stored.password_hash


def test_short_password_validation_does_not_echo_credentials(client):
    secret = "tiny"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "bad@example.com", "display_name": "Bad", "password": secret},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert secret not in response.text
    assert "bad@example.com" not in response.text


def test_password_whitespace_is_preserved(client):
    password = "  Correct horse battery 42!  "
    tokens = register_user(client, "password-spaces@example.com", password)
    exact_login = client.post(
        "/api/v1/auth/login",
        json={"email": "password-spaces@example.com", "password": password},
    )
    assert exact_login.status_code == 200
    assert client.get("/api/v1/auth/me", headers=auth_headers(tokens)).status_code == 200

    normalized_login = client.post(
        "/api/v1/auth/login",
        json={"email": "password-spaces@example.com", "password": password.strip()},
    )
    assert normalized_login.status_code == 401


def test_database_backed_login_throttle(client, app):
    tokens = register_user(client, "throttle@example.com")
    del tokens
    client.cookies.clear()
    from fastapi.testclient import TestClient as Client

    with Client(
        app, raise_server_exceptions=False, client=("198.51.100.41", 54321)
    ) as limited_client:
        for attempt in range(4):
            response = limited_client.post(
                "/api/v1/auth/login",
                json={"email": "throttle@example.com", "password": f"incorrect-{attempt}"},
            )
            assert response.status_code == 401
        response = limited_client.post(
            "/api/v1/auth/login",
            json={"email": "throttle@example.com", "password": "incorrect-final"},
        )
        assert response.status_code == 429
        assert response.headers["Retry-After"] == "900"
        repeated = limited_client.post(
            "/api/v1/auth/login",
            json={"email": "throttle@example.com", "password": "Correct horse battery 42!"},
        )
        assert repeated.status_code == 429


def test_access_tokens_validate_signature_issuer_and_expiration(client, settings):
    expired = jwt.encode(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "jti": str(uuid4()),
            "iss": TOKEN_ISSUER,
            "aud": TOKEN_AUDIENCE,
            "iat": int((datetime.now(UTC) - timedelta(minutes=10)).timestamp()),
            "exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp()),
            "token_use": "access",
        },
        settings.app_secret_key.get_secret_value(),
        algorithm=TOKEN_ALGORITHM,
    )
    expired_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert expired_response.status_code == 401

    invalid = jwt.encode(
        {
            "sub": str(uuid4()),
            "sid": str(uuid4()),
            "jti": str(uuid4()),
            "iss": "wrong-issuer",
            "aud": TOKEN_AUDIENCE,
            "iat": int(datetime.now(UTC).timestamp()),
            "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            "token_use": "access",
        },
        "x" * 48,
        algorithm=TOKEN_ALGORITHM,
    )
    invalid_response = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {invalid}"})
    assert invalid_response.status_code == 401
    assert invalid_response.json()["error"]["code"] == "invalid_token"


def test_refresh_rotation_reuse_detection_and_logout(client, admin_engine):
    tokens = register_user(client, "refresh@example.com")
    old_refresh = tokens["refresh_token"]
    padded = client.post("/api/v1/auth/refresh", json={"refresh_token": f" {old_refresh} "})
    assert padded.status_code == 401
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert rotated.status_code == 200
    new_tokens = rotated.json()
    assert new_tokens["refresh_token"] != old_refresh
    assert client.get("/api/v1/auth/me", headers=auth_headers(new_tokens)).status_code == 200

    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth_headers(new_tokens)).status_code == 401
    with admin_engine.connect() as connection:
        session_row = connection.execute(
            text(
                "SELECT s.revoked_at, s.revoke_reason, r.token_hash "
                "FROM auth_sessions s JOIN refresh_tokens r ON r.session_id = s.id "
                "WHERE s.user_id = (SELECT id FROM users WHERE email = :email) "
                "ORDER BY r.generation DESC LIMIT 1"
            ),
            {"email": "refresh@example.com"},
        ).one()
        reuse_audit = connection.scalar(
            text(
                "SELECT count(*) FROM audit_events WHERE event_type = 'auth.refresh_reuse_detected'"
            )
        )
    assert session_row.revoked_at is not None
    assert session_row.revoke_reason == "refresh_reuse"
    assert old_refresh not in session_row.token_hash
    assert reuse_audit >= 1

    logout_tokens = register_user(client, "logout@example.com")
    response = client.post("/api/v1/auth/logout", headers=auth_headers(logout_tokens))
    assert response.status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth_headers(logout_tokens)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": logout_tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_disabled_user_cannot_login_and_existing_session_is_revoked(client, admin_engine):
    tokens = register_user(client, "disabled@example.com")
    with admin_engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET status = 'disabled' WHERE email = :email"),
            {"email": "disabled@example.com"},
        )
    denied = client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert denied.status_code == 401
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "disabled@example.com", "password": "Correct horse battery 42!"},
    )
    assert login.status_code == 401
    refresh = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert refresh.status_code == 401
    with admin_engine.connect() as connection:
        revoked = connection.execute(
            text(
                "SELECT revoked_at, revoke_reason FROM auth_sessions "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": "disabled@example.com"},
        ).one()
    assert revoked.revoked_at is not None
    assert revoked.revoke_reason == "account_disabled"


def test_auth_audit_uses_only_hashes_and_never_persists_raw_tokens(client, admin_engine):
    email = "audit-secret@example.com"
    password = "Password never in audit 123!"
    response = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    assert response.status_code == 401
    tokens = register_user(client, email)
    raw_refresh = tokens["refresh_token"]
    with admin_engine.connect() as connection:
        failed_metadata = connection.execute(
            text(
                "SELECT metadata::text FROM audit_events "
                "WHERE event_type = 'auth.login_failed' ORDER BY created_at DESC LIMIT 1"
            )
        ).scalar_one()
        stored_hash = connection.execute(
            text("SELECT token_hash FROM refresh_tokens ORDER BY issued_at DESC LIMIT 1")
        ).scalar_one()
    assert email not in failed_metadata
    assert password not in failed_metadata
    assert raw_refresh not in stored_hash
    assert json.loads(failed_metadata).get("email_bucket_hash")


def test_session_refresh_returns_safe_error_for_malformed_body(client):
    secret = "never-return-this-refresh-token"
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": secret})
    assert response.status_code == 422
    assert secret not in response.text
