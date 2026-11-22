"""Add the synthetic company-scoped monthly position review slice.

Revision ID: 0008_monthly_position_review
Revises: 0007_financial_feasibility
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0008_monthly_position_review"
down_revision = "0007_financial_feasibility"
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
          IF EXISTS (
            SELECT 1 FROM pg_database
            WHERE datname = current_database()
              AND datdba = (SELECT oid FROM pg_roles WHERE rolname = 'bf_runtime')
          ) THEN
            RAISE EXCEPTION 'bf_runtime must not own the application database';
          END IF;
        END;
        $$;
        """
    )

    op.create_table(
        "position_demo_seeds",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("baseline_metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("seed_version", sa.String(length=64), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "current_template_hashes", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("synthetic_label", sa.String(length=120), nullable=False),
        sa.Column("baseline_start_week_date", sa.Date(), nullable=False),
        sa.Column("baseline_end_week_date", sa.Date(), nullable=False),
        sa.Column("current_start_week_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(seed_version) BETWEEN 1 AND 64", name="ck_position_demo_seeds_version"
        ),
        sa.CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_position_demo_seeds_manifest_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(current_template_hashes) = 'object'",
            name="ck_position_demo_seeds_template_hashes",
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM baseline_start_week_date) = 1 "
            "AND EXTRACT(ISODOW FROM current_start_week_date) = 1",
            name="ck_position_demo_seeds_monday_periods",
        ),
        sa.CheckConstraint(
            "baseline_end_week_date = baseline_start_week_date + 27 "
            "AND current_start_week_date = baseline_end_week_date + 1",
            name="ck_position_demo_seeds_adjacent_periods",
        ),
        sa.CheckConstraint(
            "length(synthetic_label) BETWEEN 1 AND 120", name="ck_position_demo_seeds_label"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_position_demo_seeds_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_demo_seeds_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_demo_seeds_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_demo_seeds_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_position_demo_seeds"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_position_demo_seeds_scope_id"
        ),
        sa.UniqueConstraint("tenant_id", "company_id", name="uq_position_demo_seeds_company"),
    )

    op.create_table(
        "position_financial_imports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("worksheet_name", sa.String(length=128), nullable=False),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False),
        sa.Column("validation_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("observations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("file_format = 'csv'", name="ck_position_financial_imports_file_format"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_position_financial_imports_file_size"),
        sa.CheckConstraint(
            "length(schema_version) BETWEEN 1 AND 64", name="ck_position_financial_imports_schema"
        ),
        sa.CheckConstraint(
            "status IN ('invalid', 'validated', 'confirmed')",
            name="ck_position_financial_imports_status",
        ),
        sa.CheckConstraint("row_count > 0", name="ck_position_financial_imports_row_count"),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_financial_imports_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(observations) = 'array'",
            name="ck_position_financial_imports_observations_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validation_report) = 'object'",
            name="ck_position_financial_imports_validation_object",
        ),
        sa.CheckConstraint(
            "(status IN ('invalid', 'validated') AND confirmed_by_user_id IS NULL "
            "AND confirmed_at IS NULL) OR (status = 'confirmed' "
            "AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_position_financial_imports_confirmation_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_position_financial_imports_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_financial_imports_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_financial_imports_uploader_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "confirmed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_financial_imports_confirmer_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_position_financial_imports"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_position_financial_imports_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_position_financial_imports_idempotency",
        ),
    )
    op.create_index(
        "ix_position_financial_imports_company_created",
        "position_financial_imports",
        ["tenant_id", "company_id", "created_at"],
    )

    op.create_table(
        "position_review_briefs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("demo_seed_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("baseline_metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("financial_import_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("start_week_date", sa.Date(), nullable=False),
        sa.Column("end_week_date", sa.Date(), nullable=False),
        sa.Column("calculation_version", sa.String(length=64), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("metric_calculation_version", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM start_week_date) = 1",
            name="ck_position_review_briefs_monday",
        ),
        sa.CheckConstraint(
            "end_week_date = start_week_date + 27", name="ck_position_review_briefs_period"
        ),
        sa.CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64 "
            "AND length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_position_review_briefs_versions",
        ),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_review_briefs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_position_review_briefs_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_position_review_briefs_result_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_position_review_briefs_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_review_briefs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_review_briefs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "demo_seed_id"],
            [
                "position_demo_seeds.tenant_id",
                "position_demo_seeds.company_id",
                "position_demo_seeds.id",
            ],
            name="fk_position_review_briefs_demo_seed",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_review_briefs_baseline_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "current_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_review_briefs_current_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "financial_import_id"],
            [
                "position_financial_imports.tenant_id",
                "position_financial_imports.company_id",
                "position_financial_imports.id",
            ],
            name="fk_position_review_briefs_financial_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_position_review_briefs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_position_review_briefs_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_position_review_briefs_idempotency",
        ),
    )
    op.create_index(
        "ix_position_review_briefs_company_created",
        "position_review_briefs",
        ["tenant_id", "company_id", "created_at"],
    )

    op.create_table(
        "position_review_operational_imports",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("review_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("import_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_hash", sa.String(length=64), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dataset_type IN ('employees', 'projects', 'weekly_capacity', 'resource_assignments')",
            name="ck_position_review_operational_imports_type",
        ),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_review_operational_imports_hashes",
        ),
        sa.CheckConstraint(
            "mapping_version >= 1", name="ck_position_review_operational_imports_mapping_version"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "review_id"],
            [
                "position_review_briefs.tenant_id",
                "position_review_briefs.company_id",
                "position_review_briefs.id",
            ],
            name="fk_position_review_operational_imports_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_position_review_operational_imports_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "dataset_type",
            name="pk_position_review_operational_imports",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "import_id",
            name="uq_position_review_operational_imports_import",
        ),
    )
    op.create_index(
        "ix_position_review_operational_imports_import",
        "position_review_operational_imports",
        ["tenant_id", "company_id", "import_id"],
    )

    op.create_table(
        "position_review_dispositions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("review_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("review_hash", sa.String(length=64), nullable=False),
        sa.Column("disposition", sa.String(length=32), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "review_hash ~ '^[0-9a-f]{64}$'", name="ck_position_review_dispositions_hash"
        ),
        sa.CheckConstraint(
            "disposition IN ('noted', 'follow_up_required', 'deferred')",
            name="ck_position_review_dispositions_value",
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) BETWEEN 1 AND 2000",
            name="ck_position_review_dispositions_rationale",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "review_id"],
            [
                "position_review_briefs.tenant_id",
                "position_review_briefs.company_id",
                "position_review_briefs.id",
            ],
            name="fk_position_review_dispositions_review",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_review_dispositions_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_position_review_dispositions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "actor_user_id",
            name="uq_position_review_dispositions_actor_review",
        ),
    )
    op.create_index(
        "ix_position_review_dispositions_review",
        "position_review_dispositions",
        ["tenant_id", "company_id", "review_id", "created_at"],
    )

    for table in (
        "position_demo_seeds",
        "position_financial_imports",
        "position_review_briefs",
        "position_review_operational_imports",
        "position_review_dispositions",
    ):
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
        CREATE FUNCTION businessflow_m10_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'M10 snapshots, source links, and human dispositions are immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m10_append_only';
        END;
        $$;

        CREATE FUNCTION businessflow_m10_financial_import_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          expected_hash varchar(64);
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'M10 financial source snapshots are retained and immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m10_financial_import_guard';
          END IF;
          IF TG_OP = 'INSERT' THEN
            SELECT current_template_hashes->>'financial_export' INTO expected_hash
              FROM position_demo_seeds
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id;
            IF NOT FOUND OR NEW.file_hash IS DISTINCT FROM expected_hash THEN
              RAISE EXCEPTION
                'M10 financial import must match its immutable synthetic template hash'
                USING ERRCODE = '23514',
                  CONSTRAINT = 'position_financial_import_template_hash_guard';
            END IF;
            RETURN NEW;
          END IF;
          IF OLD.status <> 'validated' OR NEW.status <> 'confirmed'
             OR NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.uploaded_by_user_id IS DISTINCT FROM OLD.uploaded_by_user_id
             OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
             OR NEW.file_name IS DISTINCT FROM OLD.file_name
             OR NEW.file_format IS DISTINCT FROM OLD.file_format
             OR NEW.file_hash IS DISTINCT FROM OLD.file_hash
             OR NEW.file_size_bytes IS DISTINCT FROM OLD.file_size_bytes
             OR NEW.worksheet_name IS DISTINCT FROM OLD.worksheet_name
             OR NEW.schema_version IS DISTINCT FROM OLD.schema_version
             OR NEW.row_count IS DISTINCT FROM OLD.row_count
             OR NEW.validation_hash IS DISTINCT FROM OLD.validation_hash
             OR NEW.validation_report IS DISTINCT FROM OLD.validation_report
             OR NEW.observations IS DISTINCT FROM OLD.observations
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.confirmed_by_user_id IS NULL OR NEW.confirmed_at IS NULL THEN
            RAISE EXCEPTION 'M10 financial imports permit only validated-to-confirmed transition'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m10_financial_import_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m10_demo_seed_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          baseline_row metric_runs%ROWTYPE;
        BEGIN
          SELECT * INTO baseline_row FROM metric_runs
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.baseline_metric_run_id;
          IF NOT FOUND OR baseline_row.week_count <> 4
             OR baseline_row.start_week_date <> NEW.baseline_start_week_date
             OR baseline_row.end_week_date <> NEW.baseline_end_week_date
             OR baseline_row.reliability_status <> 'complete'
             OR baseline_row.metric_catalog_version <> 'businessflow.metrics.v1'
             OR baseline_row.calculation_version <> 'businessflow.capacity-weekly.v1' THEN
            RAISE EXCEPTION 'M10 seed must bind a complete four-week M5 v1 baseline'
              USING ERRCODE = '23514', CONSTRAINT = 'position_demo_seed_baseline_guard';
          END IF;
          IF NEW.synthetic_label <> 'SYNTHETIC DEMO DATA — NOT CUSTOMER DATA' THEN
            RAISE EXCEPTION 'M10 seed must use the explicit synthetic-only label'
              USING ERRCODE = '23514', CONSTRAINT = 'position_demo_seed_synthetic_label_guard';
          END IF;
          IF NEW.current_template_hashes->>'employees' IS NULL
             OR NEW.current_template_hashes->>'employees' !~ '^[0-9a-f]{64}$'
             OR NEW.current_template_hashes->>'projects' IS NULL
             OR NEW.current_template_hashes->>'projects' !~ '^[0-9a-f]{64}$'
             OR NEW.current_template_hashes->>'weekly_capacity' IS NULL
             OR NEW.current_template_hashes->>'weekly_capacity' !~ '^[0-9a-f]{64}$'
             OR NEW.current_template_hashes->>'resource_assignments' IS NULL
             OR NEW.current_template_hashes->>'resource_assignments' !~ '^[0-9a-f]{64}$'
             OR NEW.current_template_hashes->>'financial_export' IS NULL
             OR NEW.current_template_hashes->>'financial_export' !~ '^[0-9a-f]{64}$' THEN
            RAISE EXCEPTION 'M10 seed must bind every exact synthetic template hash'
              USING ERRCODE = '23514', CONSTRAINT = 'position_demo_seed_template_hashes_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m10_review_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          seed_row position_demo_seeds%ROWTYPE;
          baseline_row metric_runs%ROWTYPE;
          current_row metric_runs%ROWTYPE;
          finance_status varchar(16);
        BEGIN
          -- Seed rows are append-only and bf_runtime cannot update/delete them, so an ordinary
          -- SELECT is sufficient and does not require row-locking privileges on the seed table.
          SELECT * INTO seed_row FROM position_demo_seeds
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.demo_seed_id;
          IF NOT FOUND OR seed_row.baseline_metric_run_id <> NEW.baseline_metric_run_id
             OR seed_row.current_start_week_date <> NEW.start_week_date THEN
            RAISE EXCEPTION 'M10 brief must use its Company synthetic baseline and period'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_demo_seed_binding_guard';
          END IF;

          SELECT * INTO baseline_row FROM metric_runs
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.baseline_metric_run_id;
          SELECT * INTO current_row FROM metric_runs
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.current_metric_run_id;
          IF NOT FOUND OR baseline_row.week_count <> 4 OR current_row.week_count <> 4
             OR baseline_row.start_week_date <> seed_row.baseline_start_week_date
             OR baseline_row.end_week_date <> seed_row.baseline_end_week_date
             OR current_row.start_week_date <> NEW.start_week_date
             OR current_row.end_week_date <> NEW.end_week_date
             OR baseline_row.metric_catalog_version <> NEW.metric_catalog_version
             OR current_row.metric_catalog_version <> NEW.metric_catalog_version
             OR baseline_row.calculation_version <> NEW.metric_calculation_version
             OR current_row.calculation_version <> NEW.metric_calculation_version THEN
            RAISE EXCEPTION 'M10 brief metric versions or four-week scope do not match M5'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_metric_binding_guard';
          END IF;
          IF NEW.input_snapshot->'scope'->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
             OR NEW.input_snapshot->'scope'->>'company_id' IS DISTINCT FROM NEW.company_id::text
             OR NEW.result_snapshot->'scope'->>'tenant_id' IS DISTINCT FROM NEW.tenant_id::text
             OR NEW.result_snapshot->'scope'->>'company_id' IS DISTINCT FROM NEW.company_id::text
             OR NEW.input_snapshot->'baseline_metric_run'->>'id'
                IS DISTINCT FROM NEW.baseline_metric_run_id::text
             OR NEW.input_snapshot->'baseline_metric_run'->>'input_hash'
                IS DISTINCT FROM baseline_row.input_hash
             OR NEW.input_snapshot->'baseline_metric_run'->>'result_hash'
                IS DISTINCT FROM baseline_row.result_hash
             OR NEW.input_snapshot->'current_metric_run'->>'id'
                IS DISTINCT FROM NEW.current_metric_run_id::text
             OR NEW.input_snapshot->'current_metric_run'->>'input_hash'
                IS DISTINCT FROM current_row.input_hash
             OR NEW.input_snapshot->'current_metric_run'->>'result_hash'
                IS DISTINCT FROM current_row.result_hash
             OR NEW.result_snapshot->>'calculation_version'
                IS DISTINCT FROM NEW.calculation_version THEN
            RAISE EXCEPTION 'M10 brief snapshot does not identify its exact M5 evidence'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_snapshot_binding_guard';
          END IF;

          IF NEW.financial_import_id IS NOT NULL THEN
            SELECT status INTO finance_status FROM position_financial_imports
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                AND id = NEW.financial_import_id;
            IF NOT FOUND OR finance_status <> 'confirmed' THEN
              RAISE EXCEPTION 'M10 brief financial source must be explicitly confirmed'
                USING ERRCODE = '23514', CONSTRAINT = 'position_review_finance_binding_guard';
            END IF;
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m10_operational_import_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          actual_dataset varchar(32);
          actual_status varchar(32);
          actual_file_hash varchar(64);
          actual_validation_hash varchar(64);
          actual_mapping_version integer;
          source_created_at timestamptz;
          seed_created_at timestamptz;
          expected_template_hash varchar(64);
        BEGIN
          SELECT i.dataset_type, i.status, i.file_hash, i.validation_hash, i.mapping_version,
                 i.created_at, s.created_at,
                 s.current_template_hashes->>NEW.dataset_type
            INTO actual_dataset, actual_status, actual_file_hash, actual_validation_hash,
                 actual_mapping_version, source_created_at, seed_created_at,
                 expected_template_hash
            FROM data_imports AS i
            JOIN position_review_briefs AS b
              ON b.tenant_id = NEW.tenant_id AND b.company_id = NEW.company_id
             AND b.id = NEW.review_id
            JOIN position_demo_seeds AS s
              ON s.tenant_id = b.tenant_id AND s.company_id = b.company_id
             AND s.id = b.demo_seed_id
            WHERE i.tenant_id = NEW.tenant_id AND i.company_id = NEW.company_id
              AND i.id = NEW.import_id;
          IF NOT FOUND OR actual_dataset <> NEW.dataset_type
             OR actual_status NOT IN ('applied', 'applied_with_warnings')
             OR actual_file_hash <> NEW.file_hash
             OR actual_file_hash <> expected_template_hash
             OR actual_validation_hash <> NEW.validation_hash
             OR actual_mapping_version <> NEW.mapping_version
             OR source_created_at <= seed_created_at THEN
            RAISE EXCEPTION 'M10 operational source link must bind to an applied M4 import'
              USING ERRCODE = '23514',
                CONSTRAINT = 'position_review_operational_import_binding_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m10_review_source_set_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          source_count integer;
        BEGIN
          SELECT count(*) INTO source_count FROM position_review_operational_imports
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND review_id = NEW.id;
          IF source_count <> 4 THEN
            RAISE EXCEPTION 'M10 brief requires exactly four distinct operational source imports'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_operational_source_set_guard';
          END IF;
          RETURN NULL;
        END;
        $$;

        CREATE FUNCTION businessflow_m10_disposition_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          saved_hash varchar(64);
        BEGIN
          SELECT result_hash INTO saved_hash FROM position_review_briefs
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.review_id;
          IF NOT FOUND OR saved_hash <> NEW.review_hash THEN
            RAISE EXCEPTION 'M10 disposition must reference the exact immutable brief hash'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_disposition_binding_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE TRIGGER trg_position_demo_seeds_insert_guard
          BEFORE INSERT ON position_demo_seeds
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_demo_seed_insert_guard();
        CREATE TRIGGER trg_position_demo_seeds_append_only
          BEFORE UPDATE OR DELETE ON position_demo_seeds
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_append_only();
        CREATE TRIGGER trg_position_financial_imports_guard
          BEFORE INSERT OR UPDATE OR DELETE ON position_financial_imports
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_financial_import_guard();
        CREATE TRIGGER trg_position_review_briefs_insert_guard
          BEFORE INSERT ON position_review_briefs
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_review_insert_guard();
        CREATE CONSTRAINT TRIGGER trg_position_review_briefs_source_set_guard
          AFTER INSERT ON position_review_briefs
          DEFERRABLE INITIALLY DEFERRED
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_review_source_set_guard();
        CREATE TRIGGER trg_position_review_briefs_append_only
          BEFORE UPDATE OR DELETE ON position_review_briefs
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_append_only();
        CREATE TRIGGER trg_position_review_operational_imports_insert_guard
          BEFORE INSERT ON position_review_operational_imports
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_operational_import_insert_guard();
        CREATE TRIGGER trg_position_review_operational_imports_append_only
          BEFORE UPDATE OR DELETE ON position_review_operational_imports
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_append_only();
        CREATE TRIGGER trg_position_review_dispositions_insert_guard
          BEFORE INSERT ON position_review_dispositions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_disposition_insert_guard();
        CREATE TRIGGER trg_position_review_dispositions_append_only
          BEFORE UPDATE OR DELETE ON position_review_dispositions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m10_append_only();
        """
    )
    op.execute(
        """
        REVOKE ALL ON position_demo_seeds, position_financial_imports,
          position_review_briefs, position_review_operational_imports,
          position_review_dispositions FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON position_demo_seeds, position_review_briefs,
          position_review_operational_imports, position_review_dispositions TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON position_demo_seeds, position_review_briefs,
          position_review_operational_imports, position_review_dispositions FROM bf_runtime;
        GRANT SELECT, INSERT ON position_financial_imports TO bf_runtime;
        GRANT UPDATE (status, confirmed_by_user_id, confirmed_at)
          ON position_financial_imports TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON position_financial_imports FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_briefs_source_set_guard "
        "ON position_review_briefs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_dispositions_append_only "
        "ON position_review_dispositions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_dispositions_insert_guard "
        "ON position_review_dispositions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_operational_imports_append_only "
        "ON position_review_operational_imports"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_operational_imports_insert_guard "
        "ON position_review_operational_imports"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_briefs_append_only ON position_review_briefs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_briefs_insert_guard ON position_review_briefs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_financial_imports_guard ON position_financial_imports"
    )
    op.execute("DROP TRIGGER IF EXISTS trg_position_demo_seeds_append_only ON position_demo_seeds")
    op.execute("DROP TRIGGER IF EXISTS trg_position_demo_seeds_insert_guard ON position_demo_seeds")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_disposition_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_review_source_set_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_operational_import_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_review_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_demo_seed_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_financial_import_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m10_append_only()")
    op.drop_table("position_review_dispositions")
    op.drop_table("position_review_operational_imports")
    op.drop_table("position_review_briefs")
    op.drop_table("position_financial_imports")
    op.drop_table("position_demo_seeds")
