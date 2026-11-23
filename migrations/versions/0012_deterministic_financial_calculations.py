"""Add keyed M14 amount commitments and bounded M15 calculation run evidence.

Revision ID: 0012_financial_calculations
Revises: 0011_finance_source_intake
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0012_financial_calculations"
down_revision = "0011_finance_source_intake"
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

    op.add_column(
        "finance_source_row_evidence",
        sa.Column("amount_commitment_hash", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "finance_source_row_evidence",
        sa.Column("amount_commitment_key_version", sa.String(length=32), nullable=True),
    )
    op.create_check_constraint(
        "ck_finance_source_row_evidence_amount_commitment",
        "finance_source_row_evidence",
        "(amount_commitment_hash IS NULL AND amount_commitment_key_version IS NULL) OR "
        "(amount_commitment_hash IS NOT NULL AND "
        "amount_commitment_hash ~ '^[0-9a-f]{64}$' AND "
        "amount_commitment_key_version IS NOT NULL AND "
        "amount_commitment_key_version = 'app-secret.v1')",
    )

    op.create_table(
        "financial_calculation_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("mapping_hash", sa.String(length=64), nullable=False),
        sa.Column("reconciliation_group_hash", sa.String(length=64), nullable=False),
        sa.Column("formula_id", sa.String(length=128), nullable=False),
        sa.Column("formula_version", sa.String(length=32), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("formula_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("trace_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_financial_calculation_runs_source_key",
        ),
        sa.CheckConstraint(
            "formula_id = 'businessflow.finance.source-group-subtotal' "
            "AND formula_version = '1.0.0'",
            name="ck_financial_calculation_runs_formula",
        ),
        sa.CheckConstraint(
            "length(engine_version) BETWEEN 1 AND 64 AND length(catalog_version) BETWEEN 1 AND 64",
            name="ck_financial_calculation_runs_versions",
        ),
        sa.CheckConstraint(
            "state IN ('calculated', 'incomplete')",
            name="ck_financial_calculation_runs_state",
        ),
        sa.CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND file_hash ~ '^[0-9a-f]{64}$' "
            "AND reconciliation_group_hash ~ '^[0-9a-f]{64}$' "
            "AND formula_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$' AND trace_hash ~ '^[0-9a-f]{64}$' "
            "AND evidence_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_financial_calculation_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array'",
            name="ck_financial_calculation_runs_reason_codes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(evidence_snapshot) = 'object'",
            name="ck_financial_calculation_runs_evidence_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_financial_calculation_runs_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_financial_calculation_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "intake_id"],
            [
                "finance_source_intakes.tenant_id",
                "finance_source_intakes.company_id",
                "finance_source_intakes.id",
            ],
            name="fk_financial_calculation_runs_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_key", "mapping_version", "mapping_hash"],
            [
                "finance_source_mappings.tenant_id",
                "finance_source_mappings.company_id",
                "finance_source_mappings.source_key",
                "finance_source_mappings.mapping_version",
                "finance_source_mappings.mapping_hash",
            ],
            name="fk_financial_calculation_runs_mapping",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_financial_calculation_runs_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_financial_calculation_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financial_calculation_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_financial_calculation_runs_scope_id"
        ),
    )
    op.create_index(
        "ix_financial_calculation_runs_intake_created",
        "financial_calculation_runs",
        ["tenant_id", "company_id", "intake_id", "created_at"],
    )
    op.create_index(
        "ix_financial_calculation_runs_group_created",
        "financial_calculation_runs",
        ["tenant_id", "company_id", "reconciliation_group_hash", "created_at"],
    )
    _add_company_context_rls("financial_calculation_runs")

    op.execute(
        """
        CREATE FUNCTION businessflow_financial_calculation_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'financial calculation evidence is immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_financial_calculation_append_only';
        END;
        $$;
        CREATE TRIGGER trg_financial_calculation_runs_append_only
          BEFORE UPDATE OR DELETE ON financial_calculation_runs
          FOR EACH ROW EXECUTE FUNCTION businessflow_financial_calculation_append_only();
        REVOKE ALL ON financial_calculation_runs FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON financial_calculation_runs TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON financial_calculation_runs FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_financial_calculation_runs_append_only
          ON financial_calculation_runs;
        DROP FUNCTION IF EXISTS businessflow_financial_calculation_append_only();
        """
    )
    op.drop_table("financial_calculation_runs")
    op.drop_constraint(
        "ck_finance_source_row_evidence_amount_commitment",
        "finance_source_row_evidence",
        type_="check",
    )
    op.drop_column("finance_source_row_evidence", "amount_commitment_key_version")
    op.drop_column("finance_source_row_evidence", "amount_commitment_hash")
