"""Add company-scoped, versioned business metric and KPI definitions.

Revision ID: 0021_bi_registry
Revises: 0020_business_semantic_layer
Create Date: 2026-11-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021_bi_registry"
down_revision: str | Sequence[str] | None = "0020_business_semantic_layer"
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
        "business_metric_definition_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("definition_type", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.String(length=2000), nullable=False),
        sa.Column("domain", sa.String(length=24), nullable=False),
        sa.Column("category", sa.String(length=64), nullable=False),
        sa.Column("business_meaning", sa.String(length=2000), nullable=False),
        sa.Column("calculation_key", sa.String(length=48), nullable=False),
        sa.Column("calculation_spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("formula", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("numerator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("denominator", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("dependencies", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("aggregation_method", sa.String(length=24), nullable=False),
        sa.Column("dimensions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "required_source_datasets", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("required_fields", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "required_relationships", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("filters", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("exclusions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("unit", sa.String(length=80), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("time_granularity", sa.String(length=16), nullable=False),
        sa.Column("period_boundary", sa.String(length=32), nullable=False),
        sa.Column("timezone_rule", sa.String(length=24), nullable=False),
        sa.Column("policy", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "completeness_requirements", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("limitations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("precision_scale", sa.Integer(), nullable=False),
        sa.Column("rounding_mode", sa.String(length=16), nullable=False),
        sa.Column("metric_owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("supersedes_metric_definition_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("author_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reviewed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_bmi_metric_definition_version"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_metric_definition_version_id IS NULL) OR "
            "(version > 1 AND supersedes_metric_definition_version_id IS NOT NULL)",
            name="ck_bmi_metric_definition_supersedes_version",
        ),
        sa.CheckConstraint(
            "metric_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bmi_metric_definition_key"
        ),
        sa.CheckConstraint(
            "definition_type IN ('company', 'custom')", name="ck_bmi_metric_definition_type"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'review', 'published', 'deprecated')",
            name="ck_bmi_metric_definition_status",
        ),
        sa.CheckConstraint(
            "domain IN ('finance', 'sales', 'customers', 'procurement', 'operations', 'people')",
            name="ck_bmi_metric_definition_domain",
        ),
        sa.CheckConstraint(
            "calculation_key IN ('universal_record_count', 'universal_source_amount_sum', "
            "'universal_distinct_source_values', 'm5_metric', "
            "'core_financial_measure', 'custom_formula')",
            name="ck_bmi_metric_definition_calculator",
        ),
        sa.CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 160", name="ck_bmi_metric_definition_name"
        ),
        sa.CheckConstraint(
            "length(btrim(description)) BETWEEN 1 AND 2000",
            name="ck_bmi_metric_definition_description",
        ),
        sa.CheckConstraint(
            "length(btrim(category)) BETWEEN 1 AND 64", name="ck_bmi_metric_definition_category"
        ),
        sa.CheckConstraint(
            "length(btrim(business_meaning)) BETWEEN 1 AND 2000",
            name="ck_bmi_metric_definition_meaning",
        ),
        sa.CheckConstraint(
            "length(btrim(unit)) BETWEEN 1 AND 80", name="ck_bmi_metric_definition_unit"
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_bmi_metric_definition_currency",
        ),
        sa.CheckConstraint(
            "precision_scale BETWEEN 0 AND 12", name="ck_bmi_metric_definition_precision"
        ),
        sa.CheckConstraint(
            "rounding_mode IN ('half_even', 'half_up', 'down', 'up', 'none')",
            name="ck_bmi_metric_definition_rounding",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_metric_definition_hash"
        ),
        sa.CheckConstraint(
            "calculation_spec IS NOT NULL AND jsonb_typeof(calculation_spec) = 'object'",
            name="ck_bmi_metric_definition_calc",
        ),
        sa.CheckConstraint(
            "(formula IS NULL OR jsonb_typeof(formula) = 'object') AND "
            "(numerator IS NULL OR jsonb_typeof(numerator) = 'object') AND "
            "(denominator IS NULL OR jsonb_typeof(denominator) = 'object')",
            name="ck_bmi_metric_definition_formula",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(dependencies) = 'array' AND jsonb_typeof(dimensions) = 'array' AND "
            "jsonb_typeof(required_source_datasets) = 'array' AND "
            "jsonb_typeof(required_fields) = 'array' AND "
            "jsonb_typeof(required_relationships) = 'array' AND "
            "jsonb_typeof(filters) = 'array' AND "
            "jsonb_typeof(exclusions) = 'array' AND jsonb_typeof(policy) = 'object' AND "
            "jsonb_typeof(completeness_requirements) = 'array' AND "
            "jsonb_typeof(limitations) = 'array'",
            name="ck_bmi_metric_definition_metadata",
        ),
        sa.CheckConstraint(
            "effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from",
            name="ck_bmi_metric_definition_effective_period",
        ),
        sa.CheckConstraint(
            "(status IN ('published', 'deprecated') AND reviewed_by_user_id IS NOT NULL "
            "AND review_note IS NOT NULL AND length(btrim(review_note)) BETWEEN 1 AND 500) OR "
            "(status IN ('draft', 'review') AND reviewed_by_user_id IS NULL "
            "AND review_note IS NULL)",
            name="ck_bmi_metric_definition_published_review",
        ),
        sa.CheckConstraint(
            "reviewed_by_user_id IS NULL OR reviewed_by_user_id <> author_user_id",
            name="ck_bmi_metric_definition_independent_reviewer",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_metric_definition_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "author_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_author",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_owner_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_reviewer",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "supersedes_metric_definition_version_id", "metric_key"],
            [
                "business_metric_definition_versions.tenant_id",
                "business_metric_definition_versions.company_id",
                "business_metric_definition_versions.id",
                "business_metric_definition_versions.metric_key",
            ],
            name="fk_bmi_metric_definition_supersedes",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_metric_definition_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_bmi_metric_definition_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", "metric_key", name="uq_bmi_metric_definition_scope_key"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "metric_key",
            "version",
            name="uq_bmi_metric_definition_version",
        ),
    )
    op.create_index(
        "ix_bmi_metric_definitions_company_domain_status",
        "business_metric_definition_versions",
        ["tenant_id", "company_id", "domain", "status", "created_at"],
    )
    _company_rls("business_metric_definition_versions")

    op.execute(
        """
        CREATE FUNCTION prevent_business_metric_definition_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            actor_id uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'draft' OR NEW.reviewed_by_user_id IS NOT NULL
                   OR NEW.review_note IS NOT NULL
                   OR NEW.author_user_id IS DISTINCT FROM actor_id
                   OR NEW.created_by_user_id IS DISTINCT FROM actor_id THEN
                    RAISE EXCEPTION 'metric definitions must begin as attributed drafts'
                        USING ERRCODE = '55000',
                              CONSTRAINT = 'business_metric_definition_initial_state';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION 'business metric definition versions cannot be deleted'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_append_only';
            END IF;
            IF (to_jsonb(NEW) - 'status' - 'reviewed_by_user_id' - 'review_note')
               IS DISTINCT FROM
               (to_jsonb(OLD) - 'status' - 'reviewed_by_user_id' - 'review_note') THEN
                RAISE EXCEPTION 'business metric definition versions are append-only'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_append_only';
            END IF;
            IF OLD.status IN ('published', 'deprecated') AND
               (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION 'published business metric definition content and '
                    'review evidence are immutable'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_'
                    'published_immutable';
            END IF;
            IF OLD.status = 'published' AND NEW.status NOT IN ('published', 'deprecated') THEN
                RAISE EXCEPTION 'published business metric definition versions cannot be '
                    'restored to draft'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_'
                    'published_immutable';
            END IF;
            IF OLD.status = 'deprecated' AND NEW.status <> 'deprecated' THEN
                RAISE EXCEPTION 'deprecated business metric definition versions cannot be restored'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_deprecated';
            END IF;
            IF NOT (
                (OLD.status = 'draft' AND NEW.status IN ('draft', 'review')) OR
                (OLD.status = 'review' AND NEW.status IN ('review', 'draft', 'published')) OR
                (OLD.status = 'published' AND NEW.status IN ('published', 'deprecated')) OR
                (OLD.status = 'deprecated' AND NEW.status = 'deprecated')
            ) THEN
                RAISE EXCEPTION 'invalid business metric definition lifecycle transition'
                    USING ERRCODE = '55000', CONSTRAINT = 'business_metric_definition_transition';
            END IF;
            IF OLD.status = 'review' AND NEW.status = 'published' THEN
                IF actor_id IS NULL OR NEW.reviewed_by_user_id IS DISTINCT FROM actor_id
                   OR NEW.reviewed_by_user_id = NEW.author_user_id
                   OR NEW.review_note IS NULL OR length(btrim(NEW.review_note)) = 0 THEN
                    RAISE EXCEPTION 'publishing requires an independent reviewer and note'
                        USING ERRCODE = '55000',
                              CONSTRAINT = 'business_metric_definition_review_guard';
                END IF;
            ELSIF NEW.reviewed_by_user_id IS DISTINCT FROM OLD.reviewed_by_user_id
               OR NEW.review_note IS DISTINCT FROM OLD.review_note THEN
                RAISE EXCEPTION 'review evidence changes only when publishing'
                    USING ERRCODE = '55000',
                          CONSTRAINT = 'business_metric_definition_review_guard';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """CREATE TRIGGER trg_business_metric_definition_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON business_metric_definition_versions
        FOR EACH ROW EXECUTE FUNCTION prevent_business_metric_definition_mutation()"""
    )
    op.execute("REVOKE ALL ON business_metric_definition_versions FROM PUBLIC, bf_runtime")
    op.execute("GRANT SELECT, INSERT, UPDATE ON business_metric_definition_versions TO bf_runtime")


def downgrade() -> None:
    _require_migrator()
    # bf_migrator is deliberately NO BYPASSRLS; disable forced RLS transactionally so
    # the retained-data guard sees every Company row. A failed guard rolls this DDL back.
    op.execute("ALTER TABLE business_metric_definition_versions DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM business_metric_definition_versions) THEN
            RAISE EXCEPTION 'Cannot downgrade while business metric definition data is retained';
          END IF;
        END;
        $$;
        """
    )
    op.execute("DROP FUNCTION IF EXISTS prevent_business_metric_definition_mutation() CASCADE")
    op.drop_index(
        "ix_bmi_metric_definitions_company_domain_status",
        table_name="business_metric_definition_versions",
    )
    op.drop_table("business_metric_definition_versions")
