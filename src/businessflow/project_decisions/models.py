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
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class ProjectDecisionCase(Base):
    """One company-scoped, append-only decision stream for a project intake."""

    __tablename__ = "project_decision_cases"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_decision_cases_scope_id"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "project_intake_id",
            name="uq_project_decision_cases_case_project",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            name="uq_project_decision_cases_intake",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "id", "current_version", "current_decision_id"],
            [
                "project_decision_versions.tenant_id",
                "project_decision_versions.company_id",
                "project_decision_versions.decision_case_id",
                "project_decision_versions.version",
                "project_decision_versions.id",
            ],
            name="fk_project_decision_cases_current_version",
            deferrable=True,
            initially="DEFERRED",
            use_alter=True,
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_decision_cases_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_decision_cases_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_decision_cases_intake",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_cases_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("current_version >= 1", name="ck_project_decision_cases_version"),
        Index(
            "ix_project_decision_cases_company_updated",
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
    current_decision_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectDecisionVersion(Base):
    """Immutable human decision submission bound to exact M6/M5 evidence."""

    __tablename__ = "project_decision_versions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "version",
            name="uq_project_decision_versions_version",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "id",
            name="uq_project_decision_versions_case_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "version",
            "id",
            name="uq_project_decision_versions_current_ref",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "project_intake_id"],
            [
                "project_decision_cases.tenant_id",
                "project_decision_cases.company_id",
                "project_decision_cases.id",
                "project_decision_cases.project_intake_id",
            ],
            name="fk_project_decision_versions_case",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id", "assessment_id"],
            [
                "capacity_assessments.tenant_id",
                "capacity_assessments.company_id",
                "capacity_assessments.project_intake_id",
                "capacity_assessments.id",
            ],
            name="fk_project_decision_versions_assessment",
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
            name="fk_project_decision_versions_intake_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_project_decision_versions_metric_run",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "supersedes_decision_id"],
            [
                "project_decision_versions.tenant_id",
                "project_decision_versions.company_id",
                "project_decision_versions.decision_case_id",
                "project_decision_versions.id",
            ],
            name="fk_project_decision_versions_supersedes",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "information_response_to_id"],
            [
                "project_decision_versions.tenant_id",
                "project_decision_versions.company_id",
                "project_decision_versions.decision_case_id",
                "project_decision_versions.id",
            ],
            name="fk_project_decision_versions_information_response",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_project_decision_versions_version"),
        CheckConstraint(
            "outcome IN ('proceed_to_financial_evaluation', 'hold', 'request_information', "
            "'capacity_declined')",
            name="ck_project_decision_versions_outcome",
        ),
        CheckConstraint(
            "capacity_status IN ('feasible', 'feasible_with_conditions', "
            "'not_feasible', 'incomplete')",
            name="ck_project_decision_versions_capacity_status",
        ),
        CheckConstraint(
            "business_feasibility_status = 'incomplete'",
            name="ck_project_decision_versions_business_status",
        ),
        CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 4000",
            name="ck_project_decision_versions_rationale",
        ),
        CheckConstraint(
            "jsonb_typeof(conditions) = 'array' AND jsonb_typeof(requested_information) = 'array'",
            name="ck_project_decision_versions_lists",
        ),
        CheckConstraint(
            "(outcome <> 'hold' OR jsonb_array_length(conditions) > 0) AND "
            "(outcome = 'request_information' OR "
            "jsonb_array_length(requested_information) = 0) AND "
            "(outcome <> 'request_information' OR "
            "jsonb_array_length(requested_information) > 0)",
            name="ck_project_decision_versions_outcome_details",
        ),
        CheckConstraint(
            "(outcome <> 'proceed_to_financial_evaluation' OR "
            "capacity_status IN ('feasible', 'feasible_with_conditions')) AND "
            "(outcome <> 'capacity_declined' OR capacity_status = 'not_feasible') AND "
            "(outcome <> 'proceed_to_financial_evaluation' OR "
            "capacity_status <> 'feasible_with_conditions' OR jsonb_array_length(conditions) > 0)",
            name="ck_project_decision_versions_capacity_outcome",
        ),
        CheckConstraint(
            "(information_response IS NULL AND information_response_to_id IS NULL) OR "
            "(information_response IS NOT NULL AND "
            "information_response_to_id = supersedes_decision_id "
            "AND length(btrim(information_response)) BETWEEN 1 AND 4000)",
            name="ck_project_decision_versions_information_response",
        ),
        CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64 AND "
            "length(metric_catalog_version) BETWEEN 1 AND 64 AND "
            "length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_project_decision_versions_source_versions",
        ),
        CheckConstraint(
            "intake_version >= 1 AND "
            "intake_version_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_input_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_result_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_input_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_result_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_hash ~ '^[0-9a-f]{64}$' AND decision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_decision_versions_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_decision_versions_snapshot"
        ),
        Index(
            "ix_project_decision_versions_history",
            "tenant_id",
            "company_id",
            "decision_case_id",
            "version",
        ),
        Index(
            "ix_project_decision_versions_assessment",
            "tenant_id",
            "company_id",
            "project_intake_id",
            "assessment_id",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_decision_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    information_response_to_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    assessment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intake_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    capacity_status: Mapped[str] = mapped_column(String(32), nullable=False)
    business_feasibility_status: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(48), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    conditions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    requested_information: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    information_response: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ProjectDecisionApproval(Base):
    """Immutable review outcome bound to one exact submitted decision and evidence hash."""

    __tablename__ = "project_decision_approvals"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_version_id",
            name="uq_project_decision_approvals_decision",
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_decision_approvals_scope_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_case_id", "decision_version_id"],
            [
                "project_decision_versions.tenant_id",
                "project_decision_versions.company_id",
                "project_decision_versions.decision_case_id",
                "project_decision_versions.id",
            ],
            name="fk_project_decision_approvals_decision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id", "assessment_id"],
            [
                "capacity_assessments.tenant_id",
                "capacity_assessments.company_id",
                "capacity_assessments.project_intake_id",
                "capacity_assessments.id",
            ],
            name="fk_project_decision_approvals_assessment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_project_decision_approvals_metric_run",
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
            name="fk_project_decision_approvals_intake_version",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "approver_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_approvals_approver_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "outcome IN ('approved', 'changes_requested')",
            name="ck_project_decision_approvals_outcome",
        ),
        CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 4000",
            name="ck_project_decision_approvals_rationale",
        ),
        CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64 AND "
            "length(metric_catalog_version) BETWEEN 1 AND 64 AND "
            "length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_project_decision_approvals_source_versions",
        ),
        CheckConstraint(
            "intake_version >= 1 AND "
            "intake_version_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND evidence_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_input_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_result_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_input_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_result_hash ~ '^[0-9a-f]{64}$' AND approval_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_decision_approvals_hashes",
        ),
        CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_decision_approvals_snapshot"
        ),
        Index(
            "ix_project_decision_approvals_case_created",
            "tenant_id",
            "company_id",
            "decision_case_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_case_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    decision_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_intake_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    assessment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    intake_version: Mapped[int] = mapped_column(Integer, nullable=False)
    intake_version_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_version: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    assessment_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_catalog_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    decision_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    outcome: Mapped[str] = mapped_column(String(24), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    approver_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    approval_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
