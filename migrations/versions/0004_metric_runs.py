"""Persist immutable, tenant-scoped deterministic metric runs.

Revision ID: 0004_metric_runs
Revises: 0003_staged_data_imports
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004_metric_runs"
down_revision = "0003_staged_data_imports"
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
        "metric_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("start_week_date", sa.Date(), nullable=False),
        sa.Column("end_week_date", sa.Date(), nullable=False),
        sa.Column("week_count", sa.Integer(), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("reliability_status", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM start_week_date) = 1", name="ck_metric_runs_monday"
        ),
        sa.CheckConstraint("week_count BETWEEN 1 AND 52", name="ck_metric_runs_week_count"),
        sa.CheckConstraint(
            "end_week_date = start_week_date + (week_count * 7 - 1)",
            name="ck_metric_runs_period_end",
        ),
        sa.CheckConstraint(
            "reliability_status IN ('complete', 'partial', 'unavailable')",
            name="ck_metric_runs_reliability",
        ),
        sa.CheckConstraint(
            "length(metric_catalog_version) BETWEEN 1 AND 64",
            name="ck_metric_runs_catalog_version",
        ),
        sa.CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64",
            name="ck_metric_runs_calculation_version",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_metric_runs_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_metric_runs_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_metric_runs_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_metric_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_metric_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_metric_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_metric_runs_tenant_company_id"
        ),
    )
    op.create_index(
        "ix_metric_runs_tenant_company_created",
        "metric_runs",
        ["tenant_id", "company_id", "created_at"],
    )
    op.execute("ALTER TABLE metric_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE metric_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY metric_runs_tenant_context ON metric_runs
          USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
          WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)
        """
    )
    op.execute(
        """
        GRANT SELECT, INSERT ON metric_runs TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON metric_runs FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.drop_table("metric_runs")
