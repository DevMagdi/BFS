"""Add governed, retention-limited M18 report exports.

Revision ID: 0027_business_reports
Revises: 0026_forecasting_engine
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0027_business_reports"
down_revision = "0026_forecasting_engine"
branch_labels = None
depends_on = None

_TABLES = (
    "business_report_exports",
    "business_report_artifact_contents",
    "business_report_artifact_revocations",
)


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


def _enable_company_rls(table_name: str) -> None:
    op.execute(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""CREATE POLICY {table_name}_company_context ON {table_name}
        USING (
          tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
          AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid
        )
        WITH CHECK (
          tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
          AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid
        )"""
    )


def upgrade() -> None:
    _require_migrator()
    op.create_table(
        "business_report_exports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("mode", sa.String(length=32), nullable=False),
        sa.Column("manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("manifest_hash", sa.String(length=64), nullable=False),
        sa.Column("artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("artifact_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "title = btrim(title) AND length(title) BETWEEN 1 AND 120",
            name="ck_report_exports_title",
        ),
        sa.CheckConstraint("format IN ('json', 'csv', 'xlsx')", name="ck_report_exports_format"),
        sa.CheckConstraint("mode = 'nonproduction_prototype'", name="ck_report_exports_mode"),
        sa.CheckConstraint("source_count BETWEEN 1 AND 20", name="ck_report_exports_source_count"),
        sa.CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_report_exports_hashes",
        ),
        sa.CheckConstraint(
            "artifact_size_bytes BETWEEN 1 AND 8388608",
            name="ck_report_exports_artifact_size",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_report_exports_expiry"),
        sa.CheckConstraint("jsonb_typeof(manifest) = 'object'", name="ck_report_exports_manifest"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_report_exports_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_report_exports_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_report_exports_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_report_exports"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_report_exports_scope_id"),
    )
    op.create_index(
        "ix_report_exports_company_created",
        "business_report_exports",
        ["tenant_id", "company_id", "created_at", "id"],
    )
    op.create_index(
        "ix_report_exports_company_expiry",
        "business_report_exports",
        ["tenant_id", "company_id", "expires_at"],
    )

    op.create_table(
        "business_report_artifact_contents",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_bytes", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "octet_length(content_bytes) BETWEEN 1 AND 8388608",
            name="ck_report_artifact_contents_size",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "report_id"],
            [
                "business_report_exports.tenant_id",
                "business_report_exports.company_id",
                "business_report_exports.id",
            ],
            name="fk_report_artifact_contents_export",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id", "company_id", "report_id", name="pk_business_report_artifact_contents"
        ),
    )
    op.create_index(
        "ix_report_artifact_contents_report",
        "business_report_artifact_contents",
        ["tenant_id", "company_id", "report_id"],
    )

    op.create_table(
        "business_report_artifact_revocations",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("report_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revoked_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=False),
        sa.Column(
            "revoked_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) BETWEEN 10 AND 500",
            name="ck_report_artifact_revocations_reason",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "report_id"],
            [
                "business_report_exports.tenant_id",
                "business_report_exports.company_id",
                "business_report_exports.id",
            ],
            name="fk_report_artifact_revocations_export",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "revoked_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_report_artifact_revocations_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "company_id",
            "report_id",
            name="pk_business_report_artifact_revocations",
        ),
    )
    op.create_index(
        "ix_report_artifact_revocations_actor",
        "business_report_artifact_revocations",
        ["tenant_id", "revoked_by_user_id", "revoked_at"],
    )

    op.execute(
        """CREATE FUNCTION businessflow_report_evidence_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'business report evidence is append-only';
        END;
        $$"""
    )
    op.execute(
        """CREATE FUNCTION businessflow_report_artifact_delete_guard()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
          report_expiry timestamptz;
          report_revoked boolean;
        BEGIN
          SELECT report.expires_at
          INTO report_expiry
          FROM business_report_exports AS report
          WHERE report.tenant_id = OLD.tenant_id
            AND report.company_id = OLD.company_id
            AND report.id = OLD.report_id;

          IF report_expiry IS NULL THEN
            RAISE EXCEPTION 'business report retention metadata is unavailable';
          END IF;

          SELECT EXISTS (
            SELECT 1
            FROM business_report_artifact_revocations AS revocation
            WHERE revocation.tenant_id = OLD.tenant_id
              AND revocation.company_id = OLD.company_id
              AND revocation.report_id = OLD.report_id
          ) INTO report_revoked;

          IF report_expiry > statement_timestamp() AND NOT report_revoked THEN
            RAISE EXCEPTION 'business report artifact is retained until expiry or revocation';
          END IF;
          RETURN OLD;
        END;
        $$"""
    )
    for table in _TABLES:
        _enable_company_rls(table)

    op.execute(
        """CREATE TRIGGER trg_business_report_exports_append_only
        BEFORE UPDATE OR DELETE ON business_report_exports
        FOR EACH ROW EXECUTE FUNCTION businessflow_report_evidence_append_only()"""
    )
    op.execute(
        """CREATE TRIGGER trg_business_report_artifact_revocations_append_only
        BEFORE UPDATE OR DELETE ON business_report_artifact_revocations
        FOR EACH ROW EXECUTE FUNCTION businessflow_report_evidence_append_only()"""
    )
    op.execute(
        """CREATE TRIGGER trg_business_report_artifact_content_update_guard
        BEFORE UPDATE ON business_report_artifact_contents
        FOR EACH ROW EXECUTE FUNCTION businessflow_report_evidence_append_only()"""
    )
    op.execute(
        """CREATE TRIGGER trg_business_report_artifact_content_delete_guard
        BEFORE DELETE ON business_report_artifact_contents
        FOR EACH ROW EXECUTE FUNCTION businessflow_report_artifact_delete_guard()"""
    )
    op.execute(
        """
        REVOKE ALL ON business_report_exports, business_report_artifact_contents,
          business_report_artifact_revocations FROM PUBLIC;
        GRANT SELECT, INSERT ON business_report_exports,
          business_report_artifact_revocations TO bf_runtime;
        GRANT SELECT, INSERT, DELETE ON business_report_artifact_contents TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON business_report_exports,
          business_report_artifact_revocations FROM bf_runtime;
        REVOKE UPDATE, TRUNCATE ON business_report_artifact_contents FROM bf_runtime;
        """
    )


def downgrade() -> None:
    _require_migrator()
    for table in _TABLES:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM business_report_exports)
             OR EXISTS (SELECT 1 FROM business_report_artifact_contents)
             OR EXISTS (SELECT 1 FROM business_report_artifact_revocations) THEN
            RAISE EXCEPTION
              'cannot downgrade while immutable report export or retention evidence is retained';
          END IF;
        END;
        $$;
        """
    )
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION businessflow_report_artifact_delete_guard()")
    op.execute("DROP FUNCTION businessflow_report_evidence_append_only()")
