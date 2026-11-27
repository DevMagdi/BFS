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
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


def _source_fk(name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", "company_id", "source_id"],
        [
            "company_data_sources.tenant_id",
            "company_data_sources.company_id",
            "company_data_sources.id",
        ],
        name=name,
        ondelete="RESTRICT",
    )


def _member_fk(field: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", field],
        ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _scope_fk(local: list[str], remote: list[str], name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(local, remote, name=name, ondelete="RESTRICT")


class CompanyDataSourceProfileVersion(Base):
    """Append-only metadata version attached to the existing Company Data source registry."""

    __tablename__ = "company_data_source_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "version", name="uq_udf_source_profile_version"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_source_profile_scope_id"
        ),
        _source_fk("fk_udf_source_profile_source"),
        _member_fk("owner_user_id", "fk_udf_source_profile_owner"),
        _member_fk("created_by_user_id", "fk_udf_source_profile_actor"),
        CheckConstraint("version > 0", name="ck_udf_source_profile_version"),
        CheckConstraint(
            "source_type IN ('manual_entry', 'local_directory', 'csv', 'xlsx', 'pdf', 'rest_api', "
            "'relational_database', 'erp', 'crm', 'accounting', 'hr', 'other')",
            name="ck_udf_source_profile_type",
        ),
        CheckConstraint(
            "source_status IN ('enabled', 'disabled')", name="ck_udf_source_profile_status"
        ),
        CheckConstraint(
            "authority_classification IN ('unclassified', 'owner_asserted_primary', "
            "'supporting', 'third_party', 'manual_assertion')",
            name="ck_udf_source_profile_authority",
        ),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_udf_source_profile_name"
        ),
        CheckConstraint(
            "business_domain IS NULL OR length(business_domain) BETWEEN 1 AND 64",
            name="ck_udf_source_profile_domain",
        ),
        CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_udf_source_profile_freshness",
        ),
        CheckConstraint(
            "connection_reference IS NULL OR connection_reference ~ '^(config|ref)://[A-Za-z0-9._/-]{1,220}$'",
            name="ck_udf_source_profile_connection_ref",
        ),
        CheckConstraint(
            "credential_reference IS NULL OR credential_reference ~ '^(secret|vault|kms)://[A-Za-z0-9._/-]{1,220}$'",
            name="ck_udf_source_profile_credential_ref",
        ),
        Index("ix_udf_source_profile_latest", "tenant_id", "company_id", "source_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    business_domain: Mapped[str | None] = mapped_column(String(64))
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="enabled")
    connection_reference: Mapped[str | None] = mapped_column(String(256))
    credential_reference: Mapped[str | None] = mapped_column(String(256))
    authority_classification: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default="unclassified"
    )
    freshness_max_age_hours: Mapped[int | None] = mapped_column(Integer)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalSourceSnapshot(Base):
    """Immutable source snapshot with retained original bytes and source fingerprint."""

    __tablename__ = "universal_source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "input_fingerprint",
            "file_format",
            name="uq_udf_snapshot_source_fingerprint",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_snapshot_scope_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_snapshot_source_scope_id"
        ),
        _source_fk("fk_udf_snapshot_source"),
        _member_fk("created_by_user_id", "fk_udf_snapshot_actor"),
        CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_udf_snapshot_format"),
        CheckConstraint("file_size_bytes > 0", name="ck_udf_snapshot_file_size"),
        CheckConstraint("input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_snapshot_fingerprint"),
        CheckConstraint(
            "content_type IS NULL OR length(content_type) <= 128",
            name="ck_udf_snapshot_content_type",
        ),
        Index(
            "ix_udf_snapshot_source_created", "tenant_id", "company_id", "source_id", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(128))
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    original_content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalMappingProfile(Base):
    __tablename__ = "universal_mapping_profiles"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "profile_key", name="uq_udf_mapping_profile_key"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_mapping_profile_scope_id"
        ),
        _source_fk("fk_udf_mapping_profile_source"),
        _member_fk("created_by_user_id", "fk_udf_mapping_profile_actor"),
        CheckConstraint(
            "profile_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_udf_mapping_profile_key"
        ),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_udf_mapping_profile_name"
        ),
        Index(
            "ix_udf_mapping_profiles_source", "tenant_id", "company_id", "source_id", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    profile_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalMappingProfileVersion(Base):
    __tablename__ = "universal_mapping_profile_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "profile_id", "version", name="uq_udf_mapping_version"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_mapping_version_scope_id"
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "profile_id"],
            [
                "universal_mapping_profiles.tenant_id",
                "universal_mapping_profiles.company_id",
                "universal_mapping_profiles.source_id",
                "universal_mapping_profiles.id",
            ],
            "fk_udf_mapping_version_profile",
        ),
        _member_fk("created_by_user_id", "fk_udf_mapping_version_actor"),
        CheckConstraint("version > 0", name="ck_udf_mapping_version_number"),
        CheckConstraint(
            "entity_type IN ('customer', 'supplier', 'product', 'sales_transaction', 'expense', "
            "'business_activity', 'operational_record')",
            name="ck_udf_mapping_entity_type",
        ),
        CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'", name="ck_udf_mapping_fields_object"
        ),
        CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'", name="ck_udf_mapping_ignored_array"
        ),
        CheckConstraint(
            "jsonb_typeof(field_options) = 'object'", name="ck_udf_mapping_options_object"
        ),
        CheckConstraint("contract_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_mapping_contract_hash"),
        Index(
            "ix_udf_mapping_versions_profile", "tenant_id", "company_id", "profile_id", "version"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    profile_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    ignored_columns: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    field_options: Mapped[dict[str, dict[str, object]]] = mapped_column(JSONB, nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalIngestion(Base):
    __tablename__ = "universal_ingestions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_ingestion_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_udf_ingestion_idempotency",
        ),
        _source_fk("fk_udf_ingestion_source"),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "snapshot_id"],
            [
                "universal_source_snapshots.tenant_id",
                "universal_source_snapshots.company_id",
                "universal_source_snapshots.source_id",
                "universal_source_snapshots.id",
            ],
            "fk_udf_ingestion_snapshot",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            "fk_udf_ingestion_source_profile",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            "fk_udf_ingestion_mapping_version",
        ),
        _member_fk("initiated_by_user_id", "fk_udf_ingestion_actor"),
        _member_fk("promoted_by_user_id", "fk_udf_ingestion_reviewer"),
        CheckConstraint(
            "status IN ('staged', 'mapped', 'validated', 'review_required', 'blocked', "
            "'failed', 'promoted')",
            name="ck_udf_ingestion_status",
        ),
        CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_udf_ingestion_format"),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_key_hash"
        ),
        CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_fingerprint"
        ),
        CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND warning_rows >= 0 "
            "AND duplicate_rows >= 0 AND promoted_rows >= 0 AND created_records >= 0 "
            "AND revised_records >= 0 AND unchanged_records >= 0",
            name="ck_udf_ingestion_counts",
        ),
        CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_udf_ingestion_validation_hash",
        ),
        CheckConstraint(
            "validation_snapshot IS NULL OR jsonb_typeof(validation_snapshot) = 'object'",
            name="ck_udf_ingestion_validation_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(parse_options) = 'object'", name="ck_udf_ingestion_parse_options"
        ),
        CheckConstraint("jsonb_typeof(inspection) = 'object'", name="ck_udf_ingestion_inspection"),
        CheckConstraint(
            "(status = 'promoted' AND promoted_at IS NOT NULL AND promoted_by_user_id IS NOT NULL) "
            "OR (status <> 'promoted' AND promoted_at IS NULL AND promoted_by_user_id IS NULL)",
            name="ck_udf_ingestion_promotion_state",
        ),
        Index(
            "ix_udf_ingestions_source_created", "tenant_id", "company_id", "source_id", "created_at"
        ),
        Index("ix_udf_ingestions_status", "tenant_id", "company_id", "status", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    initiated_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    input_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    parse_options: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    inspection: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    selected_worksheet: Mapped[str | None] = mapped_column(String(128))
    mapping_profile_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    status: Mapped[str] = mapped_column(String(24), nullable=False, server_default="staged")
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    warning_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    promoted_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    created_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    revised_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    unchanged_records: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    validation_hash: Mapped[str | None] = mapped_column(String(64))
    validation_snapshot: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    promoted_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    promoted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UniversalIngestionRow(Base):
    """Immutable parsed row for one execution; a raw evidence snapshot may be reused."""

    __tablename__ = "universal_ingestion_rows"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "worksheet_name",
            "row_number",
            name="uq_udf_ingestion_row_location",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_ingestion_row_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "id",
            name="uq_udf_ingestion_row_run_scope_id",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "snapshot_id"],
            [
                "universal_source_snapshots.tenant_id",
                "universal_source_snapshots.company_id",
                "universal_source_snapshots.source_id",
                "universal_source_snapshots.id",
            ],
            "fk_udf_ingestion_row_snapshot",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            "fk_udf_ingestion_row_run",
        ),
        CheckConstraint("row_number > 0", name="ck_udf_ingestion_row_number"),
        CheckConstraint("row_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_row_hash"),
        CheckConstraint("jsonb_typeof(raw_values) = 'array'", name="ck_udf_ingestion_row_values"),
        Index("ix_udf_ingestion_rows_run", "tenant_id", "company_id", "ingestion_id", "row_number"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    worksheet_name: Mapped[str] = mapped_column(String(128), nullable=False)
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    raw_values: Mapped[list[object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalIngestionEvent(Base):
    __tablename__ = "universal_ingestion_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_ingestion_event_scope_id"),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            "fk_udf_ingestion_event_run",
        ),
        _member_fk("actor_user_id", "fk_udf_ingestion_event_actor"),
        CheckConstraint(
            "event_type IN ('uploaded', 'mapping_applied', 'validated', "
            "'promotion_blocked', 'promoted')",
            name="ck_udf_ingestion_event_type",
        ),
        CheckConstraint("jsonb_typeof(details) = 'object'", name="ck_udf_ingestion_event_details"),
        CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_event_hash"),
        Index(
            "ix_udf_ingestion_events_run", "tenant_id", "company_id", "ingestion_id", "created_at"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str | None] = mapped_column(String(24))
    details: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    event_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalRowValidation(Base):
    __tablename__ = "universal_row_validations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "source_row_id",
            "mapping_profile_version_id",
            name="uq_udf_row_validation_version",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_row_validation_scope_id"),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            "fk_udf_row_validation_run",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            "fk_udf_row_validation_source_row",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            "fk_udf_row_validation_mapping",
        ),
        CheckConstraint(
            "row_status IN ('valid', 'warning', 'invalid', 'duplicate')",
            name="ck_udf_row_validation_status",
        ),
        CheckConstraint("row_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_row_validation_hash"),
        CheckConstraint(
            "jsonb_typeof(mapped_values) = 'object'", name="ck_udf_row_validation_values"
        ),
        CheckConstraint("jsonb_typeof(findings) = 'array'", name="ck_udf_row_validation_findings"),
        Index(
            "ix_udf_row_validations_ingestion",
            "tenant_id",
            "company_id",
            "ingestion_id",
            "row_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    mapping_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(160))
    mapped_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    findings: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    row_status: Mapped[str] = mapped_column(String(16), nullable=False)
    row_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_rule_version: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CanonicalBusinessRecord(Base):
    __tablename__ = "canonical_business_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "entity_type",
            "source_id",
            "source_record_id",
            name="uq_udf_canonical_source_record",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_canonical_record_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "id",
            name="uq_udf_canonical_record_source_scope_id",
        ),
        _source_fk("fk_udf_canonical_record_source"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "id", "current_revision"],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            name="fk_udf_canonical_current_revision",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        CheckConstraint(
            "entity_type IN ('customer', 'supplier', 'product', 'sales_transaction', 'expense', "
            "'business_activity', 'operational_record')",
            name="ck_udf_canonical_record_type",
        ),
        CheckConstraint(
            "length(source_record_id) BETWEEN 1 AND 160", name="ck_udf_canonical_record_key"
        ),
        CheckConstraint("current_revision >= 1", name="ck_udf_canonical_record_revision"),
        Index(
            "ix_udf_canonical_records_entity",
            "tenant_id",
            "company_id",
            "entity_type",
            "created_at",
        ),
        Index(
            "ix_udf_canonical_records_source",
            "tenant_id",
            "company_id",
            "source_id",
            "source_record_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class CanonicalBusinessRecordRevision(Base):
    __tablename__ = "canonical_business_record_revisions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "record_id",
            "revision",
            name="uq_udf_canonical_record_revision",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_record_revision_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "record_id",
            "revision",
            name="uq_udf_record_revision_source_scope",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "record_id"],
            [
                "canonical_business_records.tenant_id",
                "canonical_business_records.company_id",
                "canonical_business_records.id",
            ],
            "fk_udf_record_revision_record",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            "fk_udf_record_revision_ingestion",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            "fk_udf_record_revision_source_row",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            "fk_udf_record_revision_mapping",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            "fk_udf_record_revision_source_profile",
        ),
        _member_fk("created_by_user_id", "fk_udf_record_revision_actor"),
        CheckConstraint("revision >= 1", name="ck_udf_record_revision_number"),
        CheckConstraint("attributes_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_record_revision_hash"),
        CheckConstraint(
            "validation_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_record_revision_validation_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name="ck_udf_record_revision_attributes"
        ),
        Index("ix_udf_record_revisions_record", "tenant_id", "company_id", "record_id", "revision"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_record_id: Mapped[str] = mapped_column(String(160), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    snapshot_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    mapping_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    attributes: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    attributes_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    change_reason: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_note: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalIngestionRecordLink(Base):
    __tablename__ = "universal_ingestion_record_links"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "source_row_id",
            name="uq_udf_record_link_row",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_record_link_scope_id"),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            "fk_udf_record_link_ingestion",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            "fk_udf_record_link_row",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "canonical_record_id"],
            [
                "canonical_business_records.tenant_id",
                "canonical_business_records.company_id",
                "canonical_business_records.id",
            ],
            "fk_udf_record_link_record",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "canonical_record_id", "accepted_revision"],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            "fk_udf_record_link_revision",
        ),
        CheckConstraint(
            "outcome IN ('created', 'revised', 'unchanged', 'withheld_duplicate')",
            name="ck_udf_record_link_outcome",
        ),
        CheckConstraint(
            "(outcome = 'withheld_duplicate' AND canonical_record_id IS NULL "
            "AND accepted_revision IS NULL) OR (outcome <> 'withheld_duplicate' "
            "AND canonical_record_id IS NOT NULL AND accepted_revision IS NOT NULL)",
            name="ck_udf_record_link_record_state",
        ),
        Index("ix_udf_record_links_record", "tenant_id", "company_id", "canonical_record_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(160))
    canonical_record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    accepted_revision: Mapped[int | None] = mapped_column(Integer)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalRetentionPolicyVersion(Base):
    """Company-scoped retention policy configuration; no version is active without approval."""

    __tablename__ = "universal_retention_policy_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_retention_policy_scope_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "version", name="uq_udf_retention_policy_version"
        ),
        _scope_fk(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            "fk_udf_retention_policy_company",
        ),
        _member_fk("created_by_user_id", "fk_udf_retention_policy_actor"),
        CheckConstraint("version > 0", name="ck_udf_retention_policy_version"),
        CheckConstraint("retention_days BETWEEN 1 AND 36500", name="ck_udf_retention_policy_days"),
        Index("ix_udf_retention_policy_latest", "tenant_id", "company_id", "version"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    retention_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalRetentionPolicyApproval(Base):
    """Append-only approval that activates one exact retention policy version."""

    __tablename__ = "universal_retention_policy_approvals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "policy_version_id", name="uq_udf_retention_policy_approval"
        ),
        _scope_fk(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            "fk_udf_retention_approval_policy",
        ),
        _member_fk("approved_by_user_id", "fk_udf_retention_approval_actor"),
        Index(
            "ix_udf_retention_approvals_policy",
            "tenant_id",
            "company_id",
            "policy_version_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approved_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalRetentionPolicyRevocation(Base):
    """Append-only event that disables purging until a later policy is approved."""

    __tablename__ = "universal_retention_policy_revocations"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "policy_version_id",
            name="uq_udf_retention_policy_revocation",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            "fk_udf_retention_revocation_policy",
        ),
        _member_fk("revoked_by_user_id", "fk_udf_retention_revocation_actor"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revoked_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalSourceLifecycleEvent(Base):
    """Company-scoped source revocation and evidence-purge completion tombstones."""

    __tablename__ = "universal_source_lifecycle_events"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "event_type",
            name="uq_udf_source_lifecycle_event",
        ),
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_source_lifecycle_scope_id"),
        _source_fk("fk_udf_source_lifecycle_source"),
        _member_fk("actor_user_id", "fk_udf_source_lifecycle_actor"),
        CheckConstraint("event_type IN ('revoked', 'purged')", name="ck_udf_source_lifecycle_type"),
        CheckConstraint(
            "(event_type = 'revoked' AND reason_code IS NOT NULL AND reason_code IN "
            "('owner_request', 'source_replaced', 'contract_ended', 'security_concern', 'other')) "
            "OR (event_type = 'purged' AND reason_code IS NULL)",
            name="ck_udf_source_lifecycle_reason",
        ),
        Index(
            "ix_udf_source_lifecycle_source",
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
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(32))
    actor_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UniversalIngestionPurgeTombstone(Base):
    """Non-payload tombstone preserving idempotency after an ingestion is physically purged."""

    __tablename__ = "universal_ingestion_purge_tombstones"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "ingestion_id",
            name="uq_udf_purge_tombstone_ingestion",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_udf_purge_tombstone_idempotency",
        ),
        _source_fk("fk_udf_purge_tombstone_source"),
        _scope_fk(
            ["tenant_id", "company_id", "policy_version_id"],
            [
                "universal_retention_policy_versions.tenant_id",
                "universal_retention_policy_versions.company_id",
                "universal_retention_policy_versions.id",
            ],
            "fk_udf_purge_tombstone_policy",
        ),
        _member_fk("purged_by_user_id", "fk_udf_purge_tombstone_actor"),
        CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'",
            name="ck_udf_purge_tombstone_key_hash",
        ),
        Index(
            "ix_udf_purge_tombstones_source_purged",
            "tenant_id",
            "company_id",
            "source_id",
            "purged_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingestion_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    purged_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    purged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
