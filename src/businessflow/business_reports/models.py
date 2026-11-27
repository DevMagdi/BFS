from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class BusinessReportExport(Base):
    """Immutable export manifest and hashes; rendered bytes live in the expiring artifact table."""

    __tablename__ = "business_report_exports"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_report_exports_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_report_exports_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_report_exports_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_report_exports_creator",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "title = btrim(title) AND length(title) BETWEEN 1 AND 120",
            name="ck_report_exports_title",
        ),
        CheckConstraint("format IN ('json', 'csv', 'xlsx')", name="ck_report_exports_format"),
        CheckConstraint("mode = 'nonproduction_prototype'", name="ck_report_exports_mode"),
        CheckConstraint("source_count BETWEEN 1 AND 20", name="ck_report_exports_source_count"),
        CheckConstraint(
            "manifest_hash ~ '^[0-9a-f]{64}$' AND artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_report_exports_hashes",
        ),
        CheckConstraint(
            "artifact_size_bytes BETWEEN 1 AND 8388608",
            name="ck_report_exports_artifact_size",
        ),
        CheckConstraint("expires_at > created_at", name="ck_report_exports_expiry"),
        CheckConstraint("jsonb_typeof(manifest) = 'object'", name="ck_report_exports_manifest"),
        Index("ix_report_exports_company_created", "tenant_id", "company_id", "created_at", "id"),
        Index("ix_report_exports_company_expiry", "tenant_id", "company_id", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    format: Mapped[str] = mapped_column(String(8), nullable=False)
    mode: Mapped[str] = mapped_column(String(32), nullable=False)
    manifest: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    artifact_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_count: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessReportArtifactContent(Base):
    """Short-lived rendered bytes, independently deletable only after expiry or revocation."""

    __tablename__ = "business_report_artifact_contents"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "report_id"],
            [
                "business_report_exports.tenant_id",
                "business_report_exports.company_id",
                "business_report_exports.id",
            ],
            name="fk_report_artifact_contents_export",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "octet_length(content_bytes) BETWEEN 1 AND 8388608",
            name="ck_report_artifact_contents_size",
        ),
        Index("ix_report_artifact_contents_report", "tenant_id", "company_id", "report_id"),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    content_bytes: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessReportArtifactRevocation(Base):
    """Append-only early-revocation event that immediately blocks downloads and permits purge."""

    __tablename__ = "business_report_artifact_revocations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "report_id"],
            [
                "business_report_exports.tenant_id",
                "business_report_exports.company_id",
                "business_report_exports.id",
            ],
            name="fk_report_artifact_revocations_export",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "revoked_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_report_artifact_revocations_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(btrim(reason)) BETWEEN 10 AND 500",
            name="ck_report_artifact_revocations_reason",
        ),
        Index(
            "ix_report_artifact_revocations_actor", "tenant_id", "revoked_by_user_id", "revoked_at"
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    report_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    revoked_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
