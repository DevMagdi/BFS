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
    """Recreate the dedicated test DB, then prove a clean Alembic upgrade succeeds."""
    database_name = make_url(TEST_MIGRATION_DATABASE_URL).database
    if database_name is None:
        raise RuntimeError("The dedicated test database URL must name a database.")
    admin_url = make_url(TEST_ADMIN_DATABASE_URL).set(database="postgres")
    admin_engine = create_engine(
        admin_url,
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    target_admin_engine = None
    migration_engine = None
    try:
        quoted_database_name = admin_engine.dialect.identifier_preparer.quote(database_name)
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(
                f"DROP DATABASE IF EXISTS {quoted_database_name} WITH (FORCE)"
            )
            connection.exec_driver_sql(f"CREATE DATABASE {quoted_database_name} OWNER bf_migrator")

        target_admin_engine = create_engine(TEST_ADMIN_DATABASE_URL, hide_parameters=True)
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql(f"REVOKE ALL ON DATABASE {quoted_database_name} FROM PUBLIC")
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {quoted_database_name} TO bf_runtime, bf_migrator"
            )
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")
            connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM bf_runtime")

        migration_engine = create_engine(TEST_MIGRATION_DATABASE_URL, hide_parameters=True)
        config = Config(str(ROOT / "alembic.ini"))
        migration_database_url = os.environ["TEST_MIGRATION_DATABASE_URL"]
        config.set_main_option("sqlalchemy.url", migration_database_url.replace("%", "%%"))
        # migrations/env.py supports an explicit deployment override; pin it to this disposable
        # test target while the fixture performs its clean upgrade, then restore the caller's env.
        previous_migration_database_url = os.environ.get("MIGRATION_DATABASE_URL")
        os.environ["MIGRATION_DATABASE_URL"] = migration_database_url
        try:
            command.upgrade(config, "head")
        finally:
            if previous_migration_database_url is None:
                os.environ.pop("MIGRATION_DATABASE_URL", None)
            else:
                os.environ["MIGRATION_DATABASE_URL"] = previous_migration_database_url
        with migration_engine.connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM alembic_version"))
            assert revision == "0029_operations_intelligence"
    finally:
        if migration_engine is not None:
            migration_engine.dispose()
        if target_admin_engine is not None:
            target_admin_engine.dispose()
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
        finance_source_intake_enabled=True,
        financial_calculations_enabled=True,
        direct_cash_forecasts_enabled=True,
        report_exports_enabled=True,
        core_financials_enabled=True,
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
