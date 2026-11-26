from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[1]


def _config_for_database(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


USER_COLUMNS = (
    "id",
    "email",
    "display_name",
    "password_hash",
    "status",
    "created_at",
    "updated_at",
)


def _assert_runtime_user_privileges(
    connection,
    *,
    expected_insert: bool,
    table_wide_update: bool,
) -> None:
    assert (
        connection.scalar(
            text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
        )
        is expected_insert
    )
    assert (
        connection.scalar(
            text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
        )
        is table_wide_update
    )
    for column in USER_COLUMNS:
        assert (
            connection.scalar(
                text(
                    "SELECT has_column_privilege("
                    "'bf_runtime', 'public.users', :column_name, 'INSERT')"
                ),
                {"column_name": column},
            )
            is expected_insert
        )
        expected_update = table_wide_update
        assert (
            connection.scalar(
                text(
                    "SELECT has_column_privilege("
                    "'bf_runtime', 'public.users', :column_name, 'UPDATE')"
                ),
                {"column_name": column},
            )
            is expected_update
        )
    if not expected_insert and not table_wide_update:
        _assert_public_user_writes_revoked(connection)


def _has_public_user_writes(connection) -> bool:
    return bool(
        connection.scalar(
            text(
                """
                SELECT EXISTS (
                  SELECT 1
                  FROM pg_class AS relation
                  CROSS JOIN LATERAL aclexplode(
                    COALESCE(relation.relacl, acldefault('r', relation.relowner))
                  ) AS privilege(grantor, grantee, privilege_type, is_grantable)
                  WHERE relation.oid = 'public.users'::regclass
                    AND privilege.grantee = 0
                    AND privilege.privilege_type IN ('INSERT', 'UPDATE')
                ) OR EXISTS (
                  SELECT 1
                  FROM pg_attribute AS column_privilege
                  CROSS JOIN LATERAL aclexplode(column_privilege.attacl)
                    AS privilege(grantor, grantee, privilege_type, is_grantable)
                  WHERE column_privilege.attrelid = 'public.users'::regclass
                    AND column_privilege.attnum > 0
                    AND NOT column_privilege.attisdropped
                    AND privilege.grantee = 0
                    AND privilege.privilege_type IN ('INSERT', 'UPDATE')
                )
                """
            )
        )
    )


def _assert_public_user_writes_revoked(connection) -> None:
    assert _has_public_user_writes(connection) is False


def test_migration_head_upgrade_downgrade_and_reupgrade():

    database_name = f"businessflow_m14_migration_test_{uuid4().hex[:12]}"
    admin_base_url = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])
    admin_url = admin_base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    database_created = False
    migration_url = ""
    migration_engine = None
    target_admin_engine = None
    runtime_engine = None
    original_migration_url = os.environ.get("MIGRATION_DATABASE_URL")
    try:
        with admin_engine.connect() as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{database_name}" OWNER bf_migrator')
            database_created = True
            connection.exec_driver_sql(f'REVOKE ALL ON DATABASE "{database_name}" FROM PUBLIC')
            connection.exec_driver_sql(
                f'GRANT CONNECT ON DATABASE "{database_name}" TO bf_runtime, bf_migrator'
            )

        migration_url_object = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"]).set(
            database=database_name
        )
        migration_url = migration_url_object.render_as_string(hide_password=False)
        os.environ["MIGRATION_DATABASE_URL"] = migration_url
        migration_engine = create_engine(migration_url_object, hide_parameters=True)
        runtime_url_object = make_url(os.environ["TEST_DATABASE_URL"]).set(database=database_name)
        runtime_engine = create_engine(runtime_url_object, hide_parameters=True)
        target_admin_url = admin_base_url.set(database=database_name)
        target_admin_engine = create_engine(target_admin_url, hide_parameters=True)
        with migration_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            _assert_public_user_writes_revoked(connection)
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_assessments')"))
                == "financial_assessments"
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('public.project_financial_input_versions')")
                )
                == "project_financial_input_versions"
            )
            for table_name in (
                "position_demo_seeds",
                "position_financial_imports",
                "position_review_briefs",
                "position_review_operational_imports",
                "position_review_dispositions",
            ):
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE oid = to_regclass(:table_name)"
                        ),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'SELECT')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'INSERT')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'DELETE')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is False
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'TRUNCATE')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is False
                )
                if table_name != "position_financial_imports":
                    assert (
                        connection.scalar(
                            text("SELECT has_table_privilege('bf_runtime', :table_name, 'UPDATE')"),
                            {"table_name": f"public.{table_name}"},
                        )
                        is False
                    )

            assert connection.scalar(text("SELECT count(*) FROM company_data_contracts")) == 7
            assert (
                connection.scalar(
                    text(
                        "SELECT has_table_privilege('bf_runtime', "
                        "'company_data_contracts', 'SELECT')"
                    )
                )
                is True
            )
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert (
                    connection.scalar(
                        text(
                            "SELECT has_table_privilege("
                            "'bf_runtime', 'company_data_contracts', :privilege)"
                        ),
                        {"privilege": privilege},
                    )
                    is False
                )
            for table_name in ("company_data_sources", "company_data_ingestions"):
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE oid = to_regclass(:table_name)"
                        ),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'SELECT')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert (
                    connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, 'INSERT')"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert (
                        connection.scalar(
                            text(
                                "SELECT has_table_privilege('bf_runtime', :table_name, :privilege)"
                            ),
                            {"table_name": f"public.{table_name}", "privilege": privilege},
                        )
                        is False
                    )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname IN ('trg_company_data_contracts_append_only', "
                        "'trg_company_data_sources_append_only', "
                        "'trg_company_data_ingestions_append_only', "
                        "'trg_position_review_company_data_guard')"
                    )
                )
                == 4
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conname = 'fk_position_review_operational_imports_company_data'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_column_privilege('bf_runtime', "
                        "'position_review_operational_imports', "
                        "'company_data_ingestion_id', 'INSERT')"
                    )
                )
                is True
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_column_privilege('bf_runtime', "
                        "'position_financial_imports', 'observations', 'UPDATE')"
                    )
                )
                is False
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT has_column_privilege('bf_runtime', "
                        "'position_financial_imports', 'status', 'UPDATE')"
                    )
                )
                is True
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname = 'trg_position_review_briefs_source_set_guard'"
                    )
                )
                == 1
            )

            assert connection.scalar(text("SELECT count(*) FROM finance_source_contracts")) == 1
            finance_contract = connection.execute(
                text("SELECT contract_key, version, definition_hash FROM finance_source_contracts")
            ).one()
            assert finance_contract.contract_key == "businessflow.finance.source-observations"
            assert finance_contract.version == "1.0.0"
            assert len(finance_contract.definition_hash) == 64
            finance_tables = (
                "finance_source_mappings",
                "finance_source_intakes",
                "finance_source_row_evidence",
            )
            for table_name in finance_tables:
                assert (
                    connection.scalar(
                        text(
                            "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                            "WHERE oid = to_regclass(:table_name)"
                        ),
                        {"table_name": f"public.{table_name}"},
                    )
                    is True
                )
                assert connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', :table_name, 'SELECT')"),
                    {"table_name": f"public.{table_name}"},
                )
                assert connection.scalar(
                    text("SELECT has_table_privilege('bf_runtime', :table_name, 'INSERT')"),
                    {"table_name": f"public.{table_name}"},
                )
                for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                    assert not connection.scalar(
                        text("SELECT has_table_privilege('bf_runtime', :table_name, :privilege)"),
                        {"table_name": f"public.{table_name}", "privilege": privilege},
                    )
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', 'finance_source_contracts', 'SELECT')"
                )
            )
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege("
                        "'bf_runtime', 'finance_source_contracts', :privilege)"
                    ),
                    {"privilege": privilege},
                )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal AND tgname IN "
                        "('trg_finance_source_contracts_append_only', "
                        "'trg_finance_source_mappings_append_only', "
                        "'trg_finance_source_intakes_append_only', "
                        "'trg_finance_source_row_evidence_append_only')"
                    )
                )
                == 4
            )

            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                == "financial_calculation_runs"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass('public.financial_calculation_runs')"
                    )
                )
                is True
            )
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', "
                    "'financial_calculation_runs', 'SELECT')"
                )
            )
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', "
                    "'financial_calculation_runs', 'INSERT')"
                )
            )
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege('bf_runtime', "
                        "'financial_calculation_runs', :privilege)"
                    ),
                    {"privilege": privilege},
                )
            assert connection.execute(
                text("SELECT cmd FROM pg_policies WHERE tablename = 'financial_calculation_runs'")
            ).scalars().all() == ["ALL"]
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname = 'trg_financial_calculation_runs_append_only'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conname = 'ck_finance_source_row_evidence_amount_commitment'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 2
            )

            assert (
                connection.scalar(text("SELECT to_regclass('public.direct_cash_forecast_runs')"))
                == "direct_cash_forecast_runs"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT relrowsecurity AND relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass('public.direct_cash_forecast_runs')"
                    )
                )
                is True
            )
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', "
                    "'direct_cash_forecast_runs', 'SELECT')"
                )
            )
            assert connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', "
                    "'direct_cash_forecast_runs', 'INSERT')"
                )
            )
            for privilege in ("UPDATE", "DELETE", "TRUNCATE"):
                assert not connection.scalar(
                    text(
                        "SELECT has_table_privilege('bf_runtime', "
                        "'direct_cash_forecast_runs', :privilege)"
                    ),
                    {"privilege": privilege},
                )
            assert connection.execute(
                text("SELECT cmd FROM pg_policies WHERE tablename = 'direct_cash_forecast_runs'")
            ).scalars().all() == ["ALL"]
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal "
                        "AND tgname = 'trg_direct_cash_forecast_runs_append_only'"
                    )
                )
                == 1
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_constraint "
                        "WHERE conname = 'ck_direct_cash_forecast_runs_horizon'"
                    )
                )
                == 1
            )

        migration_config = _config_for_database(migration_url)
        wrong_role_url = admin_base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        os.environ["MIGRATION_DATABASE_URL"] = wrong_role_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.downgrade(
                    _config_for_database(wrong_role_url), "0014_disable_public_signup"
                )
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )

        command.downgrade(migration_config, "0014_disable_public_signup")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0014_disable_public_signup"
            )
            _assert_runtime_user_privileges(
                connection, expected_insert=False, table_wide_update=False
            )

        # Downgrading below 0014 keeps both user-table write revocations in place.
        command.downgrade(migration_config, "0013_direct_cash_forecast")
        wrong_role_url = admin_base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        os.environ["MIGRATION_DATABASE_URL"] = wrong_role_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.upgrade(_config_for_database(wrong_role_url), "0014_disable_public_signup")
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0013_direct_cash_forecast"
            )
            _assert_runtime_user_privileges(
                connection, expected_insert=False, table_wide_update=False
            )

        # A direct or inherited privilege path must fail closed instead of being
        # silently stripped from another role's ACL.
        inherited_role = f"bf_users_privilege_probe_{uuid4().hex[:12]}"
        quoted_inherited_role = f'"{inherited_role}"'
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE ROLE {quoted_inherited_role} NOLOGIN")
            connection.exec_driver_sql(f"GRANT {quoted_inherited_role} TO bf_runtime")
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                f"GRANT INSERT, UPDATE ON TABLE public.users TO {quoted_inherited_role}"
            )
        try:
            with pytest.raises(
                Exception,
                match="bf_runtime must not be a member of other PostgreSQL roles",
            ):
                command.upgrade(migration_config, "0014_disable_public_signup")
            with migration_engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0013_direct_cash_forecast"
                )
        finally:
            with target_admin_engine.begin() as connection:
                connection.exec_driver_sql(f"REVOKE {quoted_inherited_role} FROM bf_runtime")
            with migration_engine.begin() as connection:
                connection.exec_driver_sql(
                    f"REVOKE INSERT, UPDATE ON TABLE public.users FROM {quoted_inherited_role}"
                )
            with target_admin_engine.begin() as connection:
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {quoted_inherited_role}")

        # A runtime-owned users table bypasses ordinary ACL revokes; refuse the upgrade.
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql("GRANT CREATE ON SCHEMA public TO bf_runtime")
            connection.exec_driver_sql("ALTER TABLE public.users OWNER TO bf_runtime")
        try:
            with pytest.raises(Exception, match=r"bf_runtime must not own public\.users"):
                command.upgrade(migration_config, "0014_disable_public_signup")
            with migration_engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0013_direct_cash_forecast"
                )
        finally:
            with target_admin_engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE public.users OWNER TO bf_migrator")
                connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM bf_runtime")
        # Restore the supported predecessor's direct ACL after transferring ownership
        # solely for the preceding fail-closed owner probe.
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "GRANT SELECT, INSERT, UPDATE ON TABLE public.users TO bf_runtime"
            )

        # Do not cascade a runtime role's delegated INSERT grant to another role.
        delegated_role = f"bf_users_delegated_probe_{uuid4().hex[:12]}"
        quoted_delegated_role = f'"{delegated_role}"'
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql(f"CREATE ROLE {quoted_delegated_role} NOLOGIN")
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "GRANT INSERT ON TABLE public.users TO bf_runtime WITH GRANT OPTION"
            )
        with runtime_engine.begin() as connection:
            connection.exec_driver_sql(
                f"GRANT INSERT ON TABLE public.users TO {quoted_delegated_role}"
            )
        try:
            with pytest.raises(
                Exception,
                match=r"bf_runtime has delegated INSERT grants on public\.users",
            ):
                command.upgrade(migration_config, "0014_disable_public_signup")
            with migration_engine.connect() as connection:
                assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                    "0013_direct_cash_forecast"
                )
        finally:
            with migration_engine.begin() as connection:
                connection.exec_driver_sql(
                    "REVOKE INSERT ON TABLE public.users FROM bf_runtime CASCADE"
                )
                connection.exec_driver_sql("GRANT INSERT ON TABLE public.users TO bf_runtime")
            with target_admin_engine.begin() as connection:
                connection.exec_driver_sql(f"DROP ROLE IF EXISTS {quoted_delegated_role}")

        # Reproduce the predecessor's PUBLIC and direct column grants, including a
        # successful runtime insert and status update before 0014/0015 are applied.
        with migration_engine.begin() as connection:
            connection.exec_driver_sql("GRANT INSERT, UPDATE ON TABLE public.users TO PUBLIC")
            connection.exec_driver_sql(
                "GRANT INSERT (email), UPDATE (status) ON TABLE public.users TO PUBLIC"
            )
            connection.exec_driver_sql(
                "GRANT INSERT (email), UPDATE (status) ON TABLE public.users TO bf_runtime"
            )
        with migration_engine.connect() as connection:
            assert connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'INSERT')")
            )
            assert connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'public.users', 'UPDATE')")
            )
        public_grant_user_id = uuid4()
        with runtime_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.users "
                    "(id, email, display_name, password_hash, status) "
                    "VALUES (:id, :email, :display_name, :password_hash, 'active')"
                ),
                {
                    "id": public_grant_user_id,
                    "email": f"public-grant-{public_grant_user_id.hex}@example.com",
                    "display_name": "Public Grant Probe",
                    "password_hash": "test-hash-before-upgrade",
                },
            )
            connection.execute(
                text("UPDATE public.users SET status = 'disabled' WHERE id = :id"),
                {"id": public_grant_user_id},
            )

        command.upgrade(migration_config, "0014_disable_public_signup")
        wrong_role_url = admin_base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        os.environ["MIGRATION_DATABASE_URL"] = wrong_role_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.upgrade(_config_for_database(wrong_role_url), "head")
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0014_disable_public_signup"
            )
            _assert_runtime_user_privileges(
                connection, expected_insert=False, table_wide_update=True
            )

        os.environ["MIGRATION_DATABASE_URL"] = wrong_role_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.downgrade(_config_for_database(wrong_role_url), "0013_direct_cash_forecast")
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0014_disable_public_signup"
            )
            _assert_runtime_user_privileges(
                connection, expected_insert=False, table_wide_update=True
            )

        command.upgrade(migration_config, "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            _assert_public_user_writes_revoked(connection)
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text("UPDATE public.users SET status = 'active' WHERE id = :id"),
                {"id": public_grant_user_id},
            )
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text("UPDATE public.users SET password_hash = 'attacker-hash' WHERE id = :id"),
                {"id": public_grant_user_id},
            )

        # Downgrade through both privilege revisions, then repeat with column-only ACLs.
        command.downgrade(migration_config, "0013_direct_cash_forecast")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0013_direct_cash_forecast"
            )
            _assert_runtime_user_privileges(
                connection, expected_insert=False, table_wide_update=False
            )
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text("UPDATE public.users SET password_hash = 'rollback-attempt' WHERE id = :id"),
                {"id": public_grant_user_id},
            )
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.users "
                    "(id, email, display_name, password_hash) "
                    "VALUES (:id, :email, :display_name, :password_hash)"
                ),
                {
                    "id": uuid4(),
                    "email": f"blocked-after-downgrade-{uuid4().hex}@example.com",
                    "display_name": "Blocked after downgrade",
                    "password_hash": "must-not-be-inserted",
                },
            )

        # Re-upgrade with PUBLIC and direct column grants, not just table ACLs.
        with migration_engine.begin() as connection:
            connection.exec_driver_sql(
                "GRANT INSERT (id, email, display_name, password_hash) "
                "ON TABLE public.users TO PUBLIC"
            )
            connection.exec_driver_sql("GRANT UPDATE (status) ON TABLE public.users TO PUBLIC")
            connection.exec_driver_sql(
                "GRANT INSERT (email), UPDATE (status) ON TABLE public.users TO bf_runtime"
            )
        with migration_engine.connect() as connection:
            assert _has_public_user_writes(connection)

        command.upgrade(migration_config, "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            _assert_public_user_writes_revoked(connection)

        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO public.users "
                    "(id, email, display_name, password_hash) "
                    "VALUES (:id, :email, :display_name, :password_hash)"
                ),
                {
                    "id": uuid4(),
                    "email": "blocked-public-user@example.com",
                    "display_name": "Blocked User",
                    "password_hash": "must-not-be-inserted",
                },
            )
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text("UPDATE public.users SET status = 'active' WHERE id = :id"),
                {"id": public_grant_user_id},
            )
        with pytest.raises(DBAPIError), runtime_engine.begin() as connection:
            connection.execute(
                text("UPDATE public.users SET password_hash = 'attacker-hash' WHERE id = :id"),
                {"id": public_grant_user_id},
            )
        with target_admin_engine.connect() as connection:
            stored_user = connection.execute(
                text("SELECT status, password_hash FROM public.users WHERE id = :id"),
                {"id": public_grant_user_id},
            ).one()
            assert stored_user.status == "disabled"
            assert stored_user.password_hash == "test-hash-before-upgrade"

        # Exercise the 0012↔0013 M16 boundary and reject a non-migrator upgrade.
        command.downgrade(_config_for_database(migration_url), "0012_financial_calculations")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0012_financial_calculations"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.direct_cash_forecast_runs')"))
                is None
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                == "financial_calculation_runs"
            )

        wrong_role_url = admin_base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        os.environ["MIGRATION_DATABASE_URL"] = wrong_role_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.upgrade(_config_for_database(wrong_role_url), "head")
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0012_financial_calculations"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.direct_cash_forecast_runs')"))
                is None
            )

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.direct_cash_forecast_runs')"))
                == "direct_cash_forecast_runs"
            )

        # Exercise the 0012 boundary directly, preserving the complete M14 schema on downgrade.
        command.downgrade(_config_for_database(migration_url), "0011_finance_source_intake")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0011_finance_source_intake"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.finance_source_intakes')"))
                == "finance_source_intakes"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 0
            )

        # A rejected role must leave both the M14 schema and Alembic revision untouched.
        failure_url = admin_base_url.set(database=database_name).render_as_string(
            hide_password=False
        )
        os.environ["MIGRATION_DATABASE_URL"] = failure_url
        try:
            with pytest.raises(
                Exception,
                match="migrations must run as the dedicated bf_migrator role",
            ):
                command.upgrade(_config_for_database(failure_url), "head")
        finally:
            os.environ["MIGRATION_DATABASE_URL"] = migration_url
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0011_finance_source_intake"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                is None
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 0
            )

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                == "financial_calculation_runs"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 2
            )

        command.downgrade(_config_for_database(migration_url), "0006_decision_workflow")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0006_decision_workflow"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_assessments')"))
                is None
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('public.project_financial_input_versions')")
                )
                is None
            )
            for table_name in (
                "company_data_contracts",
                "company_data_sources",
                "company_data_ingestions",
                "finance_source_contracts",
                "finance_source_mappings",
                "finance_source_intakes",
                "finance_source_row_evidence",
                "financial_calculation_runs",
                "direct_cash_forecast_runs",
            ):
                assert (
                    connection.scalar(
                        text("SELECT to_regclass(:table_name)"),
                        {"table_name": f"public.{table_name}"},
                    )
                    is None
                )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'position_review_operational_imports' "
                        "AND column_name = 'company_data_ingestion_id'"
                    )
                )
                == 0
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 0
            )

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0018_customer_commitments"
            )
            _assert_runtime_user_privileges(
                connection,
                expected_insert=False,
                table_wide_update=False,
            )
            assert connection.scalar(text("SELECT count(*) FROM company_data_contracts")) == 7
            assert (
                connection.scalar(text("SELECT to_regclass('public.company_data_sources')"))
                == "company_data_sources"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.company_data_ingestions')"))
                == "company_data_ingestions"
            )
            assert (
                connection.scalar(
                    text("SELECT to_regclass('public.project_financial_input_cases')")
                )
                == "project_financial_input_cases"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT relforcerowsecurity FROM pg_class "
                        "WHERE oid = to_regclass('public.financial_assessments')"
                    )
                )
                is True
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.financial_calculation_runs')"))
                == "financial_calculation_runs"
            )
            assert (
                connection.scalar(text("SELECT to_regclass('public.direct_cash_forecast_runs')"))
                == "direct_cash_forecast_runs"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM information_schema.columns "
                        "WHERE table_name = 'finance_source_row_evidence' "
                        "AND column_name IN ("
                        "'amount_commitment_hash', 'amount_commitment_key_version')"
                    )
                )
                == 2
            )
    finally:
        if migration_engine is not None:
            migration_engine.dispose()
        if runtime_engine is not None:
            runtime_engine.dispose()
        if target_admin_engine is not None:
            target_admin_engine.dispose()
        if original_migration_url is None:
            os.environ.pop("MIGRATION_DATABASE_URL", None)
        else:
            os.environ["MIGRATION_DATABASE_URL"] = original_migration_url
        if database_created:
            with admin_engine.connect() as connection:
                connection.exec_driver_sql(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (database_name,),
                )
                connection.exec_driver_sql(f'DROP DATABASE IF EXISTS "{database_name}"')
        admin_engine.dispose()
