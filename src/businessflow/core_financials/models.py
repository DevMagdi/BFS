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
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class CoreFinancialPolicy(Base):
    """Immutable versioned management-reporting policy, explicitly non-authoritative."""

    __tablename__ = "core_financial_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_policies_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "policy_key",
            "version",
            name="uq_core_financial_policy_version",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_core_financial_policies_version"),
        CheckConstraint(
            "authority = 'illustrative_not_authoritative'",
            name="ck_core_financial_policies_authority",
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_core_financial_policies_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_core_financial_policies_definition"
        ),
        Index(
            "ix_core_financial_policies_scope_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    authority: Mapped[str] = mapped_column(
        String(40), nullable=False, server_default="illustrative_not_authoritative"
    )
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreFinancialMapping(Base):
    """Immutable source-specific header mapping version for one Company and source key."""

    __tablename__ = "core_financial_mappings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_mappings_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_key",
            "source_type",
            "version",
            name="uq_core_financial_mapping_version",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_core_financial_mappings_version"),
        CheckConstraint(
            "source_type IN ('general_ledger', 'bank_activity')",
            name="ck_core_financial_mappings_source_type",
        ),
        CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_core_financial_mappings_source_key",
        ),
        CheckConstraint("mapping_hash ~ '^[0-9a-f]{64}$'", name="ck_core_financial_mappings_hash"),
        CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'",
            name="ck_core_financial_mappings_field_mapping",
        ),
        CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'",
            name="ck_core_financial_mappings_ignored_columns",
        ),
        Index(
            "ix_core_financial_mappings_scope_source",
            "tenant_id",
            "company_id",
            "source_key",
            "source_type",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    field_mapping: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    ignored_columns: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreFinancialImport(Base):
    """Append-only CSV import and validation result for normalized financial records."""

    __tablename__ = "core_financial_imports"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_core_financial_imports_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            name="uq_core_financial_imports_idempotency",
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "mapping_id"],
            [
                "core_financial_mappings.tenant_id",
                "core_financial_mappings.company_id",
                "core_financial_mappings.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_id"],
            [
                "core_financial_policies.tenant_id",
                "core_financial_policies.company_id",
                "core_financial_policies.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_type IN ('general_ledger', 'bank_activity')",
            name="ck_core_financial_imports_source_type",
        ),
        CheckConstraint("file_format = 'csv'", name="ck_core_financial_imports_file_format"),
        CheckConstraint("file_size_bytes > 0", name="ck_core_financial_imports_file_size"),
        CheckConstraint(
            "(source_archive_status = 'retained' AND original_content IS NOT NULL "
            "AND octet_length(original_content) = file_size_bytes) OR "
            "(source_archive_status = 'legacy_unavailable' AND original_content IS NULL)",
            name="ck_core_financial_imports_source_archive",
        ),
        CheckConstraint("period_end >= period_start", name="ck_core_financial_imports_period"),
        CheckConstraint(
            "period_completeness IN ('complete', 'partial', 'unknown')",
            name="ck_core_financial_imports_completeness",
        ),
        CheckConstraint(
            "quality_status IN ('passed', 'incomplete')",
            name="ck_core_financial_imports_quality",
        ),
        CheckConstraint(
            "total_rows >= 0 AND accepted_rows >= 0 AND rejected_rows >= 0 "
            "AND duplicate_rows >= 0 AND "
            "total_rows = accepted_rows + rejected_rows + duplicate_rows",
            name="ck_core_financial_imports_counts",
        ),
        CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$' AND idempotency_key_hash ~ '^[0-9a-f]{64}$' "
            "AND request_fingerprint ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND policy_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_imports_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_core_financial_imports_input_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_core_financial_imports_result_snapshot",
        ),
        Index(
            "ix_core_financial_imports_scope_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_core_financial_imports_source_created",
            "tenant_id",
            "company_id",
            "source_key",
            "source_type",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    source_type: Mapped[str] = mapped_column(String(24), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_format: Mapped[str] = mapped_column(String(8), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    source_archive_status: Mapped[str] = mapped_column(
        String(24), nullable=False, server_default="legacy_unavailable"
    )
    original_content: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True, deferred=True
    )
    mapping_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    period_completeness: Mapped[str] = mapped_column(String(16), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(16), nullable=False)
    total_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    accepted_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    duplicate_rows: Mapped[int] = mapped_column(Integer, nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreFinancialRecord(Base):
    """Normalized canonical financial fact plus source-row lineage, including rejected rows."""

    __tablename__ = "core_financial_records"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_core_financial_records_scope_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "import_id",
            "source_row_number",
            name="uq_core_financial_record_row",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "canonical_record_key",
            name="uq_core_financial_record_source_identity",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("source_row_number >= 2", name="ck_core_financial_records_row_number"),
        CheckConstraint(
            "row_status IN ('accepted', 'rejected', 'duplicate')",
            name="ck_core_financial_records_status",
        ),
        CheckConstraint(
            "category IS NULL OR category IN ('revenue', 'revenue_reduction', 'cost_of_sales', "
            "'operating_expense', 'non_operating_income', 'non_operating_expense', 'tax_expense')",
            name="ck_core_financial_records_category",
        ),
        CheckConstraint(
            "event_type IS NULL OR event_type IN "
            "('normal', 'refund', 'reversal', 'adjustment', 'transfer')",
            name="ck_core_financial_records_event_type",
        ),
        CheckConstraint(
            "row_status <> 'accepted' OR (source_record_id IS NOT NULL AND "
            "transaction_date IS NOT NULL AND currency_code IS NOT NULL AND "
            "account_code IS NOT NULL AND event_type IS NOT NULL AND signed_amount IS NOT NULL)",
            name="ck_core_financial_records_accepted_fields",
        ),
        CheckConstraint(
            "source_row_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_records_row_hash",
        ),
        CheckConstraint(
            "canonical_record_key IS NULL OR canonical_record_key ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_records_identity_hash",
        ),
        CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_core_financial_records_currency",
        ),
        CheckConstraint(
            "signed_amount IS NULL OR signed_amount ~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$'",
            name="ck_core_financial_records_signed_amount",
        ),
        CheckConstraint(
            "jsonb_typeof(mapped_source_values) = 'object' AND jsonb_typeof(issues) = 'array'",
            name="ck_core_financial_records_json",
        ),
        Index(
            "ix_core_financial_records_import_row",
            "tenant_id",
            "company_id",
            "import_id",
            "source_row_number",
        ),
        Index(
            "ix_core_financial_records_period_currency",
            "tenant_id",
            "company_id",
            "transaction_date",
            "currency_code",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    import_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_record_id: Mapped[str | None] = mapped_column(String(256))
    transaction_date: Mapped[date | None] = mapped_column(Date)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    account_code: Mapped[str | None] = mapped_column(String(128))
    category: Mapped[str | None] = mapped_column(String(32))
    event_type: Mapped[str | None] = mapped_column(String(16))
    signed_amount: Mapped[str | None] = mapped_column(String(65))
    reverses_record_id: Mapped[str | None] = mapped_column(String(256))
    canonical_record_key: Mapped[str | None] = mapped_column(String(64))
    source_row_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapped_source_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    row_status: Mapped[str] = mapped_column(String(16), nullable=False)
    issues: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreFinancialRun(Base):
    """Append-only, policy-versioned calculation, reconciliation and evidence summary."""

    __tablename__ = "core_financial_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_core_financial_runs_scope_id"),
        Index(
            "uq_core_financial_runs_idempotency",
            "tenant_id",
            "company_id",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("idempotency_key_hash IS NOT NULL"),
        ),
        ForeignKeyConstraint(["tenant_id"], ["tenants.id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "policy_id"],
            [
                "core_financial_policies.tenant_id",
                "core_financial_policies.company_id",
                "core_financial_policies.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "ledger_import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "bank_import_id"],
            [
                "core_financial_imports.tenant_id",
                "core_financial_imports.company_id",
                "core_financial_imports.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint("period_end >= period_start", name="ck_core_financial_runs_period"),
        CheckConstraint(
            "ledger_import_id IS NOT NULL OR bank_import_id IS NOT NULL",
            name="ck_core_financial_runs_source_required",
        ),
        CheckConstraint("currency_code ~ '^[A-Z]{3}$'", name="ck_core_financial_runs_currency"),
        CheckConstraint(
            "state IN ('calculated', 'incomplete')", name="ck_core_financial_runs_state"
        ),
        CheckConstraint(
            "(idempotency_key_hash IS NULL AND request_fingerprint IS NULL) OR "
            "(idempotency_key_hash IS NOT NULL AND request_fingerprint IS NOT NULL AND "
            "idempotency_key_hash ~ '^[0-9a-f]{64}$' AND "
            "request_fingerprint ~ '^[0-9a-f]{64}$')",
            name="ck_core_financial_runs_idempotency",
        ),
        CheckConstraint(
            "input_hash ~ '^[0-9a-f]{64}$' AND result_hash ~ '^[0-9a-f]{64}$' "
            "AND trace_hash ~ '^[0-9a-f]{64}$'",
            name="ck_core_financial_runs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object' AND jsonb_typeof(result_snapshot) = 'object'",
            name="ck_core_financial_runs_json",
        ),
        Index(
            "ix_core_financial_runs_scope_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    policy_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    ledger_import_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    bank_import_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    currency_code: Mapped[str] = mapped_column(String(3), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class CoreFinancialRunEvidence(Base):
    """Line-level immutable contribution link from a reported measure to a normalized source row."""

    __tablename__ = "core_financial_run_evidence"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "run_id",
            "metric",
            "record_id",
            name="uq_core_financial_run_evidence_record",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "run_id"],
            [
                "core_financial_runs.tenant_id",
                "core_financial_runs.company_id",
                "core_financial_runs.id",
            ],
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "record_id"],
            [
                "core_financial_records.tenant_id",
                "core_financial_records.company_id",
                "core_financial_records.id",
            ],
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "metric IN ('revenue', 'expenses', 'gross_profit', 'operating_profit', "
            "'net_profit', 'cash_flow')",
            name="ck_core_financial_run_evidence_metric",
        ),
        CheckConstraint(
            "formula_coefficient IN (-1, 1)", name="ck_core_financial_run_evidence_coefficient"
        ),
        CheckConstraint(
            "contribution_amount ~ '^-?(0|[1-9][0-9]*)(\\.[0-9]+)?$'",
            name="ck_core_financial_run_evidence_amount",
        ),
        Index(
            "ix_core_financial_run_evidence_metric",
            "tenant_id",
            "company_id",
            "run_id",
            "metric",
            "id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    record_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    formula_coefficient: Mapped[int] = mapped_column(Integer, nullable=False)
    contribution_amount: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
