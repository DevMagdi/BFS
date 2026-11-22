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


class ProjectFinancialInputCase(Base):
    """Mutable, company-scoped head pointer to immutable financial input versions."""

    __tablename__ = "project_financial_input_cases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_financial_input_cases_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "project_intake_id",
            name="uq_project_financial_input_cases_case_project",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            name="uq_project_financial_input_cases_intake",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "id", "current_version", "current_version_id"],
            [
                "project_financial_input_versions.tenant_id",
                "project_financial_input_versions.company_id",
                "project_financial_input_versions.input_case_id",
                "project_financial_input_versions.version",
                "project_financial_input_versions.id",
            ],
            name="fk_project_financial_input_cases_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_financial_input_cases_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_financial_input_cases_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_financial_input_cases_intake",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_financial_input_cases_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("current_version >= 1", name="ck_project_financial_input_cases_version"),
        Index(
            "ix_project_financial_input_cases_company_updated",
            "tenant_id",
            "company_id",
            "updated_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False)
    current_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class ProjectFinancialInputVersion(Base):
    """Immutable financial observations with source/assumption metadata and exact decimals."""

    __tablename__ = "project_financial_input_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "version",
            name="uq_project_financial_input_versions_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "id",
            name="uq_project_financial_input_versions_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_project_financial_input_versions_project_ref",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "version",
            "id",
            name="uq_project_financial_input_versions_current_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "input_case_id", "project_intake_id"],
            [
                "project_financial_input_cases.tenant_id",
                "project_financial_input_cases.company_id",
                "project_financial_input_cases.id",
                "project_financial_input_cases.project_intake_id",
            ],
            name="fk_project_financial_input_versions_case",
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
            name="fk_project_financial_input_versions_intake_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_financial_input_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "input_case_id", "supersedes_version_id"],
            [
                "project_financial_input_versions.tenant_id",
                "project_financial_input_versions.company_id",
                "project_financial_input_versions.input_case_id",
                "project_financial_input_versions.id",
            ],
            name="fk_project_financial_input_versions_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_project_financial_input_versions_version"),
        CheckConstraint(
            "intake_version >= 1", name="ck_project_financial_input_versions_intake_version"
        ),
        CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_project_financial_input_versions_hash"
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_project_financial_input_versions_object",
        ),
        CheckConstraint(
            "(version = 1 AND supersedes_version_id IS NULL) OR "
            "(version > 1 AND supersedes_version_id IS NOT NULL)",
            name="ck_project_financial_input_versions_chain",
        ),
        Index(
            "ix_project_financial_input_versions_history",
            "tenant_id",
            "company_id",
            "project_intake_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    input_case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class FinancialAssessment(Base):
    """Immutable M8 snapshot linked to exact M5/M6/M7 evidence and source inputs."""

    __tablename__ = "financial_assessments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_financial_assessments_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_financial_assessments_project_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_financial_assessments_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_financial_assessments_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_financial_assessments_intake",
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
            name="fk_financial_assessments_intake_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id", "capacity_assessment_id"],
            [
                "capacity_assessments.tenant_id",
                "capacity_assessments.company_id",
                "capacity_assessments.project_intake_id",
                "capacity_assessments.id",
            ],
            name="fk_financial_assessments_capacity_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_financial_assessments_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "project_intake_id"],
            [
                "project_decision_cases.tenant_id",
                "project_decision_cases.company_id",
                "project_decision_cases.id",
                "project_decision_cases.project_intake_id",
            ],
            name="fk_financial_assessments_decision_case",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "decision_version_id"],
            [
                "project_decision_versions.tenant_id",
                "project_decision_versions.company_id",
                "project_decision_versions.decision_case_id",
                "project_decision_versions.id",
            ],
            name="fk_financial_assessments_decision_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_approval_id"],
            [
                "project_decision_approvals.tenant_id",
                "project_decision_approvals.company_id",
                "project_decision_approvals.id",
            ],
            name="fk_financial_assessments_decision_approval",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "financial_input_case_id", "project_intake_id"],
            [
                "project_financial_input_cases.tenant_id",
                "project_financial_input_cases.company_id",
                "project_financial_input_cases.id",
                "project_financial_input_cases.project_intake_id",
            ],
            name="fk_financial_assessments_financial_input_case",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id", "financial_input_version_id"],
            [
                "project_financial_input_versions.tenant_id",
                "project_financial_input_versions.company_id",
                "project_financial_input_versions.project_intake_id",
                "project_financial_input_versions.id",
            ],
            name="fk_financial_assessments_financial_input_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_financial_assessments_requester_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "financial_verdict IN ('financially_feasible', 'financially_feasible_with_conditions', "
            "'financially_not_feasible', 'incomplete')",
            name="ck_financial_assessments_verdict",
        ),
        CheckConstraint(
            "financial_verdict = 'incomplete' AND approved_policy_id IS NULL "
            "AND approved_policy_version IS NULL",
            name="ck_financial_assessments_policy_gate",
        ),
        CheckConstraint(
            "decision_outcome = 'proceed_to_financial_evaluation' "
            "AND decision_approval_outcome = 'approved'",
            name="ck_financial_assessments_handoff",
        ),
        CheckConstraint(
            "intake_version >= 1 AND decision_version >= 1",
            name="ck_financial_assessments_versions",
        ),
        CheckConstraint(
            "length(formula_catalog_version) BETWEEN 1 AND 64 "
            "AND length(capacity_assessment_version) BETWEEN 1 AND 64 "
            "AND length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_financial_assessments_catalog_versions",
        ),
        CheckConstraint(
            "intake_version_hash ~ '^[0-9a-f]{64}$' "
            "AND capacity_assessment_input_hash ~ '^[0-9a-f]{64}$' "
            "AND capacity_assessment_result_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_input_hash ~ '^[0-9a-f]{64}$' "
            "AND metric_result_hash ~ '^[0-9a-f]{64}$' "
            "AND decision_hash ~ '^[0-9a-f]{64}$' "
            "AND decision_evidence_hash ~ '^[0-9a-f]{64}$' "
            "AND decision_approval_hash ~ '^[0-9a-f]{64}$' "
            "AND input_hash ~ '^[0-9a-f]{64}$' "
            "AND result_hash ~ '^[0-9a-f]{64}$'",
            name="ck_financial_assessments_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(currency_codes) = 'array'",
            name="ck_financial_assessments_currency_codes",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_financial_assessments_input_object",
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_financial_assessments_result_object",
        ),
        CheckConstraint(
            "(financial_input_case_id IS NULL AND financial_input_version_id IS NULL "
            "AND financial_input_version IS NULL AND financial_input_hash IS NULL) OR "
            "(financial_input_case_id IS NOT NULL AND financial_input_version_id IS NOT NULL "
            "AND financial_input_version IS NOT NULL AND financial_input_hash IS NOT NULL)",
            name="ck_financial_assessments_input_binding",
        ),
        CheckConstraint(
            "financial_input_hash IS NULL OR financial_input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_financial_assessments_financial_input_hash",
        ),
        CheckConstraint(
            "financial_input_version IS NULL OR financial_input_version >= 1",
            name="ck_financial_assessments_financial_input_version",
        ),
        Index(
            "ix_financial_assessments_history",
            "tenant_id",
            "company_id",
            "project_intake_id",
            "created_at",
        ),
        Index(
            "ix_financial_assessments_decision",
            "tenant_id",
            "company_id",
            "decision_case_id",
            "decision_version_id",
        ),
        Index(
            "ix_financial_assessments_capacity",
            "tenant_id",
            "company_id",
            "capacity_assessment_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intake_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    financial_input_case_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    financial_input_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    financial_input_version: Mapped[int | None] = mapped_column(Integer)
    financial_input_hash: Mapped[str | None] = mapped_column(String(64))
    capacity_assessment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    capacity_assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capacity_assessment_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capacity_assessment_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_version: Mapped[int] = mapped_column(Integer, nullable=False)
    decision_outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_approval_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_approval_outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    decision_approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    formula_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    approved_policy_id: Mapped[str | None] = mapped_column(String(64))
    approved_policy_version: Mapped[str | None] = mapped_column(String(64))
    currency_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    financial_verdict: Mapped[str] = mapped_column(String(48), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
