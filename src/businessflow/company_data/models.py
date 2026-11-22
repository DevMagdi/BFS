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
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class CompanyDataContract(Base):
    """Global, migration-seeded M4 schema contract; runtime access is read-only."""

    __tablename__ = "company_data_contracts"
    __table_args__ = (
        PrimaryKeyConstraint("contract_key", "version", name="pk_company_data_contracts"),
        UniqueConstraint(
            "contract_key",
            "version",
            "dataset_type",
            name="uq_company_data_contracts_contract_dataset",
        ),
        CheckConstraint(
            "dataset_type IN ('employees', 'skills', 'employee_skills', "
            "'weekly_capacity', 'projects', 'resource_assignments')",
            name="ck_company_data_contracts_dataset",
        ),
        CheckConstraint(
            "length(contract_key) BETWEEN 1 AND 128", name="ck_company_data_contracts_key"
        ),
        CheckConstraint(
            "length(version) BETWEEN 1 AND 32", name="ck_company_data_contracts_version"
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_company_data_contracts_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_company_data_contracts_definition"
        ),
        Index("ix_company_data_contracts_dataset", "dataset_type", "version"),
    )

    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[str] = mapped_column(String(32), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompanyDataSource(Base):
    """Immutable, company-owned registration binding a source key to an M4 contract."""

    __tablename__ = "company_data_sources"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_company_data_sources_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "contract_key",
            "contract_version",
            name="uq_company_data_sources_contract_binding",
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "source_key", name="uq_company_data_sources_key"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_company_data_sources_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_company_data_sources_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_sources_owner_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "registered_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_sources_registrar_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_key", "contract_version", "dataset_type"],
            [
                "company_data_contracts.contract_key",
                "company_data_contracts.version",
                "company_data_contracts.dataset_type",
            ],
            name="fk_company_data_sources_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_company_data_sources_key"
        ),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_company_data_sources_name"
        ),
        CheckConstraint(
            "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
            "'projects', 'resource_assignments')",
            name="ck_company_data_sources_dataset",
        ),
        CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_company_data_sources_freshness_policy",
        ),
        Index("ix_company_data_sources_company_dataset", "tenant_id", "company_id", "dataset_type"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    registered_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    freshness_max_age_hours: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CompanyDataIngestion(Base):
    """Append-only contract, quality, freshness, and M4 version snapshot."""

    __tablename__ = "company_data_ingestions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_company_data_ingestions_scope_id"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "import_id", name="uq_company_data_ingestions_import"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_company_data_ingestions_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "contract_key", "contract_version"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
                "company_data_sources.contract_key",
                "company_data_sources.contract_version",
            ],
            name="fk_company_data_ingestions_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_company_data_ingestions_m4_import",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "ingested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_ingestions_actor_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_key", "contract_version"],
            ["company_data_contracts.contract_key", "company_data_contracts.version"],
            name="fk_company_data_ingestions_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "ingestion_status IN ('succeeded', 'succeeded_with_warnings', 'blocked')",
            name="ck_company_data_ingestions_status",
        ),
        CheckConstraint(
            "quality_status IN ('passed', 'warning', 'failed')",
            name="ck_company_data_ingestions_quality",
        ),
        CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'policy_missing', "
            "'source_as_of_missing', 'future_as_of')",
            name="ck_company_data_ingestions_freshness",
        ),
        CheckConstraint(
            "freshness_max_age_hours_snapshot IS NULL OR freshness_max_age_hours_snapshot > 0",
            name="ck_company_data_ingestions_freshness_policy",
        ),
        CheckConstraint(
            "mapping_version >= 0 AND total_rows >= 0", name="ck_company_data_ingestions_counts"
        ),
        CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$'", name="ck_company_data_ingestions_file_hash"
        ),
        CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_data_ingestions_validation_hash",
        ),
        CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_data_ingestions_snapshot_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(quality_findings) = 'array'", name="ck_company_data_ingestions_findings"
        ),
        CheckConstraint(
            "jsonb_typeof(contract_snapshot) = 'object'",
            name="ck_company_data_ingestions_contract_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_company_data_ingestions_snapshot"
        ),
        CheckConstraint(
            "(ingestion_status = 'succeeded' AND quality_status = 'passed') OR "
            "(ingestion_status = 'succeeded_with_warnings' AND quality_status = 'warning') OR "
            "(ingestion_status = 'blocked' AND quality_status = 'failed')",
            name="ck_company_data_ingestions_state_pair",
        ),
        Index(
            "ix_company_data_ingestions_source_created",
            "tenant_id",
            "company_id",
            "source_id",
            "created_at",
        ),
        Index(
            "ix_company_data_ingestions_quality",
            "tenant_id",
            "company_id",
            "quality_status",
            "freshness_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    ingested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    ingestion_status: Mapped[str] = mapped_column(String(32), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(24), nullable=False)
    source_as_of_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_max_age_hours_snapshot: Mapped[int | None] = mapped_column(Integer)
    freshness_evaluated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    validation_hash: Mapped[str | None] = mapped_column(String(64))
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    quality_findings: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    contract_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
