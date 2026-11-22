"""Add versioned financial inputs and immutable, policy-gated assessments.

Revision ID: 0007_financial_feasibility
Revises: 0006_decision_workflow
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007_financial_feasibility"
down_revision = "0006_decision_workflow"
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
          IF EXISTS (
            SELECT 1 FROM pg_database
            WHERE datname = current_database()
              AND datdba = (SELECT oid FROM pg_roles WHERE rolname = 'bf_runtime')
          ) THEN
            RAISE EXCEPTION 'bf_runtime must not own the application database';
          END IF;
        END;
        $$;
        """
    )

    op.create_table(
        "project_financial_input_cases",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("current_version", sa.Integer(), nullable=False),
        sa.Column("current_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("current_version >= 1", name="ck_project_financial_input_cases_version"),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_project_financial_input_cases_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_project_financial_input_cases_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_project_financial_input_cases_intake",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_financial_input_cases_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_project_financial_input_cases"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_project_financial_input_cases_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "project_intake_id",
            name="uq_project_financial_input_cases_case_project",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            name="uq_project_financial_input_cases_intake",
        ),
    )
    op.create_index(
        "ix_project_financial_input_cases_company_updated",
        "project_financial_input_cases",
        ["tenant_id", "company_id", "updated_at"],
    )

    op.create_table(
        "project_financial_input_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("input_case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("intake_version", sa.Integer(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_project_financial_input_versions_version"),
        sa.CheckConstraint(
            "intake_version >= 1", name="ck_project_financial_input_versions_intake_version"
        ),
        sa.CheckConstraint(
            "content_hash ~ '^[0-9a-f]{64}$'", name="ck_project_financial_input_versions_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_project_financial_input_versions_object",
        ),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_version_id IS NULL) OR "
            "(version > 1 AND supersedes_version_id IS NOT NULL)",
            name="ck_project_financial_input_versions_chain",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_project_financial_input_versions_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.PrimaryKeyConstraint("id", name="pk_project_financial_input_versions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "version",
            name="uq_project_financial_input_versions_version",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "id",
            name="uq_project_financial_input_versions_id",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_project_financial_input_versions_project_ref",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "input_case_id",
            "version",
            "id",
            name="uq_project_financial_input_versions_current_ref",
        ),
    )
    op.create_index(
        "ix_project_financial_input_versions_history",
        "project_financial_input_versions",
        ["tenant_id", "company_id", "project_intake_id", "version"],
    )
    op.create_foreign_key(
        "fk_project_financial_input_cases_current_version",
        "project_financial_input_cases",
        "project_financial_input_versions",
        ["tenant_id", "company_id", "id", "current_version", "current_version_id"],
        ["tenant_id", "company_id", "input_case_id", "version", "id"],
        deferrable=True,
        initially="DEFERRED",
    )

    op.create_table(
        "financial_assessments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_intake_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("requested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("intake_version", sa.Integer(), nullable=False),
        sa.Column("intake_version_hash", sa.String(length=64), nullable=False),
        sa.Column("financial_input_case_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("financial_input_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("financial_input_version", sa.Integer(), nullable=True),
        sa.Column("financial_input_hash", sa.String(length=64), nullable=True),
        sa.Column("capacity_assessment_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("capacity_assessment_version", sa.String(length=64), nullable=False),
        sa.Column("capacity_assessment_input_hash", sa.String(length=64), nullable=False),
        sa.Column("capacity_assessment_result_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_run_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("metric_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("metric_calculation_version", sa.String(length=64), nullable=False),
        sa.Column("metric_input_hash", sa.String(length=64), nullable=False),
        sa.Column("metric_result_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_case_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_version", sa.Integer(), nullable=False),
        sa.Column("decision_outcome", sa.String(length=48), nullable=False),
        sa.Column("decision_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_evidence_hash", sa.String(length=64), nullable=False),
        sa.Column("decision_approval_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("decision_approval_outcome", sa.String(length=24), nullable=False),
        sa.Column("decision_approval_hash", sa.String(length=64), nullable=False),
        sa.Column("formula_catalog_version", sa.String(length=64), nullable=False),
        sa.Column("approved_policy_id", sa.String(length=64), nullable=True),
        sa.Column("approved_policy_version", sa.String(length=64), nullable=True),
        sa.Column("currency_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("financial_verdict", sa.String(length=48), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("result_hash", sa.String(length=64), nullable=False),
        sa.Column("input_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("result_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "financial_verdict IN ('financially_feasible', 'financially_feasible_with_conditions', "
            "'financially_not_feasible', 'incomplete')",
            name="ck_financial_assessments_verdict",
        ),
        sa.CheckConstraint(
            "financial_verdict = 'incomplete' AND approved_policy_id IS NULL "
            "AND approved_policy_version IS NULL",
            name="ck_financial_assessments_policy_gate",
        ),
        sa.CheckConstraint(
            "decision_outcome = 'proceed_to_financial_evaluation' "
            "AND decision_approval_outcome = 'approved'",
            name="ck_financial_assessments_handoff",
        ),
        sa.CheckConstraint(
            "intake_version >= 1 AND decision_version >= 1",
            name="ck_financial_assessments_versions",
        ),
        sa.CheckConstraint(
            "length(formula_catalog_version) BETWEEN 1 AND 64 "
            "AND length(capacity_assessment_version) BETWEEN 1 AND 64 "
            "AND length(metric_catalog_version) BETWEEN 1 AND 64 "
            "AND length(metric_calculation_version) BETWEEN 1 AND 64",
            name="ck_financial_assessments_catalog_versions",
        ),
        sa.CheckConstraint(
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
        sa.CheckConstraint(
            "jsonb_typeof(currency_codes) = 'array'",
            name="ck_financial_assessments_currency_codes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'",
            name="ck_financial_assessments_input_object",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'",
            name="ck_financial_assessments_result_object",
        ),
        sa.CheckConstraint(
            "(financial_input_case_id IS NULL AND financial_input_version_id IS NULL "
            "AND financial_input_version IS NULL AND financial_input_hash IS NULL) OR "
            "(financial_input_case_id IS NOT NULL AND financial_input_version_id IS NOT NULL "
            "AND financial_input_version IS NOT NULL AND financial_input_hash IS NOT NULL)",
            name="ck_financial_assessments_input_binding",
        ),
        sa.CheckConstraint(
            "financial_input_hash IS NULL OR financial_input_hash ~ '^[0-9a-f]{64}$'",
            name="ck_financial_assessments_financial_input_hash",
        ),
        sa.CheckConstraint(
            "financial_input_version IS NULL OR financial_input_version >= 1",
            name="ck_financial_assessments_financial_input_version",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_financial_assessments_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_financial_assessments_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_intake_id"],
            ["project_intakes.tenant_id", "project_intakes.company_id", "project_intakes.id"],
            name="fk_financial_assessments_intake",
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
            name="fk_financial_assessments_intake_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_run_id"],
            ["metric_runs.tenant_id", "metric_runs.company_id", "metric_runs.id"],
            name="fk_financial_assessments_metric_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "decision_approval_id"],
            [
                "project_decision_approvals.tenant_id",
                "project_decision_approvals.company_id",
                "project_decision_approvals.id",
            ],
            name="fk_financial_assessments_decision_approval",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
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
        sa.ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_financial_assessments_requester_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_financial_assessments"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_financial_assessments_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "project_intake_id",
            "id",
            name="uq_financial_assessments_project_ref",
        ),
    )
    op.create_index(
        "ix_financial_assessments_history",
        "financial_assessments",
        ["tenant_id", "company_id", "project_intake_id", "created_at"],
    )
    op.create_index(
        "ix_financial_assessments_decision",
        "financial_assessments",
        ["tenant_id", "company_id", "decision_case_id", "decision_version_id"],
    )
    op.create_index(
        "ix_financial_assessments_capacity",
        "financial_assessments",
        ["tenant_id", "company_id", "capacity_assessment_id"],
    )

    for table in (
        "project_financial_input_cases",
        "project_financial_input_versions",
        "financial_assessments",
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
        CREATE FUNCTION businessflow_m8_financial_input_case_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.current_version <> 1 OR NEW.current_version_id IS NULL THEN
            RAISE EXCEPTION 'a financial input case must begin at version one'
              USING ERRCODE = '55000', CONSTRAINT = 'project_financial_input_case_version_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m8_financial_input_case_update_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id
             OR NEW.company_id <> OLD.company_id
             OR NEW.project_intake_id <> OLD.project_intake_id
             OR NEW.created_by_user_id <> OLD.created_by_user_id
             OR NEW.created_at <> OLD.created_at
             OR NEW.current_version <> OLD.current_version + 1 THEN
            RAISE EXCEPTION 'financial input updates must advance exactly one immutable version'
              USING ERRCODE = '55000', CONSTRAINT = 'project_financial_input_case_version_guard';
          END IF;
          IF NOT EXISTS (
            SELECT 1 FROM project_financial_input_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND input_case_id = NEW.id AND version = NEW.current_version
              AND id = NEW.current_version_id
              AND supersedes_version_id = OLD.current_version_id
          ) THEN
            RAISE EXCEPTION 'financial input pointer must identify its next superseding version'
              USING ERRCODE = '55000', CONSTRAINT = 'project_financial_input_case_pointer_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m8_financial_input_version_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          case_version integer;
          case_version_id uuid;
          case_intake_id uuid;
          intake_version integer;
        BEGIN
          SELECT current_version, current_version_id, project_intake_id
            INTO case_version, case_version_id, case_intake_id
            FROM project_financial_input_cases
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.input_case_id FOR UPDATE;
          IF NOT FOUND OR case_intake_id <> NEW.project_intake_id THEN
            RAISE EXCEPTION 'financial input version does not match its scoped case'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_project_financial_input_versions_case';
          END IF;
          IF NEW.version = 1 THEN
            IF case_version <> 1 OR case_version_id <> NEW.id
               OR NEW.supersedes_version_id IS NOT NULL THEN
              RAISE EXCEPTION 'first financial input version must be the case head'
                USING ERRCODE = '55000',
                  CONSTRAINT = 'project_financial_input_version_chain_guard';
            END IF;
          ELSIF NEW.version <> case_version + 1
             OR NEW.supersedes_version_id IS DISTINCT FROM case_version_id THEN
            RAISE EXCEPTION 'financial input versions must append to the current case head'
              USING ERRCODE = '55000',
                CONSTRAINT = 'project_financial_input_version_chain_guard';
          END IF;
          SELECT current_version INTO intake_version FROM project_intakes
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.project_intake_id FOR UPDATE;
          IF NOT FOUND OR intake_version <> NEW.intake_version THEN
            RAISE EXCEPTION 'financial input version must bind to the current intake version'
              USING ERRCODE = '23503',
                CONSTRAINT = 'fk_project_financial_input_versions_intake_version';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m8_assessment_insert_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          intake_version integer;
          intake_hash varchar(64);
          input_version project_financial_input_versions%ROWTYPE;
          capacity_row capacity_assessments%ROWTYPE;
          metric_row metric_runs%ROWTYPE;
          decision_case project_decision_cases%ROWTYPE;
          decision_row project_decision_versions%ROWTYPE;
          approval_row project_decision_approvals%ROWTYPE;
        BEGIN
          SELECT current_version INTO intake_version FROM project_intakes
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.project_intake_id FOR UPDATE;
          IF NOT FOUND OR intake_version <> NEW.intake_version THEN
            RAISE EXCEPTION 'financial assessment must bind to the current intake version'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_intake_version';
          END IF;
          SELECT content_hash INTO intake_hash FROM project_intake_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND project_intake_id = NEW.project_intake_id AND version = NEW.intake_version;
          IF NOT FOUND OR intake_hash <> NEW.intake_version_hash THEN
            RAISE EXCEPTION 'financial assessment intake hash must match its exact version'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_intake_version';
          END IF;

          SELECT * INTO capacity_row FROM capacity_assessments
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND project_intake_id = NEW.project_intake_id AND id = NEW.capacity_assessment_id;
          IF NOT FOUND
             OR capacity_row.intake_version <> NEW.intake_version
             OR capacity_row.assessment_version <> NEW.capacity_assessment_version
             OR capacity_row.input_hash <> NEW.capacity_assessment_input_hash
             OR capacity_row.result_hash <> NEW.capacity_assessment_result_hash
             OR capacity_row.metric_run_id <> NEW.metric_run_id
             OR capacity_row.metric_catalog_version <> NEW.metric_catalog_version
             OR capacity_row.metric_calculation_version <> NEW.metric_calculation_version
             OR capacity_row.metric_input_hash <> NEW.metric_input_hash
             OR capacity_row.metric_result_hash <> NEW.metric_result_hash THEN
            RAISE EXCEPTION 'financial assessment must bind to exact M6/M5 evidence'
              USING ERRCODE = '23503',
                CONSTRAINT = 'fk_financial_assessments_capacity_assessment';
          END IF;
          SELECT * INTO metric_row FROM metric_runs
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.metric_run_id;
          IF NOT FOUND
             OR metric_row.metric_catalog_version <> NEW.metric_catalog_version
             OR metric_row.calculation_version <> NEW.metric_calculation_version
             OR metric_row.input_hash <> NEW.metric_input_hash
             OR metric_row.result_hash <> NEW.metric_result_hash THEN
            RAISE EXCEPTION 'financial assessment metric hashes must match the exact M5 run'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_metric_run';
          END IF;

          SELECT * INTO decision_case FROM project_decision_cases
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND id = NEW.decision_case_id FOR UPDATE;
          IF NOT FOUND OR decision_case.project_intake_id <> NEW.project_intake_id
             OR decision_case.current_decision_id <> NEW.decision_version_id
             OR decision_case.current_version <> NEW.decision_version THEN
            RAISE EXCEPTION 'financial assessment requires the current M7 decision version'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_decision_version';
          END IF;
          SELECT * INTO decision_row FROM project_decision_versions
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND decision_case_id = NEW.decision_case_id AND id = NEW.decision_version_id;
          IF NOT FOUND
             OR decision_row.version <> NEW.decision_version
             OR decision_row.outcome <> NEW.decision_outcome
             OR decision_row.outcome <> 'proceed_to_financial_evaluation'
             OR decision_row.assessment_id <> NEW.capacity_assessment_id
             OR decision_row.assessment_version <> NEW.capacity_assessment_version
             OR decision_row.assessment_input_hash <> NEW.capacity_assessment_input_hash
             OR decision_row.assessment_result_hash <> NEW.capacity_assessment_result_hash
             OR decision_row.metric_run_id <> NEW.metric_run_id
             OR decision_row.metric_catalog_version <> NEW.metric_catalog_version
             OR decision_row.metric_calculation_version <> NEW.metric_calculation_version
             OR decision_row.metric_input_hash <> NEW.metric_input_hash
             OR decision_row.metric_result_hash <> NEW.metric_result_hash
             OR decision_row.decision_hash <> NEW.decision_hash
             OR decision_row.evidence_hash <> NEW.decision_evidence_hash THEN
            RAISE EXCEPTION 'financial assessment must bind to exact approved M7 decision evidence'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_decision_version';
          END IF;
          SELECT * INTO approval_row FROM project_decision_approvals
            WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
              AND decision_case_id = NEW.decision_case_id
              AND decision_version_id = NEW.decision_version_id
              AND id = NEW.decision_approval_id;
          IF NOT FOUND OR approval_row.outcome <> 'approved'
             OR approval_row.outcome <> NEW.decision_approval_outcome
             OR approval_row.approval_hash <> NEW.decision_approval_hash
             OR approval_row.decision_hash <> NEW.decision_hash
             OR approval_row.evidence_hash <> NEW.decision_evidence_hash THEN
            RAISE EXCEPTION 'financial assessment requires the exact approved M7 review'
              USING ERRCODE = '23503', CONSTRAINT = 'fk_financial_assessments_decision_approval';
          END IF;

          IF NEW.financial_input_version_id IS NOT NULL THEN
            SELECT * INTO input_version FROM project_financial_input_versions
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                AND project_intake_id = NEW.project_intake_id
                AND input_case_id = NEW.financial_input_case_id
                AND id = NEW.financial_input_version_id;
            IF NOT FOUND
               OR input_version.version <> NEW.financial_input_version
               OR input_version.content_hash <> NEW.financial_input_hash THEN
              RAISE EXCEPTION 'financial assessment input hash must match its immutable version'
                USING ERRCODE = '23503',
                  CONSTRAINT = 'fk_financial_assessments_financial_input_version';
            END IF;
          END IF;

          IF NEW.input_snapshot->'scope'->>'project_intake_id'
                IS DISTINCT FROM NEW.project_intake_id::text
             OR NEW.input_snapshot->'evidence'->'m5_metric_run'->>'id'
                IS DISTINCT FROM NEW.metric_run_id::text
             OR NEW.input_snapshot->'evidence'->'m6_capacity_assessment'->>'id'
                IS DISTINCT FROM NEW.capacity_assessment_id::text
             OR NEW.input_snapshot->'evidence'->'m7_decision'->>'decision_id'
                IS DISTINCT FROM NEW.decision_version_id::text THEN
            RAISE EXCEPTION 'financial assessment snapshot must identify its exact evidence'
              USING ERRCODE = '23514', CONSTRAINT = 'financial_assessment_snapshot_binding_guard';
          END IF;
          RETURN NEW;
        END;
        $$;

        CREATE FUNCTION businessflow_m8_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'financial input versions and assessments are immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_m8_append_only';
        END;
        $$;

        CREATE TRIGGER trg_project_financial_input_cases_insert_guard
          BEFORE INSERT ON project_financial_input_cases
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_financial_input_case_insert_guard();
        CREATE TRIGGER trg_project_financial_input_cases_update_guard
          BEFORE UPDATE ON project_financial_input_cases
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_financial_input_case_update_guard();
        CREATE TRIGGER trg_project_financial_input_versions_insert_guard
          BEFORE INSERT ON project_financial_input_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_financial_input_version_insert_guard();
        CREATE TRIGGER trg_financial_assessments_insert_guard
          BEFORE INSERT ON financial_assessments
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_assessment_insert_guard();
        CREATE TRIGGER trg_project_financial_input_versions_append_only
          BEFORE UPDATE OR DELETE ON project_financial_input_versions
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_append_only();
        CREATE TRIGGER trg_financial_assessments_append_only
          BEFORE UPDATE OR DELETE ON financial_assessments
          FOR EACH ROW EXECUTE FUNCTION businessflow_m8_append_only();
        """
    )
    op.execute(
        """
        REVOKE ALL ON project_financial_input_cases,
          project_financial_input_versions, financial_assessments FROM PUBLIC, bf_runtime;
        GRANT SELECT, INSERT ON project_financial_input_cases TO bf_runtime;
        GRANT UPDATE (current_version, current_version_id, updated_at)
          ON project_financial_input_cases TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON project_financial_input_cases FROM bf_runtime;
        GRANT SELECT, INSERT ON project_financial_input_versions,
          financial_assessments TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON project_financial_input_versions,
          financial_assessments FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_financial_assessments_append_only ON financial_assessments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_financial_input_versions_append_only "
        "ON project_financial_input_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_financial_assessments_insert_guard ON financial_assessments"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_financial_input_versions_insert_guard "
        "ON project_financial_input_versions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_financial_input_cases_update_guard "
        "ON project_financial_input_cases"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_project_financial_input_cases_insert_guard "
        "ON project_financial_input_cases"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_m8_append_only()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m8_assessment_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m8_financial_input_version_insert_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m8_financial_input_case_update_guard()")
    op.execute("DROP FUNCTION IF EXISTS businessflow_m8_financial_input_case_insert_guard()")
    op.drop_table("financial_assessments")
    op.drop_constraint(
        "fk_project_financial_input_cases_current_version",
        "project_financial_input_cases",
        type_="foreignkey",
    )
    op.drop_index(
        "ix_project_financial_input_versions_history",
        table_name="project_financial_input_versions",
    )
    op.drop_table("project_financial_input_versions")
    op.drop_index(
        "ix_project_financial_input_cases_company_updated",
        table_name="project_financial_input_cases",
    )
    op.drop_table("project_financial_input_cases")
