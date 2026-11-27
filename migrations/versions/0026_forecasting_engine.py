"""Add immutable M17 datasets, models, evaluations, forecasts and human promotion records.

Revision ID: 0026_forecasting_engine
Revises: 0025_domain_intelligence
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0026_forecasting_engine"
down_revision = "0025_domain_intelligence"
branch_labels = None
depends_on = None

_TABLES = (
    "forecast_dataset_versions",
    "forecast_model_versions",
    "forecast_evaluation_protocol_versions",
    "forecast_evaluations",
    "forecast_evaluation_model_versions",
    "forecast_runs",
    "forecast_promotion_decisions",
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
        "forecast_dataset_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dataset_key", sa.String(length=64), nullable=False),
        sa.Column("dataset_version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("data_origin", sa.String(length=24), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("target_label", sa.String(length=160), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=True),
        sa.Column("frequency", sa.String(length=8), nullable=False),
        sa.Column("start_period", sa.Date(), nullable=False),
        sa.Column("end_period", sa.Date(), nullable=False),
        sa.Column("observation_count", sa.Integer(), nullable=False),
        sa.Column("series_count", sa.Integer(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dataset_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_dataset_versions_key",
        ),
        sa.CheckConstraint(
            "dataset_version >= 1 AND ((dataset_version = 1 AND previous_version IS NULL) OR "
            "(dataset_version > 1 AND previous_version = dataset_version - 1))",
            name="ck_forecast_dataset_versions_order",
        ),
        sa.CheckConstraint(
            "data_origin IN ('synthetic_fixture', 'caller_assertion')",
            name="ck_forecast_dataset_versions_origin",
        ),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_forecast_dataset_versions_frequency",
        ),
        sa.CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_forecast_dataset_versions_currency",
        ),
        sa.CheckConstraint(
            "end_period >= start_period AND observation_count BETWEEN 1 AND 20000 "
            "AND series_count BETWEEN 1 AND 20000",
            name="ck_forecast_dataset_versions_counts",
        ),
        sa.CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_dataset_versions_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_forecast_dataset_versions_snapshot",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_dataset_versions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_dataset_versions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_dataset_versions_requester",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "dataset_key", "previous_version"],
            [
                "forecast_dataset_versions.tenant_id",
                "forecast_dataset_versions.company_id",
                "forecast_dataset_versions.dataset_key",
                "forecast_dataset_versions.dataset_version",
            ],
            name="fk_forecast_dataset_versions_previous",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_dataset_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_dataset_versions_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "dataset_key",
            "dataset_version",
            name="uq_forecast_dataset_versions_key_version",
        ),
    )
    op.create_index(
        "ix_forecast_dataset_versions_history",
        "forecast_dataset_versions",
        ["tenant_id", "company_id", "dataset_key", "dataset_version"],
    )

    op.create_table(
        "forecast_model_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_key", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("model_kind", sa.String(length=32), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("model_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "model_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_model_versions_key",
        ),
        sa.CheckConstraint(
            "model_version >= 1 AND ((model_version = 1 AND previous_version IS NULL) OR "
            "(model_version > 1 AND previous_version = model_version - 1))",
            name="ck_forecast_model_versions_order",
        ),
        sa.CheckConstraint(
            "model_kind IN ('naive_last_value', 'seasonal_naive', 'moving_average')",
            name="ck_forecast_model_versions_kind",
        ),
        sa.CheckConstraint(
            "model_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_model_versions_hash",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_forecast_model_versions_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_model_versions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_model_versions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_model_versions_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_key", "previous_version"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.model_key",
                "forecast_model_versions.model_version",
            ],
            name="fk_forecast_model_versions_previous",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_model_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_model_versions_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "model_key",
            "model_version",
            name="uq_forecast_model_versions_key_version",
        ),
    )
    op.create_index(
        "ix_forecast_model_versions_history",
        "forecast_model_versions",
        ["tenant_id", "company_id", "model_key", "model_version"],
    )

    op.create_table(
        "forecast_evaluation_protocol_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("protocol_key", sa.String(length=64), nullable=False),
        sa.Column("protocol_version", sa.Integer(), nullable=False),
        sa.Column("previous_version", sa.Integer(), nullable=True),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("target_key", sa.String(length=64), nullable=False),
        sa.Column("frequency", sa.String(length=8), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("protocol_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "protocol_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_evaluation_protocol_versions_key",
        ),
        sa.CheckConstraint(
            "protocol_version >= 1 AND ((protocol_version = 1 AND previous_version IS NULL) OR "
            "(protocol_version > 1 AND previous_version = protocol_version - 1))",
            name="ck_forecast_evaluation_protocol_versions_order",
        ),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_forecast_evaluation_protocol_versions_frequency",
        ),
        sa.CheckConstraint(
            "length(target_key) BETWEEN 2 AND 64 AND protocol_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_evaluation_protocol_versions_identity",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_forecast_evaluation_protocol_versions_definition",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_evaluation_protocol_versions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluation_protocol_versions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_evaluation_protocol_versions_creator",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "protocol_key", "previous_version"],
            [
                "forecast_evaluation_protocol_versions.tenant_id",
                "forecast_evaluation_protocol_versions.company_id",
                "forecast_evaluation_protocol_versions.protocol_key",
                "forecast_evaluation_protocol_versions.protocol_version",
            ],
            name="fk_forecast_evaluation_protocol_versions_previous",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_evaluation_protocol_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_forecast_evaluation_protocol_versions_scope_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "protocol_key",
            "protocol_version",
            name="uq_forecast_evaluation_protocol_versions_key_version",
        ),
    )
    op.create_index(
        "ix_forecast_evaluation_protocol_versions_history",
        "forecast_evaluation_protocol_versions",
        ["tenant_id", "company_id", "protocol_key", "protocol_version"],
    )

    op.create_table(
        "forecast_evaluations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("protocol_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("calibration_evaluation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("stage", sa.String(length=16), nullable=False),
        sa.Column("evaluation_status", sa.String(length=16), nullable=False),
        sa.Column("model_count", sa.Integer(), nullable=False),
        sa.Column("dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("protocol_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("evaluation_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "stage IN ('validation', 'holdout')", name="ck_forecast_evaluations_stage"
        ),
        sa.CheckConstraint(
            "evaluation_status IN ('evaluated', 'partial', 'not_evaluable', 'failed')",
            name="ck_forecast_evaluations_status",
        ),
        sa.CheckConstraint(
            "model_count BETWEEN 1 AND 8", name="ck_forecast_evaluations_model_count"
        ),
        sa.CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND protocol_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND evaluation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_evaluations_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_forecast_evaluations_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_evaluations_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluations_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "dataset_version_id"],
            [
                "forecast_dataset_versions.tenant_id",
                "forecast_dataset_versions.company_id",
                "forecast_dataset_versions.id",
            ],
            name="fk_forecast_evaluations_dataset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "protocol_version_id"],
            [
                "forecast_evaluation_protocol_versions.tenant_id",
                "forecast_evaluation_protocol_versions.company_id",
                "forecast_evaluation_protocol_versions.id",
            ],
            name="fk_forecast_evaluations_protocol",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "calibration_evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_evaluations_calibration",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_evaluations_creator",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_evaluations"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_evaluations_scope_id"
        ),
    )
    op.create_index(
        "ix_forecast_evaluations_history",
        "forecast_evaluations",
        ["tenant_id", "company_id", "dataset_version_id", "protocol_version_id", "created_at"],
    )
    op.create_index(
        "uq_forecast_evaluations_single_holdout_per_dataset",
        "forecast_evaluations",
        ["tenant_id", "company_id", "dataset_version_id"],
        unique=True,
        postgresql_where=sa.text("stage = 'holdout'"),
    )

    op.create_table(
        "forecast_evaluation_model_versions",
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_index", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "model_index BETWEEN 1 AND 8",
            name="ck_forecast_evaluation_model_versions_index",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluation_model_versions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_evaluation_model_versions_evaluation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_evaluation_model_versions_model",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "tenant_id",
            "company_id",
            "evaluation_id",
            "model_version_id",
            name="pk_forecast_evaluation_model_versions",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "evaluation_id",
            "model_index",
            name="uq_forecast_evaluation_model_versions_index",
        ),
    )

    op.create_table(
        "forecast_runs",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("dataset_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("protocol_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("calibration_evaluation_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("run_status", sa.String(length=16), nullable=False),
        sa.Column("horizon_periods", sa.Integer(), nullable=False),
        sa.Column("dataset_hash", sa.String(length=64), nullable=False),
        sa.Column("model_hash", sa.String(length=64), nullable=False),
        sa.Column("protocol_hash", sa.String(length=64), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("run_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "run_status IN ('forecasted', 'partial', 'not_evaluable', 'failed')",
            name="ck_forecast_runs_status",
        ),
        sa.CheckConstraint("horizon_periods BETWEEN 1 AND 366", name="ck_forecast_runs_horizon"),
        sa.CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND model_hash ~ '^[0-9a-f]{64}$' "
            "AND protocol_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$' AND run_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_runs_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_forecast_runs_snapshots",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_forecast_runs_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_runs_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "dataset_version_id"],
            [
                "forecast_dataset_versions.tenant_id",
                "forecast_dataset_versions.company_id",
                "forecast_dataset_versions.id",
            ],
            name="fk_forecast_runs_dataset",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "protocol_version_id"],
            [
                "forecast_evaluation_protocol_versions.tenant_id",
                "forecast_evaluation_protocol_versions.company_id",
                "forecast_evaluation_protocol_versions.id",
            ],
            name="fk_forecast_runs_protocol",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_runs_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "calibration_evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_runs_calibration_evaluation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_runs_requester",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_runs"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_forecast_runs_scope_id"),
    )
    op.create_index(
        "ix_forecast_runs_history",
        "forecast_runs",
        ["tenant_id", "company_id", "dataset_version_id", "created_at"],
    )

    op.create_table(
        "forecast_promotion_decisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evaluation_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("model_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision", sa.String(length=8), nullable=False),
        sa.Column("promotion_scope", sa.String(length=32), nullable=False),
        sa.Column("production_activation_status", sa.String(length=40), nullable=False),
        sa.Column("evaluation_hash", sa.String(length=64), nullable=False),
        sa.Column("model_hash", sa.String(length=64), nullable=False),
        sa.Column("rationale", sa.String(length=2000), nullable=False),
        sa.Column("promotion_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_forecast_promotion_decisions_outcome",
        ),
        sa.CheckConstraint(
            "promotion_scope = 'nonproduction_only' "
            "AND production_activation_status = 'blocked_pending_governance'",
            name="ck_forecast_promotion_decisions_scope",
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 2000",
            name="ck_forecast_promotion_decisions_rationale",
        ),
        sa.CheckConstraint(
            "evaluation_hash ~ '^[0-9a-f]{64}$' AND model_hash ~ '^[0-9a-f]{64}$' "
            "AND promotion_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_promotion_decisions_hashes",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_promotion_decisions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_promotion_decisions_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_promotion_decisions_evaluation",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_promotion_decisions_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "evaluation_id", "model_version_id"],
            [
                "forecast_evaluation_model_versions.tenant_id",
                "forecast_evaluation_model_versions.company_id",
                "forecast_evaluation_model_versions.evaluation_id",
                "forecast_evaluation_model_versions.model_version_id",
            ],
            name="fk_forecast_promotion_decisions_evaluation_model",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "decided_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_promotion_decisions_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_forecast_promotion_decisions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_promotion_decisions_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "evaluation_id",
            "model_version_id",
            name="uq_forecast_promotion_decisions_evaluation_model",
        ),
    )
    op.create_index(
        "ix_forecast_promotion_decisions_history",
        "forecast_promotion_decisions",
        ["tenant_id", "company_id", "evaluation_id", "created_at"],
    )

    op.execute(
        """CREATE FUNCTION businessflow_forecasting_append_only()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'forecast evidence is append-only';
        END;
        $$"""
    )
    for table in _TABLES:
        _enable_company_rls(table)
        op.execute(
            f"""CREATE TRIGGER trg_{table}_append_only
            BEFORE UPDATE OR DELETE ON {table}
            FOR EACH ROW EXECUTE FUNCTION businessflow_forecasting_append_only()"""
        )
    op.execute(
        """
        REVOKE ALL ON forecast_dataset_versions, forecast_model_versions,
          forecast_evaluation_protocol_versions, forecast_evaluations,
          forecast_evaluation_model_versions, forecast_runs,
          forecast_promotion_decisions FROM PUBLIC;
        GRANT SELECT, INSERT ON forecast_dataset_versions, forecast_model_versions,
          forecast_evaluation_protocol_versions, forecast_evaluations,
          forecast_evaluation_model_versions, forecast_runs,
          forecast_promotion_decisions TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON forecast_dataset_versions, forecast_model_versions,
          forecast_evaluation_protocol_versions, forecast_evaluations,
          forecast_evaluation_model_versions, forecast_runs,
          forecast_promotion_decisions FROM bf_runtime;
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
          IF EXISTS (SELECT 1 FROM forecast_dataset_versions)
             OR EXISTS (SELECT 1 FROM forecast_model_versions)
             OR EXISTS (SELECT 1 FROM forecast_evaluation_protocol_versions)
             OR EXISTS (SELECT 1 FROM forecast_evaluations)
             OR EXISTS (SELECT 1 FROM forecast_evaluation_model_versions)
             OR EXISTS (SELECT 1 FROM forecast_runs)
             OR EXISTS (SELECT 1 FROM forecast_promotion_decisions) THEN
            RAISE EXCEPTION
              'cannot downgrade while immutable M17 forecasting evidence is retained';
          END IF;
        END;
        $$;
        """
    )
    for table in reversed(_TABLES):
        op.drop_table(table)
    op.execute("DROP FUNCTION businessflow_forecasting_append_only()")
