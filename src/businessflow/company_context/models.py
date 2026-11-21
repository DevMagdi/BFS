from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base
from businessflow.models import TimestampMixin


class BusinessUnit(TimestampMixin, Base):
    __tablename__ = "business_units"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_business_units_tenant_company_id"
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "name_key", name="uq_business_units_company_name"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_business_units_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_business_units_company",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_business_units_status"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_business_units_name"),
        CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_business_units_description_length",
        ),
        Index("ix_business_units_tenant_company_status", "tenant_id", "company_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(
        String(120), Computed("lower(btrim(name))", persisted=True), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class Department(TimestampMixin, Base):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_departments_tenant_company_id"),
        UniqueConstraint("tenant_id", "company_id", "name_key", name="uq_departments_company_name"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_departments_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_departments_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "business_unit_id"],
            ["business_units.tenant_id", "business_units.company_id", "business_units.id"],
            name="fk_departments_business_unit",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_departments_status"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_departments_name"),
        CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_departments_description_length",
        ),
        Index("ix_departments_tenant_company_status", "tenant_id", "company_id", "status"),
        Index("ix_departments_business_unit", "tenant_id", "company_id", "business_unit_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    business_unit_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(
        String(120), Computed("lower(btrim(name))", persisted=True), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class Employee(TimestampMixin, Base):
    __tablename__ = "employees"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_employees_tenant_company_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "employee_code_key", name="uq_employees_company_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employees_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_employees_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "department_id"],
            ["departments.tenant_id", "departments.company_id", "departments.id"],
            name="fk_employees_department",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(employee_code)) BETWEEN 1 AND 64", name="ck_employees_code"),
        CheckConstraint(
            "length(btrim(full_name)) BETWEEN 1 AND 160", name="ck_employees_full_name"
        ),
        CheckConstraint(
            "length(btrim(job_title)) BETWEEN 1 AND 120", name="ck_employees_job_title"
        ),
        CheckConstraint(
            "employment_status IN ('active', 'on_leave', 'inactive', 'terminated')",
            name="ck_employees_employment_status",
        ),
        CheckConstraint(
            "standard_weekly_capacity_hours BETWEEN 0.01 AND 168",
            name="ck_employees_standard_weekly_capacity",
        ),
        CheckConstraint(
            "employment_start_date IS NULL OR employment_end_date IS NULL "
            "OR employment_end_date >= employment_start_date",
            name="ck_employees_employment_dates",
        ),
        CheckConstraint(
            "employment_status <> 'terminated' OR employment_end_date IS NOT NULL",
            name="ck_employees_terminated_date",
        ),
        Index("ix_employees_tenant_company_status", "tenant_id", "company_id", "employment_status"),
        Index("ix_employees_department", "tenant_id", "company_id", "department_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    employee_code: Mapped[str] = mapped_column(String(64), nullable=False)
    employee_code_key: Mapped[str] = mapped_column(
        String(64), Computed("lower(btrim(employee_code))", persisted=True), nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(160), nullable=False)
    job_title: Mapped[str] = mapped_column(String(120), nullable=False)
    department_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    employment_status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="active"
    )
    employment_start_date: Mapped[date | None] = mapped_column(Date)
    employment_end_date: Mapped[date | None] = mapped_column(Date)
    standard_weekly_capacity_hours: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)


class Skill(Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_skills_tenant_id"),
        UniqueConstraint("tenant_id", "name_key", name="uq_skills_tenant_name"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_skills_tenant", ondelete="RESTRICT"
        ),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 120", name="ck_skills_name"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_skills_status"),
        CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_skills_description_length",
        ),
        Index("ix_skills_tenant_status", "tenant_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    name_key: Mapped[str] = mapped_column(
        String(120), Computed("lower(btrim(name))", persisted=True), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class EmployeeSkill(TimestampMixin, Base):
    __tablename__ = "employee_skills"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "employee_id", "skill_id", name="uq_employee_skills_pair"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employee_skills_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_employee_skills_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "skill_id"],
            ["skills.tenant_id", "skills.id"],
            name="fk_employee_skills_skill",
            ondelete="RESTRICT",
        ),
        CheckConstraint("proficiency BETWEEN 1 AND 5", name="ck_employee_skills_proficiency"),
        CheckConstraint("status IN ('active', 'inactive')", name="ck_employee_skills_status"),
        Index(
            "ix_employee_skills_employee_status", "tenant_id", "company_id", "employee_id", "status"
        ),
        Index("ix_employee_skills_skill", "tenant_id", "skill_id", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    skill_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    proficiency: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class EmployeeWeeklyCapacity(Base):
    __tablename__ = "employee_weekly_capacity"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "employee_id",
            "week_start_date",
            "version",
            name="uq_employee_capacity_week_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_employee_capacity_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_employee_capacity_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
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
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_employee_capacity_creator_membership",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_employee_capacity_version"),
        CheckConstraint(
            "(version = 1 AND supersedes_version IS NULL) OR "
            "(version > 1 AND supersedes_version = version - 1)",
            name="ck_employee_capacity_version_chain",
        ),
        CheckConstraint(
            "EXTRACT(ISODOW FROM week_start_date) = 1", name="ck_employee_capacity_monday_start"
        ),
        CheckConstraint(
            "availability_status IN ('confirmed', 'estimated', 'unknown')",
            name="ck_employee_capacity_status",
        ),
        CheckConstraint(
            "(availability_status = 'unknown' AND scheduled_hours IS NULL "
            "AND non_project_hours IS NULL AND unavailable_hours IS NULL) OR "
            "(availability_status IN ('confirmed', 'estimated') AND scheduled_hours IS NOT NULL "
            "AND non_project_hours IS NOT NULL AND unavailable_hours IS NOT NULL "
            "AND non_project_hours + unavailable_hours <= scheduled_hours)",
            name="ck_employee_capacity_values",
        ),
        CheckConstraint(
            "scheduled_hours IS NULL OR scheduled_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_scheduled_hours",
        ),
        CheckConstraint(
            "non_project_hours IS NULL OR non_project_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_non_project_hours",
        ),
        CheckConstraint(
            "unavailable_hours IS NULL OR unavailable_hours BETWEEN 0 AND 168",
            name="ck_employee_capacity_unavailable_hours",
        ),
        CheckConstraint(
            "source IN ('manual', 'calendar', 'manager_adjustment')",
            name="ck_employee_capacity_source",
        ),
        CheckConstraint(
            "source <> 'manager_adjustment' OR (reason IS NOT NULL AND length(btrim(reason)) > 0)",
            name="ck_employee_capacity_adjustment_reason",
        ),
        CheckConstraint(
            "reason IS NULL OR length(reason) <= 500", name="ck_employee_capacity_reason_length"
        ),
        Index(
            "ix_employee_capacity_week",
            "tenant_id",
            "company_id",
            "week_start_date",
            "employee_id",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    week_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_version: Mapped[int | None] = mapped_column(Integer)
    availability_status: Mapped[str] = mapped_column(String(16), nullable=False)
    scheduled_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    non_project_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    unavailable_hours: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="manual")
    reason: Mapped[str | None] = mapped_column(String(500))
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class Project(TimestampMixin, Base):
    __tablename__ = "projects"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_projects_tenant_company_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "project_code_key", name="uq_projects_company_code"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_projects_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_projects_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_manager_employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_projects_manager",
            ondelete="RESTRICT",
        ),
        CheckConstraint("length(btrim(project_code)) BETWEEN 1 AND 64", name="ck_projects_code"),
        CheckConstraint("length(btrim(name)) BETWEEN 1 AND 160", name="ck_projects_name"),
        CheckConstraint(
            "description IS NULL OR length(description) <= 4000",
            name="ck_projects_description_length",
        ),
        CheckConstraint(
            "status IN ('draft', 'planned', 'active', 'on_hold', 'completed', 'cancelled')",
            name="ck_projects_status",
        ),
        CheckConstraint(
            "planned_end_date IS NULL OR start_date IS NULL OR planned_end_date >= start_date",
            name="ck_projects_planned_dates",
        ),
        CheckConstraint(
            "actual_end_date IS NULL OR start_date IS NULL OR actual_end_date >= start_date",
            name="ck_projects_actual_dates",
        ),
        CheckConstraint(
            "status <> 'completed' OR actual_end_date IS NOT NULL",
            name="ck_projects_completed_actual_end",
        ),
        CheckConstraint(
            "status = 'completed' OR actual_end_date IS NULL",
            name="ck_projects_actual_end_completed",
        ),
        Index("ix_projects_tenant_company_status", "tenant_id", "company_id", "status"),
        Index("ix_projects_manager", "tenant_id", "company_id", "project_manager_employee_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_code: Mapped[str] = mapped_column(String(64), nullable=False)
    project_code_key: Mapped[str] = mapped_column(
        String(64), Computed("lower(btrim(project_code))", persisted=True), nullable=False
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    start_date: Mapped[date | None] = mapped_column(Date)
    planned_end_date: Mapped[date | None] = mapped_column(Date)
    actual_end_date: Mapped[date | None] = mapped_column(Date)
    project_manager_employee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class ResourceAssignment(TimestampMixin, Base):
    __tablename__ = "resource_assignments"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "company_id", "id", name="uq_resource_assignments_tenant_company_id"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_resource_assignments_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "employee_id"],
            ["employees.tenant_id", "employees.company_id", "employees.id"],
            name="fk_resource_assignments_employee",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "project_id"],
            ["projects.tenant_id", "projects.company_id", "projects.id"],
            name="fk_resource_assignments_project",
            ondelete="RESTRICT",
        ),
        CheckConstraint("end_date >= start_date", name="ck_resource_assignments_dates"),
        CheckConstraint(
            "planned_hours_per_week BETWEEN 0.01 AND 168",
            name="ck_resource_assignments_weekly_hours",
        ),
        CheckConstraint(
            "status IN ('proposed', 'committed', 'completed', 'cancelled')",
            name="ck_resource_assignments_status",
        ),
        CheckConstraint(
            "notes IS NULL OR length(notes) <= 2000", name="ck_resource_assignments_notes_length"
        ),
        CheckConstraint(
            "source IN ('manual', 'manager_adjustment')", name="ck_resource_assignments_source"
        ),
        CheckConstraint(
            "source <> 'manager_adjustment' OR (notes IS NOT NULL AND length(btrim(notes)) > 0)",
            name="ck_resource_assignments_adjustment_note",
        ),
        Index(
            "ix_resource_assignments_employee_period",
            "tenant_id",
            "company_id",
            "employee_id",
            "start_date",
            "end_date",
            "status",
        ),
        Index(
            "ix_resource_assignments_project_status",
            "tenant_id",
            "company_id",
            "project_id",
            "status",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    project_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    employee_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    planned_hours_per_week: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="proposed")
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False, server_default="manual")
