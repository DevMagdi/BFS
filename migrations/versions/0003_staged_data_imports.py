"""Add staged CSV/XLSX imports, row provenance, and tenant RLS.

Revision ID: 0003_staged_data_imports
Revises: 0002_company_context
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0003_staged_data_imports"
down_revision = "0002_company_context"
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
        "data_imports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("uploaded_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("confirmed_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("applied_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=True),
        sa.Column("selected_worksheet", sa.String(length=128), nullable=True),
        sa.Column("inspection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "ignored_columns",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("mapping_version", sa.Integer(), server_default="0", nullable=False),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("validation_hash", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="inspected", nullable=False),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("eligible_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("skipped_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("updated_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("acknowledge_warnings", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("allow_partial", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("confirmed_validation_hash", sa.String(length=64), nullable=True),
        sa.Column("staging_expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('inspected', 'mapped', 'validated', 'ready', 'confirmed', 'applied', "
            "'applied_with_warnings', 'failed', 'cancelled', 'expired')",
            name="ck_data_imports_status",
        ),
        sa.CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_data_imports_file_format"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_data_imports_file_size"),
        sa.CheckConstraint("mapping_version >= 0", name="ck_data_imports_mapping_version"),
        sa.CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND warning_rows >= 0 "
            "AND duplicate_rows >= 0 AND eligible_rows >= 0 AND skipped_rows >= 0 "
            "AND created_rows >= 0 AND updated_rows >= 0",
            name="ck_data_imports_counts_nonnegative",
        ),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_hashes",
        ),
        sa.CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_validation_hash",
        ),
        sa.CheckConstraint(
            "confirmed_validation_hash IS NULL OR confirmed_validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_confirmed_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inspection) = 'object'", name="ck_data_imports_inspection_object"
        ),
        sa.CheckConstraint(
            "field_mapping IS NULL OR jsonb_typeof(field_mapping) = 'object'",
            name="ck_data_imports_mapping_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'", name="ck_data_imports_ignored_columns_array"
        ),
        sa.CheckConstraint(
            "validation_report IS NULL OR jsonb_typeof(validation_report) = 'object'",
            name="ck_data_imports_validation_object",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_data_imports_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_data_imports_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_uploader_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "confirmed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_confirmer_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "applied_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_applier_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_imports"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_data_imports_tenant_company_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "idempotency_key_hash", name="uq_data_imports_idempotency"
        ),
    )
    op.create_index(
        "ix_data_imports_tenant_company_created",
        "data_imports",
        ["tenant_id", "company_id", "created_at"],
    )
    op.create_index(
        "ix_data_imports_tenant_company_status",
        "data_imports",
        ["tenant_id", "company_id", "status"],
    )
    op.create_index(
        "ix_data_imports_expiry",
        "data_imports",
        ["tenant_id", "company_id", "status", "staging_expires_at"],
    )

    op.create_table(
        "data_import_rows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("worksheet_name", sa.String(length=128), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("row_status", sa.String(length=16), server_default="pending", nullable=False),
        sa.Column("canonical_type", sa.String(length=32), nullable=True),
        sa.Column("canonical_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("row_number >= 2", name="ck_data_import_rows_number"),
        sa.CheckConstraint("row_hash ~ '^[0-9a-f]{64}$'", name="ck_data_import_rows_hash"),
        sa.CheckConstraint(
            "raw_values IS NULL OR jsonb_typeof(raw_values) = 'array'",
            name="ck_data_import_rows_values_array",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(issues) = 'array'", name="ck_data_import_rows_issues_array"
        ),
        sa.CheckConstraint(
            (
                "row_status IN ('pending', 'valid', 'warning', 'duplicate', 'invalid', "
                "'applied', 'skipped')"
            ),
            name="ck_data_import_rows_status",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_data_import_rows_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_import_rows"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "import_id",
            "worksheet_name",
            "row_number",
            name="uq_data_import_rows_source_row",
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_data_import_rows_tenant_company_id"
        ),
    )
    op.create_index(
        "ix_data_import_rows_parent",
        "data_import_rows",
        ["tenant_id", "company_id", "import_id", "row_number"],
    )

    op.create_table(
        "data_import_record_links",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("canonical_type", sa.String(length=32), nullable=False),
        sa.Column("canonical_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("canonical_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "canonical_type IN ('employee', 'skill', 'employee_skill', 'weekly_capacity', "
            "'project', 'resource_assignment')",
            name="ck_data_import_links_type",
        ),
        sa.CheckConstraint(
            "canonical_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_data_import_links_fingerprint",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_data_import_links_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_row_id"],
            ["data_import_rows.tenant_id", "data_import_rows.company_id", "data_import_rows.id"],
            name="fk_data_import_links_source_row",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_data_import_record_links"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_row_id", name="uq_data_import_link_source_row"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_data_import_links_tenant_company_id"
        ),
    )
    op.create_index(
        "ix_data_import_links_import",
        "data_import_record_links",
        ["tenant_id", "company_id", "import_id", "canonical_type"],
    )
    op.create_index(
        "ix_data_import_links_target",
        "data_import_record_links",
        ["tenant_id", "company_id", "canonical_type", "canonical_id"],
    )

    for table in ("data_imports", "data_import_rows", "data_import_record_links"):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_tenant_context ON {table} "
                "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
                "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
            )
        )

    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON data_imports, data_import_rows TO bf_runtime;
        GRANT SELECT, INSERT ON data_import_record_links TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON data_imports, data_import_rows,
          data_import_record_links FROM bf_runtime;
        REVOKE UPDATE ON data_import_record_links FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.drop_table("data_import_record_links")
    op.drop_table("data_import_rows")
    op.drop_table("data_imports")
