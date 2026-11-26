"""Add pinned Company semantic configuration and immutable BI analyses.

Revision ID: 0022_bi_config_analysis
Revises: 0021_bi_registry
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0022_bi_config_analysis"
down_revision: str | Sequence[str] | None = "0021_bi_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _require_migrator() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF current_user <> 'bf_migrator' THEN
            RAISE EXCEPTION 'migrations must run as the dedicated bf_migrator role';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'required non-privileged role bf_runtime does not exist';
          END IF;
          IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'bf_runtime must not be a superuser or bypass RLS';
          END IF;
          IF pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
        END;
        $$;
        """
    )


def _company_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_company_context ON {table_name}
              USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
              WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
            """
        )
    )


def upgrade() -> None:
    _require_migrator()
    op.create_table(
        "business_company_semantic_config_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("base_currency", sa.String(length=3), nullable=False),
        sa.Column("timezone_name", sa.String(length=64), nullable=False),
        sa.Column("fiscal_year_start_month", sa.Integer(), nullable=False),
        sa.Column("fiscal_year_label", sa.String(length=16), nullable=False),
        sa.Column("week_start_day", sa.Integer(), nullable=False),
        sa.Column("fallback_policy", sa.String(length=32), nullable=False),
        sa.Column("semantic_policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("configuration_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_bmi_company_config_version"),
        sa.CheckConstraint("status IN ('draft', 'published')", name="ck_bmi_company_config_status"),
        sa.CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="ck_bmi_company_config_currency"),
        sa.CheckConstraint(
            "length(timezone_name) BETWEEN 1 AND 64", name="ck_bmi_company_config_timezone"
        ),
        sa.CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12", name="ck_bmi_company_config_fiscal_month"
        ),
        sa.CheckConstraint(
            "fiscal_year_label IN ('start_year', 'end_year')",
            name="ck_bmi_company_config_fiscal_label",
        ),
        sa.CheckConstraint(
            "week_start_day BETWEEN 1 AND 7", name="ck_bmi_company_config_week_start"
        ),
        sa.CheckConstraint(
            "fallback_policy IN ('strict_primary', 'next_ready_source')",
            name="ck_bmi_company_config_fallback",
        ),
        sa.CheckConstraint(
            "configuration_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_company_config_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(semantic_policy) = 'object'", name="ck_bmi_company_config_semantic_policy"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_company_config_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_company_config_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_company_semantic_config_versions"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_bmi_company_config_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "version", name="uq_bmi_company_config_version"
        ),
    )
    op.create_index(
        "ix_bmi_company_configs_scope_created",
        "business_company_semantic_config_versions",
        ["tenant_id", "company_id", "created_at"],
    )
    _company_rls("business_company_semantic_config_versions")

    op.create_table(
        "business_company_semantic_source_precedence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("configuration_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "configuration_version_id"],
            [
                "business_company_semantic_config_versions.tenant_id",
                "business_company_semantic_config_versions.company_id",
                "business_company_semantic_config_versions.id",
            ],
            name="fk_bmi_source_precedence_config",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_bmi_source_precedence_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_bmi_source_precedence_profile",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_company_semantic_source_precedence"),
        sa.UniqueConstraint(
            "configuration_version_id",
            "dataset_type",
            "priority",
            name="uq_bmi_source_precedence_priority",
        ),
        sa.UniqueConstraint(
            "configuration_version_id",
            "dataset_type",
            "source_id",
            name="uq_bmi_source_precedence_source",
        ),
        sa.CheckConstraint(
            "dataset_type = 'universal_records'", name="ck_bmi_source_precedence_dataset"
        ),
        sa.CheckConstraint("priority >= 1", name="ck_bmi_source_precedence_priority"),
    )
    op.create_index(
        "ix_bmi_source_precedence_scope",
        "business_company_semantic_source_precedence",
        ["tenant_id", "company_id", "configuration_version_id", "dataset_type", "priority"],
    )
    _company_rls("business_company_semantic_source_precedence")

    op.create_table(
        "business_intelligence_analysis_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("configuration_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("configuration_version", sa.Integer(), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("metric_definition_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("metric_version", sa.Integer(), nullable=True),
        sa.Column("system_metric_version", sa.String(length=96), nullable=True),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("definition_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "configuration_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "(metric_definition_version_id IS NOT NULL AND metric_version IS NOT NULL "
            "AND system_metric_version IS NULL) OR "
            "(metric_definition_version_id IS NULL AND metric_version IS NULL "
            "AND system_metric_version IS NOT NULL)",
            name="ck_bmi_analysis_metric_reference",
        ),
        sa.CheckConstraint(
            "metric_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bmi_analysis_metric_key"
        ),
        sa.CheckConstraint(
            "length(btrim(calculation_version)) BETWEEN 1 AND 64",
            name="ck_bmi_analysis_engine_version",
        ),
        sa.CheckConstraint(
            "status IN ('complete', 'partial', 'abstained')", name="ck_bmi_analysis_status"
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_definition_hash"
        ),
        sa.CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_input_hash"),
        sa.CheckConstraint("result_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_result_hash"),
        sa.CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end >= period_start)",
            name="ck_bmi_analysis_period",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition_snapshot) = 'object'",
            name="ck_bmi_analysis_definition_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(configuration_snapshot) = 'object'",
            name="ck_bmi_analysis_configuration_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'", name="ck_bmi_analysis_input_snapshot"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'", name="ck_bmi_analysis_result_snapshot"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_analysis_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_analysis_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "configuration_version_id"],
            [
                "business_company_semantic_config_versions.tenant_id",
                "business_company_semantic_config_versions.company_id",
                "business_company_semantic_config_versions.id",
            ],
            name="fk_bmi_analysis_config",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_definition_version_id", "metric_key"],
            [
                "business_metric_definition_versions.tenant_id",
                "business_metric_definition_versions.company_id",
                "business_metric_definition_versions.id",
                "business_metric_definition_versions.metric_key",
            ],
            name="fk_bmi_analysis_metric_definition",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_intelligence_analysis_runs"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_bmi_analysis_scope_id"),
    )
    op.create_index(
        "ix_bmi_analysis_company_created",
        "business_intelligence_analysis_runs",
        ["tenant_id", "company_id", "created_at"],
    )
    op.create_index(
        "ix_bmi_analysis_metric_created",
        "business_intelligence_analysis_runs",
        ["tenant_id", "company_id", "metric_key", "created_at"],
    )
    _company_rls("business_intelligence_analysis_runs")

    op.execute(
        """
        CREATE FUNCTION prevent_published_business_company_config_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' OR NEW.created_by_user_id IS DISTINCT FROM actor_id THEN
                    RAISE EXCEPTION 'Company configurations must begin as attributed drafts'
                        USING ERRCODE = '55000',
                              CONSTRAINT = 'business_company_config_initial_state';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'Company semantic configuration versions cannot be deleted'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_company_config_immutable';
            END IF;
            IF OLD.status = 'published' THEN
                RAISE EXCEPTION 'Published Company semantic configuration versions are immutable'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_company_config_immutable';
            END IF;
            IF OLD.status <> 'draft' OR NEW.status <> 'published' OR
               actor_id IS DISTINCT FROM OLD.created_by_user_id OR
               (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION 'Company configuration can only be published by its creator'
                    USING ERRCODE = '55000',
                          CONSTRAINT = 'business_company_config_transition';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """CREATE TRIGGER trg_bmi_company_config_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON business_company_semantic_config_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_published_business_company_config_mutation()"""
    )
    op.execute(
        """
        CREATE FUNCTION prevent_business_company_source_precedence_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            config_status varchar(16);
            config_creator uuid;
            actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
        BEGIN
            IF TG_OP <> 'INSERT' THEN
                RAISE EXCEPTION 'Company semantic source precedence is immutable'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_company_source_precedence_'
                    'immutable';
            END IF;
            SELECT status, created_by_user_id INTO config_status, config_creator
              FROM business_company_semantic_config_versions
             WHERE tenant_id = NEW.tenant_id
               AND company_id = NEW.company_id
               AND id = NEW.configuration_version_id;
            IF config_status IS DISTINCT FROM 'draft'
               OR config_creator IS DISTINCT FROM actor_id THEN
                RAISE EXCEPTION 'Source precedence can be added only by the attributed creator '
                    'while configuration is draft'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_company_source_precedence_'
                    'published';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """CREATE TRIGGER trg_bmi_source_precedence_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON business_company_semantic_source_precedence
        FOR EACH ROW EXECUTE FUNCTION prevent_business_company_source_precedence_mutation()"""
    )
    op.execute(
        """
        CREATE FUNCTION prevent_business_intelligence_analysis_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.requested_by_user_id IS DISTINCT FROM actor_id THEN
                    RAISE EXCEPTION 'analysis actor must match the request principal'
                        USING ERRCODE = '55000',
                              CONSTRAINT = 'business_intelligence_analysis_actor';
                END IF;
                RETURN NEW;
            END IF;
            RAISE EXCEPTION 'Business Intelligence analysis runs are immutable'
                USING ERRCODE = '55000', CONSTRAINT = 'business_intelligence_analysis_immutable';
        END;
        $$
        """
    )
    op.execute(
        """CREATE TRIGGER trg_bmi_analysis_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON business_intelligence_analysis_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_business_intelligence_analysis_mutation()"""
    )

    for table in (
        "business_company_semantic_config_versions",
        "business_company_semantic_source_precedence",
        "business_intelligence_analysis_runs",
    ):
        op.execute(f"REVOKE ALL ON {table} FROM PUBLIC, bf_runtime")
    op.execute(
        "GRANT SELECT, INSERT, UPDATE ON business_company_semantic_config_versions TO bf_runtime"
    )
    op.execute("GRANT SELECT, INSERT ON business_company_semantic_source_precedence TO bf_runtime")
    op.execute("GRANT SELECT, INSERT ON business_intelligence_analysis_runs TO bf_runtime")


def downgrade() -> None:
    _require_migrator()
    # bf_migrator is deliberately NO BYPASSRLS; disable forced RLS transactionally so
    # the retained-data guard sees every Company row. A failed guard rolls this DDL back.
    tables = (
        "business_intelligence_analysis_runs",
        "business_company_semantic_source_precedence",
        "business_company_semantic_config_versions",
    )
    for table in tables:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM business_intelligence_analysis_runs)
             OR EXISTS (SELECT 1 FROM business_company_semantic_source_precedence)
             OR EXISTS (SELECT 1 FROM business_company_semantic_config_versions) THEN
            RAISE EXCEPTION 'Cannot downgrade while Company configuration or BI evidence '
                'is retained';
          END IF;
        END;
        $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_business_intelligence_analysis_mutation() CASCADE")
    op.execute(
        "DROP FUNCTION IF EXISTS prevent_business_company_source_precedence_mutation() CASCADE"
    )
    op.execute(
        "DROP FUNCTION IF EXISTS prevent_published_business_company_config_mutation() CASCADE"
    )
    op.drop_index(
        "ix_bmi_analysis_metric_created", table_name="business_intelligence_analysis_runs"
    )
    op.drop_index(
        "ix_bmi_analysis_company_created", table_name="business_intelligence_analysis_runs"
    )
    op.drop_index(
        "ix_bmi_source_precedence_scope", table_name="business_company_semantic_source_precedence"
    )
    op.drop_index(
        "ix_bmi_company_configs_scope_created",
        table_name="business_company_semantic_config_versions",
    )
    op.drop_table("business_intelligence_analysis_runs")
    op.drop_table("business_company_semantic_source_precedence")
    op.drop_table("business_company_semantic_config_versions")
