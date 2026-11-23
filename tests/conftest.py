from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
TEST_MIGRATION_DATABASE_URL = os.environ.get("TEST_MIGRATION_DATABASE_URL")
TEST_ADMIN_DATABASE_URL = os.environ.get("TEST_ADMIN_DATABASE_URL")

if not all([TEST_DATABASE_URL, TEST_MIGRATION_DATABASE_URL, TEST_ADMIN_DATABASE_URL]):
    raise RuntimeError(
        "Set TEST_DATABASE_URL, TEST_MIGRATION_DATABASE_URL, and TEST_ADMIN_DATABASE_URL "
        "to separate real PostgreSQL runtime/migration/admin roles."
    )

test_urls = [
    make_url(TEST_DATABASE_URL),
    make_url(TEST_MIGRATION_DATABASE_URL),
    make_url(TEST_ADMIN_DATABASE_URL),
]
if any(
    url.get_backend_name() != "postgresql" or "test" not in (url.database or "").lower()
    for url in test_urls
):
    raise RuntimeError("Tests require PostgreSQL URLs with 'test' in the database name.")
if len({url.database for url in test_urls}) != 1:
    raise RuntimeError("Runtime, migration, and admin test URLs must use the same test database.")
if test_urls[0].username != "bf_runtime" or test_urls[1].username != "bf_migrator":
    raise RuntimeError(
        "TEST_DATABASE_URL must use bf_runtime; TEST_MIGRATION_DATABASE_URL bf_migrator."
    )
if test_urls[2].username in {test_urls[0].username, test_urls[1].username}:
    raise RuntimeError("TEST_ADMIN_DATABASE_URL must use a separate test-only admin role.")

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("APP_SECRET_KEY", "test-only-key-" + "x" * 48)
os.environ.setdefault("DATABASE_URL", TEST_DATABASE_URL or "")

from businessflow.config import Settings  # noqa: E402
from businessflow.database import create_database_engine  # noqa: E402
from businessflow.main import create_app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def migrated_clean_test_database() -> None:
    """Reset the dedicated test DB, then prove a clean Alembic upgrade succeeds."""
    admin_engine = create_engine(TEST_ADMIN_DATABASE_URL, hide_parameters=True)
    migration_engine = create_engine(TEST_MIGRATION_DATABASE_URL, hide_parameters=True)
    try:
        with admin_engine.connect() as connection:
            version_table = connection.scalar(text("SELECT to_regclass('public.alembic_version')"))
        if version_table is not None:
            config = Config(str(ROOT / "alembic.ini"))
            config.set_main_option("sqlalchemy.url", TEST_MIGRATION_DATABASE_URL.replace("%", "%%"))
            command.downgrade(config, "base")

        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", TEST_MIGRATION_DATABASE_URL.replace("%", "%%"))
        command.upgrade(config, "head")
        with migration_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "0010_metric_insight_runs"
    finally:
        migration_engine.dispose()
        admin_engine.dispose()


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(
        _env_file=None,
        app_secret_key=os.environ["APP_SECRET_KEY"],
        app_env="test",
        database_url=TEST_DATABASE_URL,
        access_token_ttl_seconds=300,
        refresh_session_days=30,
        login_failure_limit=5,
        login_ip_failure_limit=20,
        login_lockout_minutes=15,
        db_pool_size=5,
        db_max_overflow=5,
    )


@pytest.fixture
def app(settings: Settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


@pytest.fixture(scope="session")
def admin_engine():
    engine = create_engine(TEST_ADMIN_DATABASE_URL, hide_parameters=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def migration_engine():
    engine = create_engine(TEST_MIGRATION_DATABASE_URL, hide_parameters=True)
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def runtime_engine():
    engine = create_database_engine(TEST_DATABASE_URL, pool_size=1, max_overflow=0)
    yield engine
    engine.dispose()
