from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from businessflow import management

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def bootstrap_database(admin_engine, monkeypatch):
    database_name = f"businessflow_bootstrap_{uuid4().hex[:12]}_test"
    admin_url = make_url(os.environ["TEST_ADMIN_DATABASE_URL"]).set(database=database_name)
    migration_url = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"]).set(database=database_name)
    runtime_url = make_url(os.environ["TEST_DATABASE_URL"]).set(database=database_name)
    database_created = False
    target_admin_engine = None

    try:
        with admin_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" OWNER bf_migrator')
            database_created = True
            connection.exec_driver_sql(f'REVOKE ALL ON DATABASE "{database_name}" FROM PUBLIC')
            connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{database_name}" TO bf_runtime, bf_migrator'
            )

        target_admin_engine = create_engine(admin_url, hide_parameters=True)
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")
            connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM bf_runtime")

        migration_url_text = migration_url.render_as_string(hide_password=False)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url_text)
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("sqlalchemy.url", migration_url_text.replace("%", "%%"))
        command.upgrade(config, "head")

        yield {
            "database": database_name,
            "admin_url": admin_url,
            "migration_url": migration_url.render_as_string(hide_password=False),
            "runtime_url": runtime_url.render_as_string(hide_password=False),
        }
    finally:
        if target_admin_engine is not None:
            target_admin_engine.dispose()
        if database_created:
            with admin_engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.exec_driver_sql(f'DROP DATABASE "{database_name}"')


def _bootstrap_args() -> list[str]:
    return [
        "bootstrap-owner",
        "--email",
        "first.owner@example.com",
        "--display-name",
        "First Owner",
        "--tenant-name",
        "Initial Tenant",
        "--company-name",
        "Initial Company",
        "--base-currency",
        "USD",
        "--timezone",
        "UTC",
        "--locale",
        "en",
    ]


def _provision_initial_owner(database_url: str, email: str = "guard.owner@example.com") -> None:
    management.bootstrap_owner(
        database_url,
        email=email,
        display_name="Guard Owner",
        password="A secure bootstrap password 42!",
        tenant_name="Initial Tenant",
        company_name="Initial Company",
        base_currency="USD",
        timezone="UTC",
        locale="en",
    )


def _bootstrap_config(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


def test_bootstrap_parser_errors_never_echo_unrecognized_secret_values(capsys):
    secret = "REVIEW_SENTINEL_SECRET"
    invalid_arguments = (
        ["--password", secret],
        [f"--password={secret}"],
        [secret],
    )

    for invalid in invalid_arguments:
        with pytest.raises(SystemExit) as exit_error:
            management.main([*_bootstrap_args(), *invalid])
        captured = capsys.readouterr()
        assert exit_error.value.code == 2
        assert not captured.out
        assert secret not in captured.err
        assert "details were withheld" in captured.err


def test_bootstrap_cli_is_operator_only_one_time_and_does_not_log_credentials(
    bootstrap_database, monkeypatch, capsys
):
    monkeypatch.setattr(management, "_interactive_terminal_available", lambda: True)
    secret_password = "A secure bootstrap password 42!"
    connection_passwords = {
        password
        for key in ("admin_url", "migration_url", "runtime_url")
        if (password := make_url(bootstrap_database[key]).password)
    }
    prompt_calls: list[str] = []

    def assert_no_credentials_are_logged(output: str) -> None:
        assert secret_password not in output
        assert all(password not in output for password in connection_passwords)

    def record_unexpected_prompt(prompt: str) -> str:
        prompt_calls.append(prompt)
        return "unused password"

    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", bootstrap_database["runtime_url"])
    monkeypatch.setattr(management.getpass, "getpass", record_unexpected_prompt)
    assert management.main(_bootstrap_args()) == 1
    assert prompt_calls == []
    output = capsys.readouterr()
    assert "bf_migrator" in output.err
    assert_no_credentials_are_logged(output.out + output.err)

    monkeypatch.setenv("BOOTSTRAP_DATABASE_URL", bootstrap_database["migration_url"])

    def echoing_prompt(_prompt: str) -> str:
        raise management.getpass.GetPassWarning("terminal cannot disable echo")

    monkeypatch.setattr(management.getpass, "getpass", echoing_prompt)
    assert management.main(_bootstrap_args()) == 1
    echo_fallback_output = capsys.readouterr()
    assert "cannot hide password input" in echo_fallback_output.err
    assert_no_credentials_are_logged(echo_fallback_output.out + echo_fallback_output.err)

    password_inputs = iter(["tiny", "tiny"])
    monkeypatch.setattr(management.getpass, "getpass", lambda _prompt: next(password_inputs))
    assert management.main(_bootstrap_args()) == 1
    invalid_password_output = capsys.readouterr()
    assert "tiny" not in invalid_password_output.out + invalid_password_output.err
    assert_no_credentials_are_logged(invalid_password_output.out + invalid_password_output.err)

    password_inputs = iter([secret_password, secret_password])
    monkeypatch.setattr(management.getpass, "getpass", lambda _prompt: next(password_inputs))
    assert management.main(_bootstrap_args()) == 0
    successful_output = capsys.readouterr()
    assert_no_credentials_are_logged(successful_output.out + successful_output.err)
    assert "No email or invitation was sent" in successful_output.out

    prompt_calls.clear()
    monkeypatch.setattr(management.getpass, "getpass", record_unexpected_prompt)
    assert management.main(_bootstrap_args()) == 1
    assert prompt_calls == []
    repeated_output = capsys.readouterr()
    assert "already contains user accounts" in repeated_output.err
    assert_no_credentials_are_logged(repeated_output.out + repeated_output.err)

    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 1
            membership = connection.execute(
                text("SELECT role, status FROM tenant_memberships")
            ).one()
            assert membership.role == "owner" and membership.status == "active"
            stored = connection.execute(
                text("SELECT email, display_name, password_hash FROM users")
            ).one()
            assert stored.email == "first.owner@example.com"
            assert stored.display_name == "First Owner"
            assert secret_password not in stored.password_hash
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
                )
                is False
            )
            assert (
                connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
                )
                is False
            )
    finally:
        inspection_engine.dispose()


def test_bootstrap_requires_0014_or_later_and_preserves_pristine_target(bootstrap_database):
    database_url = bootstrap_database["migration_url"]
    config = _bootstrap_config(database_url)
    command.downgrade(config, "0013_direct_cash_forecast")

    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0013_direct_cash_forecast"
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
            )
    finally:
        inspection_engine.dispose()

    with pytest.raises(management.BootstrapAuthorizationError, match="migration 0014"):
        _provision_initial_owner(database_url)

    command.upgrade(config, "head")
    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0017_core_financial_cash_transfers"
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
            )
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
    finally:
        inspection_engine.dispose()

    _provision_initial_owner(database_url)
    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 1
    finally:
        inspection_engine.dispose()


def test_bootstrap_rechecks_runtime_user_write_privileges_inside_provisioning_transaction(
    bootstrap_database,
):
    database_url = bootstrap_database["migration_url"]
    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("GRANT INSERT ON public.users TO bf_runtime")
        with pytest.raises(
            management.BootstrapAuthorizationError,
            match="effective INSERT or UPDATE",
        ):
            _provision_initial_owner(database_url)
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
    finally:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE INSERT ON public.users FROM bf_runtime")

    try:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql(
                "GRANT INSERT (id, email, display_name, password_hash) ON public.users TO PUBLIC"
            )
        with pytest.raises(
            management.BootstrapAuthorizationError,
            match="effective INSERT or UPDATE",
        ):
            _provision_initial_owner(database_url)
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
    finally:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql(
                "REVOKE INSERT (id, email, display_name, password_hash) ON public.users FROM PUBLIC"
            )

    try:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("GRANT UPDATE ON public.users TO bf_runtime")
        with pytest.raises(
            management.BootstrapAuthorizationError,
            match="effective INSERT or UPDATE",
        ):
            _provision_initial_owner(database_url)
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
    finally:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE UPDATE ON public.users FROM bf_runtime")

    try:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("GRANT UPDATE (status) ON public.users TO PUBLIC")
        with pytest.raises(
            management.BootstrapAuthorizationError,
            match="effective INSERT or UPDATE",
        ):
            _provision_initial_owner(database_url)
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
    finally:
        with inspection_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE UPDATE (status) ON public.users FROM PUBLIC")

    try:
        _provision_initial_owner(database_url)
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
            )
            assert not connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
            )
    finally:
        inspection_engine.dispose()


def test_bootstrap_rolls_back_user_if_tenant_creation_fails(bootstrap_database, monkeypatch):
    def fail_tenant_creation(*_args, **_kwargs):
        raise SQLAlchemyError("test-only failure after user insert")

    monkeypatch.setattr(management, "create_tenant", fail_tenant_creation)
    with pytest.raises(management.BootstrapError, match="Bootstrap failed"):
        _provision_initial_owner(bootstrap_database["migration_url"])

    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 0
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 0
    finally:
        inspection_engine.dispose()


def test_concurrent_bootstrap_attempts_create_only_one_initial_owner(bootstrap_database):
    def provision(email: str):
        return management.bootstrap_owner(
            bootstrap_database["migration_url"],
            email=email,
            display_name="Concurrent Owner",
            password="A secure bootstrap password 42!",
            tenant_name="Initial Tenant",
            company_name="Initial Company",
            base_currency="USD",
            timezone="UTC",
            locale="en",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(provision, "first-a@example.com"),
            executor.submit(provision, "first-b@example.com"),
        ]
    results = []
    for future in futures:
        try:
            future.result()
            results.append("created")
        except management.BootstrapAlreadyInitialized:
            results.append("refused")
    assert sorted(results) == ["created", "refused"]

    inspection_engine = create_engine(bootstrap_database["admin_url"], hide_parameters=True)
    try:
        with inspection_engine.connect() as connection:
            assert connection.scalar(text("SELECT count(*) FROM users")) == 1
            assert connection.scalar(text("SELECT count(*) FROM tenants")) == 1
            assert connection.scalar(text("SELECT count(*) FROM tenant_memberships")) == 1
    finally:
        inspection_engine.dispose()
