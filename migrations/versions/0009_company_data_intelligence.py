"""Add M12 company data contracts, source registry, quality snapshots, and M10 binding.

Revision ID: 0009_company_data_intelligence
Revises: 0008_monthly_position_review
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0009_company_data_intelligence"
down_revision = "0008_monthly_position_review"
branch_labels = None
depends_on = None

CONTRACT_SEEDS: tuple[tuple[str, str, str, str, str], ...] = (
    (
        "businessflow.m4.employees",
        "1.0.0",
        "employees",
        (
            '{"dataset_type":"employees","fields":[{"name":"employee_code","required"'
            ':true,"description":"Unique company employee code.","aliases":["employee'
            ' code","staff code","code"]},{"name":"full_name","required":true,"descri'
            'ption":"Employee\'s full name.","aliases":["full name","employee name","s'
            'taff name","name"]},{"name":"job_title","required":true,"description":"E'
            'mployee\'s job title.","aliases":["job title","title","position"]},{"name'
            '":"standard_weekly_capacity_hours","required":true,"description":"Standa'
            'rd weekly capacity in hours; a value is required.","aliases":["standard '
            'weekly capacity hours","weekly capacity hours","standard capacity hours"'
            ']},{"name":"department_id","required":false,"description":"Existing acti'
            've department UUID.","aliases":["department id","department uuid","dept '
            'id"]},{"name":"department_name","required":false,"description":"Name of '
            'an existing active department.","aliases":["department","department name'
            '","dept name"]},{"name":"employment_status","required":false,"descriptio'
            'n":"One of active, on_leave, inactive, or terminated.","aliases":["emplo'
            'yment status","employee status"]},{"name":"employment_start_date","requi'
            'red":false,"description":"Employment start date in ISO format (YYYY-MM-D'
            'D).","aliases":["employment start date","start date","hire date"]},{"nam'
            'e":"employment_end_date","required":false,"description":"Employment end '
            'date in ISO format (YYYY-MM-DD).","aliases":["employment end date","end '
            'date","termination date"]}],"required":["employee_code","full_name","job'
            '_title","standard_weekly_capacity_hours"],"required_one_of":[],"mutually'
            '_exclusive":[["department_id","department_name"]],"defaults":["employmen'
            't_status"]}'
        ),
        "87f2d047afcf9eab7e6cd0b59ef7bd4df8e513702fb0ff6bed98d63dc6039e3e",
    ),
    (
        "businessflow.m4.skills",
        "1.0.0",
        "skills",
        (
            '{"dataset_type":"skills","fields":[{"name":"name","required":true,"descr'
            'iption":"Unique tenant skill name.","aliases":["skill","skill name","ski'
            'll title"]},{"name":"description","required":false,"description":"Option'
            'al description.","aliases":["skill description","description","details"]'
            '}],"required":["name"],"required_one_of":[],"mutually_exclusive":[],"def'
            'aults":[]}'
        ),
        "8c3957e93d1d079f35ff0c3c060160f5a7d6f5cb5415688ddd200b171ec6b148",
    ),
    (
        "businessflow.m4.employee_skills",
        "1.0.0",
        "employee_skills",
        (
            '{"dataset_type":"employee_skills","fields":[{"name":"employee_code","req'
            'uired":false,"description":"Existing employee code.","aliases":["employe'
            'e code","staff code"]},{"name":"employee_id","required":false,"descripti'
            'on":"Existing employee UUID.","aliases":["employee id","employee uuid","'
            'staff id"]},{"name":"skill_name","required":false,"description":"Existin'
            'g active skill name.","aliases":["skill","skill name"]},{"name":"skill_i'
            'd","required":false,"description":"Existing active skill UUID.","aliases'
            '":["skill id","skill uuid"]},{"name":"proficiency","required":true,"desc'
            'ription":"Proficiency level from 1 to 5.","aliases":["proficiency","prof'
            'iciency level","skill level"]}],"required":["proficiency"],"required_one'
            '_of":[["employee_code","employee_id"],["skill_id","skill_name"]],"mutual'
            'ly_exclusive":[["employee_code","employee_id"],["skill_id","skill_name"]'
            '],"defaults":[]}'
        ),
        "e574e14d4da9a933a2c99acfc9587f03efa063af7564e67a62f09fdf2cf53c8b",
    ),
    (
        "businessflow.m4.weekly_capacity",
        "1.0.0",
        "weekly_capacity",
        (
            '{"dataset_type":"weekly_capacity","fields":[{"name":"employee_code","req'
            'uired":false,"description":"Existing employee code.","aliases":["employe'
            'e code","staff code"]},{"name":"employee_id","required":false,"descripti'
            'on":"Existing employee UUID.","aliases":["employee id","employee uuid","'
            'staff id"]},{"name":"week_start_date","required":true,"description":"Mon'
            'day date identifying the capacity week.","aliases":["week start","week s'
            'tart date","week of"]},{"name":"availability_status","required":true,"de'
            'scription":"One of confirmed, estimated, or unknown.","aliases":["availa'
            'bility","availability status","capacity status"]},{"name":"scheduled_hou'
            'rs","required":false,"description":"Required for confirmed or estimated '
            'availability; do not substitute zero for missing data.","aliases":["sche'
            'duled hours","weekly scheduled hours"]},{"name":"non_project_hours","req'
            'uired":false,"description":"Required for confirmed or estimated availabi'
            'lity.","aliases":["non project hours","non-project hours","internal hour'
            's"]},{"name":"unavailable_hours","required":false,"description":"Require'
            'd for confirmed or estimated availability.","aliases":["unavailable hour'
            's","leave hours"]},{"name":"source","required":false,"description":"Opti'
            'onal source: manual, calendar, or manager_adjustment.","aliases":["sourc'
            'e","capacity source"]},{"name":"reason","required":false,"description":"'
            'Reason required for manager_adjustment source.","aliases":["reason","adj'
            'ustment reason"]}],"required":["availability_status","week_start_date"],'
            '"required_one_of":[["employee_code","employee_id"]],"mutually_exclusive"'
            ':[["employee_code","employee_id"]],"defaults":["source"]}'
        ),
        "06a4f990a9dac01318771d46fde3b6b09e16cc036de36e79a16ccd3a873846d7",
    ),
    (
        "businessflow.m4.projects",
        "1.0.0",
        "projects",
        (
            '{"dataset_type":"projects","fields":[{"name":"project_code","required":t'
            'rue,"description":"Unique company project code.","aliases":["project cod'
            'e","project key","code"]},{"name":"name","required":true,"description":"'
            'Project name.","aliases":["project name","name","title"]},{"name":"descr'
            'iption","required":false,"description":"Optional description.","aliases"'
            ':["description","project description","details"]},{"name":"status","requ'
            'ired":false,"description":"New projects may be draft or planned; omitted'
            ' status defaults to draft.","aliases":["status","project status"]},{"nam'
            'e":"start_date","required":false,"description":"Optional project start d'
            'ate.","aliases":["start date","project start date"]},{"name":"planned_en'
            'd_date","required":false,"description":"Optional planned end date.","ali'
            'ases":["planned end","planned end date","target end date"]},{"name":"pro'
            'ject_manager_employee_code","required":false,"description":"Optional act'
            'ive employee code for the project manager.","aliases":["project manager '
            'employee code","manager employee code","manager code"]},{"name":"project'
            '_manager_employee_id","required":false,"description":"Optional active em'
            'ployee UUID for the project manager.","aliases":["project manager employ'
            'ee id","manager employee id","manager employee uuid"]}],"required":["nam'
            'e","project_code"],"required_one_of":[],"mutually_exclusive":[["project_'
            'manager_employee_code","project_manager_employee_id"]],"defaults":["stat'
            'us"]}'
        ),
        "c900d9eed5fd0c71c1983f9c0668778e13491e943abd338c96388b7a25961ad3",
    ),
    (
        "businessflow.m4.resource_assignments",
        "1.0.0",
        "resource_assignments",
        (
            '{"dataset_type":"resource_assignments","fields":[{"name":"project_code",'
            '"required":false,"description":"Existing project code.","aliases":["proj'
            'ect code","project key"]},{"name":"project_id","required":false,"descrip'
            'tion":"Existing project UUID.","aliases":["project id","project uuid"]},'
            '{"name":"employee_code","required":false,"description":"Existing employe'
            'e code.","aliases":["employee code","staff code"]},{"name":"employee_id"'
            ',"required":false,"description":"Existing employee UUID.","aliases":["em'
            'ployee id","employee uuid","staff id"]},{"name":"start_date","required":'
            'true,"description":"Assignment start date.","aliases":["start date","ass'
            'ignment start date"]},{"name":"end_date","required":true,"description":"'
            'Assignment end date.","aliases":["end date","assignment end date"]},{"na'
            'me":"planned_hours_per_week","required":true,"description":"Planned week'
            'ly hours, greater than zero and at most 168.","aliases":["planned hours '
            'per week","weekly hours","hours per week"]},{"name":"status","required":'
            'false,"description":"New assignments may be proposed or committed; omitt'
            'ed status defaults to proposed.","aliases":["status","assignment status"'
            ']},{"name":"notes","required":false,"description":"Optional notes; requi'
            'red for manager_adjustment source.","aliases":["notes","assignment notes'
            '"]},{"name":"source","required":false,"description":"Optional source: ma'
            'nual or manager_adjustment.","aliases":["source","assignment source"]}],'
            '"required":["end_date","planned_hours_per_week","start_date"],"required_'
            'one_of":[["project_code","project_id"],["employee_code","employee_id"]],'
            '"mutually_exclusive":[["project_code","project_id"],["employee_code","em'
            'ployee_id"]],"defaults":["status","source"]}'
        ),
        "fe005b970834ca1066df209c82a31bc2ce8248c45139bba5d204d379a2b740cc",
    ),
)


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

    op.create_table(
        "company_data_contracts",
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.String(length=32), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("definition_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
            "'projects', 'resource_assignments')",
            name="ck_company_data_contracts_dataset",
        ),
        sa.CheckConstraint(
            "length(contract_key) BETWEEN 1 AND 128", name="ck_company_data_contracts_key"
        ),
        sa.CheckConstraint(
            "length(version) BETWEEN 1 AND 32", name="ck_company_data_contracts_version"
        ),
        sa.CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_company_data_contracts_hash"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(definition) = 'object'", name="ck_company_data_contracts_definition"
        ),
        sa.PrimaryKeyConstraint("contract_key", "version", name="pk_company_data_contracts"),
        sa.UniqueConstraint(
            "contract_key",
            "version",
            "dataset_type",
            name="uq_company_data_contracts_contract_dataset",
        ),
    )
    op.create_index(
        "ix_company_data_contracts_dataset", "company_data_contracts", ["dataset_type", "version"]
    )
    for key, version, dataset, encoded_definition, definition_hash in CONTRACT_SEEDS:
        statement = sa.text(
            "INSERT INTO company_data_contracts "
            "(contract_key, version, dataset_type, definition, definition_hash) "
            "VALUES (:key, :version, :dataset, CAST(:definition AS jsonb), :definition_hash)"
        ).bindparams(
            sa.bindparam("key", value=key, type_=sa.String(length=128)),
            sa.bindparam("version", value=version, type_=sa.String(length=32)),
            sa.bindparam("dataset", value=dataset, type_=sa.String(length=32)),
            sa.bindparam("definition", value=encoded_definition, type_=sa.Text()),
            sa.bindparam("definition_hash", value=definition_hash, type_=sa.String(length=64)),
        )
        op.execute(statement)

    op.create_table(
        "company_data_sources",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_key", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=160), nullable=False),
        sa.Column("dataset_type", sa.String(length=32), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("owner_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("registered_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("freshness_max_age_hours", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "source_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_company_data_sources_key"
        ),
        sa.CheckConstraint(
            "length(display_name) BETWEEN 1 AND 160", name="ck_company_data_sources_name"
        ),
        sa.CheckConstraint(
            "dataset_type IN ('employees', 'skills', 'employee_skills', 'weekly_capacity', "
            "'projects', 'resource_assignments')",
            name="ck_company_data_sources_dataset",
        ),
        sa.CheckConstraint(
            "freshness_max_age_hours IS NULL OR freshness_max_age_hours > 0",
            name="ck_company_data_sources_freshness_policy",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_company_data_sources_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_company_data_sources_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "owner_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_sources_owner_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "registered_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_sources_registrar_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version", "dataset_type"],
            [
                "company_data_contracts.contract_key",
                "company_data_contracts.version",
                "company_data_contracts.dataset_type",
            ],
            name="fk_company_data_sources_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_data_sources"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_company_data_sources_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "contract_key",
            "contract_version",
            name="uq_company_data_sources_contract_binding",
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "source_key", name="uq_company_data_sources_key"
        ),
    )
    op.create_index(
        "ix_company_data_sources_company_dataset",
        "company_data_sources",
        ["tenant_id", "company_id", "dataset_type"],
    )

    op.create_table(
        "company_data_ingestions",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("source_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("import_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("ingested_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("contract_key", sa.String(length=128), nullable=False),
        sa.Column("contract_version", sa.String(length=32), nullable=False),
        sa.Column("ingestion_status", sa.String(length=32), nullable=False),
        sa.Column("quality_status", sa.String(length=16), nullable=False),
        sa.Column("freshness_status", sa.String(length=24), nullable=False),
        sa.Column("source_as_of_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("freshness_max_age_hours_snapshot", sa.Integer(), nullable=True),
        sa.Column("freshness_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("contract_hash", sa.String(length=64), nullable=False),
        sa.Column("file_hash", sa.String(length=64), nullable=False),
        sa.Column("validation_hash", sa.String(length=64), nullable=True),
        sa.Column("mapping_version", sa.Integer(), nullable=False),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("quality_findings", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contract_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "ingestion_status IN ('succeeded', 'succeeded_with_warnings', 'blocked')",
            name="ck_company_data_ingestions_status",
        ),
        sa.CheckConstraint(
            "quality_status IN ('passed', 'warning', 'failed')",
            name="ck_company_data_ingestions_quality",
        ),
        sa.CheckConstraint(
            "freshness_status IN ('fresh', 'stale', 'policy_missing', "
            "'source_as_of_missing', 'future_as_of')",
            name="ck_company_data_ingestions_freshness",
        ),
        sa.CheckConstraint(
            "freshness_max_age_hours_snapshot IS NULL OR freshness_max_age_hours_snapshot > 0",
            name="ck_company_data_ingestions_freshness_policy",
        ),
        sa.CheckConstraint(
            "mapping_version >= 0 AND total_rows >= 0", name="ck_company_data_ingestions_counts"
        ),
        sa.CheckConstraint(
            "file_hash ~ '^[0-9a-f]{64}$'", name="ck_company_data_ingestions_file_hash"
        ),
        sa.CheckConstraint(
            "validation_hash IS NULL OR validation_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_data_ingestions_validation_hash",
        ),
        sa.CheckConstraint(
            "contract_hash ~ '^[0-9a-f]{64}$' AND snapshot_hash ~ '^[0-9a-f]{64}$'",
            name="ck_company_data_ingestions_snapshot_hashes",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(quality_findings) = 'array'", name="ck_company_data_ingestions_findings"
        ),
        sa.CheckConstraint(
            "jsonb_typeof(contract_snapshot) = 'object'",
            name="ck_company_data_ingestions_contract_snapshot",
        ),
        sa.CheckConstraint(
            "jsonb_typeof(snapshot) = 'object'", name="ck_company_data_ingestions_snapshot"
        ),
        sa.CheckConstraint(
            "(ingestion_status = 'succeeded' AND quality_status = 'passed') OR "
            "(ingestion_status = 'succeeded_with_warnings' AND quality_status = 'warning') OR "
            "(ingestion_status = 'blocked' AND quality_status = 'failed')",
            name="ck_company_data_ingestions_state_pair",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_company_data_ingestions_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "contract_key", "contract_version"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
                "company_data_sources.contract_key",
                "company_data_sources.contract_version",
            ],
            name="fk_company_data_ingestions_source",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "import_id"],
            ["data_imports.tenant_id", "data_imports.company_id", "data_imports.id"],
            name="fk_company_data_ingestions_m4_import",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "ingested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_company_data_ingestions_actor_membership",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["contract_key", "contract_version"],
            ["company_data_contracts.contract_key", "company_data_contracts.version"],
            name="fk_company_data_ingestions_contract",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_data_ingestions"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_company_data_ingestions_scope_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "import_id", name="uq_company_data_ingestions_import"
        ),
    )
    op.create_index(
        "ix_company_data_ingestions_source_created",
        "company_data_ingestions",
        ["tenant_id", "company_id", "source_id", "created_at"],
    )
    op.create_index(
        "ix_company_data_ingestions_quality",
        "company_data_ingestions",
        ["tenant_id", "company_id", "quality_status", "freshness_status"],
    )

    op.add_column(
        "position_review_operational_imports",
        sa.Column("company_data_ingestion_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_position_review_operational_imports_company_data",
        "position_review_operational_imports",
        "company_data_ingestions",
        ["tenant_id", "company_id", "company_data_ingestion_id"],
        ["tenant_id", "company_id", "id"],
        ondelete="RESTRICT",
    )

    for table in ("company_data_sources", "company_data_ingestions"):
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
        CREATE FUNCTION businessflow_company_data_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION
            'company-data contracts, source registrations, and ingestion snapshots are immutable'
            USING ERRCODE = '55000', CONSTRAINT = 'businessflow_company_data_append_only';
        END;
        $$;
        CREATE TRIGGER trg_company_data_contracts_append_only
          BEFORE UPDATE OR DELETE ON company_data_contracts
          FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only();
        CREATE TRIGGER trg_company_data_sources_append_only
          BEFORE UPDATE OR DELETE ON company_data_sources
          FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only();
        CREATE TRIGGER trg_company_data_ingestions_append_only
          BEFORE UPDATE OR DELETE ON company_data_ingestions
          FOR EACH ROW EXECUTE FUNCTION businessflow_company_data_append_only();

        CREATE FUNCTION businessflow_m12_m10_bridge_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE
          linked_import uuid;
          ingestion_state varchar(32);
          quality_state varchar(16);
          freshness_state varchar(24);
          source_dataset varchar(32);
          actual_file_hash varchar(64);
          actual_validation_hash varchar(64);
          actual_mapping_version integer;
          source_as_of timestamptz;
          freshness_limit_hours integer;
          source_freshness_limit_hours integer;
          ingestion_contract_key varchar(128);
          ingestion_contract_version varchar(32);
          actual_contract_hash varchar(64);
          contract_definition_hash varchar(64);
          contract_snapshot jsonb;
          contract_definition jsonb;
          quality_items jsonb;
        BEGIN
          IF NEW.company_data_ingestion_id IS NULL THEN
            RETURN NEW;
          END IF;
          SELECT i.import_id, i.ingestion_status, i.quality_status, i.freshness_status,
                 s.dataset_type, i.file_hash, i.validation_hash, i.mapping_version,
                 i.source_as_of_at, i.freshness_max_age_hours_snapshot,
                 s.freshness_max_age_hours, i.contract_key, i.contract_version,
                 i.contract_hash, c.definition_hash, i.contract_snapshot,
                 c.definition, i.quality_findings
            INTO linked_import, ingestion_state, quality_state, freshness_state,
                 source_dataset, actual_file_hash, actual_validation_hash,
                 actual_mapping_version, source_as_of, freshness_limit_hours,
                 source_freshness_limit_hours, ingestion_contract_key,
                 ingestion_contract_version, actual_contract_hash,
                 contract_definition_hash, contract_snapshot, contract_definition, quality_items
            FROM company_data_ingestions AS i
            JOIN company_data_sources AS s
              ON s.tenant_id = i.tenant_id AND s.company_id = i.company_id
             AND s.id = i.source_id
            JOIN company_data_contracts AS c
              ON c.contract_key = i.contract_key AND c.version = i.contract_version
            WHERE i.tenant_id = NEW.tenant_id AND i.company_id = NEW.company_id
              AND i.id = NEW.company_data_ingestion_id;
          IF NOT FOUND THEN
            RAISE EXCEPTION 'M10 company-data reference is not visible in this Company'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_company_data_binding_guard';
          END IF;
          IF linked_import IS DISTINCT FROM NEW.import_id
             OR source_dataset IS DISTINCT FROM NEW.dataset_type
             OR actual_file_hash IS DISTINCT FROM NEW.file_hash
             OR actual_validation_hash IS DISTINCT FROM NEW.validation_hash
             OR actual_mapping_version IS DISTINCT FROM NEW.mapping_version
             OR freshness_limit_hours IS DISTINCT FROM source_freshness_limit_hours
             OR actual_contract_hash IS DISTINCT FROM contract_definition_hash
             OR contract_snapshot->>'contract_key' IS DISTINCT FROM ingestion_contract_key
             OR contract_snapshot->>'version' IS DISTINCT FROM ingestion_contract_version
             OR contract_snapshot->>'definition_hash' IS DISTINCT FROM contract_definition_hash
             OR contract_snapshot->>'dataset_type' IS DISTINCT FROM source_dataset
             OR contract_snapshot->'definition' IS DISTINCT FROM contract_definition
             OR freshness_state <> 'fresh'
             OR source_as_of IS NULL
             OR freshness_limit_hours IS NULL
             OR source_as_of > clock_timestamp()
             OR source_as_of + make_interval(hours => freshness_limit_hours) < clock_timestamp()
             OR ingestion_state = 'blocked'
             OR (quality_state <> 'passed' AND NOT (
                  quality_state = 'warning'
                  AND NEW.dataset_type IN ('employees', 'projects')
                  AND EXISTS (
                    SELECT 1 FROM jsonb_array_elements(quality_items) finding
                    WHERE finding->>'severity' = 'warning'
                  )
                  AND NOT EXISTS (
                    SELECT 1 FROM jsonb_array_elements(quality_items) finding
                    WHERE finding->>'severity' = 'warning'
                      AND COALESCE(finding->>'issue_code', '') NOT IN
                        ('duplicate_existing', 'duplicate_in_file')
                  )
                )) THEN
            RAISE EXCEPTION
              'M10 company-data source must be the selected fresh, validated M4 version'
              USING ERRCODE = '23514', CONSTRAINT = 'position_review_company_data_binding_guard';
          END IF;
          RETURN NEW;
        END;
        $$;
        CREATE TRIGGER trg_position_review_company_data_guard
          BEFORE INSERT ON position_review_operational_imports
          FOR EACH ROW EXECUTE FUNCTION businessflow_m12_m10_bridge_guard();
        """
    )
    op.execute(
        """
        REVOKE ALL ON company_data_contracts, company_data_sources,
          company_data_ingestions FROM PUBLIC, bf_runtime;
        GRANT SELECT ON company_data_contracts TO bf_runtime;
        GRANT SELECT, INSERT ON company_data_sources, company_data_ingestions TO bf_runtime;
        REVOKE UPDATE, DELETE, TRUNCATE ON company_data_contracts, company_data_sources,
          company_data_ingestions FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_position_review_company_data_guard "
        "ON position_review_operational_imports"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_m12_m10_bridge_guard()")
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_ingestions_append_only ON company_data_ingestions"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_sources_append_only ON company_data_sources"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_company_data_contracts_append_only ON company_data_contracts"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_company_data_append_only()")
    op.drop_constraint(
        "fk_position_review_operational_imports_company_data",
        "position_review_operational_imports",
        type_="foreignkey",
    )
    op.drop_column("position_review_operational_imports", "company_data_ingestion_id")
    op.drop_table("company_data_ingestions")
    op.drop_table("company_data_sources")
    op.drop_index("ix_company_data_contracts_dataset", table_name="company_data_contracts")
    op.drop_table("company_data_contracts")
