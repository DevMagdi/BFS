"""Add versioned universal source profiles, evidence ingestion, and canonical lineage.

Revision ID: 0019_universal_data_foundation
Revises: 0018_customer_commitments
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0019_universal_data_foundation"
down_revision = "0018_customer_commitments"
branch_labels = None
depends_on = None

UNIVERSAL_CONTRACT_KEY = "businessflow.universal.business_records"
UNIVERSAL_CONTRACT_VERSION = "1.0.0"
UNIVERSAL_CONTRACT_HASH = "5f5fef068312aaa79ef0f863d7fb4a982626053fb9f06b322224d2e71aa5bd1e"
UNIVERSAL_CONTRACT_DEFINITION = (
    '{"dataset_type":"universal_records","entities":[{"description":"A source'
    "-scoped customer reference; not proof of customer identity or authority."
    '","entity_type":"customer","fields":[{"description":"Stable code used by'
    ' this source.","name":"customer_code","required":true,"type":"text"},{"d'
    'escription":"Customer name as supplied by the source.","name":"name","re'
    'quired":true,"type":"text"},{"description":"Optional tax or registration'
    ' identifier.","name":"tax_identifier","required":false,"type":"text"},{"'
    'description":"Optional contact email.","name":"email","required":false,"'
    'type":"text"},{"description":"Optional contact phone.","name":"phone","r'
    'equired":false,"type":"text"},{"description":"Optional country or jurisd'
    'iction.","name":"country","required":false,"type":"text"},{"description"'
    ':"Optional source-defined segment.","name":"customer_segment","required"'
    ':false,"type":"text"}],"key_field":"customer_code","label":"Customer"},{'
    '"description":"A source-scoped supplier reference; supplier identity is '
    'not cross-matched here.","entity_type":"supplier","fields":[{"descriptio'
    'n":"Stable code used by this source.","name":"supplier_code","required":'
    'true,"type":"text"},{"description":"Supplier name as supplied by the sou'
    'rce.","name":"name","required":true,"type":"text"},{"description":"Optio'
    'nal tax or registration identifier.","name":"tax_identifier","required":'
    'false,"type":"text"},{"description":"Optional contact email.","name":"em'
    'ail","required":false,"type":"text"},{"description":"Optional contact ph'
    'one.","name":"phone","required":false,"type":"text"},{"description":"Opt'
    'ional country or jurisdiction.","name":"country","required":false,"type"'
    ':"text"},{"description":"Optional source-defined payment terms.","name":'
    '"payment_terms","required":false,"type":"text"}],"key_field":"supplier_c'
    'ode","label":"Supplier"},{"description":"A source-scoped product or serv'
    'ice reference; pricing is not a financial fact.","entity_type":"product"'
    ',"fields":[{"description":"Stable code used by this source.","name":"pro'
    'duct_code","required":true,"type":"text"},{"description":"Product or ser'
    'vice name.","name":"name","required":true,"type":"text"},{"description":'
    '"Optional source description.","name":"description","required":false,"ty'
    'pe":"text"},{"description":"Optional source-defined category.","name":"c'
    'ategory","required":false,"type":"text"},{"description":"Optional unit l'
    'abel.","name":"unit_of_measure","required":false,"type":"text"}],"key_fi'
    'eld":"product_code","label":"Product or service"},{"description":"A sour'
    "ce-supplied transaction record, retained outside Core Financials until s"
    'eparately qualified.","entity_type":"sales_transaction","fields":[{"desc'
    'ription":"Stable transaction reference in the source.","name":"transacti'
    'on_reference","required":true,"type":"text"},{"description":"Transaction'
    ' date as supplied.","name":"transaction_date","required":true,"type":"da'
    'te"},{"description":"Optional source customer reference.","name":"custom'
    'er_code","required":false,"type":"text"},{"description":"Currency code s'
    'upplied with the amount.","name":"currency_code","required":true,"type":'
    '"text"},{"description":"Source amount; no currency conversion is applied'
    '.","name":"amount","required":true,"type":"decimal"},{"description":"Opt'
    'ional source-defined transaction status.","name":"status","required":fal'
    'se,"type":"text"},{"description":"Optional source description.","name":"'
    'description","required":false,"type":"text"}],"key_field":"transaction_r'
    'eference","label":"Sales transaction reference"},{"description":"A sourc'
    "e-supplied expense record, not an accounting entry or Core Financials me"
    'asure.","entity_type":"expense","fields":[{"description":"Stable expense'
    ' reference in the source.","name":"expense_reference","required":true,"t'
    'ype":"text"},{"description":"Expense date as supplied.","name":"expense_'
    'date","required":true,"type":"date"},{"description":"Source-defined expe'
    'nse category.","name":"category","required":true,"type":"text"},{"descri'
    'ption":"Currency code supplied with the amount.","name":"currency_code",'
    '"required":true,"type":"text"},{"description":"Source amount; no currenc'
    'y conversion is applied.","name":"amount","required":true,"type":"decima'
    'l"},{"description":"Optional source supplier reference.","name":"supplie'
    'r_code","required":false,"type":"text"},{"description":"Optional source '
    'description.","name":"description","required":false,"type":"text"}],"key'
    '_field":"expense_reference","label":"Expense reference"},{"description":'
    '"A source-scoped operational activity; it does not assert a financial ev'
    'ent.","entity_type":"business_activity","fields":[{"description":"Stable'
    ' activity reference in the source.","name":"activity_reference","require'
    'd":true,"type":"text"},{"description":"Date associated with the activity'
    '.","name":"activity_date","required":true,"type":"date"},{"description":'
    '"Source-defined activity type.","name":"activity_type","required":true,"'
    'type":"text"},{"description":"Activity description as supplied.","name":'
    '"description","required":true,"type":"text"},{"description":"Optional so'
    'urce-defined status.","name":"status","required":false,"type":"text"}],"'
    'key_field":"activity_reference","label":"Business activity"},{"descripti'
    'on":"A source-scoped operational reference with no implied business inte'
    'rpretation.","entity_type":"operational_record","fields":[{"description"'
    ':"Stable record reference in the source.","name":"record_reference","req'
    'uired":true,"type":"text"},{"description":"Date associated with the reco'
    'rd.","name":"record_date","required":true,"type":"date"},{"description":'
    '"Source-defined record type.","name":"record_type","required":true,"type'
    '":"text"},{"description":"Description as supplied by the source.","name"'
    ':"description","required":true,"type":"text"},{"description":"Optional s'
    'ource-defined status.","name":"status","required":false,"type":"text"}],'
    '"key_field":"record_reference","label":"Operational record"}],"promotion'
    '_boundary":"Accepted source records are not automatically financial fact'
    's or Core Financials inputs.","source_kind":"universal_business_data","v'
    'ersion":"1.0.0"}'
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


def _company_rls(table_name: str) -> None:
    op.execute(sa.text(f"ALTER TABLE {table_name} ENABLE ROW LEVEL SECURITY"))
    op.execute(sa.text(f"ALTER TABLE {table_name} FORCE ROW LEVEL SECURITY"))
    op.execute(
        sa.text(
            f"""
            CREATE POLICY {table_name}_company_context ON {table_name}
              USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
              WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid
                AND company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)
            """
        )
    )


def _create_table(name: str, *columns: sa.SchemaItem) -> None:
    op.create_table(name, *columns)
    _company_rls(name)


def upgrade() -> None:
    _require_migrator()

    op.drop_constraint("ck_company_data_contracts_dataset", "company_data_contracts", type_="check")
    op.create_check_constraint(
        "ck_company_data_contracts_dataset",
        "company_data_contracts",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments', 'universal_records')",
    )
    op.drop_constraint("ck_company_data_sources_dataset", "company_data_sources", type_="check")
    op.create_check_constraint(
        "ck_company_data_sources_dataset",
        "company_data_sources",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments', 'universal_records')",
    )

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

    _create_table(
        "company_data_source_profile_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("business_domain", sa.String(length=64), nullable=True),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_status", sa.String(length=16), server_default="enabled", nullable=False),
        sa.Column("connection_reference", sa.String(length=256), nullable=True),
        sa.Column("credential_reference", sa.String(length=256), nullable=True),
        sa.Column(
            "authority_classification",
            sa.String(length=32),
            server_default="unclassified",
            nullable=False,
        ),
        sa.Column("freshness_max_age_hours", sa.Integer(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_udf_source_profile_version"),
        sa.CheckConstraint(
            "source_type IN ('manual_entry', 'local_directory', 'csv', 'xlsx', 'pdf', 'rest_api', "
            "'relational_database', 'erp', 'crm', 'accounting', 'hr', 'other')",
            name="ck_udf_source_profile_type",
        ),
        sa.CheckConstraint(
            "source_status IN ('enabled', 'disabled')", name="ck_udf_source_profile_status"
        ),
        sa.CheckConstraint(
            "authority_classification IN ('unclassified', 'owner_asserted_primary', "
            "'supporting', 'third_party', 'manual_assertion')",
            name="ck_udf_source_profile_authority",
        ),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_udf_source_profile_name"
        ),
        sa.CheckConstraint(
            "business_domain IS NULL OR length(business_domain) BETWEEN 1 AND 64",
            name="ck_udf_source_profile_domain",
        ),
        sa.CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_udf_source_profile_freshness",
        ),
        sa.CheckConstraint(
            "connection_reference IS NULL OR connection_reference ~ '^(config|ref)://[A-Za-z0-9._/-]{1,220}$'",
            name="ck_udf_source_profile_connection_ref",
        ),
        sa.CheckConstraint(
            "credential_reference IS NULL OR credential_reference ~ '^(secret|vault|kms)://[A-Za-z0-9._/-]{1,220}$'",
            name="ck_udf_source_profile_credential_ref",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_source_profile_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_source_profile_owner",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_source_profile_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_data_source_profile_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "version", name="uq_udf_source_profile_version"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_source_profile_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_source_profile_latest",
        "company_data_source_profile_versions",
        ["tenant_id", "company_id", "source_id", "version"],
    )

    _create_table(
        "universal_source_snapshots",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=True),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("original_content", sa.LargeBinary(), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_udf_snapshot_format"),
        sa.CheckConstraint("file_size_bytes > 0", name="ck_udf_snapshot_file_size"),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_snapshot_fingerprint"
        ),
        sa.CheckConstraint(
            "content_type IS NULL OR length(content_type) <= 128",
            name="ck_udf_snapshot_content_type",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_snapshot_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_snapshot_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_source_snapshots"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "input_fingerprint",
            "file_format",
            name="uq_udf_snapshot_source_fingerprint",
        ),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_snapshot_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_snapshot_source_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_snapshot_source_created",
        "universal_source_snapshots",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )

    _create_table(
        "universal_mapping_profiles",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("profile_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "profile_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_udf_mapping_profile_key"
        ),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_udf_mapping_profile_name"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_mapping_profile_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_mapping_profile_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_mapping_profiles"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "profile_key", name="uq_udf_mapping_profile_key"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_mapping_profile_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_mapping_profiles_source",
        "universal_mapping_profiles",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )

    _create_table(
        "universal_mapping_profile_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("profile_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("worksheet_name", sa.String(length=128), nullable=False),
        sa.Column("field_mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("ignored_columns", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("field_options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version > 0", name="ck_udf_mapping_version_number"),
        sa.CheckConstraint(
            "entity_type IN ('customer', 'supplier', 'product', 'sales_transaction', 'expense', "
            "'business_activity', 'operational_record')",
            name="ck_udf_mapping_entity_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_mapping) = 'object'", name="ck_udf_mapping_fields_object"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(ignored_columns) = 'array'", name="ck_udf_mapping_ignored_array"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_options) = 'object'", name="ck_udf_mapping_options_object"
        ),
        sa.CheckConstraint("contract_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_mapping_contract_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "profile_id"],
            [
                "universal_mapping_profiles.tenant_id",
                "universal_mapping_profiles.company_id",
                "universal_mapping_profiles.source_id",
                "universal_mapping_profiles.id",
            ],
            name="fk_udf_mapping_version_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_mapping_version_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_mapping_profile_versions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "profile_id", "version", name="uq_udf_mapping_version"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_id", "id", name="uq_udf_mapping_version_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_mapping_versions_profile",
        "universal_mapping_profile_versions",
        ["tenant_id", "company_id", "profile_id", "version"],
    )

    _create_table(
        "universal_ingestions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("initiated_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("file_name", sa.String(length=255), nullable=False),
        sa.Column("file_format", sa.String(length=8), nullable=False),
        sa.Column("input_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("parse_options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("inspection", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("selected_worksheet", sa.String(length=128), nullable=True),
        sa.Column("mapping_profile_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="staged", nullable=False),
        sa.Column("total_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("promoted_rows", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("revised_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("unchanged_records", sa.Integer(), server_default="0", nullable=False),
        sa.Column("validation_hash", sa.String(length=64), nullable=True),
        sa.Column("validation_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("failure_code", sa.String(length=64), nullable=True),
        sa.Column("promoted_by_user_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('staged', 'mapped', 'validated', 'review_required', 'blocked', "
            "'failed', 'promoted')",
            name="ck_udf_ingestion_status",
        ),
        sa.CheckConstraint("file_format IN ('csv', 'xlsx')", name="ck_udf_ingestion_format"),
        sa.CheckConstraint(
            "idempotency_key_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_key_hash"
        ),
        sa.CheckConstraint(
            "input_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_fingerprint"
        ),
        sa.CheckConstraint(
            "total_rows >= 0 AND valid_rows >= 0 AND invalid_rows >= 0 AND warning_rows >= 0 "
            "AND duplicate_rows >= 0 AND promoted_rows >= 0 AND created_records >= 0 "
            "AND revised_records >= 0 AND unchanged_records >= 0",
            name="ck_udf_ingestion_counts",
        ),
        sa.CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_udf_ingestion_validation_hash",
        ),
        sa.CheckConstraint(
            "validation_snapshot IS NULL OR jsonb_typeof(validation_snapshot) = 'object'",
            name="ck_udf_ingestion_validation_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(parse_options) = 'object'", name="ck_udf_ingestion_parse_options"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(inspection) = 'object'", name="ck_udf_ingestion_inspection"
        ),
        sa.CheckConstraint(
            "(status = 'promoted' AND promoted_at IS NOT NULL AND promoted_by_user_id IS NOT NULL) "
            "OR (status <> 'promoted' AND promoted_at IS NULL AND promoted_by_user_id IS NULL)",
            name="ck_udf_ingestion_promotion_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_ingestion_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "snapshot_id"],
            [
                "universal_source_snapshots.tenant_id",
                "universal_source_snapshots.company_id",
                "universal_source_snapshots.source_id",
                "universal_source_snapshots.id",
            ],
            name="fk_udf_ingestion_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_udf_ingestion_source_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            name="fk_udf_ingestion_mapping_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "initiated_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_ingestion_actor",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "promoted_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_ingestion_reviewer",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_ingestions"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_ingestion_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "source_id",
            "idempotency_key_hash",
            name="uq_udf_ingestion_idempotency",
        ),
    )
    op.create_index(
        "ix_udf_ingestions_source_created",
        "universal_ingestions",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )
    op.create_index(
        "ix_udf_ingestions_status",
        "universal_ingestions",
        ["tenant_id", "company_id", "status", "created_at"],
    )

    _create_table(
        "universal_ingestion_rows",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("worksheet_name", sa.String(length=128), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("row_hash", sa.String(length=64), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("row_number > 0", name="ck_udf_ingestion_row_number"),
        sa.CheckConstraint("row_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_row_hash"),
        sa.CheckConstraint(
            "jsonb_typeof(raw_values) = 'array'", name="ck_udf_ingestion_row_values"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "snapshot_id"],
            [
                "universal_source_snapshots.tenant_id",
                "universal_source_snapshots.company_id",
                "universal_source_snapshots.source_id",
                "universal_source_snapshots.id",
            ],
            name="fk_udf_ingestion_row_snapshot",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            name="fk_udf_ingestion_row_run",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_ingestion_rows"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "worksheet_name",
            "row_number",
            name="uq_udf_ingestion_row_location",
        ),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_ingestion_row_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "id",
            name="uq_udf_ingestion_row_run_scope_id",
        ),
    )
    op.create_index(
        "ix_udf_ingestion_rows_run",
        "universal_ingestion_rows",
        ["tenant_id", "company_id", "ingestion_id", "row_number"],
    )

    _create_table(
        "universal_ingestion_events",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("event_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "event_type IN ('uploaded', 'mapping_applied', 'validated', "
            "'promotion_blocked', 'promoted')",
            name="ck_udf_ingestion_event_type",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(details) = 'object'", name="ck_udf_ingestion_event_details"
        ),
        sa.CheckConstraint("event_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_ingestion_event_hash"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            name="fk_udf_ingestion_event_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "actor_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_ingestion_event_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_ingestion_events"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_udf_ingestion_event_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_ingestion_events_run",
        "universal_ingestion_events",
        ["tenant_id", "company_id", "ingestion_id", "created_at"],
    )

    _create_table(
        "universal_row_validations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mapping_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
        sa.Column("mapped_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("row_status", sa.String(length=16), nullable=False),
        sa.Column("row_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("validation_rule_version", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "row_status IN ('valid', 'warning', 'invalid', 'duplicate')",
            name="ck_udf_row_validation_status",
        ),
        sa.CheckConstraint("row_fingerprint ~ '^[0-9a-f]{64}$'", name="ck_udf_row_validation_hash"),
        sa.CheckConstraint(
            "jsonb_typeof(mapped_values) = 'object'", name="ck_udf_row_validation_values"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(findings) = 'array'", name="ck_udf_row_validation_findings"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            name="fk_udf_row_validation_run",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            name="fk_udf_row_validation_source_row",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            name="fk_udf_row_validation_mapping",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_row_validations"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "source_row_id",
            "mapping_profile_version_id",
            name="uq_udf_row_validation_version",
        ),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_row_validation_scope_id"),
    )
    op.create_index(
        "ix_udf_row_validations_ingestion",
        "universal_row_validations",
        ["tenant_id", "company_id", "ingestion_id", "row_status"],
    )

    _create_table(
        "canonical_business_records",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.String(length=160), nullable=False),
        sa.Column("current_revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "entity_type IN ('customer', 'supplier', 'product', 'sales_transaction', 'expense', "
            "'business_activity', 'operational_record')",
            name="ck_udf_canonical_record_type",
        ),
        sa.CheckConstraint(
            "length(source_record_id) BETWEEN 1 AND 160", name="ck_udf_canonical_record_key"
        ),
        sa.CheckConstraint("current_revision >= 1", name="ck_udf_canonical_record_revision"),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_udf_canonical_record_source",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_canonical_business_records"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "entity_type",
            "source_id",
            "source_record_id",
            name="uq_udf_canonical_source_record",
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_udf_canonical_record_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_canonical_records_entity",
        "canonical_business_records",
        ["tenant_id", "company_id", "entity_type", "created_at"],
    )
    op.create_index(
        "ix_udf_canonical_records_source",
        "canonical_business_records",
        ["tenant_id", "company_id", "source_id", "source_record_id"],
    )

    _create_table(
        "canonical_business_record_revisions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("record_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.String(length=160), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mapping_profile_version_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("attributes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("attributes_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_hash", sa.String(length=64), nullable=False),
        sa.Column("change_reason", sa.String(length=64), nullable=False),
        sa.Column("correction_note", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("revision >= 1", name="ck_udf_record_revision_number"),
        sa.CheckConstraint(
            "attributes_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_record_revision_hash"
        ),
        sa.CheckConstraint(
            "validation_hash ~ '^[0-9a-f]{64}$'", name="ck_udf_record_revision_validation_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(attributes) = 'object'", name="ck_udf_record_revision_attributes"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "record_id"],
            [
                "canonical_business_records.tenant_id",
                "canonical_business_records.company_id",
                "canonical_business_records.id",
            ],
            name="fk_udf_record_revision_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            name="fk_udf_record_revision_ingestion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            name="fk_udf_record_revision_source_row",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            name="fk_udf_record_revision_mapping",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_udf_record_revision_source_profile",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_udf_record_revision_actor",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_canonical_business_record_revisions"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "record_id",
            "revision",
            name="uq_udf_canonical_record_revision",
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_udf_record_revision_scope_id"
        ),
    )
    op.create_index(
        "ix_udf_record_revisions_record",
        "canonical_business_record_revisions",
        ["tenant_id", "company_id", "record_id", "revision"],
    )
    op.create_foreign_key(
        "fk_udf_canonical_current_revision",
        "canonical_business_records",
        "canonical_business_record_revisions",
        ["tenant_id", "company_id", "id", "current_revision"],
        ["tenant_id", "company_id", "record_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )

    _create_table(
        "universal_ingestion_record_links",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingestion_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_row_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_record_id", sa.String(length=160), nullable=True),
        sa.Column("canonical_record_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("accepted_revision", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "outcome IN ('created', 'revised', 'unchanged', 'withheld_duplicate')",
            name="ck_udf_record_link_outcome",
        ),
        sa.CheckConstraint(
            "(outcome = 'withheld_duplicate' AND canonical_record_id IS NULL "
            "AND accepted_revision IS NULL) OR "
            "(outcome <> 'withheld_duplicate' AND canonical_record_id IS NOT NULL "
            "AND accepted_revision IS NOT NULL)",
            name="ck_udf_record_link_record_state",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id"],
            [
                "universal_ingestions.tenant_id",
                "universal_ingestions.company_id",
                "universal_ingestions.id",
            ],
            name="fk_udf_record_link_ingestion",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "ingestion_id", "source_row_id"],
            [
                "universal_ingestion_rows.tenant_id",
                "universal_ingestion_rows.company_id",
                "universal_ingestion_rows.ingestion_id",
                "universal_ingestion_rows.id",
            ],
            name="fk_udf_record_link_row",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "canonical_record_id"],
            [
                "canonical_business_records.tenant_id",
                "canonical_business_records.company_id",
                "canonical_business_records.id",
            ],
            name="fk_udf_record_link_record",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "canonical_record_id", "accepted_revision"],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            name="fk_udf_record_link_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_universal_ingestion_record_links"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "ingestion_id",
            "source_row_id",
            name="uq_udf_record_link_row",
        ),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_udf_record_link_scope_id"),
    )
    op.create_index(
        "ix_udf_record_links_record",
        "universal_ingestion_record_links",
        ["tenant_id", "company_id", "canonical_record_id"],
    )

    op.execute(
        """
        CREATE FUNCTION businessflow_udf_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Universal Data evidence and version rows are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_append_only';
        END;
        $$;
        CREATE FUNCTION businessflow_udf_ingestion_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Universal Data ingestion executions cannot be deleted'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF OLD.status = 'promoted' THEN
            RAISE EXCEPTION 'Promoted Universal Data executions are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.snapshot_id IS DISTINCT FROM OLD.snapshot_id
             OR NEW.source_profile_version_id IS DISTINCT FROM OLD.source_profile_version_id
             OR NEW.initiated_by_user_id IS DISTINCT FROM OLD.initiated_by_user_id
             OR NEW.idempotency_key_hash IS DISTINCT FROM OLD.idempotency_key_hash
             OR NEW.file_name IS DISTINCT FROM OLD.file_name
             OR NEW.file_format IS DISTINCT FROM OLD.file_format
             OR NEW.input_fingerprint IS DISTINCT FROM OLD.input_fingerprint
             OR NEW.parse_options IS DISTINCT FROM OLD.parse_options
             OR NEW.inspection IS DISTINCT FROM OLD.inspection
             OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
            RAISE EXCEPTION 'Universal Data ingestion identity and input are immutable'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_ingestion_guard';
          END IF;
          IF NEW.status NOT IN (
            'staged', 'mapped', 'validated', 'review_required', 'blocked', 'failed', 'promoted'
          ) THEN
            RAISE EXCEPTION 'Invalid Universal Data ingestion state'
              USING ERRCODE = '23514', CONSTRAINT = 'ck_udf_ingestion_status';
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_universal_ingestions_guard
          BEFORE UPDATE OR DELETE ON universal_ingestions
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_ingestion_guard();

        CREATE FUNCTION businessflow_udf_canonical_record_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE revision_exists boolean;
        BEGIN
          IF TG_OP = 'DELETE' THEN
            RAISE EXCEPTION 'Canonical Universal Data record identities cannot be deleted'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_canonical_record_guard';
          END IF;
          IF NEW.id IS DISTINCT FROM OLD.id
             OR NEW.tenant_id IS DISTINCT FROM OLD.tenant_id
             OR NEW.company_id IS DISTINCT FROM OLD.company_id
             OR NEW.entity_type IS DISTINCT FROM OLD.entity_type
             OR NEW.source_id IS DISTINCT FROM OLD.source_id
             OR NEW.source_record_id IS DISTINCT FROM OLD.source_record_id
             OR NEW.first_seen_at IS DISTINCT FROM OLD.first_seen_at
             OR NEW.created_at IS DISTINCT FROM OLD.created_at
             OR NEW.current_revision < OLD.current_revision
             OR NEW.current_revision > OLD.current_revision + 1 THEN
            RAISE EXCEPTION 'Canonical record identity is immutable and revisions are monotonic'
              USING ERRCODE = '55000', CONSTRAINT = 'businessflow_udf_canonical_record_guard';
          END IF;
          IF NEW.current_revision = OLD.current_revision + 1 THEN
            SELECT EXISTS (
              SELECT 1 FROM canonical_business_record_revisions
              WHERE tenant_id = NEW.tenant_id AND company_id = NEW.company_id
                AND record_id = NEW.id AND revision = NEW.current_revision
            ) INTO revision_exists;
            IF NOT revision_exists THEN
              RAISE EXCEPTION 'Canonical record revision must exist before it becomes current'
                USING ERRCODE = '23514', CONSTRAINT = 'businessflow_udf_canonical_record_guard';
            END IF;
          END IF;
          NEW.updated_at := clock_timestamp();
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_canonical_business_records_guard
          BEFORE UPDATE OR DELETE ON canonical_business_records
          FOR EACH ROW EXECUTE FUNCTION businessflow_udf_canonical_record_guard();
        """
    )
    append_only_tables = (
        "company_data_source_profile_versions",
        "universal_source_snapshots",
        "universal_ingestion_rows",
        "universal_mapping_profiles",
        "universal_mapping_profile_versions",
        "universal_ingestion_events",
        "universal_row_validations",
        "canonical_business_record_revisions",
        "universal_ingestion_record_links",
    )
    for table in append_only_tables:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION businessflow_udf_append_only()"
            )
        )

    runtime_read_insert_tables = (
        "company_data_source_profile_versions",
        "universal_source_snapshots",
        "universal_ingestion_rows",
        "universal_mapping_profiles",
        "universal_mapping_profile_versions",
        "universal_ingestion_events",
        "universal_row_validations",
        "canonical_business_record_revisions",
        "universal_ingestion_record_links",
    )
    runtime_update_tables = ("universal_ingestions", "canonical_business_records")
    all_tables = (*runtime_read_insert_tables, *runtime_update_tables)
    op.execute("REVOKE ALL ON " + ", ".join(all_tables) + " FROM PUBLIC, bf_runtime")
    for table in runtime_read_insert_tables:
        op.execute(sa.text(f"GRANT SELECT, INSERT ON {table} TO bf_runtime"))
    for table in runtime_update_tables:
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO bf_runtime"))
    op.execute(
        "REVOKE UPDATE, DELETE, TRUNCATE ON "
        + ", ".join(runtime_read_insert_tables)
        + " FROM bf_runtime"
    )
    op.execute(
        "REVOKE DELETE, TRUNCATE ON universal_ingestions, canonical_business_records "
        "FROM bf_runtime"
    )


def downgrade() -> None:
    # The migrator owns these tables but is intentionally NOBYPASSRLS. Temporarily disable
    # forced RLS so the downgrade safety check can see every tenant's retained data. If the
    # check raises, PostgreSQL rolls the transaction back and restores each original policy.
    protected_tables = (
        "company_data_sources",
        "universal_source_snapshots",
        "universal_ingestions",
        "canonical_business_records",
    )
    for table in protected_tables:
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM company_data_sources WHERE dataset_type = 'universal_records')
             OR EXISTS (SELECT 1 FROM universal_source_snapshots)
             OR EXISTS (SELECT 1 FROM universal_ingestions)
             OR EXISTS (SELECT 1 FROM canonical_business_records) THEN
            RAISE EXCEPTION 'Cannot downgrade Universal Data Foundation while source, evidence, '
              'ingestion, or canonical records exist';
          END IF;
        END;
        $$;
        """
    )
    for table in protected_tables:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    op.execute(
        "DROP TRIGGER IF EXISTS trg_canonical_business_records_guard ON canonical_business_records"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_canonical_record_guard()")
    op.execute("DROP TRIGGER IF EXISTS trg_universal_ingestions_guard ON universal_ingestions")
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_ingestion_guard()")
    append_only_tables = (
        "company_data_source_profile_versions",
        "universal_source_snapshots",
        "universal_ingestion_rows",
        "universal_mapping_profiles",
        "universal_mapping_profile_versions",
        "universal_ingestion_events",
        "universal_row_validations",
        "canonical_business_record_revisions",
        "universal_ingestion_record_links",
    )
    for table in append_only_tables:
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"))
    op.execute("DROP FUNCTION IF EXISTS businessflow_udf_append_only()")
    op.drop_constraint(
        "fk_udf_canonical_current_revision", "canonical_business_records", type_="foreignkey"
    )

    op.drop_table("universal_ingestion_record_links")
    op.drop_table("canonical_business_record_revisions")
    op.drop_table("canonical_business_records")
    op.drop_table("universal_row_validations")
    op.drop_table("universal_ingestion_events")
    op.drop_table("universal_ingestion_rows")
    op.drop_table("universal_ingestions")
    op.drop_table("universal_mapping_profile_versions")
    op.drop_table("universal_mapping_profiles")
    op.drop_table("universal_source_snapshots")
    op.drop_table("company_data_source_profile_versions")

    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_contracts_append_only ON company_data_contracts"
    )
    op.execute(
        sa.text(
            "DELETE FROM company_data_contracts WHERE contract_key = :key AND version = :version"
        ).bindparams(
            sa.bindparam("key", value=UNIVERSAL_CONTRACT_KEY, type_=sa.String(length=128)),
            sa.bindparam("version", value=UNIVERSAL_CONTRACT_VERSION, type_=sa.String(length=32)),
        )
    )
    op.execute(
        "CREATE TRIGGER trg_company_data_contracts_append_only "
        "BEFORE UPDATE OR DELETE ON company_data_contracts "
        "FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only()"
    )
    op.drop_constraint("ck_company_data_sources_dataset", "company_data_sources", type_="check")
    op.create_check_constraint(
        "ck_company_data_sources_dataset",
        "company_data_sources",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments')",
    )
    op.drop_constraint("ck_company_data_contracts_dataset", "company_data_contracts", type_="check")
    op.create_check_constraint(
        "ck_company_data_contracts_dataset",
        "company_data_contracts",
        "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
        "'projects', 'resource_assignments', 'customer_commitments')",
    )
