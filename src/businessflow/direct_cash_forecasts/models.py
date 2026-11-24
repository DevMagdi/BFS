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


class DirectCashForecastRun(Base):
    """Append-only M16 hashes, redacted evidence, and calculated weekly result snapshot."""

    __tablename__ = "direct_cash_forecast_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_direct_cash_forecast_runs_scope_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_direct_cash_forecast_runs_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_direct_cash_forecast_runs_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "intake_id"],
            [
                "finance_source_intakes.tenant_id",
                "finance_source_intakes.company_id",
                "finance_source_intakes.id",
            ],
            name="fk_direct_cash_forecast_runs_intake",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            [
                "tenant_id",
                "company_id",
                "source_key",
                "mapping_version",
                "mapping_hash",
            ],
            [
                "finance_source_mappings.tenant_id",
                "finance_source_mappings.company_id",
                "finance_source_mappings.source_key",
                "finance_source_mappings.mapping_version",
                "finance_source_mappings.mapping_hash",
            ],
            name="fk_direct_cash_forecast_runs_mapping",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["contract_key", "contract_version", "contract_hash"],
            [
                "finance_source_contracts.contract_key",
                "finance_source_contracts.version",
                "finance_source_contracts.definition_hash",
            ],
            name="fk_direct_cash_forecast_runs_contract",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_direct_cash_forecast_runs_requester_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'",
            name="ck_direct_cash_forecast_runs_source_key",
        ),
        CheckConstraint(
            "formula_id = 'businessflow.finance.direct-cash-forecast' "
            "AND formula_version = '1.0.0'",
            name="ck_direct_cash_forecast_runs_formula",
        ),
        CheckConstraint(
            "length(engine_version) BETWEEN 1 AND 64 AND length(catalog_version) BETWEEN 1 AND 64",
            name="ck_direct_cash_forecast_runs_versions",
        ),
        CheckConstraint(
            "state IN ('calculated', 'incomplete')",
            name="ck_direct_cash_forecast_runs_state",
        ),
        CheckConstraint(
            "horizon_weeks = 13",
            name="ck_direct_cash_forecast_runs_horizon",
        ),
        CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_direct_cash_forecast_runs_currency",
        ),
        CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND mapping_hash ~ '^[0-9a-f]{64}$' "
            "AND file_hash ~ '^[0-9a-f]{64}$' "
            "AND reconciliation_group_hash ~ '^[0-9a-f]{64}$' "
            "AND formula_hash ~ '^[0-9a-f]{64}$' AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$' AND trace_hash ~ '^[0-9a-f]{64}$' "
            "AND evidence_snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_direct_cash_forecast_runs_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(reason_codes) = 'array'",
            name="ck_direct_cash_forecast_runs_reason_codes",
        ),
        CheckConstraint(
            "jsonb_typeof(formula_definition) = 'object'",
            name="ck_direct_cash_forecast_runs_formula_definition",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_direct_cash_forecast_runs_input_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_direct_cash_forecast_runs_result_snapshot",
        ),
        Index(
            "ix_direct_cash_forecast_runs_intake_created",
            "tenant_id",
            "company_id",
            "intake_id",
            "created_at",
        ),
        Index(
            "ix_direct_cash_forecast_runs_as_of_created",
            "tenant_id",
            "company_id",
            "as_of_date",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_key: Mapped[str] = mapped_column(String(64), nullable=False)
    file_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_key: Mapped[str] = mapped_column(String(128), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(32), nullable=False)
    contract_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    reconciliation_group_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_id: Mapped[str] = mapped_column(String(128), nullable=False)
    formula_version: Mapped[str] = mapped_column(String(32), nullable=False)
    formula_definition: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    formula_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    engine_version: Mapped[str] = mapped_column(String(64), nullable=False)
    catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, server_default="[]")
    horizon_weeks: Mapped[int] = mapped_column(Integer, nullable=False)
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    company_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    trace_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
