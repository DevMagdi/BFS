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


class FinanceSourceContract(Base):
    """Global migration-seeded finance source contract catalog; runtime access is read-only."""

    __tablename__ = "finance_source_contracts"
    __table_args__ = (
        CheckConstraint(
            "length(contract_key) BETWEEN 1 AND 128", name="ck_finance_source_contracts_key"
        ),
        CheckConstraint(
            "length(version) BETWEEN 1 AND 32", name="ck_finance_source_contracts_version"
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_finance_source_contracts_hash"
        ),
        UniqueConstraint(
            "contract_key",
            "version",
            "definition_hash",
            name="uq_finance_source_contracts_hash_ref",
        ),
        CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_finance_source_contracts_definition"
        ),
    )

    contract_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinanceSourceMapping(Base):
    """Immutable Company-scoped mapping version for one caller-asserted source key."""

    __tablename__ = "finance_source_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "mapping_version",
            name="uq_finance_source_mappings_scope_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "mapping_version",
            "mapping_hash",
            name="uq_finance_source_mappings_exact_version",
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_finance_source_mappings_scope_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_finance_source_mappings_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_finance_source_mappings_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_finance_source_mappings_creator_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_finance_source_mappings_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_finance_source_mappings_key"
        ),
        CheckConstraint("mapping_version >= 1", name="ck_finance_source_mappings_version"),
        CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_mappings_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'",
            name="ck_finance_source_mappings_field_mapping",
        ),
        CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'",
            name="ck_finance_source_mappings_ignored_columns",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    ignored_columns: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinanceSourceIntake(Base):
    """Immutable one-request source profile, validation, mapping and control comparison evidence."""

    __tablename__ = "finance_source_intakes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_finance_source_intakes_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_finance_source_intakes_idempotency",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_finance_source_intakes_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_finance_source_intakes_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_finance_source_intakes_requester_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_key", "mapping_version", "mapping_hash"],
            [
                "finance_source_mappings.tenant_id",
                "finance_source_mappings.company_id",
                "finance_source_mappings.source_key",
                "finance_source_mappings.mapping_version",
                "finance_source_mappings.mapping_hash",
            ],
            name="fk_finance_source_intakes_mapping",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_finance_source_intakes_contract",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_finance_source_intakes_key"
        ),
        CheckConstraint("file_format = 'csv'", name="ck_finance_source_intakes_file_format"),
        CheckConstraint("file_size_bytes > 0", name="ck_finance_source_intakes_file_size"),
        CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'policy_missing', "
            "'source_as_of_missing', 'future_as_of')",
            name="ck_finance_source_intakes_freshness",
        ),
        CheckConstraint(
            "quality_status IN ('passed', 'warning', 'failed')",
            name="ck_finance_source_intakes_quality",
        ),
        CheckConstraint(
            "reconciliation_status IN ('passed', 'failed', 'not_verifiable')",
            name="ck_finance_source_intakes_reconciliation",
        ),
        CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_finance_source_intakes_freshness_age",
        ),
        CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND duplicate_rows >= 0 "
            "AND total_rows = valid_rows + invalid_rows + duplicate_rows",
            name="ck_finance_source_intakes_counts",
        ),
        CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_intakes_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_finance_source_intakes_input_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_finance_source_intakes_result_snapshot",
        ),
        Index(
            "ix_finance_source_intakes_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_finance_source_intakes_source_created",
            "tenant_id",
            "company_id",
            "source_key",
            "created_at",
        ),
        Index(
            "ix_finance_source_intakes_states",
            "tenant_id",
            "company_id",
            "quality_status",
            "freshness_status",
            "reconciliation_status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    source_as_of_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_max_age_hours: Mapped[int | None] = mapped_column(Integer)
    freshness_status: Mapped[str] = mapped_column(String(24), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reconciliation_status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    valid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    invalid_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinanceSourceRowEvidence(Base):
    """Append-only hashes and validation codes for rows without retaining raw source values."""

    __tablename__ = "finance_source_row_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "intake_id",
            "source_row_number",
            name="uq_finance_source_row_evidence_row",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "intake_id"],
            [
                "finance_source_intakes.tenant_id",
                "finance_source_intakes.company_id",
                "finance_source_intakes.id",
            ],
            name="fk_finance_source_row_evidence_intake",
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_row_number >= 2", name="ck_finance_source_row_evidence_number"),
        CheckConstraint(
            "source_row_hash ~ '^[0-9a-f]{64}$' AND source_record_identity_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_row_evidence_hashes",
        ),
        CheckConstraint(
            "(amount_commitment_hash IS NULL AND amount_commitment_key_version IS NULL) OR "
            "(amount_commitment_hash IS NOT NULL AND "
            "amount_commitment_hash ~ '^[0-9a-f]{64}$' AND "
            "amount_commitment_key_version IS NOT NULL AND "
            "amount_commitment_key_version = 'app-secret.v1')",
            name="ck_finance_source_row_evidence_amount_commitment",
        ),
        CheckConstraint(
            "reconciliation_group_hash IS NULL OR reconciliation_group_hash ~ '^[0-9a-f]{64}$'",
            name="ck_finance_source_row_evidence_group_hash",
        ),
        CheckConstraint(
            "row_status IN ('valid', 'duplicate', 'invalid')",
            name="ck_finance_source_row_evidence_status",
        ),
        CheckConstraint(
            "jsonb_typeof(issues) = 'array'", name="ck_finance_source_row_evidence_issues"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_commitment_hash: Mapped[str | None] = mapped_column(String(64))
    amount_commitment_key_version: Mapped[str | None] = mapped_column(String(32))
    source_record_identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciliation_group_hash: Mapped[str | None] = mapped_column(String(64))
    row_status: Mapped[str] = mapped_column(String(16), nullable=False)
    issues: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
