from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from businessflow import management

ROOT = Path(__file__).resolve().parents[1]


def _config_for_database(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def _runtime_role_settings(connection) -> dict[str, bool]:
    role = (
        connection.execute(
            text(
                """
            SELECT rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolreplication, rolinherit
            FROM pg_roles
            WHERE rolname = 'bf_runtime'
            """
            )
        )
        .mappings()
        .one()
    )
    return {setting: bool(enabled) for setting, enabled in role.items()}


def _role_setting_clauses(settings: dict[str, bool]) -> str:
    return " ".join(
        (
            "SUPERUSER" if settings["rolsuper"] else "NOSUPERUSER",
            "BYPASSRLS" if settings["rolbypassrls"] else "NOBYPASSRLS",
            "CREATEDB" if settings["rolcreatedb"] else "NOCREATEDB",
            "CREATEROLE" if settings["rolcreaterole"] else "NOCREATEROLE",
            "REPLICATION" if settings["rolreplication"] else "NOREPLICATION",
            "INHERIT" if settings["rolinherit"] else "NOINHERIT",
        )
    )


def _alter_runtime_role(cluster_admin_engine: Engine, clause: str) -> None:
    with cluster_admin_engine.connect() as connection:
        connection.exec_driver_sql(f"ALTER ROLE bf_runtime {clause}")


def _snapshot_database_state(
    target_admin_engine: Engine, unrelated_role: str
) -> tuple[object, ...]:
    with target_admin_engine.connect() as connection:
        revision = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        users = connection.scalar(text("SELECT count(*) FROM public.users"))
        tenants = connection.scalar(text("SELECT count(*) FROM public.tenants"))
        runtime_settings = tuple(_runtime_role_settings(connection).items())
        runtime_memberships = tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT granted.rolname
                    FROM pg_auth_members AS membership
                    JOIN pg_roles AS granted ON granted.oid = membership.roleid
                    WHERE membership.member = 'bf_runtime'::regrole
                    ORDER BY granted.rolname
                    """
                )
            ).all()
        )
        relation_acls = tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT relation.relname, relation.relkind,
                           relation.relowner::regrole::text, relation.relacl::text
                    FROM pg_class AS relation
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND relation.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')
                    ORDER BY relation.relname
                    """
                )
            ).all()
        )
        column_acls = tuple(
            tuple(row)
            for row in connection.execute(
                text(
                    """
                    SELECT relation.relname, column_privilege.attnum,
                           column_privilege.attname, column_privilege.attacl::text
                    FROM pg_attribute AS column_privilege
                    JOIN pg_class AS relation ON relation.oid = column_privilege.attrelid
                    JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
                    WHERE namespace.nspname = 'public'
                      AND column_privilege.attnum > 0
                      AND NOT column_privilege.attisdropped
                    ORDER BY relation.relname, column_privilege.attnum
                    """
                )
            ).all()
        )
        unrelated_privileges = tuple(
            connection.execute(
                text(
                    """
                    SELECT has_table_privilege(:role_name, 'public.users', 'SELECT'),
                           has_table_privilege(:role_name, 'public.users', 'UPDATE')
                    """
                ),
                {"role_name": unrelated_role},
            ).one()
        )
    return (
        revision,
        users,
        tenants,
        runtime_settings,
        runtime_memberships,
        relation_acls,
        column_acls,
        unrelated_privileges,
    )


@pytest.fixture(scope="module")
def role_policy_database():
    database_name = f"businessflow_role_policy_{uuid4().hex[:12]}_test"
    probe_role = f"bf_unrelated_probe_{uuid4().hex[:12]}"
    quoted_probe_role = f'"{probe_role}"'
    admin_base_url = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])
    cluster_admin_url = admin_base_url.set(database="postgres")
    cluster_admin_engine = create_engine(
        cluster_admin_url,
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    migration_url_object = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"]).set(
        database=database_name
    )
    migration_url = migration_url_object.render_as_string(hide_password=False)
    target_admin_url = admin_base_url.set(database=database_name)
    target_admin_engine = None
    database_created = False
    probe_role_created = False
    original_migration_url = os.environ.get("MIGRATION_DATABASE_URL")

    try:
        with cluster_admin_engine.connect() as connection:
            original_runtime_settings = _runtime_role_settings(connection)
            assert original_runtime_settings == {
                "rolsuper": False,
                "rolbypassrls": False,
                "rolcreatedb": False,
                "rolcreaterole": False,
                "rolreplication": False,
                "rolinherit": False,
            }
            assert connection.scalar(
                text(
                    "SELECT NOT EXISTS (SELECT 1 FROM pg_auth_members "
                    "WHERE member = 'bf_runtime'::regrole)"
                )
            )
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" OWNER bf_migrator')
            database_created = True
            connection.exec_driver_sql(f'REVOKE ALL ON DATABASE "{database_name}" FROM PUBLIC')
            connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{database_name}" TO bf_runtime, bf_migrator'
            )
            connection.exec_driver_sql(f"CREATE ROLE {quoted_probe_role} NOLOGIN")
            probe_role_created = True

        target_admin_engine = create_engine(target_admin_url, hide_parameters=True)
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")
            connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM bf_runtime")

        os.environ["MIGRATION_DATABASE_URL"] = migration_url
        migration_config = _config_for_database(migration_url)
        command.upgrade(migration_config, "0013_direct_cash_forecast")
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql(
                f"GRANT SELECT, UPDATE ON TABLE public.users TO {quoted_probe_role}"
            )

        yield {
            "cluster_admin_engine": cluster_admin_engine,
            "database": database_name,
            "migration_config": migration_config,
            "migration_url": migration_url,
            "probe_role": probe_role,
            "quoted_probe_role": quoted_probe_role,
            "target_admin_engine": target_admin_engine,
            "original_runtime_settings": original_runtime_settings,
        }
    finally:
        if target_admin_engine is not None:
            if probe_role_created:
                with cluster_admin_engine.connect() as connection:
                    connection.exec_driver_sql(f"REVOKE {quoted_probe_role} FROM bf_runtime")
                with target_admin_engine.begin() as connection:
                    if connection.scalar(text("SELECT to_regclass('public.users')")) is not None:
                        connection.exec_driver_sql(
                            f"REVOKE ALL ON TABLE public.users FROM {quoted_probe_role}"
                        )
            target_admin_engine.dispose()
        if probe_role_created:
            with cluster_admin_engine.connect() as connection:
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {quoted_probe_role}")
        if "original_runtime_settings" in locals():
            _alter_runtime_role(
                cluster_admin_engine,
                _role_setting_clauses(original_runtime_settings),
            )
        if database_created:
            with cluster_admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        if original_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = original_migration_url
        cluster_admin_engine.dispose()


def _enable_policy_case(
    database: dict[str, object],
    unsafe_setting: str | None,
) -> None:
    if unsafe_setting == "MEMBERSHIP":
        with database["cluster_admin_engine"].connect() as connection:
            connection.exec_driver_sql(f"GRANT {database['quoted_probe_role']} TO bf_runtime")
    else:
        _alter_runtime_role(
            database["cluster_admin_engine"],
            str(unsafe_setting),
        )


def _disable_policy_case(
    database: dict[str, object],
    safe_setting: str | None,
) -> None:
    if safe_setting == "MEMBERSHIP":
        with database["cluster_admin_engine"].connect() as connection:
            connection.exec_driver_sql(f"REVOKE {database['quoted_probe_role']} FROM bf_runtime")
    else:
        _alter_runtime_role(database["cluster_admin_engine"], str(safe_setting))


def _assert_migration_refuses_policy_case(
    database: dict[str, object],
    *,
    target_revision: str,
    expected_revision: str,
    unsafe_setting: str | None,
    safe_setting: str | None,
    diagnostic: str,
) -> None:
    _enable_policy_case(database, unsafe_setting)
    try:
        before = _snapshot_database_state(database["target_admin_engine"], database["probe_role"])
        with pytest.raises(DBAPIError) as error:
            command.upgrade(database["migration_config"], target_revision)
        assert diagnostic.lower() in str(error.value).lower()
        after = _snapshot_database_state(database["target_admin_engine"], database["probe_role"])
        assert after == before
        assert after[0] == expected_revision
    finally:
        _disable_policy_case(database, safe_setting)


def _assert_bootstrap_refuses_policy_case(
    database: dict[str, object],
    *,
    unsafe_setting: str | None,
    safe_setting: str | None,
    diagnostic: str,
) -> None:
    _enable_policy_case(database, unsafe_setting)
    try:
        before = _snapshot_database_state(database["target_admin_engine"], database["probe_role"])
        with pytest.raises(management.BootstrapAuthorizationError) as error:
            management.validate_bootstrap_target(database["migration_url"])
        assert diagnostic.lower() in str(error.value).lower()
        after = _snapshot_database_state(database["target_admin_engine"], database["probe_role"])
        assert after == before
        assert after[1:3] == (0, 0)
    finally:
        _disable_policy_case(database, safe_setting)


@pytest.mark.parametrize(
    ("unsafe_setting", "safe_setting", "migration_diagnostic", "bootstrap_diagnostic"),
    [
        pytest.param("SUPERUSER", "NOSUPERUSER", "NOSUPERUSER", "NOSUPERUSER", id="superuser"),
        pytest.param("BYPASSRLS", "NOBYPASSRLS", "NOBYPASSRLS", "NOBYPASSRLS", id="bypassrls"),
        pytest.param("CREATEDB", "NOCREATEDB", "NOCREATEDB", "NOCREATEDB", id="createdb"),
        pytest.param("CREATEROLE", "NOCREATEROLE", "NOCREATEROLE", "NOCREATEROLE", id="createrole"),
        pytest.param(
            "REPLICATION", "NOREPLICATION", "NOREPLICATION", "NOREPLICATION", id="replication"
        ),
        pytest.param("INHERIT", "NOINHERIT", "NOINHERIT", "NOINHERIT", id="inherit"),
        pytest.param(
            "MEMBERSHIP",
            "MEMBERSHIP",
            "member of other PostgreSQL roles",
            "role memberships",
            id="membership",
        ),
    ],
)
def test_migrations_and_bootstrap_reject_unsafe_runtime_role_policy(
    role_policy_database,
    unsafe_setting: str,
    safe_setting: str,
    migration_diagnostic: str,
    bootstrap_diagnostic: str,
):
    database = role_policy_database
    migration_config = database["migration_config"]

    try:
        with database["target_admin_engine"].connect() as connection:
            revision = connection.scalar(text("SELECT version_num FROM public.alembic_version"))
        if revision != "0013_direct_cash_forecast":
            command.downgrade(migration_config, "0013_direct_cash_forecast")

        _assert_migration_refuses_policy_case(
            database,
            target_revision="0014_disable_public_signup",
            expected_revision="0013_direct_cash_forecast",
            unsafe_setting=unsafe_setting,
            safe_setting=safe_setting,
            diagnostic=migration_diagnostic,
        )
        command.upgrade(migration_config, "0014_disable_public_signup")

        _assert_migration_refuses_policy_case(
            database,
            target_revision="head",
            expected_revision="0014_disable_public_signup",
            unsafe_setting=unsafe_setting,
            safe_setting=safe_setting,
            diagnostic=migration_diagnostic,
        )
        command.upgrade(migration_config, "head")

        _assert_bootstrap_refuses_policy_case(
            database,
            unsafe_setting=unsafe_setting,
            safe_setting=safe_setting,
            diagnostic=bootstrap_diagnostic,
        )
    finally:
        _disable_policy_case(database, safe_setting)
