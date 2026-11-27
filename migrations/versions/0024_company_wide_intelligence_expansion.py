"""Add verified project-manager links and workforce authorization scoping.

Revision ID: 0024_company_wide_intelligence
Revises: 0023_core_financial_guardrails
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0024_company_wide_intelligence"
down_revision = "0023_core_financial_guardrails"
branch_labels = None
depends_on = None


def _require_migrator() -> None:
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


def upgrade() -> None:
    _require_migrator()

    op.drop_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        "relationship_type IN ('customer_has_sales_transaction', 'supplier_has_expense', "
        "'employee_belongs_to_department', 'department_in_business_unit', "
        "'project_assigned_to_employee', 'project_managed_by_employee')",
    )

    op.add_column(
        "business_intelligence_analysis_runs",
        sa.Column(
            "workforce_sensitive",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    # The analysis table is both forced-RLS and append-only. The dedicated table owner may
    # briefly bypass those protections inside this atomic migration solely to backfill the new
    # authorization metadata; the trigger and forced RLS are restored before commit.
    op.execute("ALTER TABLE business_metric_definition_versions DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs DISABLE ROW LEVEL SECURITY")
    op.execute("DROP TRIGGER trg_bmi_analysis_immutable ON business_intelligence_analysis_runs")
    op.execute(
        """
        WITH RECURSIVE workforce_definitions (tenant_id, company_id, id) AS (
          SELECT definition.tenant_id, definition.company_id, definition.id
          FROM business_metric_definition_versions AS definition
          WHERE definition.domain = 'people'
             OR definition.calculation_spec->>'kind' = 'm5_metric'
             OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements_text(
                 CASE
                   WHEN jsonb_typeof(definition.dimensions) = 'array'
                   THEN definition.dimensions
                   ELSE '[]'::jsonb
                 END
               ) AS dimension(value)
               WHERE dimension.value = 'employee'
             )
             OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements(
                 CASE
                   WHEN jsonb_typeof(definition.dependencies) = 'array'
                   THEN definition.dependencies
                   ELSE '[]'::jsonb
                 END
               ) AS dependency(item)
               WHERE dependency.item->>'workforce_sensitive' = 'true'
                  OR dependency.item->>'metric_key' LIKE 'm5.%'
             )
             OR EXISTS (
               SELECT 1
               FROM jsonb_array_elements_text(
                 CASE
                   WHEN jsonb_typeof(definition.required_relationships) = 'array'
                   THEN definition.required_relationships
                   ELSE '[]'::jsonb
                 END
               ) AS relationship_type(value)
               WHERE relationship_type.value IN (
                 'employee_belongs_to_department',
                 'project_assigned_to_employee',
                 'project_managed_by_employee'
               )
             )
          UNION
          SELECT parent.tenant_id, parent.company_id, parent.id
          FROM business_metric_definition_versions AS parent
          CROSS JOIN LATERAL jsonb_array_elements(
            CASE
              WHEN jsonb_typeof(parent.dependencies) = 'array'
              THEN parent.dependencies
              ELSE '[]'::jsonb
            END
          ) AS dependency(item)
          JOIN workforce_definitions AS child
            ON child.tenant_id = parent.tenant_id
           AND child.company_id = parent.company_id
           AND child.id = CASE
             WHEN dependency.item->>'version_id' ~
               '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
             THEN (dependency.item->>'version_id')::uuid
             ELSE NULL::uuid
           END
        )
        UPDATE business_intelligence_analysis_runs AS analysis
        SET workforce_sensitive = (
          COALESCE(analysis.definition_snapshot->>'domain' = 'people', false)
          OR COALESCE(
            analysis.definition_snapshot->'calculation'->>'kind' = 'm5_metric', false
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
              CASE
                WHEN jsonb_typeof(analysis.definition_snapshot->'dimensions') = 'array'
                THEN analysis.definition_snapshot->'dimensions'
                ELSE '[]'::jsonb
              END
            ) AS dimension(value)
            WHERE dimension.value = 'employee'
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
              CASE
                WHEN jsonb_typeof(analysis.input_snapshot->'dimensions') = 'array'
                THEN analysis.input_snapshot->'dimensions'
                ELSE '[]'::jsonb
              END
            ) AS dimension(value)
            WHERE dimension.value = 'employee'
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements_text(
              CASE
                WHEN jsonb_typeof(analysis.definition_snapshot->'required_relationships') = 'array'
                THEN analysis.definition_snapshot->'required_relationships'
                ELSE '[]'::jsonb
              END
            ) AS relationship_type(value)
            WHERE relationship_type.value IN (
              'employee_belongs_to_department',
              'project_assigned_to_employee',
              'project_managed_by_employee'
            )
          )
          OR EXISTS (
            SELECT 1
            FROM jsonb_array_elements(
              CASE
                WHEN jsonb_typeof(analysis.definition_snapshot->'dependencies') = 'array'
                THEN analysis.definition_snapshot->'dependencies'
                ELSE '[]'::jsonb
              END
            ) AS dependency(item)
            WHERE dependency.item->>'workforce_sensitive' = 'true'
               OR dependency.item->>'metric_key' LIKE 'm5.%'
               OR (
                 dependency.item->>'version_id' IS NOT NULL
                 AND dependency.item->>'version_id' !~
                   '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
               )
               OR EXISTS (
                 SELECT 1
                 FROM workforce_definitions AS workforce
                 WHERE workforce.tenant_id = analysis.tenant_id
                   AND workforce.company_id = analysis.company_id
                   AND workforce.id = CASE
                     WHEN dependency.item->>'version_id' ~
                       '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
                     THEN (dependency.item->>'version_id')::uuid
                     ELSE NULL::uuid
                   END
               )
          )
        )
        """
    )
    op.execute(
        """CREATE TRIGGER trg_bmi_analysis_immutable
        BEFORE INSERT OR UPDATE OR DELETE ON business_intelligence_analysis_runs
        FOR EACH ROW EXECUTE FUNCTION prevent_business_intelligence_analysis_mutation()"""
    )
    op.execute("ALTER TABLE business_metric_definition_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_metric_definition_versions FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs FORCE ROW LEVEL SECURITY")
    op.execute(
        "COMMENT ON COLUMN business_intelligence_analysis_runs.workforce_sensitive IS "
        "'Company-scoped authorization flag for M5/People analysis snapshots containing "
        "individual workforce evidence; enforced in addition to RLS.'"
    )


def downgrade() -> None:
    _require_migrator()
    op.execute("ALTER TABLE business_semantic_relationship_versions DISABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs DISABLE ROW LEVEL SECURITY")
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (
            SELECT 1 FROM business_semantic_relationship_versions
            WHERE relationship_type = 'project_managed_by_employee'
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while project-manager semantic relationship evidence is retained';
          END IF;
          IF EXISTS (
            SELECT 1 FROM business_intelligence_analysis_runs
            WHERE workforce_sensitive = true
          ) THEN
            RAISE EXCEPTION
              'Cannot downgrade while workforce-sensitive analysis evidence is retained';
          END IF;
        END;
        $$;
        """
    )
    op.drop_column("business_intelligence_analysis_runs", "workforce_sensitive")
    op.drop_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        "relationship_type IN ('customer_has_sales_transaction', 'supplier_has_expense', "
        "'employee_belongs_to_department', 'department_in_business_unit', "
        "'project_assigned_to_employee')",
    )
    op.execute("ALTER TABLE business_semantic_relationship_versions ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_semantic_relationship_versions FORCE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE business_intelligence_analysis_runs FORCE ROW LEVEL SECURITY")
