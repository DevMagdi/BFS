from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier
from uuid import uuid4

import jwt
import pytest
from argon2 import PasswordHasher
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from businessflow.config import Settings
from businessflow.security.tokens import TOKEN_ALGORITHM, TOKEN_AUDIENCE, TOKEN_ISSUER
from tests.helpers import auth_headers, register_user


def _expired_access_token(access_token: str, settings: Settings) -> str:
    claims = jwt.decode(
        access_token,
        options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
    )
    now = int(datetime.now(UTC).timestamp())
    claims["iat"] = now - 120
    claims["exp"] = now - 60
    return jwt.encode(
        claims,
        settings.app_secret_key.get_secret_value(),
        algorithm=TOKEN_ALGORITHM,
    )


def test_production_rejects_development_and_test_signing_keys():

    for prefix in ("dev-", "test-", "ci-"):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                app_secret_key=prefix + "x" * 48,
                app_env="production",
                database_url="postgresql+psycopg://bf_runtime:local-only@localhost/businessflow",
            )


def test_health_registration_is_disabled_and_existing_accounts_can_login(client, admin_engine):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/health/ready").json() == {"status": "ok"}
    assert "/api/v1/auth/register" not in client.get("/openapi.json").json()["paths"]

    raw_password = "Correct horse battery 42!"
    rejected = client.post(
        "/api/v1/auth/register",
        json={
            "email": "  CEO@Example.COM ",
            "display_name": "Example Owner",
            "password": raw_password,
        },
    )
    assert rejected.status_code == 405
    assert rejected.json()["error"]["code"] == "method_not_allowed"
    assert raw_password not in rejected.text
    assert "ceo@example.com" not in rejected.text
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM users WHERE email = 'ceo@example.com'"))
            == 0
        )
        assert (
            connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
            )
            is False
        )

    tokens = register_user(client, "  CEO@Example.COM ", raw_password, "Example Owner")
    me = client.get("/api/v1/auth/me", headers=auth_headers(tokens))
    assert me.status_code == 200
    assert me.json()["email"] == "ceo@example.com"

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


def test_public_registration_payload_is_not_processed_or_echoed(client, admin_engine):
    secret = "tiny"
    response = client.post(
        "/api/v1/auth/register",
        json={"email": "bad@example.com", "display_name": "Bad", "password": secret},
    )
    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert secret not in response.text
    assert "bad@example.com" not in response.text
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT count(*) FROM users WHERE email = 'bad@example.com'"))
            == 0
        )


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


def test_runtime_cannot_change_another_users_hash_and_legacy_hash_login_works(
    client, admin_engine, runtime_engine
):
    target_email = "legacy-password-target@example.com"
    target_password = "Legacy password remains valid 42!"
    actor_email = "password-update-actor@example.com"
    register_user(client, target_email, target_password)
    actor_tokens = register_user(client, actor_email)
    assert client.get("/api/v1/auth/me", headers=auth_headers(actor_tokens)).status_code == 200

    legacy_hash = PasswordHasher(
        time_cost=1,
        memory_cost=8_192,
        parallelism=1,
        hash_len=32,
        salt_len=16,
    ).hash(target_password)
    old_updated_at = datetime.now(UTC) - timedelta(days=1)
    with admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE users SET password_hash = :password_hash, updated_at = :updated_at "
                "WHERE email = :email"
            ),
            {
                "password_hash": legacy_hash,
                "updated_at": old_updated_at,
                "email": target_email,
            },
        )
        target_user_id = connection.scalar(
            text("SELECT id FROM users WHERE email = :email"), {"email": target_email}
        )
        actor_user_id = connection.scalar(
            text("SELECT id FROM users WHERE email = :email"), {"email": actor_email}
        )
    assert target_user_id != actor_user_id

    login = client.post(
        "/api/v1/auth/login", json={"email": target_email, "password": target_password}
    )
    assert login.status_code == 200, login.text
    with admin_engine.connect() as connection:
        stored_user = connection.execute(
            text("SELECT password_hash, updated_at FROM users WHERE id = :user_id"),
            {"user_id": target_user_id},
        ).one()
    assert stored_user.password_hash == legacy_hash
    assert stored_user.updated_at == old_updated_at

    # Direct SQL sees only the shared bf_runtime role, not the authenticated actor's identity.
    with (
        pytest.raises(ProgrammingError, match="permission denied"),
        runtime_engine.begin() as connection,
    ):
        connection.execute(
            text("UPDATE users SET password_hash = :hash WHERE id = :user_id"),
            {"hash": "attacker-controlled-hash", "user_id": target_user_id},
        )
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT password_hash FROM users WHERE id = :user_id"),
                {"user_id": target_user_id},
            )
            == legacy_hash
        )


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
    repeated_logout = client.post("/api/v1/auth/logout", headers=auth_headers(logout_tokens))
    assert repeated_logout.status_code == 204, "logout remains idempotent with a valid access JWT"
    assert client.get("/api/v1/auth/me", headers=auth_headers(logout_tokens)).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/refresh", json={"refresh_token": logout_tokens["refresh_token"]}
        ).status_code
        == 401
    )


def test_logout_rejects_unknown_refresh_token_without_echoing_it(client):
    secret = "not-a-stored-refresh-credential" * 2
    response = client.post("/api/v1/auth/logout", json={"refresh_token": secret})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_logout_credential"
    assert secret not in response.text


def test_logout_with_expired_access_and_valid_refresh_revokes_session(
    client, admin_engine, settings
):
    tokens = register_user(client, "expired-access-logout@example.com")
    expired_access = _expired_access_token(tokens["access_token"], settings)

    response = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_headers({"access_token": expired_access}),
    )
    assert response.status_code == 204, response.text
    assert tokens["refresh_token"] not in response.text
    repeated_logout = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": tokens["refresh_token"]},
        headers=auth_headers({"access_token": expired_access}),
    )
    assert repeated_logout.status_code == 204
    refresh_after_logout = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert refresh_after_logout.status_code == 401

    with admin_engine.connect() as connection:
        session_row = connection.execute(
            text(
                "SELECT revoke_reason, revoked_at FROM auth_sessions "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": "expired-access-logout@example.com"},
        ).one()
    assert session_row.revoke_reason == "logout"
    assert session_row.revoked_at is not None


def test_refresh_replay_after_logout_cannot_restore_session(client, admin_engine):
    tokens = register_user(client, "refresh-replay-after-logout@example.com")
    rotated = client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})
    assert rotated.status_code == 200
    latest_tokens = rotated.json()

    logout_response = client.post(
        "/api/v1/auth/logout", json={"refresh_token": latest_tokens["refresh_token"]}
    )
    assert logout_response.status_code == 204
    for refresh_token in (tokens["refresh_token"], latest_tokens["refresh_token"]):
        replay = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert replay.status_code == 401

    with admin_engine.connect() as connection:
        session_row = connection.execute(
            text(
                "SELECT revoke_reason, revoked_at FROM auth_sessions "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": "refresh-replay-after-logout@example.com"},
        ).one()
    assert session_row.revoke_reason == "logout"
    assert session_row.revoked_at is not None


def test_logout_revokes_only_the_presented_authentication_session(client):
    first_tokens = register_user(client, "logout-one-session@example.com")
    second_login = client.post(
        "/api/v1/auth/login",
        json={
            "email": "logout-one-session@example.com",
            "password": "Correct horse battery 42!",
        },
    )
    assert second_login.status_code == 200
    second_tokens = second_login.json()

    logout_response = client.post("/api/v1/auth/logout", headers=auth_headers(first_tokens))
    assert logout_response.status_code == 204
    assert client.get("/api/v1/auth/me", headers=auth_headers(first_tokens)).status_code == 401
    assert client.get("/api/v1/auth/me", headers=auth_headers(second_tokens)).status_code == 200

    rotated = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": second_tokens["refresh_token"]}
    )
    assert rotated.status_code == 200, rotated.text
    cleanup = client.post(
        "/api/v1/auth/logout", json={"refresh_token": rotated.json()["refresh_token"]}
    )
    assert cleanup.status_code == 204


def test_logout_racing_refresh_never_leaves_a_live_replacement(client, admin_engine):
    tokens = register_user(client, "refresh-logout-race@example.com")
    barrier = Barrier(2)

    def rotate():
        barrier.wait(timeout=5)
        return client.post("/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]})

    def revoke():
        barrier.wait(timeout=5)
        return client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})

    with ThreadPoolExecutor(max_workers=2) as executor:
        refresh_future = executor.submit(rotate)
        logout_future = executor.submit(revoke)
        refresh_response = refresh_future.result(timeout=20)
        logout_response = logout_future.result(timeout=20)

    assert logout_response.status_code == 204, logout_response.text
    assert refresh_response.status_code in {200, 401}, refresh_response.text
    if refresh_response.status_code == 200:
        replacement = refresh_response.json()["refresh_token"]
        replacement_replay = client.post(
            "/api/v1/auth/refresh", json={"refresh_token": replacement}
        )
        assert replacement_replay.status_code == 401

    with admin_engine.connect() as connection:
        session_row = connection.execute(
            text(
                "SELECT revoke_reason, revoked_at FROM auth_sessions "
                "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
            ),
            {"email": "refresh-logout-race@example.com"},
        ).one()
    assert session_row.revoke_reason == "logout"
    assert session_row.revoked_at is not None


def test_logout_failure_does_not_claim_revocation_while_refresh_is_active(client, admin_engine):
    tokens = register_user(client, "logout-update-failure@example.com")
    with admin_engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION public.test_reject_logout_update()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
              IF NEW.revoke_reason = 'logout' THEN
                RAISE EXCEPTION 'intentional test-only logout failure';
              END IF;
              RETURN NEW;
            END;
            $$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER test_reject_logout_update
            BEFORE UPDATE ON public.auth_sessions
            FOR EACH ROW EXECUTE FUNCTION public.test_reject_logout_update()
            """
        )

    try:
        failed = client.post("/api/v1/auth/logout", json={"refresh_token": tokens["refresh_token"]})
        assert failed.status_code == 500
        assert tokens["refresh_token"] not in failed.text
        with admin_engine.connect() as connection:
            active_state = connection.execute(
                text(
                    "SELECT revoked_at, revoke_reason FROM auth_sessions "
                    "WHERE user_id = (SELECT id FROM users WHERE email = :email)"
                ),
                {"email": "logout-update-failure@example.com"},
            ).one()
        assert active_state.revoked_at is None
        assert active_state.revoke_reason is None
    finally:
        with admin_engine.begin() as connection:
            connection.exec_driver_sql(
                "DROP TRIGGER IF EXISTS test_reject_logout_update ON public.auth_sessions"
            )
            connection.exec_driver_sql("DROP FUNCTION IF EXISTS public.test_reject_logout_update()")

    active_refresh = client.post(
        "/api/v1/auth/refresh", json={"refresh_token": tokens["refresh_token"]}
    )
    assert active_refresh.status_code == 200, active_refresh.text
    successful_logout = client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": active_refresh.json()["refresh_token"]},
    )
    assert successful_logout.status_code == 204


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
