from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
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
from businessflow.models import TimestampMixin


class DataImport(TimestampMixin, Base):
    __tablename__ = "data_imports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_data_imports_tenant_company_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_data_imports_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_data_imports_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_data_imports_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "uploaded_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_uploader_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "confirmed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_confirmer_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "applied_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_data_imports_applier_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('inspected', 'mapped', 'validated', 'ready', 'confirmed', "
            "'applied', 'applied_with_warnings', 'failed', 'cancelled', 'expired')",
            name="ck_data_imports_status",
        ),
        CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_data_imports_file_format"),
        CheckConstraint("file_size_bytes > 0", name="ck_data_imports_file_size"),
        CheckConstraint("mapping_version >= 0", name="ck_data_imports_mapping_version"),
        CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND warning_rows >= 0 "
            "AND duplicate_rows >= 0 AND eligible_rows >= 0 AND skipped_rows >= 0 "
            "AND created_rows >= 0 AND updated_rows >= 0",
            name="ck_data_imports_counts_nonnegative",
        ),
        CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_hashes",
        ),
        CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_validation_hash",
        ),
        CheckConstraint(
            "confirmed_validation_hash IS NULL OR confirmed_validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_data_imports_confirmed_hash",
        ),
        CheckConstraint(
            "jsonb_typeof(inspection) = 'object'", name="ck_data_imports_inspection_object"
        ),
        CheckConstraint(
            "field_mapping IS NULL OR jsonb_typeof(field_mapping) = 'object'",
            name="ck_data_imports_mapping_object",
        ),
        CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'", name="ck_data_imports_ignored_columns_array"
        ),
        CheckConstraint(
            "validation_report IS NULL OR jsonb_typeof(validation_report) = 'object'",
            name="ck_data_imports_validation_object",
        ),
        Index("ix_data_imports_tenant_company_created", "tenant_id", "company_id", "created_at"),
        Index("ix_data_imports_tenant_company_status", "tenant_id", "company_id", "status"),
        Index(
            "ix_data_imports_expiry",
            "tenant_id",
            "company_id",
            "status",
            "staging_expires_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    uploaded_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    confirmed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    applied_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    dataset_type: Mapped[str | None] = mapped_column(String(32))
    selected_worksheet: Mapped[str | None] = mapped_column(String(128))
    inspection: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    field_mapping: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    ignored_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    validation_report: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    validation_hash: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), nullable=False, server_default="inspected")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    eligible_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    skipped_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    updated_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    acknowledge_warnings: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    allow_partial: Mapped[bool] = mapped_column(nullable=False, server_default="false")
    confirmed_validation_hash: Mapped[str | None] = mapped_column(String(64))
    staging_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DataImportRow(Base):
    __tablename__ = "data_import_rows"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "import_id",
            "worksheet_name",
            "row_number",
            name="uq_data_import_rows_source_row",
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_data_import_rows_tenant_company_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_data_import_rows_import",
            ondelete="RESTRICT",
        ),
        CheckConstraint("row_number >= 2", name="ck_data_import_rows_number"),
        CheckConstraint("row_hash ~ '^[0-9a-f]{64}$'", name="ck_data_import_rows_hash"),
        CheckConstraint(
            "raw_values IS NULL OR jsonb_typeof(raw_values) = 'array'",
            name="ck_data_import_rows_values_array",
        ),
        CheckConstraint("jsonb_typeof(issues) = 'array'", name="ck_data_import_rows_issues_array"),
        CheckConstraint(
            "row_status IN ('pending', 'valid', 'warning', 'duplicate', 'invalid', "
            "'applied', 'skipped')",
            name="ck_data_import_rows_status",
        ),
        Index("ix_data_import_rows_parent", "tenant_id", "company_id", "import_id", "row_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_values: Mapped[list[object] | None] = mapped_column(JSONB)
    issues: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    row_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    canonical_type: Mapped[str | None] = mapped_column(String(32))
    canonical_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class DataImportRecordLink(Base):
    __tablename__ = "data_import_record_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "source_row_id", name="uq_data_import_link_source_row"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_data_import_links_tenant_company_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_data_import_links_import",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_row_id"],
            ["data_import_rows.tenant_id", "data_import_rows.company_id", "data_import_rows.id"],
            name="fk_data_import_links_source_row",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "canonical_type IN ('employee', 'skill', 'employee_skill', 'weekly_capacity', "
            "'project', 'resource_assignment')",
            name="ck_data_import_links_type",
        ),
        CheckConstraint(
            "canonical_fingerprint ~ '^[0-9a-f]{64}$'",
            name="ck_data_import_links_fingerprint",
        ),
        Index(
            "ix_data_import_links_import",
            "tenant_id",
            "company_id",
            "import_id",
            "canonical_type",
        ),
        Index(
            "ix_data_import_links_target",
            "tenant_id",
            "company_id",
            "canonical_type",
            "canonical_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    canonical_type: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    canonical_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
