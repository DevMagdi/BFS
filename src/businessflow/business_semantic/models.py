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
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


def _member_fk(field: str, name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(
        ["tenant_id", field],
        ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
        name=name,
        ondelete="RESTRICT",
    )


def _scope_fk(local: list[str], remote: list[str], name: str) -> ForeignKeyConstraint:
    return ForeignKeyConstraint(local, remote, name=name, ondelete="RESTRICT")


class BusinessSemanticEntity(Base):
    """A Company-owned semantic identity; source facts remain in their existing models."""

    __tablename__ = "business_semantic_entities"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_entity_scope_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "entity_type", "entity_key", name="uq_bse_entity_key"
        ),
        _scope_fk(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            "fk_bse_entity_company",
        ),
        _member_fk("created_by_user_id", "fk_bse_entity_actor"),
        CheckConstraint(
            "entity_type IN ('business_unit', 'department', 'employee', 'project', 'customer', "
            "'supplier', 'product', 'sales_transaction', 'expense', 'business_activity', "
            "'operational_record')",
            name="ck_bse_entity_type",
        ),
        CheckConstraint("entity_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bse_entity_key"),
        CheckConstraint("length(btrim(display_name)) BETWEEN 1 AND 160", name="ck_bse_entity_name"),
        CheckConstraint(
            "description IS NULL OR length(description) <= 2000",
            name="ck_bse_entity_description_length",
        ),
        Index("ix_bse_entities_scope_type", "tenant_id", "company_id", "entity_type", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessSemanticMappingVersion(Base):
    """Append-only, human-reviewed binding from an existing source identity to a semantic ID."""

    __tablename__ = "business_semantic_mapping_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_mapping_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "semantic_entity_id",
            name="uq_bse_mapping_entity_scope",
        ),
        UniqueConstraint(
            "tenant_id", "company_id", "id", "reference_key_hash", name="uq_bse_mapping_scope_key"
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "reference_key_hash",
            "version",
            name="uq_bse_mapping_reference_version",
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
        CheckConstraint("version >= 1", name="ck_bse_mapping_version"),
        CheckConstraint(
            "(version = 1 AND supersedes_mapping_version_id IS NULL) OR "
            "(version > 1 AND supersedes_mapping_version_id IS NOT NULL)",
            name="ck_bse_mapping_supersedes_version",
        ),
        CheckConstraint(
            "reference_kind IN ('universal_record', 'employee', 'department', 'business_unit', "
            "'project')",
            name="ck_bse_mapping_reference_kind",
        ),
        CheckConstraint(
            "reference_key_hash ~ '^[0-9a-f]{64}$'", name="ck_bse_mapping_reference_hash"
        ),
        CheckConstraint(
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
        CheckConstraint(
            "length(btrim(review_note)) BETWEEN 1 AND 500", name="ck_bse_mapping_review_note"
        ),
        CheckConstraint(
            "correction_reason IS NULL OR length(btrim(correction_reason)) BETWEEN 1 AND 500",
            name="ck_bse_mapping_correction_reason",
        ),
        Index(
            "ix_bse_mappings_entity",
            "tenant_id",
            "company_id",
            "semantic_entity_id",
            "created_at",
        ),
        Index(
            "ix_bse_mappings_reference_latest",
            "tenant_id",
            "company_id",
            "reference_key_hash",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    semantic_entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reference_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    reference_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    universal_record_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    universal_revision: Mapped[int | None] = mapped_column(Integer)
    source_mapping_profile_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    employee_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    department_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    business_unit_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    supersedes_mapping_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    review_note: Mapped[str] = mapped_column(Text, nullable=False)
    correction_reason: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessSemanticRelationshipVersion(Base):
    """Append-only semantic edge; source-verified edges retain exact reviewed mapping versions."""

    __tablename__ = "business_semantic_relationship_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bse_relationship_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "relationship_key_hash",
            name="uq_bse_relationship_scope_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "relationship_key_hash",
            "version",
            name="uq_bse_relationship_key_version",
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
        CheckConstraint(
            "relationship_type IN ('customer_has_sales_transaction', "
            "'product_has_sales_transaction', 'supplier_has_expense', "
            "'employee_belongs_to_department', 'department_in_business_unit', "
            "'project_assigned_to_employee', 'project_managed_by_employee')",
            name="ck_bse_relationship_type",
        ),
        _member_fk("created_by_user_id", "fk_bse_relationship_actor"),
        CheckConstraint("version >= 1", name="ck_bse_relationship_version"),
        CheckConstraint(
            "(version = 1 AND supersedes_relationship_version_id IS NULL) OR "
            "(version > 1 AND supersedes_relationship_version_id IS NOT NULL)",
            name="ck_bse_relationship_supersedes_version",
        ),
        CheckConstraint(
            "relationship_key_hash ~ '^[0-9a-f]{64}$'", name="ck_bse_relationship_key_hash"
        ),
        CheckConstraint("status IN ('active', 'retracted')", name="ck_bse_relationship_status"),
        CheckConstraint(
            "evidence_kind IN ('source_verified', 'manual_assertion')",
            name="ck_bse_relationship_evidence_kind",
        ),
        CheckConstraint(
            "subject_entity_id <> object_entity_id", name="ck_bse_relationship_not_self"
        ),
        CheckConstraint(
            "length(btrim(evidence_note)) BETWEEN 1 AND 500",
            name="ck_bse_relationship_evidence_note",
        ),
        CheckConstraint(
            "(evidence_kind = 'source_verified' AND subject_mapping_version_id IS NOT NULL "
            "AND object_mapping_version_id IS NOT NULL) OR "
            "(evidence_kind = 'manual_assertion' AND "
            "((subject_mapping_version_id IS NULL AND object_mapping_version_id IS NULL) OR "
            "(subject_mapping_version_id IS NOT NULL AND object_mapping_version_id IS NOT NULL)))",
            name="ck_bse_relationship_evidence_mappings",
        ),
        Index(
            "ix_bse_relationship_subject",
            "tenant_id",
            "company_id",
            "subject_entity_id",
            "created_at",
        ),
        Index(
            "ix_bse_relationship_object",
            "tenant_id",
            "company_id",
            "object_entity_id",
            "created_at",
        ),
        Index(
            "ix_bse_relationship_latest",
            "tenant_id",
            "company_id",
            "relationship_key_hash",
            "version",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    relationship_key_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    subject_entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    relationship_type: Mapped[str] = mapped_column(String(64), nullable=False)
    object_entity_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    evidence_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_mapping_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    object_mapping_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    evidence_note: Mapped[str] = mapped_column(Text, nullable=False)
    supersedes_relationship_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
