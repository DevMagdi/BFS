"""Add versioned project intake and immutable capacity-only assessment evidence.

Revision ID: 0005_capacity_assessment
Revises: 0004_metric_runs
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005_capacity_assessment"
down_revision = "0004_metric_runs"
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
        "project_intakes",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_code", sa.String(length=64), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_project_intakes_current_version"),
        sa.CheckConstraint(
            "length(project_code) BETWEEN 1 AND 64 AND project_code = upper(project_code)",
            name="ck_project_intakes_project_code",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_project_intakes_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_intakes_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_intakes_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_intakes"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_intakes_tenant_company_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "project_code", name="uq_project_intakes_company_code"
        ),
    )
    op.create_index(
        "ix_project_intakes_tenant_company_updated",
        "project_intakes",
        ["tenant_id", "company_id", "updated_at"],
    )

    op.create_table(
        "project_intake_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_project_intake_versions_version"),
        sa.CheckConstraint(
            "length(content_hash) = 64 AND content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_intake_versions_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_intake_versions_object"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_intake_versions_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_intake_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_intake_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "version",
            name="uq_project_intake_versions_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_project_intake_versions_id",
        ),
    )
    op.create_index(
        "ix_project_intake_versions_history",
        "project_intake_versions",
        ["tenant_id", "company_id", "project_intake_id", "version"],
    )
    op.create_foreign_key(
        "fk_project_intakes_current_version",
        "project_intakes",
        "project_intake_versions",
        ["tenant_id", "company_id", "id", "current_version"],
        ["tenant_id", "company_id", "project_intake_id", "version"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "capacity_assessments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_version", sa.Integer(), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_version", sa.String(length=64), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("metric_calculation_version", sa.String(length=64), nullable=False),
        sa.Column("start_week_date", sa.Date(), nullable=False),
        sa.Column("end_week_date", sa.Date(), nullable=False),
        sa.Column("week_count", sa.Integer(), nullable=False),
        sa.Column("capacity_status", sa.String(length=32), nullable=False),
        sa.Column("business_feasibility_status", sa.String(length=16), nullable=False),
        sa.Column("metric_input_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "capacity_status IN ('feasible', 'feasible_with_conditions', "
            "'not_feasible', 'incomplete')",
            name="ck_capacity_assessments_capacity_status",
        ),
        sa.CheckConstraint(
            "business_feasibility_status = 'incomplete'",
            name="ck_capacity_assessments_business_status",
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM start_week_date) = 1", name="ck_capacity_assessments_monday"
        ),
        sa.CheckConstraint(
            "week_count BETWEEN 1 AND 52", name="ck_capacity_assessments_week_count"
        ),
        sa.CheckConstraint(
            "end_week_date = start_week_date + (week_count * 7 - 1)",
            name="ck_capacity_assessments_period_end",
        ),
        sa.CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64",
            name="ck_capacity_assessments_version_length",
        ),
        sa.CheckConstraint(
            "length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_capacity_assessments_metric_versions",
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_input_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_capacity_assessments_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_capacity_assessments_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_capacity_assessments_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_capacity_assessments_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id", "intake_version"],
            [
                "project_intake_versions.tenant_id",
                "project_intake_versions.company_id",
                "project_intake_versions.project_intake_id",
                "project_intake_versions.version",
            ],
            name="fk_capacity_assessments_intake_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_capacity_assessments_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_capacity_assessments_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_capacity_assessments"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_capacity_assessments_tenant_company_id"
        ),
    )
    op.create_index(
        "ix_capacity_assessments_history",
        "capacity_assessments",
        ["tenant_id", "company_id", "project_intake_id", "created_at"],
    )
    op.create_index(
        "ix_capacity_assessments_metric_run",
        "capacity_assessments",
        ["tenant_id", "company_id", "metric_run_id"],
    )

    for table in ("project_intakes", "project_intake_versions", "capacity_assessments"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_company_context ON {table} "
                "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
                "AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid) "
                "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
                "AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
            )
        )

    op.execute(
        """
        CREATE FUNCTION businessflow_project_intake_update_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.company_id <> OLD.company_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
             OR NEW.current_version <> OLD.current_version + 1 THEN
            RAISE EXCEPTION 'project intake updates must advance exactly one immutable version'
              USING ERRCODE = '55000', CONSTRAINT = 'project_intake_version_guard';
          END IF;
          IF NEW.project_code <> upper(NEW.project_code) THEN
            RAISE EXCEPTION 'project intake code must be canonical uppercase'
              USING ERRCODE = '23514', CONSTRAINT = 'ck_project_intakes_project_code';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_project_intake_update_guard
          BEFORE UPDATE ON project_intakes
          FOR EACH ROW EXECUTE FUNCTION businessflow_project_intake_update_guard();

        CREATE FUNCTION businessflow_m6_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'project intake versions and capacity assessments are immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m6_append_only';
        END;
        $$;
        CREATE TRIGGER trg_project_intake_versions_append_only
          BEFORE UPDATE OR DELETE ON project_intake_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m6_append_only();
        CREATE TRIGGER trg_capacity_assessments_append_only
          BEFORE UPDATE OR DELETE ON capacity_assessments
          FOR EACH ROW EXECUTE FUNCTION businessflow_m6_append_only();
        """
    )
    op.execute(
        """
        GRANT SELECT, INSERT ON project_intakes TO bf_runtime;
        GRANT UPDATE (project_code, current_version, updated_at) ON project_intakes TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON project_intake_versions,
          capacity_assessments FROM bf_runtime;
        GRANT SELECT, INSERT ON project_intake_versions, capacity_assessments TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON project_intakes FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_capacity_assessments_append_only ON capacity_assessments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_intake_versions_append_only ON project_intake_versions"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_project_intake_update_guard ON project_intakes")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m6_append_only()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_project_intake_update_guard()")
    op.drop_table("capacity_assessments")
    op.drop_constraint("fk_project_intakes_current_version", "project_intakes", type_="foreignkey")
    op.drop_table("project_intake_versions")
    op.drop_table("project_intakes")
