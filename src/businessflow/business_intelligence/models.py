from __future__ import annotations

from datetime import date, datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class BusinessMetricDefinitionVersion(Base):
    """Append-only company-owned metric or KPI definition version."""

    __tablename__ = "business_metric_definition_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bmi_metric_definition_scope_id"),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "id",
            "metric_key",
            name="uq_bmi_metric_definition_scope_key",
        ),
        UniqueConstraint(
            "tenant_id",
            "company_id",
            "metric_key",
            "version",
            name="uq_bmi_metric_definition_version",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_metric_definition_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "author_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_author",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "metric_owner_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_owner",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "reviewed_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_metric_definition_reviewer",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "supersedes_metric_definition_version_id", "metric_key"],
            [
                "business_metric_definition_versions.tenant_id",
                "business_metric_definition_versions.company_id",
                "business_metric_definition_versions.id",
                "business_metric_definition_versions.metric_key",
            ],
            name="fk_bmi_metric_definition_supersedes",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_bmi_metric_definition_version"),
        CheckConstraint(
            "(version = 1 AND supersedes_metric_definition_version_id IS NULL) OR "
            "(version > 1 AND supersedes_metric_definition_version_id IS NOT NULL)",
            name="ck_bmi_metric_definition_supersedes_version",
        ),
        CheckConstraint(
            "metric_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bmi_metric_definition_key"
        ),
        CheckConstraint(
            "definition_type IN ('company', 'custom')", name="ck_bmi_metric_definition_type"
        ),
        CheckConstraint(
            "status IN ('draft', 'review', 'published', 'deprecated')",
            name="ck_bmi_metric_definition_status",
        ),
        CheckConstraint(
            "domain IN ('finance', 'sales', 'customers', 'procurement', 'operations', 'people')",
            name="ck_bmi_metric_definition_domain",
        ),
        CheckConstraint(
            "calculation_key IN ('universal_record_count', 'universal_source_amount_sum', "
            "'universal_distinct_source_values', 'm5_metric', "
            "'core_financial_measure', 'custom_formula', "
            "'manual_customer_commitment_count')",
            name="ck_bmi_metric_definition_calculator",
        ),
        CheckConstraint(
            "length(btrim(name)) BETWEEN 1 AND 160", name="ck_bmi_metric_definition_name"
        ),
        CheckConstraint(
            "length(btrim(description)) BETWEEN 1 AND 2000",
            name="ck_bmi_metric_definition_description",
        ),
        CheckConstraint(
            "length(btrim(category)) BETWEEN 1 AND 64", name="ck_bmi_metric_definition_category"
        ),
        CheckConstraint(
            "length(btrim(business_meaning)) BETWEEN 1 AND 2000",
            name="ck_bmi_metric_definition_meaning",
        ),
        CheckConstraint(
            "length(btrim(unit)) BETWEEN 1 AND 80", name="ck_bmi_metric_definition_unit"
        ),
        CheckConstraint(
            "currency_code IS NULL OR currency_code ~ '^[A-Z]{3}$'",
            name="ck_bmi_metric_definition_currency",
        ),
        CheckConstraint(
            "precision_scale BETWEEN 0 AND 12", name="ck_bmi_metric_definition_precision"
        ),
        CheckConstraint(
            "rounding_mode IN ('half_even', 'half_up', 'down', 'up', 'none')",
            name="ck_bmi_metric_definition_rounding",
        ),
        CheckConstraint("definition_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_metric_definition_hash"),
        CheckConstraint(
            "calculation_spec IS NOT NULL AND jsonb_typeof(calculation_spec) = 'object'",
            name="ck_bmi_metric_definition_calc",
        ),
        CheckConstraint(
            "(formula IS NULL OR jsonb_typeof(formula) = 'object') AND "
            "(numerator IS NULL OR jsonb_typeof(numerator) = 'object') AND "
            "(denominator IS NULL OR jsonb_typeof(denominator) = 'object')",
            name="ck_bmi_metric_definition_formula",
        ),
        CheckConstraint(
            "jsonb_typeof(dependencies) = 'array' AND jsonb_typeof(dimensions) = 'array' "
            "AND jsonb_typeof(required_source_datasets) = 'array' "
            "AND jsonb_typeof(required_fields) = 'array' "
            "AND jsonb_typeof(required_relationships) = 'array' "
            "AND jsonb_typeof(filters) = 'array' AND jsonb_typeof(exclusions) = 'array' "
            "AND jsonb_typeof(policy) = 'object' "
            "AND jsonb_typeof(completeness_requirements) = 'array' "
            "AND jsonb_typeof(limitations) = 'array'",
            name="ck_bmi_metric_definition_metadata",
        ),
        CheckConstraint(
            "(effective_from IS NULL OR effective_to IS NULL OR effective_to >= effective_from)",
            name="ck_bmi_metric_definition_effective_period",
        ),
        CheckConstraint(
            "(status IN ('published', 'deprecated') AND reviewed_by_user_id IS NOT NULL "
            "AND review_note IS NOT NULL AND length(btrim(review_note)) BETWEEN 1 AND 500) OR "
            "(status IN ('draft', 'review') AND reviewed_by_user_id IS NULL "
            "AND review_note IS NULL)",
            name="ck_bmi_metric_definition_published_review",
        ),
        CheckConstraint(
            "reviewed_by_user_id IS NULL OR reviewed_by_user_id <> author_user_id",
            name="ck_bmi_metric_definition_independent_reviewer",
        ),
        Index(
            "ix_bmi_metric_definitions_company_domain_status",
            "tenant_id",
            "company_id",
            "domain",
            "status",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    definition_type: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), nullable=False)
    domain: Mapped[str] = mapped_column(String(24), nullable=False)
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    business_meaning: Mapped[str] = mapped_column(String(2000), nullable=False)
    calculation_key: Mapped[str] = mapped_column(String(48), nullable=False)
    calculation_spec: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    formula: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    numerator: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    denominator: Mapped[dict[str, object] | None] = mapped_column(JSONB(none_as_null=True))
    dependencies: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    aggregation_method: Mapped[str] = mapped_column(String(24), nullable=False)
    dimensions: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    required_source_datasets: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    required_fields: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    required_relationships: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    filters: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    exclusions: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    unit: Mapped[str] = mapped_column(String(80), nullable=False)
    currency_code: Mapped[str | None] = mapped_column(String(3))
    time_granularity: Mapped[str] = mapped_column(String(16), nullable=False)
    period_boundary: Mapped[str] = mapped_column(String(32), nullable=False)
    timezone_rule: Mapped[str] = mapped_column(String(24), nullable=False)
    policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    completeness_requirements: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    limitations: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    precision_scale: Mapped[int] = mapped_column(Integer, nullable=False)
    rounding_mode: Mapped[str] = mapped_column(String(16), nullable=False)
    metric_owner_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date)
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    supersedes_metric_definition_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    author_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    reviewed_by_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    review_note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessCompanySemanticConfigVersion(Base):
    """Published, immutable Company semantic and source-precedence configuration snapshot."""

    __tablename__ = "business_company_semantic_config_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bmi_company_config_scope_id"),
        UniqueConstraint(
            "tenant_id", "company_id", "version", name="uq_bmi_company_config_version"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_company_config_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "created_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_company_config_actor",
            ondelete="RESTRICT",
        ),
        CheckConstraint("version >= 1", name="ck_bmi_company_config_version"),
        CheckConstraint("status IN ('draft', 'published')", name="ck_bmi_company_config_status"),
        CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="ck_bmi_company_config_currency"),
        CheckConstraint(
            "length(timezone_name) BETWEEN 1 AND 64", name="ck_bmi_company_config_timezone"
        ),
        CheckConstraint(
            "fiscal_year_start_month BETWEEN 1 AND 12", name="ck_bmi_company_config_fiscal_month"
        ),
        CheckConstraint(
            "fiscal_year_label IN ('start_year', 'end_year')",
            name="ck_bmi_company_config_fiscal_label",
        ),
        CheckConstraint("week_start_day BETWEEN 1 AND 7", name="ck_bmi_company_config_week_start"),
        CheckConstraint(
            "fallback_policy IN ('strict_primary', 'next_ready_source')",
            name="ck_bmi_company_config_fallback",
        ),
        CheckConstraint("configuration_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_company_config_hash"),
        CheckConstraint(
            "jsonb_typeof(semantic_policy) = 'object'",
            name="ck_bmi_company_config_semantic_policy",
        ),
        Index(
            "ix_bmi_company_configs_scope_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone_name: Mapped[str] = mapped_column(String(64), nullable=False)
    fiscal_year_start_month: Mapped[int] = mapped_column(Integer, nullable=False)
    fiscal_year_label: Mapped[str] = mapped_column(String(16), nullable=False)
    week_start_day: Mapped[int] = mapped_column(Integer, nullable=False)
    fallback_policy: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_policy: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    configuration_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class BusinessCompanySemanticSourcePrecedence(Base):
    """Exact source profile version and explicit precedence within one config version."""

    __tablename__ = "business_company_semantic_source_precedence"
    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "configuration_version_id"],
            [
                "business_company_semantic_config_versions.tenant_id",
                "business_company_semantic_config_versions.company_id",
                "business_company_semantic_config_versions.id",
            ],
            name="fk_bmi_source_precedence_config",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id"],
            [
                "company_data_sources.tenant_id",
                "company_data_sources.company_id",
                "company_data_sources.id",
            ],
            name="fk_bmi_source_precedence_source",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "source_id", "source_profile_version_id"],
            [
                "company_data_source_profile_versions.tenant_id",
                "company_data_source_profile_versions.company_id",
                "company_data_source_profile_versions.source_id",
                "company_data_source_profile_versions.id",
            ],
            name="fk_bmi_source_precedence_profile",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "configuration_version_id",
            "dataset_type",
            "priority",
            name="uq_bmi_source_precedence_priority",
        ),
        UniqueConstraint(
            "configuration_version_id",
            "dataset_type",
            "source_id",
            name="uq_bmi_source_precedence_source",
        ),
        CheckConstraint(
            "dataset_type IN ('universal_records', 'customer_commitments')",
            name="ck_bmi_source_precedence_dataset",
        ),
        CheckConstraint("priority >= 1", name="ck_bmi_source_precedence_priority"),
        Index(
            "ix_bmi_source_precedence_scope",
            "tenant_id",
            "company_id",
            "configuration_version_id",
            "dataset_type",
            "priority",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    configuration_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    dataset_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    source_profile_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)


class BusinessIntelligenceAnalysisRun(Base):
    """Immutable reproducible analysis with pinned definitions, config, and evidence lineage."""

    __tablename__ = "business_intelligence_analysis_runs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "company_id", "id", name="uq_bmi_analysis_scope_id"),
        ForeignKeyConstraint(
            ["tenant_id", "company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_bmi_analysis_company",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "requested_by_user_id"],
            ["tenant_memberships.tenant_id", "tenant_memberships.user_id"],
            name="fk_bmi_analysis_actor",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "configuration_version_id"],
            [
                "business_company_semantic_config_versions.tenant_id",
                "business_company_semantic_config_versions.company_id",
                "business_company_semantic_config_versions.id",
            ],
            name="fk_bmi_analysis_config",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "company_id", "metric_definition_version_id", "metric_key"],
            [
                "business_metric_definition_versions.tenant_id",
                "business_metric_definition_versions.company_id",
                "business_metric_definition_versions.id",
                "business_metric_definition_versions.metric_key",
            ],
            name="fk_bmi_analysis_metric_definition",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(metric_definition_version_id IS NOT NULL AND metric_version IS NOT NULL AND "
            "system_metric_version IS NULL) OR "
            "(metric_definition_version_id IS NULL AND metric_version IS NULL AND "
            "system_metric_version IS NOT NULL)",
            name="ck_bmi_analysis_metric_reference",
        ),
        CheckConstraint(
            "metric_key ~ '^[a-z0-9][a-z0-9._-]{1,63}$'", name="ck_bmi_analysis_metric_key"
        ),
        CheckConstraint(
            "length(btrim(calculation_version)) BETWEEN 1 AND 64",
            name="ck_bmi_analysis_engine_version",
        ),
        CheckConstraint(
            "status IN ('complete', 'partial', 'abstained')", name="ck_bmi_analysis_status"
        ),
        CheckConstraint(
            "definition_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_definition_hash"
        ),
        CheckConstraint("input_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_input_hash"),
        CheckConstraint("result_hash ~ '^[0-9a-f]{64}$'", name="ck_bmi_analysis_result_hash"),
        CheckConstraint(
            "(period_start IS NULL AND period_end IS NULL) OR "
            "(period_start IS NOT NULL AND period_end IS NOT NULL AND period_end >= period_start)",
            name="ck_bmi_analysis_period",
        ),
        CheckConstraint(
            "jsonb_typeof(definition_snapshot) = 'object'",
            name="ck_bmi_analysis_definition_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(configuration_snapshot) = 'object'",
            name="ck_bmi_analysis_configuration_snapshot",
        ),
        CheckConstraint(
            "jsonb_typeof(input_snapshot) = 'object'", name="ck_bmi_analysis_input_snapshot"
        ),
        CheckConstraint(
            "jsonb_typeof(result_snapshot) = 'object'", name="ck_bmi_analysis_result_snapshot"
        ),
        Index(
            "ix_bmi_analysis_company_created",
            "tenant_id",
            "company_id",
            "created_at",
        ),
        Index(
            "ix_bmi_analysis_metric_created",
            "tenant_id",
            "company_id",
            "metric_key",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    requested_by_user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    configuration_version_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    configuration_version: Mapped[int] = mapped_column(Integer, nullable=False)
    metric_key: Mapped[str] = mapped_column(String(64), nullable=False)
    metric_definition_version_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    metric_version: Mapped[int | None] = mapped_column(Integer)
    system_metric_version: Mapped[str | None] = mapped_column(String(96))
    definition_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    definition_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    configuration_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(64), nullable=False)
    input_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    result_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    input_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    result_snapshot: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    workforce_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
