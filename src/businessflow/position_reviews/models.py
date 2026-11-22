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
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class PositionDemoSeed(Base):
    """One explicitly requested, immutable synthetic baseline per Company."""

    __tablename__ = "position_demo_seeds"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_position_demo_seeds_scope_id"),
        UniqueConstraint("tenant_id", "company_id", name="uq_position_demo_seeds_company"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_position_demo_seeds_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_demo_seeds_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_demo_seeds_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_demo_seeds_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(seed_version) BETWEEN 1 AND 64", name="ck_position_demo_seeds_version"
        ),
        CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$'", name="ck_position_demo_seeds_manifest_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(current_template_hashes) = 'object'",
            name="ck_position_demo_seeds_template_hashes",
        ),
        CheckConstraint(
            "EXTRACT(ISODOW FROM baseline_start_week_date) = 1 "
            "AND EXTRACT(ISODOW FROM current_start_week_date) = 1",
            name="ck_position_demo_seeds_monday_periods",
        ),
        CheckConstraint(
            "baseline_end_week_date = baseline_start_week_date + 27 "
            "AND current_start_week_date = baseline_end_week_date + 1",
            name="ck_position_demo_seeds_adjacent_periods",
        ),
        CheckConstraint(
            "length(synthetic_label) BETWEEN 1 AND 120", name="ck_position_demo_seeds_label"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    baseline_metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    seed_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_template_hashes: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    synthetic_label: Mapped[str] = mapped_column(String(120), nullable=False)
    baseline_start_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    baseline_end_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    current_start_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PositionFinancialImport(Base):
    """Company-scoped source-native financial CSV observations and validation snapshot."""

    __tablename__ = "position_financial_imports"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_position_financial_imports_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_position_financial_imports_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_position_financial_imports_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_financial_imports_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_financial_imports_uploader_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "confirmed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_financial_imports_confirmer_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("file_format = 'csv'", name="ck_position_financial_imports_file_format"),
        CheckConstraint("file_size_bytes > 0", name="ck_position_financial_imports_file_size"),
        CheckConstraint(
            "length(schema_version) BETWEEN 1 AND 64", name="ck_position_financial_imports_schema"
        ),
        CheckConstraint(
            "status IN ('invalid', 'validated', 'confirmed')",
            name="ck_position_financial_imports_status",
        ),
        CheckConstraint("row_count > 0", name="ck_position_financial_imports_row_count"),
        CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_financial_imports_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(observations) = 'array'",
            name="ck_position_financial_imports_observations_array",
        ),
        CheckConstraint(
            "jsonb_typeof(validation_report) = 'object'",
            name="ck_position_financial_imports_validation_object",
        ),
        CheckConstraint(
            "(status IN ('invalid', 'validated') AND confirmed_by_user_id IS NULL "
            "AND confirmed_at IS NULL) OR (status = 'confirmed' "
            "AND confirmed_by_user_id IS NOT NULL AND confirmed_at IS NOT NULL)",
            name="ck_position_financial_imports_confirmation_state",
        ),
        Index(
            "ix_position_financial_imports_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    worksheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    validation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_report: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    observations: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PositionReviewBrief(Base):
    """Immutable M10 output bound to exact M5 and source-import versions."""

    __tablename__ = "position_review_briefs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_position_review_briefs_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_position_review_briefs_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_position_review_briefs_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_position_review_briefs_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_review_briefs_requester_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "demo_seed_id"],
            [
                "position_demo_seeds.tenant_id",
                "position_demo_seeds.company_id",
                "position_demo_seeds.id",
            ],
            name="fk_position_review_briefs_demo_seed",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "baseline_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_review_briefs_baseline_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "current_metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_position_review_briefs_current_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "financial_import_id"],
            [
                "position_financial_imports.tenant_id",
                "position_financial_imports.company_id",
                "position_financial_imports.id",
            ],
            name="fk_position_review_briefs_financial_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "EXTRACT(ISODOW FROM start_week_date) = 1", name="ck_position_review_briefs_monday"
        ),
        CheckConstraint(
            "end_week_date = start_week_date + 27", name="ck_position_review_briefs_period"
        ),
        CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64 "
            "AND length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_position_review_briefs_versions",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_review_briefs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_position_review_briefs_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_position_review_briefs_result_object",
        ),
        Index(
            "ix_position_review_briefs_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    demo_seed_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    baseline_metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    current_metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    financial_import_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    start_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_week_date: Mapped[date] = mapped_column(Date, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PositionReviewOperationalImport(Base):
    """Exact M4 import versions used to prepare a Company position review."""

    __tablename__ = "position_review_operational_imports"
    __table_args__ = (
        PrimaryKeyConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "dataset_type",
            name="pk_position_review_operational_imports",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "import_id",
            name="uq_position_review_operational_imports_import",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "review_id"],
            [
                "position_review_briefs.tenant_id",
                "position_review_briefs.company_id",
                "position_review_briefs.id",
            ],
            name="fk_position_review_operational_imports_review",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_position_review_operational_imports_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "dataset_type IN ('employees', 'projects', 'weekly_capacity', 'resource_assignments')",
            name="ck_position_review_operational_imports_type",
        ),
        CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_position_review_operational_imports_hashes",
        ),
        CheckConstraint(
            "mapping_version >= 1", name="ck_position_review_operational_imports_mapping_version"
        ),
        Index(
            "ix_position_review_operational_imports_import",
            "tenant_id",
            "company_id",
            "import_id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    import_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class PositionReviewDisposition(Base):
    """Append-only, user-authored disposition bound to one exact brief hash."""

    __tablename__ = "position_review_dispositions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "review_id",
            "actor_user_id",
            name="uq_position_review_dispositions_actor_review",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "review_id"],
            [
                "position_review_briefs.tenant_id",
                "position_review_briefs.company_id",
                "position_review_briefs.id",
            ],
            name="fk_position_review_dispositions_review",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_position_review_dispositions_actor_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "review_hash ~ '^[0-9a-f]{64}$'", name="ck_position_review_dispositions_hash"
        ),
        CheckConstraint(
            "disposition IN ('noted', 'follow_up_required', 'deferred')",
            name="ck_position_review_dispositions_value",
        ),
        CheckConstraint(
            "length(btrim(rationale)) BETWEEN 1 AND 2000",
            name="ck_position_review_dispositions_rationale",
        ),
        Index(
            "ix_position_review_dispositions_review",
            "tenant_id",
            "company_id",
            "review_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    review_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    disposition: Mapped[str] = mapped_column(String(32), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
