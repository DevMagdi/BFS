"""Add immutable, evidence-bound human project decisions and approvals.

Revision ID: 0006_decision_workflow
Revises: 0005_capacity_assessment
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006_decision_workflow"
down_revision = "0005_capacity_assessment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF current_user <> 'bf_migrator' THEN
            RAISE EXCEPTION 'migrations must run as the dedicated bf_migrator role';
          END IF;
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'required non-privileged role bf_runtime does not exist';
          END IF;
          IF (SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = 'bf_runtime') THEN
            RAISE EXCEPTION 'bf_runtime must not be a superuser or bypass RLS';
          END IF;
          IF pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
        END;
        $$;
        """
    )

    # M7's composite evidence FK ensures that an assessment belongs to the same intake
    # as the immutable decision version that references it.
    op.create_unique_constraint(
        "uq_capacity_assessments_project_ref",
        "capacity_assessments",
        ["tenant_id", "company_id", "project_intake_id", "id"],
    )

    op.create_table(
        "project_decision_cases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("current_decision_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_project_decision_cases_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_decision_cases_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_decision_cases_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_decision_cases_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_cases_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_decision_cases"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_decision_cases_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "project_intake_id",
            name="uq_project_decision_cases_case_project",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            name="uq_project_decision_cases_intake",
        ),
    )
    op.create_index(
        "ix_project_decision_cases_company_updated",
        "project_decision_cases",
        ["tenant_id", "company_id", "updated_at"],
    )

    op.create_table(
        "project_decision_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_decision_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("information_response_to_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_version", sa.Integer(), nullable=False),
        sa.Column("intake_version_hash", sa.String(length=64), nullable=False),
        sa.Column("assessment_version", sa.String(length=64), nullable=False),
        sa.Column("assessment_input_hash", sa.String(length=64), nullable=False),
        sa.Column("assessment_result_hash", sa.String(length=64), nullable=False),
        sa.Column("capacity_status", sa.String(length=32), nullable=False),
        sa.Column("business_feasibility_status", sa.String(length=16), nullable=False),
        sa.Column("metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("metric_calculation_version", sa.String(length=64), nullable=False),
        sa.Column("metric_input_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_result_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=48), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("conditions", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("requested_information", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("information_response", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_project_decision_versions_version"),
        sa.CheckConstraint(
            "outcome IN ('proceed_to_financial_evaluation', 'hold', 'request_information', "
            "'capacity_declined')",
            name="ck_project_decision_versions_outcome",
        ),
        sa.CheckConstraint(
            "capacity_status IN ('feasible', 'feasible_with_conditions', "
            "'not_feasible', 'incomplete')",
            name="ck_project_decision_versions_capacity_status",
        ),
        sa.CheckConstraint(
            "business_feasibility_status = 'incomplete'",
            name="ck_project_decision_versions_business_status",
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 4000",
            name="ck_project_decision_versions_rationale",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(conditions) = 'array' AND jsonb_typeof(requested_information) = 'array'",
            name="ck_project_decision_versions_lists",
        ),
        sa.CheckConstraint(
            "(outcome <> 'hold' OR jsonb_array_length(conditions) > 0) AND "
            "(outcome = 'request_information' OR "
            "jsonb_array_length(requested_information) = 0) AND "
            "(outcome <> 'request_information' OR "
            "jsonb_array_length(requested_information) > 0)",
            name="ck_project_decision_versions_outcome_details",
        ),
        sa.CheckConstraint(
            "(outcome <> 'proceed_to_financial_evaluation' OR "
            "capacity_status IN ('feasible', 'feasible_with_conditions')) AND "
            "(outcome <> 'capacity_declined' OR capacity_status = 'not_feasible') AND "
            "(outcome <> 'proceed_to_financial_evaluation' OR "
            "capacity_status <> 'feasible_with_conditions' OR jsonb_array_length(conditions) > 0)",
            name="ck_project_decision_versions_capacity_outcome",
        ),
        sa.CheckConstraint(
            "(information_response IS NULL AND information_response_to_id IS NULL) OR "
            "(information_response IS NOT NULL AND "
            "information_response_to_id = supersedes_decision_id "
            "AND length(btrim(information_response)) BETWEEN 1 AND 4000)",
            name="ck_project_decision_versions_information_response",
        ),
        sa.CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64 AND "
            "length(metric_catalog_version) BETWEEN 1 AND 64 AND "
            "length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_project_decision_versions_source_versions",
        ),
        sa.CheckConstraint(
            "intake_version >= 1 AND "
            "intake_version_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_input_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_result_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_input_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_result_hash ~ '^[0-9a-f]{64}$' AND "
            "evidence_hash ~ '^[0-9a-f]{64}$' AND decision_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_decision_versions_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_decision_versions_snapshot"
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_project_decision_versions_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id", name="pk_project_decision_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "version",
            name="uq_project_decision_versions_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "id",
            name="uq_project_decision_versions_case_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_case_id",
            "version",
            "id",
            name="uq_project_decision_versions_current_ref",
        ),
    )
    op.create_index(
        "ix_project_decision_versions_history",
        "project_decision_versions",
        ["tenant_id", "company_id", "decision_case_id", "version"],
    )
    op.create_index(
        "ix_project_decision_versions_assessment",
        "project_decision_versions",
        ["tenant_id", "company_id", "project_intake_id", "assessment_id"],
    )

    op.create_table(
        "project_decision_approvals",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_version", sa.Integer(), nullable=False),
        sa.Column("intake_version_hash", sa.String(length=64), nullable=False),
        sa.Column("assessment_version", sa.String(length=64), nullable=False),
        sa.Column("assessment_input_hash", sa.String(length=64), nullable=False),
        sa.Column("assessment_result_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("metric_calculation_version", sa.String(length=64), nullable=False),
        sa.Column("metric_input_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_result_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("approver_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("approval_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('approved', 'changes_requested')",
            name="ck_project_decision_approvals_outcome",
        ),
        sa.CheckConstraint(
            "length(btrim(rationale)) BETWEEN 10 AND 4000",
            name="ck_project_decision_approvals_rationale",
        ),
        sa.CheckConstraint(
            "length(assessment_version) BETWEEN 1 AND 64 AND "
            "length(metric_catalog_version) BETWEEN 1 AND 64 AND "
            "length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_project_decision_approvals_source_versions",
        ),
        sa.CheckConstraint(
            "intake_version >= 1 AND "
            "intake_version_hash ~ '^[0-9a-f]{64}$' AND "
            "decision_hash ~ '^[0-9a-f]{64}$' AND evidence_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_input_hash ~ '^[0-9a-f]{64}$' AND "
            "assessment_result_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_input_hash ~ '^[0-9a-f]{64}$' AND "
            "metric_result_hash ~ '^[0-9a-f]{64}$' AND approval_hash ~ '^[0-9a-f]{64}$'",
            name="ck_project_decision_approvals_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_project_decision_approvals_snapshot"
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_project_decision_approvals_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "approver_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_decision_approvals_approver_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_decision_approvals"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "decision_version_id",
            name="uq_project_decision_approvals_decision",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            name="uq_project_decision_approvals_scope_id",
        ),
    )
    op.create_index(
        "ix_project_decision_approvals_case_created",
        "project_decision_approvals",
        ["tenant_id", "company_id", "decision_case_id", "created_at"],
    )
    op.create_foreign_key(
        "fk_project_decision_cases_current_version",
        "project_decision_cases",
        "project_decision_versions",
        ["tenant_id", "company_id", "id", "current_version", "current_decision_id"],
        ["tenant_id", "company_id", "decision_case_id", "version", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    for table in (
        "project_decision_cases",
        "project_decision_versions",
        "project_decision_approvals",
    ):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_company_context ON {table} "
                "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
                "AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid) "
                "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid "
                "AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
            )
        )

    op.execute(
        """
        CREATE FUNCTION businessflow_m7_decision_case_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.current_version <> 1 THEN
            RAISE EXCEPTION 'a decision case must begin at version one'
              USING ERRCODE = '55000', CONSTRAINT = 'project_decision_case_version_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m7_decision_case_update_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.company_id <> OLD.company_id
             OR NEW.project_intake_id <> OLD.project_intake_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
             OR NEW.current_version <> OLD.current_version + 1 THEN
            RAISE EXCEPTION 'decision case updates must advance exactly one immutable version'
              USING ERRCODE = '55000', CONSTRAINT = 'project_decision_case_version_guard';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM project_decision_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND decision_case_id = NEW.id AND version = NEW.current_version
              AND id = NEW.current_decision_id
              AND supersedes_decision_id = OLD.current_decision_id
          ) THEN
            RAISE EXCEPTION 'decision case pointer must identify its next superseding version'
              USING ERRCODE = '55000', CONSTRAINT = 'project_decision_case_pointer_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m7_decision_version_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          case_version integer;
          case_decision_id uuid;
          case_intake_id uuid;
          prior_outcome varchar(48);
          assessment_row capacity_assessments%ROWTYPE;
          intake_hash varchar(64);
        BEGIN
          SELECT current_version, current_decision_id, project_intake_id
            INTO case_version, case_decision_id, case_intake_id
            FROM project_decision_cases
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.decision_case_id
            FOR UPDATE;
          IF NOT FOUND OR case_intake_id <> NEW.project_intake_id THEN
            RAISE EXCEPTION 'decision version does not match a company-scoped decision case'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_project_decision_versions_case';
          END IF;
          IF NEW.version = 1 THEN
            IF case_version <> 1 OR case_decision_id <> NEW.id
               OR NEW.supersedes_decision_id IS NOT NULL THEN
              RAISE EXCEPTION 'first decision version must be the case head'
                USING ERRCODE = '55000', CONSTRAINT = 'project_decision_version_chain_guard';
            END IF;
          ELSIF NEW.version <> case_version + 1
             OR NEW.supersedes_decision_id IS DISTINCT FROM case_decision_id THEN
            RAISE EXCEPTION 'decision versions must append to the current case head'
              USING ERRCODE = '55000', CONSTRAINT = 'project_decision_version_chain_guard';
          END IF;

          IF NEW.supersedes_decision_id IS NOT NULL THEN
            SELECT outcome INTO prior_outcome
              FROM project_decision_versions
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                AND decision_case_id = NEW.decision_case_id
                AND id = NEW.supersedes_decision_id;
            IF NEW.information_response_to_id IS NOT NULL
               AND prior_outcome <> 'request_information' THEN
              RAISE EXCEPTION 'information responses must answer an open information request'
                USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_decision_versions_information_response';
            END IF;
            IF prior_outcome = 'request_information'
               AND NEW.information_response_to_id IS NULL THEN
              RAISE EXCEPTION 'a superseding version must record its information response'
                USING ERRCODE = '23514',
                  CONSTRAINT = 'ck_project_decision_versions_information_response';
            END IF;
          ELSIF NEW.information_response_to_id IS NOT NULL THEN
            RAISE EXCEPTION 'an initial decision cannot respond to an earlier request'
              USING ERRCODE = '23514',
                CONSTRAINT = 'ck_project_decision_versions_information_response';
          END IF;

          SELECT * INTO assessment_row
            FROM capacity_assessments
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND project_intake_id = NEW.project_intake_id AND id = NEW.assessment_id;
          IF NOT FOUND OR assessment_row.intake_version <> NEW.intake_version
             OR assessment_row.assessment_version <> NEW.assessment_version
             OR assessment_row.input_hash <> NEW.assessment_input_hash
             OR assessment_row.result_hash <> NEW.assessment_result_hash
             OR assessment_row.capacity_status <> NEW.capacity_status
             OR assessment_row.business_feasibility_status <> NEW.business_feasibility_status
             OR assessment_row.metric_run_id <> NEW.metric_run_id
             OR assessment_row.metric_catalog_version <> NEW.metric_catalog_version
             OR assessment_row.metric_calculation_version <> NEW.metric_calculation_version
             OR assessment_row.metric_input_hash <> NEW.metric_input_hash
             OR assessment_row.metric_result_hash <> NEW.metric_result_hash THEN
            RAISE EXCEPTION 'decision evidence must match its exact capacity assessment'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_project_decision_versions_assessment';
          END IF;
          SELECT content_hash INTO intake_hash FROM project_intake_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND project_intake_id = NEW.project_intake_id AND version = NEW.intake_version;
          IF NOT FOUND OR intake_hash <> NEW.intake_version_hash THEN
            RAISE EXCEPTION 'decision intake hash must match its exact immutable version'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_project_decision_versions_intake_version';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m7_decision_approval_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          decision_row project_decision_versions%ROWTYPE;
          current_id uuid;
        BEGIN
          SELECT * INTO decision_row FROM project_decision_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND decision_case_id = NEW.decision_case_id AND id = NEW.decision_version_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'approval must reference an existing decision version'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_project_decision_approvals_decision';
          END IF;
          SELECT current_decision_id INTO current_id FROM project_decision_cases
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.decision_case_id FOR UPDATE;
          IF NOT FOUND OR current_id <> NEW.decision_version_id THEN
            RAISE EXCEPTION 'only the current decision version may be reviewed'
              USING ERRCODE = '55000', CONSTRAINT = 'project_decision_approval_current_guard';
          END IF;
          IF decision_row.created_by_user_id = NEW.approver_user_id THEN
            RAISE EXCEPTION 'decision submitter cannot approve the same version'
              USING ERRCODE = '23514', CONSTRAINT = 'project_decision_separation_of_duties';
          END IF;
          IF NEW.project_intake_id IS DISTINCT FROM decision_row.project_intake_id
             OR NEW.assessment_id IS DISTINCT FROM decision_row.assessment_id
             OR NEW.intake_version IS DISTINCT FROM decision_row.intake_version
             OR NEW.intake_version_hash IS DISTINCT FROM decision_row.intake_version_hash
             OR NEW.assessment_version IS DISTINCT FROM decision_row.assessment_version
             OR NEW.assessment_input_hash IS DISTINCT FROM decision_row.assessment_input_hash
             OR NEW.assessment_result_hash IS DISTINCT FROM decision_row.assessment_result_hash
             OR NEW.metric_run_id IS DISTINCT FROM decision_row.metric_run_id
             OR NEW.metric_catalog_version IS DISTINCT FROM decision_row.metric_catalog_version
             OR NEW.metric_calculation_version IS DISTINCT FROM
                decision_row.metric_calculation_version
             OR NEW.metric_input_hash IS DISTINCT FROM decision_row.metric_input_hash
             OR NEW.metric_result_hash IS DISTINCT FROM decision_row.metric_result_hash
             OR NEW.decision_hash IS DISTINCT FROM decision_row.decision_hash
             OR NEW.evidence_hash IS DISTINCT FROM decision_row.evidence_hash THEN
            RAISE EXCEPTION 'approval must bind to the exact decision and assessment evidence'
              USING ERRCODE = '23514', CONSTRAINT = 'project_decision_approval_binding_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m7_lock_assessment_intake() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          PERFORM 1 FROM project_intakes
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.project_intake_id FOR UPDATE;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'capacity assessment intake was not found'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_capacity_assessments_intake';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m7_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'decision versions and approvals are immutable; append a new version'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m7_append_only';
        END;
        $$;

        CREATE TRIGGER trg_project_decision_cases_insert_guard
          BEFORE INSERT ON project_decision_cases
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_decision_case_insert_guard();
        CREATE TRIGGER trg_project_decision_cases_update_guard
          BEFORE UPDATE ON project_decision_cases
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_decision_case_update_guard();
        CREATE TRIGGER trg_project_decision_versions_insert_guard
          BEFORE INSERT ON project_decision_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_decision_version_insert_guard();
        CREATE TRIGGER trg_project_decision_approvals_insert_guard
          BEFORE INSERT ON project_decision_approvals
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_decision_approval_insert_guard();
        CREATE TRIGGER trg_project_decision_versions_append_only
          BEFORE UPDATE OR DELETE ON project_decision_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_append_only();
        CREATE TRIGGER trg_project_decision_approvals_append_only
          BEFORE UPDATE OR DELETE ON project_decision_approvals
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_append_only();
        CREATE TRIGGER trg_capacity_assessments_lock_intake
          BEFORE INSERT ON capacity_assessments
          FOR EACH ROW EXECUTE FUNCTION businessflow_m7_lock_assessment_intake();
        """
    )
    op.execute(
        """
        GRANT SELECT, INSERT ON project_decision_cases TO bf_runtime;
        GRANT UPDATE (current_version, current_decision_id, updated_at)
          ON project_decision_cases TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON project_decision_cases FROM bf_runtime;
        GRANT SELECT, INSERT ON project_decision_versions, project_decision_approvals TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON project_decision_versions,
          project_decision_approvals FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_capacity_assessments_lock_intake ON capacity_assessments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_approvals_append_only "
        "ON project_decision_approvals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_versions_append_only "
        "ON project_decision_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_approvals_insert_guard "
        "ON project_decision_approvals"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_versions_insert_guard "
        "ON project_decision_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_cases_update_guard ON project_decision_cases"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_decision_cases_insert_guard ON project_decision_cases"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_lock_assessment_intake()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_append_only()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_decision_approval_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_decision_version_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_decision_case_update_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m7_decision_case_insert_guard()")
    op.drop_constraint(
        "fk_project_decision_cases_current_version",
        "project_decision_cases",
        type_="foreignkey",
    )
    op.drop_table("project_decision_approvals")
    op.drop_table("project_decision_versions")
    op.drop_table("project_decision_cases")
    op.drop_constraint(
        "uq_capacity_assessments_project_ref",
        "capacity_assessments",
        type_="unique",
    )
