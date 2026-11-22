from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[1]


def _config_for_database(url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return config


def test_migration_head_upgrade_downgrade_and_reupgrade():
    database_name = f"businessflow_m12_migration_test_{uuid4().hex[:12]}"
    admin_base_url = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])
    admin_url = admin_base_url.set(database="postgres")
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT", hide_parameters=True)
    database_created = False
    migration_url = ""
    migration_engine = None
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
        with migration_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_company_data_intelligence"
            )
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

            assert connection.scalar(text("SELECT count(*) FROM company_data_contracts")) == 6
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

        command.upgrade(_config_for_database(migration_url), "head")
        with migration_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0009_company_data_intelligence"
            )
            assert connection.scalar(text("SELECT count(*) FROM company_data_contracts")) == 6
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
    finally:
        if migration_engine is not None:
            migration_engine.dispose()
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
