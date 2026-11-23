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
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class MetricRun(Base):
    __tablename__ = "metric_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_metric_runs_tenant_company_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_metric_runs_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_metric_runs_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_metric_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("EXTRACT(ISODOW FROM start_week_date) = 1", name="ck_metric_runs_monday"),
        CheckConstraint("week_count BETWEEN 1 AND 52", name="ck_metric_runs_week_count"),
        CheckConstraint(
            "end_week_date = start_week_date + (week_count * 7 - 1)",
            name="ck_metric_runs_period_end",
        ),
        CheckConstraint(
            "reliability_status IN ('complete', 'partial', 'unavailable')",
            name="ck_metric_runs_reliability",
        ),
        CheckConstraint(
            "length(metric_catalog_version) BETWEEN 1 AND 64",
            name="ck_metric_runs_catalog_version",
        ),
        CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64",
            name="ck_metric_runs_calculation_version",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_runs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_metric_runs_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_metric_runs_result_object",
        ),
        Index("ix_metric_runs_tenant_company_created", "tenant_id", "company_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    start_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    week_count: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    reliability_status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class MetricInsightRun(Base):
    """Immutable descriptive comparison over two same-company M5 runs."""

    __tablename__ = "metric_insight_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_metric_insight_runs_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_metric_insight_runs_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_metric_insight_runs_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_metric_insight_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_metric_insight_runs_baseline_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "comparison_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_metric_insight_runs_comparison_metric_run",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "baseline_metric_run_id <> comparison_metric_run_id",
            name="ck_metric_insight_runs_distinct_runs",
        ),
        CheckConstraint(
            "length(metric_id) BETWEEN 1 AND 64", name="ck_metric_insight_runs_metric_id"
        ),
        CheckConstraint(
            "availability_status IN ('available', 'partial', 'abstained')",
            name="ck_metric_insight_runs_availability",
        ),
        CheckConstraint(
            "length(insight_catalog_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_catalog_version",
        ),
        CheckConstraint(
            "length(engine_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_engine_version",
        ),
        CheckConstraint(
            "length(comparison_definition_version) BETWEEN 1 AND 64",
            name="ck_metric_insight_runs_definition_version",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_metric_insight_runs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_metric_insight_runs_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_metric_insight_runs_result_object",
        ),
        Index(
            "ix_metric_insight_runs_tenant_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_metric_insight_runs_baseline",
            "tenant_id",
            "company_id",
            "baseline_metric_run_id",
        ),
        Index(
            "ix_metric_insight_runs_comparison",
            "tenant_id",
            "company_id",
            "comparison_metric_run_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    baseline_metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    comparison_metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_id: Mapped[str] = mapped_column(String(64), nullable=False)
    insight_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    comparison_definition_version: Mapped[str] = mapped_column(String(64), nullable=False)
    availability_status: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
