"""Add append-only M16 direct-cash forecast run evidence.

Revision ID: 0013_direct_cash_forecast
Revises: 0012_financial_calculations
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0013_direct_cash_forecast"
down_revision = "0012_financial_calculations"
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
        "direct_cash_forecast_runs",
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
        sa.Column("formula_definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("formula_hash", sa.String(length=64), nullable=False),
        sa.Column("engine_version", sa.String(length=64), nullable=False),
        sa.Column("catalog_version", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column(
            "reason_codes",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("horizon_weeks", sa.Integer(), nullable=False),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("company_timezone", sa.String(length=64), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("trace_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_direct_cash_forecast_runs_source_key",
        ),
        sa.CheckConstraint(
            "formula_id = 'businessflow.finance.direct-cash-forecast' "
            "AND formula_version = '1.0.0'",
            name="ck_direct_cash_forecast_runs_formula",
        ),
        sa.CheckConstraint(
            "length(engine_version) BETWEEN 1 AND 64 AND length(catalog_version) BETWEEN 1 AND 64",
            name="ck_direct_cash_forecast_runs_versions",
        ),
        sa.CheckConstraint(
            "state IN ('calculated', 'incomplete')",
            name="ck_direct_cash_forecast_runs_state",
        ),
        sa.CheckConstraint("horizon_weeks = 13", name="ck_direct_cash_forecast_runs_horizon"),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_direct_cash_forecast_runs_currency",
        ),
        sa.CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND file_hash ~ '^[0-9a-f]{64}$' "
            "AND reconciliation_group_hash ~ '^[0-9a-f]{64}$' "
            "AND formula_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$' AND trace_hash ~ '^[0-9a-f]{64}$' "
            "AND evidence_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_direct_cash_forecast_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array'",
            name="ck_direct_cash_forecast_runs_reason_codes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(formula_definition) = 'object'",
            name="ck_direct_cash_forecast_runs_formula_definition",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_direct_cash_forecast_runs_input_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_direct_cash_forecast_runs_result_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_direct_cash_forecast_runs_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_direct_cash_forecast_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "intake_id"],
            [
                "finance_source_intakes.tenant_id",
                "finance_source_intakes.company_id",
                "finance_source_intakes.id",
            ],
            name="fk_direct_cash_forecast_runs_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            [
                "tenant_id",
                "company_id",
                "source_key",
                "mapping_version",
                "mapping_hash",
            ],
            [
                "finance_source_mappings.tenant_id",
                "finance_source_mappings.company_id",
                "finance_source_mappings.source_key",
                "finance_source_mappings.mapping_version",
                "finance_source_mappings.mapping_hash",
            ],
            name="fk_direct_cash_forecast_runs_mapping",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_direct_cash_forecast_runs_contract",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_direct_cash_forecast_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_direct_cash_forecast_runs"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_direct_cash_forecast_runs_scope_id"
        ),
    )
    op.create_index(
        "ix_direct_cash_forecast_runs_intake_created",
        "direct_cash_forecast_runs",
        ["tenant_id", "company_id", "intake_id", "created_at"],
    )
    op.create_index(
        "ix_direct_cash_forecast_runs_as_of_created",
        "direct_cash_forecast_runs",
        ["tenant_id", "company_id", "as_of_date", "created_at"],
    )
    _add_company_context_rls("direct_cash_forecast_runs")

    op.execute(
        """
        CREATE FUNCTION businessflow_direct_cash_forecast_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'direct cash forecast evidence is immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_direct_cash_forecast_append_only';
        END;
        $$;
        CREATE TRIGGER trg_direct_cash_forecast_runs_append_only
          BEFORE UPDATE OR DELETE ON direct_cash_forecast_runs
          FOR EACH ROW EXECUTE FUNCTION businessflow_direct_cash_forecast_append_only();
        REVOKE ALL ON direct_cash_forecast_runs FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON direct_cash_forecast_runs TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON direct_cash_forecast_runs FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DROP TRIGGER IF EXISTS trg_direct_cash_forecast_runs_append_only
          ON direct_cash_forecast_runs;
        DROP FUNCTION IF EXISTS businessflow_direct_cash_forecast_append_only();
        """
    )
    op.drop_table("direct_cash_forecast_runs")
