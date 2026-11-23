"""Persist immutable, tenant/company-scoped M5 metric comparisons.

Revision ID: 0010_metric_insight_runs
Revises: 0009_company_data_intelligence
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010_metric_insight_runs"
down_revision = "0009_company_data_intelligence"
branch_labels = None
depends_on = None


def upgrade() -> None:
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

    op.create_table(
        "metric_insight_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("baseline_metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("comparison_metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_id", sa.String(length=64), nullable=False),
        sa.Column("insight_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("comparison_definition_version", sa.String(length=64), nullable=False),
        sa.Column("availability_status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "baseline_metric_run_id <> comparison_metric_run_id",
            name="ck_metric_insight_runs_distinct_runs",
        ),
        sa.CheckConstraint(
            "length(metric_id) BETWEEN 1 AND 64", name="ck_metric_insight_runs_metric_id"
        ),
        sa.CheckConstraint(
            "availability_status IN ('available', 'partial', 'abstained')",
            name="ck_metric_insight_runs_availability",
        ),
        sa.CheckConstraint(
            "length(insight_catalog_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_catalog_version",
        ),
        sa.CheckConstraint(
            "length(engine_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_engine_version",
        ),
        sa.CheckConstraint(
            "length(comparison_definition_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_definition_version",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_insight_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_metric_insight_runs_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_metric_insight_runs_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_metric_insight_runs_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_metric_insight_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_metric_insight_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_metric_insight_runs_baseline_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "comparison_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_metric_insight_runs_comparison_metric_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metric_insight_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_metric_insight_runs_scope_id"
        ),
    )
    op.create_index(
        "ix_metric_insight_runs_tenant_company_created",
        "metric_insight_runs",
        ["tenant_id", "company_id", "created_at"],
    )
    op.create_index(
        "ix_metric_insight_runs_baseline",
        "metric_insight_runs",
        ["tenant_id", "company_id", "baseline_metric_run_id"],
    )
    op.create_index(
        "ix_metric_insight_runs_comparison",
        "metric_insight_runs",
        ["tenant_id", "company_id", "comparison_metric_run_id"],
    )
    op.execute("ALTER TABLE metric_insight_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE metric_insight_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY metric_insight_runs_company_context ON metric_insight_runs
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
            AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        CREATE FUNCTION businessflow_metric_insights_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'metric insight runs are immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_metric_insights_append_only';
        END;
        $$;
        CREATE TRIGGER trg_metric_insight_runs_append_only
          BEFORE UPDATE OR DELETE ON metric_insight_runs
          FOR EACH ROW EXECUTE FUNCTION businessflow_metric_insights_append_only();
        REVOKE ALL ON metric_insight_runs FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON metric_insight_runs TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON metric_insight_runs FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_metric_insight_runs_append_only ON metric_insight_runs")
    op.execute("DROP FUNCTION IF EXISTS businessflow_metric_insights_append_only()")
    op.drop_table("metric_insight_runs")
