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


class ProjectIntake(Base):
    """Mutable pointer to an immutable sequence of project-intake versions."""

    __tablename__ = "project_intakes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_intakes_tenant_company_id"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "project_code", name="uq_project_intakes_company_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "id", "current_version"],
            [
                "project_intake_versions.tenant_id",
                "project_intake_versions.company_id",
                "project_intake_versions.project_intake_id",
                "project_intake_versions.version",
            ],
            name="fk_project_intakes_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_project_intakes_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_intakes_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_intakes_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("current_version >= 1", name="ck_project_intakes_current_version"),
        CheckConstraint(
            "length(project_code) BETWEEN 1 AND 64 AND project_code = upper(project_code)",
            name="ck_project_intakes_project_code",
        ),
        Index("ix_project_intakes_tenant_company_updated", "tenant_id", "company_id", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_code: Mapped[str] = mapped_column(String(64), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectIntakeVersion(Base):
    """Immutable, canonical JSON snapshot of one intake version."""

    __tablename__ = "project_intake_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "version",
            name="uq_project_intake_versions_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_project_intake_versions_id",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_intake_versions_intake",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_intake_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_project_intake_versions_version"),
        CheckConstraint(
            "length(content_hash) = 64 AND content_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_intake_versions_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_intake_versions_object"
        ),
        Index(
            "ix_project_intake_versions_history",
            "tenant_id",
            "company_id",
            "project_intake_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CapacityAssessment(Base):
    """Immutable capacity-only assessment and evidence linked to an M5 metric run."""

    __tablename__ = "capacity_assessments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_capacity_assessments_tenant_company_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_capacity_assessments_project_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_capacity_assessments_intake",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_capacity_assessments_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_capacity_assessments_requester_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "capacity_status IN ('feasible', 'feasible_with_conditions', "
            "'not_feasible', 'incomplete')",
            name="ck_capacity_assessments_capacity_status",
        ),
        CheckConstraint(
            "business_feasibility_status = 'incomplete'",
            name="ck_capacity_assessments_business_status",
        ),
        CheckConstraint(
            "EXTRACT(ISODOW FROM start_week_date) = 1", name="ck_capacity_assessments_monday"
        ),
        CheckConstraint("week_count BETWEEN 1 AND 52", name="ck_capacity_assessments_week_count"),
        CheckConstraint(
            "end_week_date = start_week_date + (week_count * 7 - 1)",
            name="ck_capacity_assessments_period_end",
        ),
        CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64",
            name="ck_capacity_assessments_version_length",
        ),
        CheckConstraint(
            "length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_capacity_assessments_metric_versions",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_input_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_capacity_assessments_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_capacity_assessments_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_capacity_assessments_result_object",
        ),
        Index(
            "ix_capacity_assessments_history",
            "tenant_id",
            "company_id",
            "project_intake_id",
            "created_at",
        ),
        Index(
            "ix_capacity_assessments_metric_run",
            "tenant_id",
            "company_id",
            "metric_run_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    start_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    week_count: Mapped[int] = mapped_column(Integer, nullable=False)
    capacity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    business_feasibility_status: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
