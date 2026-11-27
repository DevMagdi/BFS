from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class ForecastDatasetVersion(Base):
    """Immutable, company-scoped historical series and provenance snapshot."""

    __tablename__ = "forecast_dataset_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_dataset_versions_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "dataset_key",
            "dataset_version",
            name="uq_forecast_dataset_versions_key_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_dataset_versions_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_dataset_versions_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_dataset_versions_requester",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "dataset_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_dataset_versions_key",
        ),
        CheckConstraint(
            "dataset_version >= 1 AND ((dataset_version = 1 AND previous_version IS NULL) OR "
            "(dataset_version > 1 AND previous_version = dataset_version - 1))",
            name="ck_forecast_dataset_versions_order",
        ),
        CheckConstraint(
            "data_origin IN ('synthetic_fixture', 'caller_assertion')",
            name="ck_forecast_dataset_versions_origin",
        ),
        CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_forecast_dataset_versions_frequency",
        ),
        CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_forecast_dataset_versions_currency",
        ),
        CheckConstraint(
            "end_period >= start_period AND observation_count BETWEEN 1 AND 20000 "
            "AND series_count BETWEEN 1 AND 20000",
            name="ck_forecast_dataset_versions_counts",
        ),
        CheckConstraint(
            "snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_dataset_versions_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'",
            name="ck_forecast_dataset_versions_snapshot",
        ),
        Index(
            "ix_forecast_dataset_versions_history",
            "tenant_id",
            "company_id",
            "dataset_key",
            "dataset_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dataset_key: Mapped[str] = mapped_column(String(64), nullable=False)
    dataset_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    data_origin: Mapped[str] = mapped_column(String(24), nullable=False)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    target_label: Mapped[str] = mapped_column(String(160), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)
    start_period: Mapped[date] = mapped_column(Date, nullable=False)
    end_period: Mapped[date] = mapped_column(Date, nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False)
    series_count: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastModelVersion(Base):
    """Immutable, versioned deterministic model configuration; no arbitrary executable code."""

    __tablename__ = "forecast_model_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_model_versions_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "model_key",
            "model_version",
            name="uq_forecast_model_versions_key_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_model_versions_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_model_versions_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_model_versions_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "model_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_model_versions_key",
        ),
        CheckConstraint(
            "model_version >= 1 AND ((model_version = 1 AND previous_version IS NULL) OR "
            "(model_version > 1 AND previous_version = model_version - 1))",
            name="ck_forecast_model_versions_order",
        ),
        CheckConstraint(
            "model_kind IN ('naive_last_value', 'seasonal_naive', 'moving_average')",
            name="ck_forecast_model_versions_kind",
        ),
        CheckConstraint(
            "model_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_model_versions_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_forecast_model_versions_definition",
        ),
        Index(
            "ix_forecast_model_versions_history",
            "tenant_id",
            "company_id",
            "model_key",
            "model_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    model_key: Mapped[str] = mapped_column(String(64), nullable=False)
    model_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    model_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastEvaluationProtocolVersion(Base):
    """Immutable versioned split, metric, missing-data, outlier and interval configuration."""

    __tablename__ = "forecast_evaluation_protocol_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_forecast_evaluation_protocol_versions_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "protocol_key",
            "protocol_version",
            name="uq_forecast_evaluation_protocol_versions_key_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_evaluation_protocol_versions_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluation_protocol_versions_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_evaluation_protocol_versions_creator",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        CheckConstraint(
            "protocol_key ~ '^[a-z][a-z0-9._-]{1,63}$'",
            name="ck_forecast_evaluation_protocol_versions_key",
        ),
        CheckConstraint(
            "protocol_version >= 1 AND ((protocol_version = 1 AND previous_version IS NULL) OR "
            "(protocol_version > 1 AND previous_version = protocol_version - 1))",
            name="ck_forecast_evaluation_protocol_versions_order",
        ),
        CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly')",
            name="ck_forecast_evaluation_protocol_versions_frequency",
        ),
        CheckConstraint(
            "length(target_key) BETWEEN 2 AND 64 AND protocol_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_evaluation_protocol_versions_identity",
        ),
        CheckConstraint(
            "jsonb_typeof(definition) = 'object'",
            name="ck_forecast_evaluation_protocol_versions_definition",
        ),
        Index(
            "ix_forecast_evaluation_protocol_versions_history",
            "tenant_id",
            "company_id",
            "protocol_key",
            "protocol_version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    protocol_key: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version: Mapped[int | None] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    frequency: Mapped[str] = mapped_column(String(8), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastEvaluation(Base):
    """Immutable chronological evaluation experiment with retained failure/abstention evidence."""

    __tablename__ = "forecast_evaluations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_forecast_evaluations_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_evaluations_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluations_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "dataset_version_id"],
            [
                "forecast_dataset_versions.tenant_id",
                "forecast_dataset_versions.company_id",
                "forecast_dataset_versions.id",
            ],
            name="fk_forecast_evaluations_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "protocol_version_id"],
            [
                "forecast_evaluation_protocol_versions.tenant_id",
                "forecast_evaluation_protocol_versions.company_id",
                "forecast_evaluation_protocol_versions.id",
            ],
            name="fk_forecast_evaluations_protocol",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "calibration_evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_evaluations_calibration",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_evaluations_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "stage IN ('validation', 'holdout')",
            name="ck_forecast_evaluations_stage",
        ),
        CheckConstraint(
            "evaluation_status IN ('evaluated', 'partial', 'not_evaluable', 'failed')",
            name="ck_forecast_evaluations_status",
        ),
        CheckConstraint("model_count BETWEEN 1 AND 8", name="ck_forecast_evaluations_model_count"),
        CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND protocol_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND evaluation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_evaluations_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_forecast_evaluations_snapshots",
        ),
        Index(
            "ix_forecast_evaluations_history",
            "tenant_id",
            "company_id",
            "dataset_version_id",
            "protocol_version_id",
            "created_at",
        ),
        Index(
            "uq_forecast_evaluations_single_holdout_per_dataset",
            "tenant_id",
            "company_id",
            "dataset_version_id",
            unique=True,
            postgresql_where=text("stage = 'holdout'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    protocol_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    calibration_evaluation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    stage: Mapped[str] = mapped_column(String(16), nullable=False)
    evaluation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    model_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evaluation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastEvaluationModelVersion(Base):
    """Typed same-company references from one experiment to its candidate model versions."""

    __tablename__ = "forecast_evaluation_model_versions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_evaluation_model_versions_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_evaluation_model_versions_evaluation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_evaluation_model_versions_model",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "model_index BETWEEN 1 AND 8", name="ck_forecast_evaluation_model_versions_index"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "evaluation_id",
            "model_index",
            name="uq_forecast_evaluation_model_versions_index",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    evaluation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    model_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    model_index: Mapped[int] = mapped_column(Integer, nullable=False)


class ForecastRun(Base):
    """Immutable future forecast snapshot tied to exact data/model/protocol versions."""

    __tablename__ = "forecast_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_forecast_runs_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_forecast_runs_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_runs_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "dataset_version_id"],
            [
                "forecast_dataset_versions.tenant_id",
                "forecast_dataset_versions.company_id",
                "forecast_dataset_versions.id",
            ],
            name="fk_forecast_runs_dataset",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "protocol_version_id"],
            [
                "forecast_evaluation_protocol_versions.tenant_id",
                "forecast_evaluation_protocol_versions.company_id",
                "forecast_evaluation_protocol_versions.id",
            ],
            name="fk_forecast_runs_protocol",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_runs_model",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "calibration_evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_runs_calibration_evaluation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_runs_requester",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "run_status IN ('forecasted', 'partial', 'not_evaluable', 'failed')",
            name="ck_forecast_runs_status",
        ),
        CheckConstraint("horizon_periods BETWEEN 1 AND 366", name="ck_forecast_runs_horizon"),
        CheckConstraint(
            "dataset_hash ~ '^[0-9a-f]{64}$' AND model_hash ~ '^[0-9a-f]{64}$' "
            "AND protocol_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$' AND run_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_runs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_forecast_runs_snapshots",
        ),
        Index(
            "ix_forecast_runs_history",
            "tenant_id",
            "company_id",
            "dataset_version_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dataset_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    protocol_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    model_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    calibration_evaluation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    run_status: Mapped[str] = mapped_column(String(16), nullable=False)
    horizon_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    protocol_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    run_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ForecastPromotionDecision(Base):
    """Append-only human decision over one exact holdout evaluation/model pair."""

    __tablename__ = "forecast_promotion_decisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_forecast_promotion_decisions_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "evaluation_id",
            "model_version_id",
            name="uq_forecast_promotion_decisions_evaluation_model",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_forecast_promotion_decisions_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_forecast_promotion_decisions_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "evaluation_id"],
            [
                "forecast_evaluations.tenant_id",
                "forecast_evaluations.company_id",
                "forecast_evaluations.id",
            ],
            name="fk_forecast_promotion_decisions_evaluation",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "model_version_id"],
            [
                "forecast_model_versions.tenant_id",
                "forecast_model_versions.company_id",
                "forecast_model_versions.id",
            ],
            name="fk_forecast_promotion_decisions_model",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["tenant_id", "decided_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_forecast_promotion_decisions_reviewer",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "decision IN ('approve', 'reject')",
            name="ck_forecast_promotion_decisions_outcome",
        ),
        CheckConstraint(
            "promotion_scope = 'nonproduction_only' "
            "AND production_activation_status = 'blocked_pending_governance'",
            name="ck_forecast_promotion_decisions_scope",
        ),
        CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 2000",
            name="ck_forecast_promotion_decisions_rationale",
        ),
        CheckConstraint(
            "evaluation_hash ~ '^[0-9a-f]{64}$' AND model_hash ~ '^[0-9a-f]{64}$' "
            "AND promotion_hash ~ '^[0-9a-f]{64}$'",
            name="ck_forecast_promotion_decisions_hashes",
        ),
        Index(
            "ix_forecast_promotion_decisions_history",
            "tenant_id",
            "company_id",
            "evaluation_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    evaluation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    model_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decided_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    promotion_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    production_activation_status: Mapped[str] = mapped_column(String(40), nullable=False)
    evaluation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    model_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    rationale: Mapped[str] = mapped_column(String(2000), nullable=False)
    promotion_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
