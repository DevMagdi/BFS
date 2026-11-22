"""Add tenant-isolated organization, workforce, capacity, and project context.

Revision ID: 0002_company_context
Revises: 0001_backend_foundation
Create Date: 2026-11-21
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_company_context"
down_revision = "0001_backend_foundation"
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

    op.create_table(
        "business_units",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "name_key",
            sa.String(length=120),
            sa.Computed("lower(btrim(name))", persisted=True),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_business_units_status"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_business_units_name"),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_business_units_description_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_business_units_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_business_units_company",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_business_units"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_business_units_tenant_company_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "name_key", name="uq_business_units_company_name"
        ),
    )
    op.create_index(
        "ix_business_units_tenant_company_status",
        "business_units",
        ["tenant_id", "company_id", "status"],
    )

    op.create_table(
        "departments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("business_unit_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "name_key",
            sa.String(length=120),
            sa.Computed("lower(btrim(name))", persisted=True),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_departments_status"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_departments_name"),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_departments_description_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_departments_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_departments_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "business_unit_id"],
            ["business_units.tenant_id", "business_units.company_id", "business_units.id"],
            name="fk_departments_business_unit",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_departments"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_departments_tenant_company_id"
        ),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "name_key", name="uq_departments_company_name"
        ),
    )
    op.create_index(
        "ix_departments_tenant_company_status",
        "departments",
        ["tenant_id", "company_id", "status"],
    )
    op.create_index(
        "ix_departments_business_unit",
        "departments",
        ["tenant_id", "company_id", "business_unit_id"],
    )

    op.create_table(
        "employees",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("employee_code", sa.String(length=64), nullable=False),
        sa.Column(
            "employee_code_key",
            sa.String(length=64),
            sa.Computed("lower(btrim(employee_code))", persisted=True),
            nullable=False,
        ),
        sa.Column("full_name", sa.String(length=160), nullable=False),
        sa.Column("job_title", sa.String(length=120), nullable=False),
        sa.Column("department_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "employment_status", sa.String(length=16), server_default="active", nullable=False
        ),
        sa.Column("employment_start_date", sa.Date(), nullable=True),
        sa.Column("employment_end_date", sa.Date(), nullable=True),
        sa.Column(
            "standard_weekly_capacity_hours", sa.Numeric(precision=6, scale=2), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "length(btrim(employee_code)) BETWEEN 1 AND 64", name="ck_employees_code"
        ),
        sa.CheckConstraint(
            "length(btrim(full_name)) BETWEEN 1 AND 160", name="ck_employees_full_name"
        ),
        sa.CheckConstraint(
            "length(btrim(job_title)) BETWEEN 1 AND 120", name="ck_employees_job_title"
        ),
        sa.CheckConstraint(
            "employment_status IN ('active', 'on_leave', 'inactive', 'terminated')",
            name="ck_employees_employment_status",
        ),
        sa.CheckConstraint(
            "standard_weekly_capacity_hours BETWEEN 0.01 AND 168",
            name="ck_employees_standard_weekly_capacity",
        ),
        sa.CheckConstraint(
            "employment_start_date IS NULL OR employment_end_date IS NULL "
            "OR employment_end_date >= employment_start_date",
            name="ck_employees_employment_dates",
        ),
        sa.CheckConstraint(
            "employment_status <> 'terminated' OR employment_end_date IS NOT NULL",
            name="ck_employees_terminated_date",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employees_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_employees_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "department_id"],
            ["departments.tenant_id", "departments.company_id", "departments.id"],
            name="fk_employees_department",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employees"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_employees_tenant_company_id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "employee_code_key", name="uq_employees_company_code"
        ),
    )
    op.create_index(
        "ix_employees_tenant_company_status",
        "employees",
        ["tenant_id", "company_id", "employment_status"],
    )
    op.create_index(
        "ix_employees_department", "employees", ["tenant_id", "company_id", "department_id"]
    )

    op.create_table(
        "skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "name_key",
            sa.String(length=120),
            sa.Computed("lower(btrim(name))", persisted=True),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_skills_name"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_skills_status"),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_skills_description_length",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_skills_tenant", ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skills"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_skills_tenant_id"),
        sa.UniqueConstraint("tenant_id", "name_key", name="uq_skills_tenant_name"),
    )
    op.create_index("ix_skills_tenant_status", "skills", ["tenant_id", "status"])

    op.create_table(
        "employee_skills",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("employee_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("skill_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("proficiency", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("proficiency BETWEEN 1 AND 5", name="ck_employee_skills_proficiency"),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="ck_employee_skills_status"),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employee_skills_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_employee_skills_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            name="fk_employee_skills_skill",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_skills"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "employee_id", "skill_id", name="uq_employee_skills_pair"
        ),
    )
    op.create_index(
        "ix_employee_skills_employee_status",
        "employee_skills",
        ["tenant_id", "company_id", "employee_id", "status"],
    )
    op.create_index(
        "ix_employee_skills_skill", "employee_skills", ["tenant_id", "skill_id", "status"]
    )

    op.create_table(
        "employee_weekly_capacity",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("employee_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("week_start_date", sa.Date(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("supersedes_version", sa.Integer(), nullable=True),
        sa.Column("availability_status", sa.String(length=16), nullable=False),
        sa.Column("scheduled_hours", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("non_project_hours", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("unavailable_hours", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column("reason", sa.String(length=500), nullable=True),
        sa.Column("created_by_user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("version >= 1", name="ck_employee_capacity_version"),
        sa.CheckConstraint(
            "(version = 1 AND supersedes_version IS NULL) OR "
            "(version > 1 AND supersedes_version = version - 1)",
            name="ck_employee_capacity_version_chain",
        ),
        sa.CheckConstraint(
            "EXTRACT(ISODOW FROM week_start_date) = 1", name="ck_employee_capacity_monday_start"
        ),
        sa.CheckConstraint(
            "availability_status IN ('confirmed', 'estimated', 'unknown')",
            name="ck_employee_capacity_status",
        ),
        sa.CheckConstraint(
            "(availability_status = 'unknown' AND scheduled_hours IS NULL "
            "AND non_project_hours IS NULL AND unavailable_hours IS NULL) OR "
            "(availability_status IN ('confirmed', 'estimated') AND scheduled_hours IS NOT NULL "
            "AND non_project_hours IS NOT NULL AND unavailable_hours IS NOT NULL "
            "AND non_project_hours + unavailable_hours <= scheduled_hours)",
            name="ck_employee_capacity_values",
        ),
        sa.CheckConstraint(
            "scheduled_hours IS NULL OR scheduled_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_scheduled_hours",
        ),
        sa.CheckConstraint(
            "non_project_hours IS NULL OR non_project_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_non_project_hours",
        ),
        sa.CheckConstraint(
            "unavailable_hours IS NULL OR unavailable_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_unavailable_hours",
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'calendar', 'manager_adjustment')",
            name="ck_employee_capacity_source",
        ),
        sa.CheckConstraint(
            "source <> 'manager_adjustment' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_employee_capacity_adjustment_reason",
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(reason) <= 500", name="ck_employee_capacity_reason_length"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employee_capacity_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_employee_capacity_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id", "week_start_date", "supersedes_version"],
            [
                "employee_weekly_capacity.tenant_id",
                "employee_weekly_capacity.company_id",
                "employee_weekly_capacity.employee_id",
                "employee_weekly_capacity.week_start_date",
                "employee_weekly_capacity.version",
            ],
            name="fk_employee_capacity_previous_version",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_employee_capacity_creator_membership",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_employee_weekly_capacity"),
        sa.UniqueConstraint(
            "tenant_id",
            "company_id",
            "employee_id",
            "week_start_date",
            "version",
            name="uq_employee_capacity_week_version",
        ),
    )
    op.create_index(
        "ix_employee_capacity_week",
        "employee_weekly_capacity",
        ["tenant_id", "company_id", "week_start_date", "employee_id", "version"],
    )

    op.create_table(
        "projects",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_code", sa.String(length=64), nullable=False),
        sa.Column(
            "project_code_key",
            sa.String(length=64),
            sa.Computed("lower(btrim(project_code))", persisted=True),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=16), server_default="draft", nullable=False),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("planned_end_date", sa.Date(), nullable=True),
        sa.Column("actual_end_date", sa.Date(), nullable=True),
        sa.Column("project_manager_employee_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("length(btrim(project_code)) BETWEEN 1 AND 64", name="ck_projects_code"),
        sa.CheckConstraint("length(btrim(name)) BETWEEN 1 AND 160", name="ck_projects_name"),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 4000",
            name="ck_projects_description_length",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'planned', 'active', 'on_hold', 'completed', 'cancelled')",
            name="ck_projects_status",
        ),
        sa.CheckConstraint(
            "planned_end_date IS NULL OR start_date IS NULL OR planned_end_date >= start_date",
            name="ck_projects_planned_dates",
        ),
        sa.CheckConstraint(
            "actual_end_date IS NULL OR start_date IS NULL OR actual_end_date >= start_date",
            name="ck_projects_actual_dates",
        ),
        sa.CheckConstraint(
            "status <> 'completed' OR actual_end_date IS NOT NULL",
            name="ck_projects_completed_actual_end",
        ),
        sa.CheckConstraint(
            "status = 'completed' OR actual_end_date IS NULL",
            name="ck_projects_actual_end_completed",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_projects_tenant", ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_projects_company",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_manager_employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_projects_manager",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
        sa.UniqueConstraint("tenant_id", "company_id", "id", name="uq_projects_tenant_company_id"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "project_code_key", name="uq_projects_company_code"
        ),
    )
    op.create_index(
        "ix_projects_tenant_company_status", "projects", ["tenant_id", "company_id", "status"]
    )
    op.create_index(
        "ix_projects_manager",
        "projects",
        ["tenant_id", "company_id", "project_manager_employee_id"],
    )

    op.create_table(
        "resource_assignments",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("tenant_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("project_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("employee_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("planned_hours_per_week", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="proposed", nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=32), server_default="manual", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("end_date >= start_date", name="ck_resource_assignments_dates"),
        sa.CheckConstraint(
            "planned_hours_per_week BETWEEN 0.01 AND 168",
            name="ck_resource_assignments_weekly_hours",
        ),
        sa.CheckConstraint(
            "status IN ('proposed', 'committed', 'completed', 'cancelled')",
            name="ck_resource_assignments_status",
        ),
        sa.CheckConstraint(
            "notes IS NULL OR length(notes) <= 2000", name="ck_resource_assignments_notes_length"
        ),
        sa.CheckConstraint(
            "source IN ('manual', 'manager_adjustment')", name="ck_resource_assignments_source"
        ),
        sa.CheckConstraint(
            "source <> 'manager_adjustment' OR (notes IS NOT NULL AND length(btrim(notes)) > 0)",
            name="ck_resource_assignments_adjustment_note",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_resource_assignments_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_resource_assignments_employee",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_id"],
            ["projects.tenant_id", "projects.company_id", "projects.id"],
            name="fk_resource_assignments_project",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_resource_assignments"),
        sa.UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_resource_assignments_tenant_company_id"
        ),
    )
    op.create_index(
        "ix_resource_assignments_employee_period",
        "resource_assignments",
        ["tenant_id", "company_id", "employee_id", "start_date", "end_date", "status"],
    )
    op.create_index(
        "ix_resource_assignments_project_status",
        "resource_assignments",
        ["tenant_id", "company_id", "project_id", "status"],
    )

    for table in (
        "business_units",
        "departments",
        "employees",
        "skills",
        "employee_skills",
        "employee_weekly_capacity",
        "projects",
        "resource_assignments",
    ):
        op.execute(sa.text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        op.execute(sa.text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))
        op.execute(
            sa.text(
                f"CREATE POLICY {table}_tenant_context ON {table} "
                "USING (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid) "
                "WITH CHECK (tenant_id = NULLIF(current_setting('app.tenant_id', true), '')::uuid)"
            )
        )

    op.execute(
        """
        CREATE FUNCTION businessflow_capacity_is_immutable() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'weekly capacity versions are immutable; record a new version'
            USING ERRCODE = '55000', CONSTRAINT = 'employee_weekly_capacity_immutable';
        END;
        $$;
        CREATE TRIGGER trg_employee_weekly_capacity_immutable
          BEFORE UPDATE OR DELETE ON employee_weekly_capacity
          FOR EACH ROW EXECUTE FUNCTION businessflow_capacity_is_immutable();
        """
    )
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE ON business_units, departments, employees, skills,
          employee_skills, projects, resource_assignments TO bf_runtime;
        GRANT SELECT, INSERT ON employee_weekly_capacity TO bf_runtime;
        REVOKE DELETE, TRUNCATE ON business_units, departments, employees, skills,
          employee_skills, employee_weekly_capacity, projects, resource_assignments FROM bf_runtime;
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_employee_weekly_capacity_immutable ON employee_weekly_capacity"
    )
    op.execute("DROP FUNCTION IF EXISTS businessflow_capacity_is_immutable()")
    for table in (
        "resource_assignments",
        "projects",
        "employee_weekly_capacity",
        "employee_skills",
        "skills",
        "employees",
        "departments",
        "business_units",
    ):
        op.drop_table(table)
