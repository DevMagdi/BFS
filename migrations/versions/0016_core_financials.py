"""Add the default-disabled, versioned Core Financials management-reporting slice.

Revision ID: 0016_core_financials
Revises: 0015_limit_runtime_users_update
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0016_core_financials"
down_revision = "0015_limit_runtime_users_update"
branch_labels = None
depends_on = None


def _require_runtime_role() -> None:
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


def _add_company_context_rls(table_name: str) -> None:
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
    _require_runtime_role()

    op.create_table(
        "core_financial_policies",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "authority",
            sa.String(length=40),
            server_default="illustrative_not_authoritative",
            nullable=False,
        ),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_core_financial_policies_version"),
        sa.CheckConstraint(
            "authority = 'illustrative_not_authoritative'",
            name="ck_core_financial_policies_authority",
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_core_financial_policies_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_core_financial_policies_definition"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_core_financial_policies_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_core_financial_policies_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_core_financial_policies_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_policies"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_policies_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "policy_key",
            "version",
            name="uq_core_financial_policy_version",
        ),
    )
    op.create_index(
        "ix_core_financial_policies_scope_created",
        "core_financial_policies",
        ["tenant_id", "company_id", "created_at"],
    )

    op.create_table(
        "core_financial_mappings",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ignored_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_core_financial_mappings_version"),
        sa.CheckConstraint(
            "source_type IN ('general_ledger', 'bank_activity')",
            name="ck_core_financial_mappings_source_type",
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_core_financial_mappings_source_key",
        ),
        sa.CheckConstraint(
            "mapping_hash ~ '^[0-9a-f]{64}$'", name="ck_core_financial_mappings_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'",
            name="ck_core_financial_mappings_field_mapping",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'",
            name="ck_core_financial_mappings_ignored_columns",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_core_financial_mappings_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_core_financial_mappings_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_core_financial_mappings_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_mappings"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_mappings_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "source_type",
            "version",
            name="uq_core_financial_mapping_version",
        ),
    )
    op.create_index(
        "ix_core_financial_mappings_scope_source",
        "core_financial_mappings",
        ["tenant_id", "company_id", "source_key", "source_type", "version"],
    )

    op.create_table(
        "core_financial_imports",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=24), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("mapping_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("period_completeness", sa.String(length=16), nullable=False),
        sa.Column("quality_status", sa.String(length=16), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("accepted_rows", sa.Integer(), nullable=False),
        sa.Column("rejected_rows", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_type IN ('general_ledger', 'bank_activity')",
            name="ck_core_financial_imports_source_type",
        ),
        sa.CheckConstraint("file_format = 'csv'", name="ck_core_financial_imports_file_format"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_core_financial_imports_file_size"),
        sa.CheckConstraint("period_end >= period_start", name="ck_core_financial_imports_period"),
        sa.CheckConstraint(
            "period_completeness IN ('complete', 'partial', 'unknown')",
            name="ck_core_financial_imports_completeness",
        ),
        sa.CheckConstraint(
            "quality_status IN ('passed', 'incomplete')",
            name="ck_core_financial_imports_quality",
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND accepted_rows >= 0 AND rejected_rows >= 0 "
            "AND duplicate_rows >= 0 AND "
            "total_rows = accepted_rows + rejected_rows + duplicate_rows",
            name="ck_core_financial_imports_counts",
        ),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND policy_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_imports_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_core_financial_imports_input_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_core_financial_imports_result_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_core_financial_imports_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_core_financial_imports_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_core_financial_imports_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "mapping_id"],
            [
                "core_financial_mappings.tenant_id",
                "core_financial_mappings.company_id",
                "core_financial_mappings.id",
            ],
            name="fk_core_financial_imports_mapping",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_id"],
            [
                "core_financial_policies.tenant_id",
                "core_financial_policies.company_id",
                "core_financial_policies.id",
            ],
            name="fk_core_financial_imports_policy",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_imports"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_imports_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_core_financial_imports_idempotency",
        ),
    )
    op.create_index(
        "ix_core_financial_imports_scope_created",
        "core_financial_imports",
        ["tenant_id", "company_id", "created_at"],
    )
    op.create_index(
        "ix_core_financial_imports_source_created",
        "core_financial_imports",
        ["tenant_id", "company_id", "source_key", "source_type", "created_at"],
    )

    op.create_table(
        "core_financial_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_number", sa.Integer(), nullable=False),
        sa.Column("source_record_id", sa.String(length=256), nullable=True),
        sa.Column("transaction_date", sa.Date(), nullable=True),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("account_code", sa.String(length=128), nullable=True),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("event_type", sa.String(length=16), nullable=True),
        sa.Column("signed_amount", sa.String(length=64), nullable=True),
        sa.Column("reverses_record_id", sa.String(length=256), nullable=True),
        sa.Column("canonical_record_key", sa.String(length=64), nullable=True),
        sa.Column("source_row_hash", sa.String(length=64), nullable=False),
        sa.Column("mapped_source_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_status", sa.String(length=16), nullable=False),
        sa.Column(
            "issues",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("source_row_number >= 2", name="ck_core_financial_records_row_number"),
        sa.CheckConstraint(
            "row_status IN ('accepted', 'rejected', 'duplicate')",
            name="ck_core_financial_records_status",
        ),
        sa.CheckConstraint(
            "category IS NULL OR category IN ('revenue', 'revenue_reduction', 'cost_of_sales', "
            "'operating_expense', 'non_operating_income', 'non_operating_expense', 'tax_expense')",
            name="ck_core_financial_records_category",
        ),
        sa.CheckConstraint(
            "event_type IS NULL OR event_type IN ('normal', 'refund', 'reversal', 'adjustment')",
            name="ck_core_financial_records_event_type",
        ),
        sa.CheckConstraint(
            "row_status <> 'accepted' OR (source_record_id IS NOT NULL AND "
            "transaction_date IS NOT NULL AND currency_code IS NOT NULL AND "
            "account_code IS NOT NULL AND event_type IS NOT NULL AND signed_amount IS NOT NULL)",
            name="ck_core_financial_records_accepted_fields",
        ),
        sa.CheckConstraint(
            "source_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_records_row_hash",
        ),
        sa.CheckConstraint(
            "canonical_record_key IS NULL OR canonical_record_key ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_records_identity_hash",
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_core_financial_records_currency",
        ),
        sa.CheckConstraint(
            "signed_amount IS NULL OR signed_amount ~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$'",
            name="ck_core_financial_records_signed_amount",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(mapped_source_values) = 'object' AND jsonb_typeof(issues) = 'array'",
            name="ck_core_financial_records_json",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            name="fk_core_financial_records_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_records"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_core_financial_records_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "import_id",
            "source_row_number",
            name="uq_core_financial_record_row",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "canonical_record_key",
            name="uq_core_financial_record_source_identity",
        ),
    )
    op.create_index(
        "ix_core_financial_records_import_row",
        "core_financial_records",
        ["tenant_id", "company_id", "import_id", "source_row_number"],
    )
    op.create_index(
        "ix_core_financial_records_period_currency",
        "core_financial_records",
        ["tenant_id", "company_id", "transaction_date", "currency_code"],
    )

    op.create_table(
        "core_financial_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("policy_hash", sa.String(length=64), nullable=False),
        sa.Column("ledger_import_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("bank_import_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("trace_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("period_end >= period_start", name="ck_core_financial_runs_period"),
        sa.CheckConstraint(
            "ledger_import_id IS NOT NULL OR bank_import_id IS NOT NULL",
            name="ck_core_financial_runs_source_required",
        ),
        sa.CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="ck_core_financial_runs_currency"),
        sa.CheckConstraint(
            "state IN ('calculated', 'incomplete')", name="ck_core_financial_runs_state"
        ),
        sa.CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND trace_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_core_financial_runs_json",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_core_financial_runs_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_core_financial_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_core_financial_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_id"],
            [
                "core_financial_policies.tenant_id",
                "core_financial_policies.company_id",
                "core_financial_policies.id",
            ],
            name="fk_core_financial_runs_policy",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ledger_import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            name="fk_core_financial_runs_ledger_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "bank_import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            name="fk_core_financial_runs_bank_import",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_runs_scope_id"
        ),
    )
    op.create_index(
        "ix_core_financial_runs_scope_created",
        "core_financial_runs",
        ["tenant_id", "company_id", "created_at"],
    )

    op.create_table(
        "core_financial_run_evidence",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("record_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("formula_coefficient", sa.Integer(), nullable=False),
        sa.Column("contribution_amount", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "metric IN ('revenue', 'expenses', 'gross_profit', 'operating_profit', "
            "'net_profit', 'cash_flow')",
            name="ck_core_financial_run_evidence_metric",
        ),
        sa.CheckConstraint(
            "formula_coefficient IN (-1, 1)",
            name="ck_core_financial_run_evidence_coefficient",
        ),
        sa.CheckConstraint(
            "contribution_amount ~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$'",
            name="ck_core_financial_run_evidence_amount",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "run_id"],
            [
                "core_financial_runs.tenant_id",
                "core_financial_runs.company_id",
                "core_financial_runs.id",
            ],
            name="fk_core_financial_run_evidence_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "record_id"],
            [
                "core_financial_records.tenant_id",
                "core_financial_records.company_id",
                "core_financial_records.id",
            ],
            name="fk_core_financial_run_evidence_record",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_core_financial_run_evidence"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "run_id",
            "metric",
            "record_id",
            name="uq_core_financial_run_evidence_record",
        ),
    )
    op.create_index(
        "ix_core_financial_run_evidence_metric",
        "core_financial_run_evidence",
        ["tenant_id", "company_id", "run_id", "metric", "id"],
    )

    for table_name in (
        "core_financial_policies",
        "core_financial_mappings",
        "core_financial_imports",
        "core_financial_records",
        "core_financial_runs",
        "core_financial_run_evidence",
    ):
        _add_company_context_rls(table_name)

    op.execute(
        """
        CREATE FUNCTION businessflow_core_financial_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Core Financials evidence is immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_core_financial_append_only';
        END;
        $$;
        CREATE TRIGGER trg_core_financial_policies_append_only
          BEFORE UPDATE OR DELETE ON core_financial_policies
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        CREATE TRIGGER trg_core_financial_mappings_append_only
          BEFORE UPDATE OR DELETE ON core_financial_mappings
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        CREATE TRIGGER trg_core_financial_imports_append_only
          BEFORE UPDATE OR DELETE ON core_financial_imports
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        CREATE TRIGGER trg_core_financial_records_append_only
          BEFORE UPDATE OR DELETE ON core_financial_records
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        CREATE TRIGGER trg_core_financial_runs_append_only
          BEFORE UPDATE OR DELETE ON core_financial_runs
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        CREATE TRIGGER trg_core_financial_run_evidence_append_only
          BEFORE UPDATE OR DELETE ON core_financial_run_evidence
          FOR EACH ROW EXECUTE FUNCTION businessflow_core_financial_append_only();
        REVOKE ALL ON core_financial_policies, core_financial_mappings, core_financial_imports,
          core_financial_records, core_financial_runs, core_financial_run_evidence
          FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON core_financial_policies, core_financial_mappings,
          core_financial_imports, core_financial_records, core_financial_runs,
          core_financial_run_evidence TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON core_financial_policies, core_financial_mappings,
          core_financial_imports, core_financial_records, core_financial_runs,
          core_financial_run_evidence FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_core_financial_run_evidence_append_only
          ON core_financial_run_evidence;
        DROP TRIGGER IF EXISTS trg_core_financial_runs_append_only ON core_financial_runs;
        DROP TRIGGER IF EXISTS trg_core_financial_records_append_only ON core_financial_records;
        DROP TRIGGER IF EXISTS trg_core_financial_imports_append_only ON core_financial_imports;
        DROP TRIGGER IF EXISTS trg_core_financial_mappings_append_only ON core_financial_mappings;
        DROP TRIGGER IF EXISTS trg_core_financial_policies_append_only ON core_financial_policies;
        DROP FUNCTION IF EXISTS businessflow_core_financial_append_only();
        """
    )
    op.drop_table("core_financial_run_evidence")
    op.drop_table("core_financial_runs")
    op.drop_table("core_financial_records")
    op.drop_table("core_financial_imports")
    op.drop_table("core_financial_mappings")
    op.drop_table("core_financial_policies")
