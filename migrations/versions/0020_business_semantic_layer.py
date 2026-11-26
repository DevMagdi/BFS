"""Add Company-scoped semantic identities, reviewed mappings, and relationships.

Revision ID: 0020_business_semantic_layer
Revises: 0019_universal_data_foundation
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020_business_semantic_layer"
down_revision = "0019_universal_data_foundation"
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


def _member_fk(field: str, name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["tenant_id", field],
        ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _scope_fk(local: list[str], remote: list[str], name: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(local, remote, name=name, ondelete="RESTRICT")


def upgrade() -> None:
    _require_migrator()

    # These scope keys let the new references prove that a Universal record and the
    # cited immutable revision both belong to the same source, Company, and tenant.
    op.create_unique_constraint(
        "uq_udf_canonical_record_source_scope_id",
        "canonical_business_records",
        ["tenant_id", "company_id", "source_id", "id"],
    )
    op.create_unique_constraint(
        "uq_udf_record_revision_source_scope",
        "canonical_business_record_revisions",
        ["tenant_id", "company_id", "source_id", "record_id", "revision"],
    )

    op.create_table(
        "business_semantic_entities",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "entity_type IN ('business_unit', 'department', 'employee', 'project', 'customer', "
            "'supplier', 'product', 'sales_transaction', 'expense', 'business_activity', "
            "'operational_record')",
            name="ck_bse_entity_type",
        ),
        sa.CheckConstraint("entity_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bse_entity_key"),
        sa.CheckConstraint(
            "length(btrim(display_name)) BETWEEN 1 AND 160", name="ck_bse_entity_name"
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_bse_entity_description_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bse_entity_company",
            ondelete="RESTRICT",
        ),
        _member_fk("created_by_user_id", "fk_bse_entity_actor"),
        sa.PrimaryKeyConstraint("id", name="pk_business_semantic_entities"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_entity_scope_id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "entity_type", "entity_key", name="uq_bse_entity_key"
        ),
    )
    op.create_index(
        "ix_bse_entities_scope_type",
        "business_semantic_entities",
        ["tenant_id", "company_id", "entity_type", "created_at"],
    )
    _company_rls("business_semantic_entities")

    op.create_table(
        "business_semantic_mapping_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("semantic_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reference_kind", sa.String(length=32), nullable=False),
        sa.Column("reference_key_hash", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("universal_record_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("universal_revision", sa.Integer(), nullable=True),
        sa.Column("source_mapping_profile_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("employee_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("department_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("business_unit_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("supersedes_mapping_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("review_note", sa.Text(), nullable=False),
        sa.Column("correction_reason", sa.Text(), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_bse_mapping_version"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_mapping_version_id IS NULL) OR "
            "(version > 1 AND supersedes_mapping_version_id IS NOT NULL)",
            name="ck_bse_mapping_supersedes_version",
        ),
        sa.CheckConstraint(
            "reference_kind IN ('universal_record', 'employee', 'department', 'business_unit', "
            "'project')",
            name="ck_bse_mapping_reference_kind",
        ),
        sa.CheckConstraint(
            "reference_key_hash ~ '^[0-9a-f]{64}$'", name="ck_bse_mapping_reference_hash"
        ),
        sa.CheckConstraint(
            "(reference_kind = 'universal_record' AND universal_record_id IS NOT NULL "
            "AND universal_revision IS NOT NULL AND source_id IS NOT NULL "
            "AND source_mapping_profile_version_id IS NOT NULL AND employee_id IS NULL "
            "AND department_id IS NULL AND business_unit_id IS NULL AND project_id IS NULL) OR "
            "(reference_kind = 'employee' AND employee_id IS NOT NULL AND source_id IS NULL "
            "AND universal_record_id IS NULL AND universal_revision IS NULL "
            "AND source_mapping_profile_version_id IS NULL AND department_id IS NULL "
            "AND business_unit_id IS NULL AND project_id IS NULL) OR "
            "(reference_kind = 'department' AND department_id IS NOT NULL AND source_id IS NULL "
            "AND universal_record_id IS NULL AND universal_revision IS NULL "
            "AND source_mapping_profile_version_id IS NULL AND employee_id IS NULL "
            "AND business_unit_id IS NULL AND project_id IS NULL) OR "
            "(reference_kind = 'business_unit' AND business_unit_id IS NOT NULL "
            "AND source_id IS NULL "
            "AND universal_record_id IS NULL AND universal_revision IS NULL "
            "AND source_mapping_profile_version_id IS NULL AND employee_id IS NULL "
            "AND department_id IS NULL AND project_id IS NULL) OR "
            "(reference_kind = 'project' AND project_id IS NOT NULL AND source_id IS NULL "
            "AND universal_record_id IS NULL AND universal_revision IS NULL "
            "AND source_mapping_profile_version_id IS NULL AND employee_id IS NULL "
            "AND department_id IS NULL AND business_unit_id IS NULL)",
            name="ck_bse_mapping_typed_reference",
        ),
        sa.CheckConstraint(
            "length(btrim(review_note)) BETWEEN 1 AND 500", name="ck_bse_mapping_review_note"
        ),
        sa.CheckConstraint(
            "correction_reason IS NULL OR length(btrim(correction_reason)) BETWEEN 1 AND 500",
            name="ck_bse_mapping_correction_reason",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "semantic_entity_id"],
            [
                "business_semantic_entities.tenant_id",
                "business_semantic_entities.company_id",
                "business_semantic_entities.id",
            ],
            "fk_bse_mapping_entity",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            "fk_bse_mapping_source",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "universal_record_id"],
            [
                "canonical_business_records.tenant_id",
                "canonical_business_records.company_id",
                "canonical_business_records.source_id",
                "canonical_business_records.id",
            ],
            "fk_bse_mapping_universal_record",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "universal_record_id", "universal_revision"],
            [
                "canonical_business_record_revisions.tenant_id",
                "canonical_business_record_revisions.company_id",
                "canonical_business_record_revisions.source_id",
                "canonical_business_record_revisions.record_id",
                "canonical_business_record_revisions.revision",
            ],
            "fk_bse_mapping_universal_revision",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "source_id", "source_mapping_profile_version_id"],
            [
                "universal_mapping_profile_versions.tenant_id",
                "universal_mapping_profile_versions.company_id",
                "universal_mapping_profile_versions.source_id",
                "universal_mapping_profile_versions.id",
            ],
            "fk_bse_mapping_source_mapping",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            "fk_bse_mapping_employee",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "department_id"],
            ["departments.tenant_id", "departments.company_id", "departments.id"],
            "fk_bse_mapping_department",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "business_unit_id"],
            ["business_units.tenant_id", "business_units.company_id", "business_units.id"],
            "fk_bse_mapping_business_unit",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "project_id"],
            ["projects.tenant_id", "projects.company_id", "projects.id"],
            "fk_bse_mapping_project",
        ),
        _member_fk("created_by_user_id", "fk_bse_mapping_actor"),
        _scope_fk(
            ["tenant_id", "company_id", "supersedes_mapping_version_id", "reference_key_hash"],
            [
                "business_semantic_mapping_versions.tenant_id",
                "business_semantic_mapping_versions.company_id",
                "business_semantic_mapping_versions.id",
                "business_semantic_mapping_versions.reference_key_hash",
            ],
            "fk_bse_mapping_supersedes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_semantic_mapping_versions"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_mapping_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "semantic_entity_id",
            name="uq_bse_mapping_entity_scope",
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", "reference_key_hash", name="uq_bse_mapping_scope_key"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "reference_key_hash",
            "version",
            name="uq_bse_mapping_reference_version",
        ),
    )
    op.create_index(
        "ix_bse_mappings_entity",
        "business_semantic_mapping_versions",
        ["tenant_id", "company_id", "semantic_entity_id", "created_at"],
    )
    op.create_index(
        "ix_bse_mappings_reference_latest",
        "business_semantic_mapping_versions",
        ["tenant_id", "company_id", "reference_key_hash", "version"],
    )
    _company_rls("business_semantic_mapping_versions")

    op.create_table(
        "business_semantic_relationship_versions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("relationship_key_hash", sa.String(length=64), nullable=False),
        sa.Column("subject_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(length=64), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("evidence_kind", sa.String(length=24), nullable=False),
        sa.Column("subject_mapping_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("object_mapping_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("evidence_note", sa.Text(), nullable=False),
        sa.Column("supersedes_relationship_version_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_bse_relationship_version"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_relationship_version_id IS NULL) OR "
            "(version > 1 AND supersedes_relationship_version_id IS NOT NULL)",
            name="ck_bse_relationship_supersedes_version",
        ),
        sa.CheckConstraint(
            "relationship_key_hash ~ '^[0-9a-f]{64}$'", name="ck_bse_relationship_key_hash"
        ),
        sa.CheckConstraint(
            "relationship_type IN ('customer_has_sales_transaction', 'supplier_has_expense', "
            "'employee_belongs_to_department', 'department_in_business_unit', "
            "'project_assigned_to_employee')",
            name="ck_bse_relationship_type",
        ),
        sa.CheckConstraint("status IN ('active', 'retracted')", name="ck_bse_relationship_status"),
        sa.CheckConstraint(
            "evidence_kind IN ('source_verified', 'manual_assertion')",
            name="ck_bse_relationship_evidence_kind",
        ),
        sa.CheckConstraint(
            "subject_entity_id <> object_entity_id", name="ck_bse_relationship_not_self"
        ),
        sa.CheckConstraint(
            "length(btrim(evidence_note)) BETWEEN 1 AND 500",
            name="ck_bse_relationship_evidence_note",
        ),
        sa.CheckConstraint(
            "(evidence_kind = 'source_verified' AND subject_mapping_version_id IS NOT NULL "
            "AND object_mapping_version_id IS NOT NULL) OR "
            "(evidence_kind = 'manual_assertion' AND "
            "((subject_mapping_version_id IS NULL AND object_mapping_version_id IS NULL) OR "
            "(subject_mapping_version_id IS NOT NULL AND object_mapping_version_id IS NOT NULL)))",
            name="ck_bse_relationship_evidence_mappings",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "subject_entity_id"],
            [
                "business_semantic_entities.tenant_id",
                "business_semantic_entities.company_id",
                "business_semantic_entities.id",
            ],
            "fk_bse_relationship_subject",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "object_entity_id"],
            [
                "business_semantic_entities.tenant_id",
                "business_semantic_entities.company_id",
                "business_semantic_entities.id",
            ],
            "fk_bse_relationship_object",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "subject_mapping_version_id", "subject_entity_id"],
            [
                "business_semantic_mapping_versions.tenant_id",
                "business_semantic_mapping_versions.company_id",
                "business_semantic_mapping_versions.id",
                "business_semantic_mapping_versions.semantic_entity_id",
            ],
            "fk_bse_relationship_subject_mapping",
        ),
        _scope_fk(
            ["tenant_id", "company_id", "object_mapping_version_id", "object_entity_id"],
            [
                "business_semantic_mapping_versions.tenant_id",
                "business_semantic_mapping_versions.company_id",
                "business_semantic_mapping_versions.id",
                "business_semantic_mapping_versions.semantic_entity_id",
            ],
            "fk_bse_relationship_object_mapping",
        ),
        _member_fk("created_by_user_id", "fk_bse_relationship_actor"),
        _scope_fk(
            [
                "tenant_id",
                "company_id",
                "supersedes_relationship_version_id",
                "relationship_key_hash",
            ],
            [
                "business_semantic_relationship_versions.tenant_id",
                "business_semantic_relationship_versions.company_id",
                "business_semantic_relationship_versions.id",
                "business_semantic_relationship_versions.relationship_key_hash",
            ],
            "fk_bse_relationship_supersedes",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_semantic_relationship_versions"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_relationship_scope_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "relationship_key_hash",
            name="uq_bse_relationship_scope_key",
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "relationship_key_hash",
            "version",
            name="uq_bse_relationship_key_version",
        ),
    )
    op.create_index(
        "ix_bse_relationship_subject",
        "business_semantic_relationship_versions",
        ["tenant_id", "company_id", "subject_entity_id", "created_at"],
    )
    op.create_index(
        "ix_bse_relationship_object",
        "business_semantic_relationship_versions",
        ["tenant_id", "company_id", "object_entity_id", "created_at"],
    )
    op.create_index(
        "ix_bse_relationship_latest",
        "business_semantic_relationship_versions",
        ["tenant_id", "company_id", "relationship_key_hash", "version"],
    )
    _company_rls("business_semantic_relationship_versions")

    append_only_tables = (
        "business_semantic_entities",
        "business_semantic_mapping_versions",
        "business_semantic_relationship_versions",
    )
    op.execute(
        """
        CREATE FUNCTION businessflow_semantic_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'Business Semantic definitions and versions are append-only'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_semantic_append_only';
        END;
        $$;
        """
    )
    for table in append_only_tables:
        op.execute(
            sa.text(
                f"CREATE TRIGGER trg_{table}_append_only BEFORE UPDATE OR DELETE ON {table} "
                "FOR EACH ROW EXECUTE FUNCTION businessflow_semantic_append_only()"
            )
        )

    op.execute(
        "REVOKE ALL ON business_semantic_entities, business_semantic_mapping_versions, "
        "business_semantic_relationship_versions FROM PUBLIC, bf_runtime"
    )
    op.execute(
        "GRANT SELECT, INSERT ON business_semantic_entities, business_semantic_mapping_versions, "
        "business_semantic_relationship_versions TO bf_runtime"
    )


def downgrade() -> None:
    _require_migrator()
    tables = (
        "business_semantic_entities",
        "business_semantic_mapping_versions",
        "business_semantic_relationship_versions",
    )
    for table in tables:
        op.execute(sa.text(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY"))
    op.execute(
        """
        DO $$
        BEGIN
          IF EXISTS (SELECT 1 FROM business_semantic_relationship_versions)
             OR EXISTS (SELECT 1 FROM business_semantic_mapping_versions)
             OR EXISTS (SELECT 1 FROM business_semantic_entities) THEN
            RAISE EXCEPTION 'Cannot downgrade while semantic data is retained';
          END IF;
        END;
        $$;
        """
    )
    for table in tables:
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
    for table in (
        "business_semantic_relationship_versions",
        "business_semantic_mapping_versions",
        "business_semantic_entities",
    ):
        op.execute(sa.text(f"DROP TRIGGER IF EXISTS trg_{table}_append_only ON {table}"))
    op.execute("DROP FUNCTION IF EXISTS businessflow_semantic_append_only()")
    op.drop_table("business_semantic_relationship_versions")
    op.drop_table("business_semantic_mapping_versions")
    op.drop_table("business_semantic_entities")
    op.drop_constraint(
        "uq_udf_record_revision_source_scope",
        "canonical_business_record_revisions",
        type_="unique",
    )
    op.drop_constraint(
        "uq_udf_canonical_record_source_scope_id",
        "canonical_business_records",
        type_="unique",
    )
