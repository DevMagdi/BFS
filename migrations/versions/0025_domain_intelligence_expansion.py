"""Add source-verified product links and commitment-aware BI source scoping.

Revision ID: 0025_domain_intelligence_expansion
Revises: 0024_company_wide_intelligence
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0025_domain_intelligence_expansion"
down_revision = "0024_company_wide_intelligence"
branch_labels = None
depends_on = None

UNIVERSAL_CONTRACT_KEY = "businessflow.universal.business_records"
UNIVERSAL_CONTRACT_VERSION = "1.1.0"
UNIVERSAL_CONTRACT_HASH = "2a4b696e581496ef71778e74aad7d1510298b849a78f6d8e407173f894af20dc"
# Frozen compact JSON matches the canonical contract hash and remains migration-independent.
UNIVERSAL_CONTRACT_DEFINITION = (
    r'{"dataset_type":"universal_records","entities":[{"description":"A source-sco'
    r'ped customer reference; not proof of customer identity or authority.","entit'
    r'y_type":"customer","fields":[{"description":"Stable code used by this source'
    r'.","name":"customer_code","required":true,"type":"text"},{"description":"Cus'
    r'tomer name as supplied by the source.","name":"name","required":true,"type":'
    r'"text"},{"description":"Optional tax or registration identifier.","name":"ta'
    r'x_identifier","required":false,"type":"text"},{"description":"Optional conta'
    r'ct email.","name":"email","required":false,"type":"text"},{"description":"Op'
    r'tional contact phone.","name":"phone","required":false,"type":"text"},{"desc'
    r'ription":"Optional country or jurisdiction.","name":"country","required":fal'
    r'se,"type":"text"},{"description":"Optional source-defined segment.","name":"'
    r'customer_segment","required":false,"type":"text"}],"key_field":"customer_cod'
    r'e","label":"Customer"},{"description":"A source-scoped supplier reference; s'
    r'upplier identity is not cross-matched here.","entity_type":"supplier","field'
    r's":[{"description":"Stable code used by this source.","name":"supplier_code"'
    r',"required":true,"type":"text"},{"description":"Supplier name as supplied by'
    r' the source.","name":"name","required":true,"type":"text"},{"description":"O'
    r'ptional tax or registration identifier.","name":"tax_identifier","required":'
    r'false,"type":"text"},{"description":"Optional contact email.","name":"email"'
    r',"required":false,"type":"text"},{"description":"Optional contact phone.","n'
    r'ame":"phone","required":false,"type":"text"},{"description":"Optional countr'
    r'y or jurisdiction.","name":"country","required":false,"type":"text"},{"descr'
    r'iption":"Optional source-defined payment terms.","name":"payment_terms","req'
    r'uired":false,"type":"text"}],"key_field":"supplier_code","label":"Supplier"}'
    r',{"description":"A source-scoped product or service reference; pricing is no'
    r't a financial fact.","entity_type":"product","fields":[{"description":"Stabl'
    r'e code used by this source.","name":"product_code","required":true,"type":"t'
    r'ext"},{"description":"Product or service name.","name":"name","required":tru'
    r'e,"type":"text"},{"description":"Optional source description.","name":"descr'
    r'iption","required":false,"type":"text"},{"description":"Optional source-defi'
    r'ned category.","name":"category","required":false,"type":"text"},{"descripti'
    r'on":"Optional unit label.","name":"unit_of_measure","required":false,"type":'
    r'"text"}],"key_field":"product_code","label":"Product or service"},{"descript'
    r'ion":"A source-supplied transaction record, retained outside Core Financials'
    r' until separately qualified.","entity_type":"sales_transaction","fields":[{"'
    r'description":"Stable transaction reference in the source.","name":"transacti'
    r'on_reference","required":true,"type":"text"},{"description":"Transaction dat'
    r'e as supplied.","name":"transaction_date","required":true,"type":"date"},{"d'
    r'escription":"Optional source customer reference.","name":"customer_code","re'
    r'quired":false,"type":"text"},{"description":"Optional source product referen'
    r'ce.","name":"product_code","required":false,"type":"text"},{"description":"C'
    r'urrency code supplied with the amount.","name":"currency_code","required":tr'
    r'ue,"type":"text"},{"description":"Source amount; no currency conversion is a'
    r'pplied.","name":"amount","required":true,"type":"decimal"},{"description":"O'
    r'ptional source-defined transaction status.","name":"status","required":false'
    r',"type":"text"},{"description":"Optional source description.","name":"descri'
    r'ption","required":false,"type":"text"}],"key_field":"transaction_reference",'
    r'"label":"Sales transaction reference"},{"description":"A source-supplied exp'
    r'ense record, not an accounting entry or Core Financials measure.","entity_ty'
    r'pe":"expense","fields":[{"description":"Stable expense reference in the sour'
    r'ce.","name":"expense_reference","required":true,"type":"text"},{"description'
    r'":"Expense date as supplied.","name":"expense_date","required":true,"type":"'
    r'date"},{"description":"Source-defined expense category.","name":"category","'
    r'required":true,"type":"text"},{"description":"Currency code supplied with th'
    r'e amount.","name":"currency_code","required":true,"type":"text"},{"descripti'
    r'on":"Source amount; no currency conversion is applied.","name":"amount","req'
    r'uired":true,"type":"decimal"},{"description":"Optional source supplier refer'
    r'ence.","name":"supplier_code","required":false,"type":"text"},{"description"'
    r':"Optional source description.","name":"description","required":false,"type"'
    r':"text"}],"key_field":"expense_reference","label":"Expense reference"},{"des'
    r'cription":"A source-scoped operational activity; it does not assert a financ'
    r'ial event.","entity_type":"business_activity","fields":[{"description":"Stab'
    r'le activity reference in the source.","name":"activity_reference","required"'
    r':true,"type":"text"},{"description":"Date associated with the activity.","na'
    r'me":"activity_date","required":true,"type":"date"},{"description":"Source-de'
    r'fined activity type.","name":"activity_type","required":true,"type":"text"},'
    r'{"description":"Activity description as supplied.","name":"description","req'
    r'uired":true,"type":"text"},{"description":"Optional source-defined status.",'
    r'"name":"status","required":false,"type":"text"}],"key_field":"activity_refer'
    r'ence","label":"Business activity"},{"description":"A source-scoped operation'
    r'al reference with no implied business interpretation.","entity_type":"operat'
    r'ional_record","fields":[{"description":"Stable record reference in the sourc'
    r'e.","name":"record_reference","required":true,"type":"text"},{"description":'
    r'"Date associated with the record.","name":"record_date","required":true,"typ'
    r'e":"date"},{"description":"Source-defined record type.","name":"record_type"'
    r',"required":true,"type":"text"},{"description":"Description as supplied by t'
    r'he source.","name":"description","required":true,"type":"text"},{"descriptio'
    r'n":"Optional source-defined status.","name":"status","required":false,"type"'
    r':"text"}],"key_field":"record_reference","label":"Operational record"}],"pro'
    r'motion_boundary":"Accepted source records are not automatically financial fa'
    r'cts or Core Financials inputs.","source_kind":"universal_business_data","ver'
    r'sion":"1.1.0"}'
)


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

    op.execute(
        sa.text(
            "INSERT INTO company_data_contracts "
            "(contract_key, version, dataset_type, definition, definition_hash) "
            "VALUES (:key, :version, 'universal_records', CAST(:definition AS jsonb), :hash)"
        ).bindparams(
            sa.bindparam("key", value=UNIVERSAL_CONTRACT_KEY, type_=sa.String(length=128)),
            sa.bindparam("version", value=UNIVERSAL_CONTRACT_VERSION, type_=sa.String(length=32)),
            sa.bindparam("definition", value=UNIVERSAL_CONTRACT_DEFINITION, type_=sa.Text()),
            sa.bindparam("hash", value=UNIVERSAL_CONTRACT_HASH, type_=sa.String(length=64)),
        )
    )
    op.execute(
        """
        DO $$
        BEGIN
          IF NOT EXISTS (
            SELECT 1 FROM company_data_contracts
            WHERE contract_key = 'businessflow.universal.business_records'
              AND version = '1.1.0'
              AND dataset_type = 'universal_records'
              AND definition_hash =
                  '2a4b696e581496ef71778e74aad7d1510298b849a78f6d8e407173f894af20dc'
          ) THEN
            RAISE EXCEPTION 'Universal Data contract 1.1.0 did not match the versioned schema';
          END IF;
        END;
        $$;
        """
    )

    op.drop_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bse_relationship_type",
        "business_semantic_relationship_versions",
        "relationship_type IN ('customer_has_sales_transaction', 'product_has_sales_transaction', "
        "'supplier_has_expense', 'employee_belongs_to_department', 'department_in_business_unit', "
        "'project_assigned_to_employee', 'project_managed_by_employee')",
    )

    op.drop_constraint(
        "ck_bmi_source_precedence_dataset",
        "business_company_semantic_source_precedence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bmi_source_precedence_dataset",
        "business_company_semantic_source_precedence",
        "dataset_type IN ('universal_records', 'customer_commitments')",
    )

    op.drop_constraint(
        "ck_bmi_metric_definition_calculator",
        "business_metric_definition_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bmi_metric_definition_calculator",
        "business_metric_definition_versions",
        "calculation_key IN ('universal_record_count', 'universal_source_amount_sum', "
        "'universal_distinct_source_values', 'm5_metric', 'core_financial_measure', "
        "'custom_formula', 'manual_customer_commitment_count')",
    )

    # The migration role is intentionally NOBYPASSRLS, and these Company-scoped tables FORCE
    # RLS. Temporarily disable RLS inside this transaction so the backfill can see every tenant;
    # restore it before the migration commits. No runtime role can execute this migration.
    for table in ("company_data_sources", "company_data_source_profile_versions"):
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    # Existing manual commitment sources predate versioned BI source configuration. Pin an
    # explicit profile so future configurations can record the exact source status used.
    op.execute(
        """
        INSERT INTO company_data_source_profile_versions (
          id, tenant_id, company_id, source_id, version, source_type, display_name,
          business_domain, owner_user_id, source_status, authority_classification,
          created_by_user_id
        )
        SELECT gen_random_uuid(), source.tenant_id, source.company_id, source.id, 1,
               'manual_entry', source.display_name, 'operations', source.owner_user_id,
               'enabled', 'manual_assertion', source.registered_by_user_id
        FROM company_data_sources AS source
        WHERE source.dataset_type = 'customer_commitments'
          AND source.source_key = 'manual.customer-commitments'
          AND NOT EXISTS (
            SELECT 1 FROM company_data_source_profile_versions AS profile
            WHERE profile.tenant_id = source.tenant_id
              AND profile.company_id = source.company_id
              AND profile.source_id = source.id
          )
        """
    )

    for table in ("company_data_sources", "company_data_source_profile_versions"):
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")


def downgrade() -> None:
    _require_migrator()
    # Forced RLS would otherwise hide every tenant's rows from the non-BYPASSRLS migration role,
    # making these retention guards ineffective. This transaction restores all policies before
    # commit; any failed guard rolls the temporary DDL back with the rest of the migration.
    protected_tables = (
        "business_semantic_relationship_versions",
        "company_data_sources",
        "business_company_semantic_source_precedence",
        "business_metric_definition_versions",
        "business_intelligence_analysis_runs",
    )
    for table in protected_tables:
        op.execute(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.execute(
        """
        DO $$
        DECLARE
          has_commitment_dependent_definitions boolean;
          has_commitment_dependent_analyses boolean;
        BEGIN
          IF EXISTS (
            SELECT 1 FROM business_semantic_relationship_versions
            WHERE relationship_type = 'product_has_sales_transaction'
          ) THEN
            RAISE EXCEPTION 'cannot downgrade: verified product relationship evidence exists';
          END IF;
          IF EXISTS (
            SELECT 1 FROM company_data_sources
            WHERE contract_key = 'businessflow.universal.business_records'
              AND contract_version = '1.1.0'
          ) THEN
            RAISE EXCEPTION 'cannot downgrade: Universal Data 1.1.0 sources exist';
          END IF;
          IF EXISTS (
            SELECT 1 FROM business_company_semantic_source_precedence
            WHERE dataset_type = 'customer_commitments'
          ) THEN
            RAISE EXCEPTION 'cannot downgrade: customer commitment configurations exist';
          END IF;

          WITH RECURSIVE commitment_dependent_definitions (tenant_id, company_id, id) AS (
            SELECT definition.tenant_id, definition.company_id, definition.id
            FROM business_metric_definition_versions AS definition
            WHERE definition.calculation_key = 'manual_customer_commitment_count'
               OR EXISTS (
                 SELECT 1
                 FROM jsonb_array_elements(
                   CASE
                     WHEN jsonb_typeof(definition.dependencies) = 'array'
                     THEN definition.dependencies
                     ELSE '[]'::jsonb
                   END
                 ) AS dependency(item)
                 WHERE dependency.item->>'metric_key' =
                   'operations.accepted_customer_commitment_count'
                    OR dependency.item->>'customer_commitment_read_required' = 'true'
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
            JOIN commitment_dependent_definitions AS child
              ON child.tenant_id = parent.tenant_id
             AND child.company_id = parent.company_id
             AND child.id = CASE
               WHEN dependency.item->>'version_id' ~
                 '^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'
               THEN (dependency.item->>'version_id')::uuid
               ELSE NULL::uuid
             END
          ),
          commitment_dependent_analyses AS (
            SELECT 1
            FROM business_intelligence_analysis_runs AS analysis
            WHERE analysis.metric_key = 'operations.accepted_customer_commitment_count'
               OR analysis.definition_snapshot->>'calculation_key' =
                 'manual_customer_commitment_count'
               OR analysis.definition_snapshot->'calculation'->>'kind' =
                 'manual_customer_commitment_count'
               OR EXISTS (
                 SELECT 1 FROM commitment_dependent_definitions AS definition
                 WHERE definition.tenant_id = analysis.tenant_id
                   AND definition.company_id = analysis.company_id
                   AND definition.id = analysis.metric_definition_version_id
               )
          )
          SELECT EXISTS (SELECT 1 FROM commitment_dependent_definitions),
                 EXISTS (SELECT 1 FROM commitment_dependent_analyses)
            INTO has_commitment_dependent_definitions,
                 has_commitment_dependent_analyses;

          IF has_commitment_dependent_analyses THEN
            RAISE EXCEPTION 'cannot downgrade: customer commitment analysis evidence exists';
          END IF;
          IF has_commitment_dependent_definitions THEN
            RAISE EXCEPTION 'cannot downgrade: saved customer commitment KPI definitions exist';
          END IF;
        END;
        $$;
        """
    )

    for table in protected_tables:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

    op.drop_constraint(
        "ck_bmi_metric_definition_calculator",
        "business_metric_definition_versions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bmi_metric_definition_calculator",
        "business_metric_definition_versions",
        "calculation_key IN ('universal_record_count', 'universal_source_amount_sum', "
        "'universal_distinct_source_values', 'm5_metric', 'core_financial_measure', "
        "'custom_formula')",
    )
    op.drop_constraint(
        "ck_bmi_source_precedence_dataset",
        "business_company_semantic_source_precedence",
        type_="check",
    )
    op.create_check_constraint(
        "ck_bmi_source_precedence_dataset",
        "business_company_semantic_source_precedence",
        "dataset_type = 'universal_records'",
    )
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
    # The contract catalog is append-only at runtime. Permit this exact, retention-guarded
    # rollback deletion only within the migrator-owned transaction, then restore the guard trigger.
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_contracts_append_only ON company_data_contracts"
    )
    op.execute(
        sa.text(
            "DELETE FROM company_data_contracts "
            "WHERE contract_key = :key AND version = :version "
            "AND definition_hash = :hash"
        ).bindparams(
            sa.bindparam("key", value=UNIVERSAL_CONTRACT_KEY, type_=sa.String(length=128)),
            sa.bindparam("version", value=UNIVERSAL_CONTRACT_VERSION, type_=sa.String(length=32)),
            sa.bindparam("hash", value=UNIVERSAL_CONTRACT_HASH, type_=sa.String(length=64)),
        )
    )
    op.execute(
        "CREATE TRIGGER trg_company_data_contracts_append_only "
        "BEFORE UPDATE OR DELETE ON company_data_contracts "
        "FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only()"
    )
