from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
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


class OperationsEventStream(Base):
    """Immutable source-scoped identity for one source event key."""

    __tablename__ = "operations_event_streams"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_event_stream_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "id",
            name="uq_operations_event_stream_source_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "source_event_key",
            name="uq_operations_event_stream_source_key",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_event_stream_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_operations_event_stream_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_event_stream_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "length(btrim(source_event_key)) BETWEEN 1 AND 160",
            name="ck_operations_event_stream_key",
        ),
        Index(
            "ix_operations_event_stream_source_created",
            "tenant_id",
            "company_id",
            "source_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_event_key: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperationsEventVersion(Base):
    """Append-only observation version with source and correction provenance."""

    __tablename__ = "operations_event_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_event_version_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "version",
            name="uq_operations_event_version_number",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "id",
            name="uq_operations_event_version_event_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_operations_event_version_idempotency",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "previous_version_id",
            name="uq_operations_event_version_previous",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_event_version_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id"],
            [
                "operations_event_streams.tenant_id",
                "operations_event_streams.company_id",
                "operations_event_streams.source_id",
                "operations_event_streams.id",
            ],
            name="fk_operations_event_version_stream",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_operations_event_version_source_profile",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id", "previous_version_id"],
            [
                "operations_event_versions.tenant_id",
                "operations_event_versions.company_id",
                "operations_event_versions.source_id",
                "operations_event_versions.event_id",
                "operations_event_versions.id",
            ],
            name="fk_operations_event_version_previous",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "company_id",
                "source_id",
                "canonical_record_id",
                "canonical_record_revision",
            ],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.source_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            name="fk_operations_event_version_canonical_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_event_version_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_operations_event_version_number"),
        CheckConstraint(
            "evidence_kind IN ('manual_assertion', 'canonical_record_revision')",
            name="ck_operations_event_version_evidence_kind",
        ),
        CheckConstraint(
            "(evidence_kind = 'manual_assertion' AND canonical_record_id IS NULL "
            "AND canonical_record_revision IS NULL AND canonical_attributes_hash IS NULL) OR "
            "(evidence_kind = 'canonical_record_revision' AND canonical_record_id IS NOT NULL "
            "AND canonical_record_revision IS NOT NULL "
            "AND canonical_attributes_hash ~ '^[0-9a-f]{64}$')",
            name="ck_operations_event_version_evidence_reference",
        ),
        CheckConstraint(
            "(version = 1 AND previous_version_id IS NULL AND previous_event_hash IS NULL "
            "AND correction_reason IS NULL) OR "
            "(version > 1 AND previous_version_id IS NOT NULL "
            "AND previous_event_hash ~ '^[0-9a-f]{64}$' "
            "AND length(btrim(correction_reason)) BETWEEN 10 AND 500)",
            name="ck_operations_event_version_correction",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND event_hash ~ '^[0-9a-f]{64}$'",
            name="ck_operations_event_version_hashes",
        ),
        CheckConstraint(
            "length(btrim(event_type)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_type",
        ),
        CheckConstraint(
            "length(btrim(source_subject_reference)) BETWEEN 1 AND 160",
            name="ck_operations_event_version_subject",
        ),
        CheckConstraint(
            "process_reference IS NULL OR length(btrim(process_reference)) BETWEEN 1 AND 160",
            name="ck_operations_event_version_process",
        ),
        CheckConstraint(
            "state_before IS NULL OR length(btrim(state_before)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_state_before",
        ),
        CheckConstraint(
            "state_after IS NULL OR length(btrim(state_after)) BETWEEN 1 AND 128",
            name="ck_operations_event_version_state_after",
        ),
        CheckConstraint(
            "description IS NULL OR length(description) <= 1000",
            name="ck_operations_event_version_description",
        ),
        Index(
            "ix_operations_event_version_source_time",
            "tenant_id",
            "company_id",
            "source_id",
            "occurred_at",
        ),
        Index(
            "ix_operations_event_version_stream",
            "tenant_id",
            "company_id",
            "source_id",
            "event_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    previous_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    previous_event_hash: Mapped[str | None] = mapped_column(String(64))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    source_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    source_subject_reference: Mapped[str] = mapped_column(String(160), nullable=False)
    process_reference: Mapped[str | None] = mapped_column(String(160))
    state_before: Mapped[str | None] = mapped_column(String(128))
    state_after: Mapped[str | None] = mapped_column(String(128))
    description: Mapped[str | None] = mapped_column(Text)
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    canonical_record_revision: Mapped[int | None] = mapped_column(Integer)
    canonical_attributes_hash: Mapped[str | None] = mapped_column(String(64))
    correction_reason: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class OperationsEventCurrent(Base):
    """Indexed current-version pointer for bounded source-and-time analyses."""

    __tablename__ = "operations_event_current"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id"],
            [
                "operations_event_streams.tenant_id",
                "operations_event_streams.company_id",
                "operations_event_streams.source_id",
                "operations_event_streams.id",
            ],
            name="fk_operations_event_current_stream",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "event_id", "event_version_id"],
            [
                "operations_event_versions.tenant_id",
                "operations_event_versions.company_id",
                "operations_event_versions.source_id",
                "operations_event_versions.event_id",
                "operations_event_versions.id",
            ],
            name="fk_operations_event_current_version",
            ondelete="RESTRICT",
        ),
        PrimaryKeyConstraint(
            "tenant_id", "company_id", "source_id", "event_id", name="pk_operations_event_current"
        ),
        Index(
            "ix_operations_event_current_source_time",
            "tenant_id",
            "company_id",
            "source_id",
            "occurred_at",
            "event_id",
        ),
    )

    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationsAnalysisRun(Base):
    """Immutable deterministic count snapshot for one source and explicit period."""

    __tablename__ = "operations_analysis_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_operations_analysis_run_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_operations_analysis_run_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_operations_analysis_run_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_operations_analysis_run_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_operations_analysis_run_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("end_at > start_at", name="ck_operations_analysis_run_period"),
        CheckConstraint(
            "input_count BETWEEN 0 AND 500", name="ck_operations_analysis_run_input_count"
        ),
        CheckConstraint(
            "length(calculation_version) BETWEEN 1 AND 64",
            name="ck_operations_analysis_run_calculation_version",
        ),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND request_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_operations_analysis_run_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_operations_analysis_run_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_operations_analysis_run_result_object",
        ),
        Index(
            "ix_operations_analysis_run_source_created",
            "tenant_id",
            "company_id",
            "source_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    input_count: Mapped[int] = mapped_column(Integer, nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
