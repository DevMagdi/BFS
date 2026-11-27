from __future__ import annotations

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import DBAPIError

from businessflow import management

ROOT = Path(__file__).resolve().parents[1]
COMMITMENT_METRIC_KEY = "operations.accepted_customer_commitment_count"


def _config_for_database(database_url: str) -> Config:
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@contextmanager
def _temporary_migration_database(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Engine, Config, str]]:
    database_name = f"businessflow_domain_0025_test_{uuid4().hex[:12]}"
    admin_base_url = make_url(os.environ["TEST_ADMIN_DATABASE_URL"])
    admin_root_engine = create_engine(
        admin_base_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        hide_parameters=True,
    )
    database_created = False
    migration_engine: Engine | None = None
    target_admin_engine: Engine | None = None
    try:
        quoted_database_name = admin_root_engine.dialect.identifier_preparer.quote(database_name)
        with admin_root_engine.connect() as connection:
            connection.exec_driver_sql(f"CREATE DATABASE {quoted_database_name} OWNER bf_migrator")
            database_created = True
            connection.exec_driver_sql(f"REVOKE ALL ON DATABASE {quoted_database_name} FROM PUBLIC")
            connection.exec_driver_sql(
                f"GRANT CONNECT ON DATABASE {quoted_database_name} TO bf_runtime, bf_migrator"
            )

        migration_url = make_url(os.environ["TEST_MIGRATION_DATABASE_URL"]).set(
            database=database_name
        )
        migration_url_text = migration_url.render_as_string(hide_password=False)
        monkeypatch.setenv("MIGRATION_DATABASE_URL", migration_url_text)

        target_admin_engine = create_engine(
            admin_base_url.set(database=database_name), hide_parameters=True
        )
        with target_admin_engine.begin() as connection:
            connection.exec_driver_sql("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            connection.exec_driver_sql("ALTER SCHEMA public OWNER TO bf_migrator")
            connection.exec_driver_sql("GRANT USAGE ON SCHEMA public TO bf_runtime")
            connection.exec_driver_sql("REVOKE CREATE ON SCHEMA public FROM bf_runtime")

        migration_engine = create_engine(migration_url, hide_parameters=True)
        config = _config_for_database(migration_url_text)
        command.upgrade(config, "0024_company_wide_intelligence")
        yield migration_engine, target_admin_engine, config, migration_url_text
    finally:
        if migration_engine is not None:
            migration_engine.dispose()
        if target_admin_engine is not None:
            target_admin_engine.dispose()
        if database_created:
            with admin_root_engine.connect() as connection:
                connection.exec_driver_sql(f"DROP DATABASE {quoted_database_name} WITH (FORCE)")
        admin_root_engine.dispose()


def _create_migration_owner(migration_url_text: str, target_admin_engine: Engine):
    management.bootstrap_owner(
        migration_url_text,
        email="migration-0025-owner@example.com",
        display_name="Migration 0025 Owner",
        password="A secure migration test password 42!",
        tenant_name="Migration 0025 Tenant",
        company_name="Migration 0025 Company",
        base_currency="USD",
        timezone="UTC",
        locale="en",
    )
    with target_admin_engine.connect() as connection:
        return connection.execute(
            text(
                "SELECT membership.tenant_id, membership.user_id, company.id AS company_id "
                "FROM tenant_memberships AS membership "
                "JOIN companies AS company ON company.tenant_id = membership.tenant_id "
                "WHERE membership.role = 'owner' AND membership.status = 'active' "
                "AND company.legal_name = 'Migration 0025 Company'"
            )
        ).one()


def _set_company_scope(connection, scope) -> None:
    connection.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(scope.tenant_id)},
    )
    connection.execute(
        text("SELECT set_config('app.company_id', :company_id, true)"),
        {"company_id": str(scope.company_id)},
    )


def _insert_metric_definition(
    connection,
    *,
    scope,
    definition_id: UUID,
    metric_key: str,
    calculation_key: str,
    dependencies: list[dict[str, str]],
) -> None:
    connection.execute(
        text(
            "INSERT INTO business_metric_definition_versions ("
            "id, tenant_id, company_id, metric_key, version, definition_type, status, "
            "name, description, domain, category, business_meaning, calculation_key, "
            "calculation_spec, dependencies, aggregation_method, dimensions, "
            "required_source_datasets, required_fields, required_relationships, filters, "
            "exclusions, unit, time_granularity, period_boundary, timezone_rule, policy, "
            "completeness_requirements, limitations, precision_scale, rounding_mode, "
            "metric_owner_user_id, definition_hash, author_user_id, created_by_user_id"
            ") VALUES ("
            ":id, :tenant_id, :company_id, :metric_key, 1, 'company', 'draft', "
            ":name, 'Migration retention regression definition', 'operations', "
            "'delivery_commitment', 'A test-only commitment observation.', :calculation_key, "
            "CAST(:calculation_spec AS jsonb), CAST(:dependencies AS jsonb), 'count', "
            "'[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb, "
            "'[]'::jsonb, 'records', 'none', 'calendar_period', 'company', '{}'::jsonb, "
            "'[]'::jsonb, '[]'::jsonb, 0, 'none', :user_id, :definition_hash, :user_id, :user_id"
            ")"
        ),
        {
            "id": str(definition_id),
            "tenant_id": str(scope.tenant_id),
            "company_id": str(scope.company_id),
            "metric_key": metric_key,
            "name": f"Migration retention {metric_key}",
            "calculation_key": calculation_key,
            "calculation_spec": json.dumps({"kind": calculation_key}),
            "dependencies": json.dumps(dependencies),
            "user_id": str(scope.user_id),
            "definition_hash": "0" * 64,
        },
    )


def _insert_commitment_definition_chain(connection, scope) -> tuple[UUID, UUID, UUID]:
    base_id = uuid4()
    dependent_id = uuid4()
    transitive_id = uuid4()
    _insert_metric_definition(
        connection,
        scope=scope,
        definition_id=base_id,
        metric_key=COMMITMENT_METRIC_KEY,
        calculation_key="manual_customer_commitment_count",
        dependencies=[],
    )
    _insert_metric_definition(
        connection,
        scope=scope,
        definition_id=dependent_id,
        metric_key="operations.commitment_ratio",
        calculation_key="custom_formula",
        dependencies=[{"metric_key": COMMITMENT_METRIC_KEY, "version_id": str(base_id)}],
    )
    _insert_metric_definition(
        connection,
        scope=scope,
        definition_id=transitive_id,
        metric_key="operations.commitment_rate",
        calculation_key="custom_formula",
        dependencies=[
            {"metric_key": "operations.commitment_ratio", "version_id": str(dependent_id)}
        ],
    )
    return base_id, dependent_id, transitive_id


def _insert_configuration_and_linked_analysis(
    connection,
    scope,
    *,
    metric_definition_id: UUID,
    metric_key: str,
) -> None:
    configuration_id = uuid4()
    analysis_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO business_company_semantic_config_versions ("
            "id, tenant_id, company_id, version, status, base_currency, timezone_name, "
            "fiscal_year_start_month, fiscal_year_label, week_start_day, fallback_policy, "
            "semantic_policy, configuration_hash, created_by_user_id"
            ") VALUES ("
            ":id, :tenant_id, :company_id, 1, 'published', 'USD', 'UTC', 1, 'start_year', "
            "1, 'strict_primary', '{}'::jsonb, :configuration_hash, :user_id"
            ")"
        ),
        {
            "id": str(configuration_id),
            "tenant_id": str(scope.tenant_id),
            "company_id": str(scope.company_id),
            "configuration_hash": "0" * 64,
            "user_id": str(scope.user_id),
        },
    )
    connection.execute(
        text(
            "INSERT INTO business_intelligence_analysis_runs ("
            "id, tenant_id, company_id, requested_by_user_id, configuration_version_id, "
            "configuration_version, metric_key, metric_definition_version_id, metric_version, "
            "system_metric_version, definition_hash, definition_snapshot, configuration_snapshot, "
            "status, calculation_version, input_hash, result_hash, input_snapshot, result_snapshot"
            ") VALUES ("
            ":id, :tenant_id, :company_id, :user_id, :configuration_id, 1, :metric_key, "
            ":metric_definition_id, 1, NULL, :definition_hash, "
            "CAST(:definition_snapshot AS jsonb), '{}'::jsonb, 'complete', "
            "'migration-retention-test-v1', :input_hash, :result_hash, "
            "'{}'::jsonb, '{}'::jsonb"
            ")"
        ),
        {
            "id": str(analysis_id),
            "tenant_id": str(scope.tenant_id),
            "company_id": str(scope.company_id),
            "user_id": str(scope.user_id),
            "configuration_id": str(configuration_id),
            "metric_key": metric_key,
            "metric_definition_id": str(metric_definition_id),
            "definition_hash": "0" * 64,
            "definition_snapshot": json.dumps(
                {"calculation_key": "custom_formula", "calculation": {"kind": "custom_formula"}}
            ),
            "input_hash": "0" * 64,
            "result_hash": "0" * 64,
        },
    )


def test_domain_expansion_migration_backfills_manual_profiles_and_enforces_source_retention(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temporary_migration_database(monkeypatch) as (
        migration_engine,
        target_admin_engine,
        config,
        migration_url_text,
    ):
        scope = _create_migration_owner(migration_url_text, target_admin_engine)
        with target_admin_engine.connect() as connection:
            manual_contract = connection.execute(
                text(
                    "SELECT contract_key, version FROM company_data_contracts "
                    "WHERE dataset_type = 'customer_commitments' ORDER BY version DESC LIMIT 1"
                )
            ).one()
        manual_source_id = uuid4()
        with migration_engine.begin() as connection:
            _set_company_scope(connection, scope)
            connection.execute(
                text(
                    "INSERT INTO company_data_sources "
                    "(id, tenant_id, company_id, source_key, display_name, dataset_type, "
                    "contract_key, contract_version, owner_user_id, registered_by_user_id) "
                    "VALUES (:id, :tenant_id, :company_id, :source_key, :display_name, "
                    ":dataset_type, :contract_key, :contract_version, :user_id, :user_id)"
                ),
                {
                    "id": str(manual_source_id),
                    "tenant_id": str(scope.tenant_id),
                    "company_id": str(scope.company_id),
                    "source_key": "manual.customer-commitments",
                    "display_name": "Manual customer delivery commitments",
                    "dataset_type": "customer_commitments",
                    "contract_key": manual_contract.contract_key,
                    "contract_version": manual_contract.version,
                    "user_id": str(scope.user_id),
                },
            )
        with target_admin_engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT count(*) FROM company_data_source_profile_versions "
                        "WHERE source_id = :source_id"
                    ),
                    {"source_id": str(manual_source_id)},
                )
                == 0
            )

        command.upgrade(config, "0025_domain_intelligence")
        with target_admin_engine.connect() as connection:
            profile = connection.execute(
                text(
                    "SELECT version, source_type, business_domain, source_status, "
                    "authority_classification, owner_user_id, created_by_user_id "
                    "FROM company_data_source_profile_versions WHERE source_id = :source_id"
                ),
                {"source_id": str(manual_source_id)},
            ).one()
        assert profile.version == 1
        assert profile.source_type == "manual_entry"
        assert profile.business_domain == "operations"
        assert profile.source_status == "enabled"
        assert profile.authority_classification == "manual_assertion"
        assert profile.owner_user_id == scope.user_id
        assert profile.created_by_user_id == scope.user_id

        universal_source_id = uuid4()
        with migration_engine.begin() as connection:
            _set_company_scope(connection, scope)
            connection.execute(
                text(
                    "INSERT INTO company_data_sources "
                    "(id, tenant_id, company_id, source_key, display_name, dataset_type, "
                    "contract_key, contract_version, owner_user_id, registered_by_user_id) "
                    "VALUES (:id, :tenant_id, :company_id, :source_key, :display_name, "
                    "'universal_records', :contract_key, '1.1.0', :user_id, :user_id)"
                ),
                {
                    "id": str(universal_source_id),
                    "tenant_id": str(scope.tenant_id),
                    "company_id": str(scope.company_id),
                    "source_key": "migration-universal-source",
                    "display_name": "Migration Universal Source",
                    "contract_key": "businessflow.universal.business_records",
                    "user_id": str(scope.user_id),
                },
            )

        with pytest.raises(
            DBAPIError,
            match=r"cannot downgrade: Universal Data 1.1.0 sources exist",
        ):
            command.downgrade(config, "0024_company_wide_intelligence")
        with target_admin_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0025_domain_intelligence"
            )
            protected_table_security = connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:tables)"
                ),
                {
                    "tables": [
                        "business_semantic_relationship_versions",
                        "company_data_sources",
                        "business_company_semantic_source_precedence",
                        "business_metric_definition_versions",
                        "business_intelligence_analysis_runs",
                    ]
                },
            ).all()
        assert len(protected_table_security) == 5
        assert all(
            row.relrowsecurity and row.relforcerowsecurity for row in protected_table_security
        )


def test_domain_expansion_downgrade_blocks_transitively_dependent_commitment_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temporary_migration_database(monkeypatch) as (
        migration_engine,
        target_admin_engine,
        config,
        migration_url_text,
    ):
        scope = _create_migration_owner(migration_url_text, target_admin_engine)
        command.upgrade(config, "0025_domain_intelligence")
        with migration_engine.begin() as connection:
            _set_company_scope(connection, scope)
            _insert_commitment_definition_chain(connection, scope)

        with pytest.raises(
            DBAPIError,
            match=r"cannot downgrade: saved customer commitment KPI definitions exist",
        ):
            command.downgrade(config, "0024_company_wide_intelligence")
        with target_admin_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0025_domain_intelligence"
            )


def test_domain_expansion_downgrade_blocks_analysis_linked_to_transitive_commitment_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _temporary_migration_database(monkeypatch) as (
        migration_engine,
        target_admin_engine,
        config,
        migration_url_text,
    ):
        scope = _create_migration_owner(migration_url_text, target_admin_engine)
        command.upgrade(config, "0025_domain_intelligence")
        with migration_engine.begin() as connection:
            _set_company_scope(connection, scope)
            _base_id, _dependent_id, transitive_id = _insert_commitment_definition_chain(
                connection, scope
            )
            _insert_configuration_and_linked_analysis(
                connection,
                scope,
                metric_definition_id=transitive_id,
                metric_key="operations.commitment_rate",
            )

        with pytest.raises(
            DBAPIError,
            match=r"cannot downgrade: customer commitment analysis evidence exists",
        ):
            command.downgrade(config, "0024_company_wide_intelligence")
        with target_admin_engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0025_domain_intelligence"
            )
