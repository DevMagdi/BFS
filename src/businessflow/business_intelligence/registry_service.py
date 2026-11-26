from __future__ import annotations

import hashlib
from typing import Any, cast
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.authorization import role_allows
from businessflow.business_intelligence.models import (
    BusinessCompanySemanticConfigVersion,
    BusinessCompanySemanticSourcePrecedence,
    BusinessMetricDefinitionVersion,
)
from businessflow.business_intelligence.registry import (
    CALCULATION_CAPABILITIES,
    CALCULATION_CATALOG_VERSION,
    METRIC_AUTHORITY_BOUNDARY,
    METRIC_REGISTRY_VERSION,
    SYSTEM_METRIC_BY_KEY,
    metric_authority,
    system_metric_definition_hash,
    system_metric_payloads,
)
from businessflow.business_intelligence.schemas import (
    CompanySemanticConfigurationCreateRequest,
    CompanySemanticConfigurationPage,
    CompanySemanticConfigurationResponse,
    MetricDefinitionCreateRequest,
    MetricDefinitionLifecycleRequest,
    MetricDefinitionPage,
    MetricDefinitionResponse,
    MetricRegistryCatalog,
    SourcePrecedenceResponse,
    formula_metric_references,
    metric_calculation_dict,
)
from businessflow.company_data.models import CompanyDataSource
from businessflow.errors import ApiError
from businessflow.metrics.snapshot import content_hash
from businessflow.models import TenantMembership, UserAccount
from businessflow.services.company_context_access import require_company_context_access
from businessflow.universal_data.models import CompanyDataSourceProfileVersion

SEMANTIC_POLICY: dict[str, object] = {
    "policy_version": "businessflow.semantic-governance.v1",
    "cross_source_identity_resolution": "explicit_reviewed_semantic_mappings_only",
    "source_record_identity": "source_scoped",
    "relationship_evidence": "reviewed_version_required_for_analytic_use",
    "manual_assertion_relationships": "not_analytically_usable",
    "source_authority": "not_inferred_from_record_volume_or_name_similarity",
    "financial_boundary": "universal_source_amounts_are_not_core_financial_facts",
}


def _error(status: int, code: str, message: str) -> ApiError:
    return ApiError(status, code, message)


def has_finance_read_access(session: Session, user: UserAccount, tenant_id: UUID) -> bool:
    role = session.scalar(
        select(TenantMembership.role).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
    )
    return role is not None and role_allows(role, "finance:read")


def definition_requires_finance_read(definition: dict[str, object]) -> bool:
    calculation = definition.get("calculation")
    calculation_kind = calculation.get("kind") if isinstance(calculation, dict) else None
    if definition.get("domain") == "finance" or calculation_kind in {
        "core_financial_measure",
        "universal_source_amount_sum",
    }:
        return True
    dependencies = definition.get("dependencies")
    if not isinstance(dependencies, list):
        return False
    for dependency in dependencies:
        if not isinstance(dependency, dict):
            continue
        if dependency.get("sensitivity_scope") == "finance":
            return True
        metric_key = dependency.get("metric_key")
        system_metric = (
            SYSTEM_METRIC_BY_KEY.get(metric_key) if isinstance(metric_key, str) else None
        )
        if system_metric is not None and (
            system_metric.domain == "finance"
            or system_metric.calculation.get("kind")
            in {"core_financial_measure", "universal_source_amount_sum"}
        ):
            return True
    return False


def metric_definition_requires_finance_read(
    session: Session,
    row: BusinessMetricDefinitionVersion,
    *,
    visited: set[UUID] | None = None,
) -> bool:
    if definition_requires_finance_read(
        {
            "domain": row.domain,
            "calculation": row.calculation_spec,
            "dependencies": row.dependencies,
        }
    ):
        return True
    visited_ids = set() if visited is None else set(visited)
    if row.id in visited_ids:
        return True
    for dependency in row.dependencies:
        if not isinstance(dependency, dict):
            return True
        if dependency.get("sensitivity_scope") in {"finance", "company_context"}:
            continue
        metric_key = dependency.get("metric_key")
        if not isinstance(metric_key, str):
            return True
        if metric_key in SYSTEM_METRIC_BY_KEY:
            continue
        version_id = dependency.get("version_id")
        if not isinstance(version_id, str):
            return True
        try:
            parsed_version_id = UUID(version_id)
        except ValueError:
            return True
        dependency_row = session.scalar(
            select(BusinessMetricDefinitionVersion).where(
                BusinessMetricDefinitionVersion.id == parsed_version_id,
                BusinessMetricDefinitionVersion.tenant_id == row.tenant_id,
                BusinessMetricDefinitionVersion.company_id == row.company_id,
                BusinessMetricDefinitionVersion.metric_key == metric_key,
            )
        )
        if dependency_row is None or metric_definition_requires_finance_read(
            session, dependency_row, visited=visited_ids | {row.id}
        ):
            return True
    return False


def _lock(session: Session, key: str) -> None:
    raw = int.from_bytes(hashlib.sha256(key.encode("utf-8")).digest()[:8], "big", signed=False)
    if raw >= 1 << 63:
        raw -= 1 << 64
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": raw})


def registry_catalog(session: Session, user: UserAccount, tenant_id: UUID) -> MetricRegistryCatalog:
    require_company_context_access(session, user, tenant_id, "company_context:read")
    can_read_finance = has_finance_read_access(session, user, tenant_id)
    visible_system_metrics = [
        item
        for item in system_metric_payloads()
        if can_read_finance or not definition_requires_finance_read(item)
    ]
    visible_calculations = [
        item
        for item in CALCULATION_CAPABILITIES
        if can_read_finance
        or item["calculation_key"] not in {"universal_source_amount_sum", "core_financial_measure"}
    ]
    return MetricRegistryCatalog.model_validate(
        {
            "registry_version": METRIC_REGISTRY_VERSION,
            "calculation_catalog_version": CALCULATION_CATALOG_VERSION,
            "authority_boundary": METRIC_AUTHORITY_BOUNDARY,
            "system_metrics": visible_system_metrics,
            "calculations": [
                {
                    **item,
                    "supported": True,
                    "authority_boundary": metric_authority(str(item["calculation_key"])),
                }
                for item in visible_calculations
            ],
            "expression_operators": [
                "constant",
                "metric_ref",
                "add",
                "subtract",
                "multiply",
                "divide",
                "negate",
            ],
            "unsupported": [
                {
                    "calculation_key": "arbitrary_sql_or_user_code",
                    "supported": False,
                    "reason": (
                        "Definitions accept only a versioned, allow-listed calculation schema."
                    ),
                },
                {
                    "calculation_key": "automatic_cross_source_party_merge",
                    "supported": False,
                    "reason": (
                        "Source-scoped references are not automatically merged or deduplicated."
                    ),
                },
                {
                    "calculation_key": "ai_generated_arithmetic",
                    "supported": False,
                    "reason": "All arithmetic is performed by a deterministic Decimal engine.",
                },
            ],
        }
    )


def _definition_response(row: BusinessMetricDefinitionVersion) -> MetricDefinitionResponse:
    return MetricDefinitionResponse(
        id=row.id,
        metric_key=row.metric_key,
        version=row.version,
        definition_type=cast(Any, row.definition_type),
        status=cast(Any, row.status),
        name=row.name,
        description=row.description,
        domain=cast(Any, row.domain),
        category=row.category,
        business_meaning=row.business_meaning,
        calculation=row.calculation_spec,
        calculation_key=row.calculation_key,
        formula=row.formula,
        numerator=row.numerator,
        denominator=row.denominator,
        dependencies=row.dependencies,
        aggregation_method=row.aggregation_method,
        dimensions=row.dimensions,
        required_source_datasets=row.required_source_datasets,
        required_fields=row.required_fields,
        required_relationships=row.required_relationships,
        filters=row.filters,
        exclusions=row.exclusions,
        unit=row.unit,
        currency_code=row.currency_code,
        time_granularity=cast(Any, row.time_granularity),
        period_boundary=cast(Any, row.period_boundary),
        timezone_rule=cast(Any, row.timezone_rule),
        policy=row.policy,
        completeness_requirements=row.completeness_requirements,
        precision_scale=row.precision_scale,
        rounding_mode=cast(Any, row.rounding_mode),
        metric_owner_user_id=row.metric_owner_user_id,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        definition_hash=row.definition_hash,
        supersedes_metric_definition_version_id=row.supersedes_metric_definition_version_id,
        author_user_id=row.author_user_id,
        created_by_user_id=row.created_by_user_id,
        reviewed_by_user_id=row.reviewed_by_user_id,
        review_note=row.review_note,
        limitations=row.limitations,
        created_at=row.created_at,
    )


def list_metric_definitions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    metric_key: str | None,
    domain: str | None,
    status: str | None,
    include_history: bool,
    limit: int,
    offset: int,
) -> MetricDefinitionPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [
        BusinessMetricDefinitionVersion.tenant_id == tenant_id,
        BusinessMetricDefinitionVersion.company_id == access.company.id,
    ]
    if not has_finance_read_access(session, user, tenant_id):
        finance_sensitive = or_(
            BusinessMetricDefinitionVersion.domain == "finance",
            BusinessMetricDefinitionVersion.calculation_key.in_(
                {"core_financial_measure", "universal_source_amount_sum"}
            ),
            BusinessMetricDefinitionVersion.dependencies.contains(
                [{"sensitivity_scope": "finance"}]
            ),
        )
        filters.append(~finance_sensitive)
    if metric_key is not None:
        filters.append(BusinessMetricDefinitionVersion.metric_key == metric_key)
    if domain is not None:
        filters.append(BusinessMetricDefinitionVersion.domain == domain)
    if status is not None:
        filters.append(BusinessMetricDefinitionVersion.status == status)
    statement = select(BusinessMetricDefinitionVersion).where(*filters)
    if not include_history:
        latest_versions = (
            select(
                BusinessMetricDefinitionVersion.metric_key.label("metric_key"),
                func.max(BusinessMetricDefinitionVersion.version).label("latest_version"),
            )
            .where(
                BusinessMetricDefinitionVersion.tenant_id == tenant_id,
                BusinessMetricDefinitionVersion.company_id == access.company.id,
            )
            .group_by(BusinessMetricDefinitionVersion.metric_key)
            .subquery()
        )
        statement = statement.join(
            latest_versions,
            and_(
                BusinessMetricDefinitionVersion.metric_key == latest_versions.c.metric_key,
                BusinessMetricDefinitionVersion.version == latest_versions.c.latest_version,
            ),
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(
        session.scalars(
            statement.order_by(
                BusinessMetricDefinitionVersion.metric_key,
                BusinessMetricDefinitionVersion.version.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return MetricDefinitionPage(
        items=[_definition_response(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _latest_definition(
    session: Session, tenant_id: UUID, company_id: UUID, metric_key: str
) -> BusinessMetricDefinitionVersion | None:
    return session.scalar(
        select(BusinessMetricDefinitionVersion)
        .where(
            BusinessMetricDefinitionVersion.tenant_id == tenant_id,
            BusinessMetricDefinitionVersion.company_id == company_id,
            BusinessMetricDefinitionVersion.metric_key == metric_key,
        )
        .order_by(BusinessMetricDefinitionVersion.version.desc())
        .limit(1)
    )


def _latest_published_definition(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    metric_key: str,
) -> BusinessMetricDefinitionVersion | None:
    latest = _latest_definition(session, tenant_id, company_id, metric_key)
    if latest is None or latest.status == "deprecated":
        return None
    return session.scalar(
        select(BusinessMetricDefinitionVersion)
        .where(
            BusinessMetricDefinitionVersion.tenant_id == tenant_id,
            BusinessMetricDefinitionVersion.company_id == company_id,
            BusinessMetricDefinitionVersion.metric_key == metric_key,
            BusinessMetricDefinitionVersion.status == "published",
        )
        .order_by(BusinessMetricDefinitionVersion.version.desc())
        .limit(1)
    )


def get_metric_definition(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    metric_key: str,
    version: int | None = None,
) -> MetricDefinitionResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = [
        BusinessMetricDefinitionVersion.tenant_id == tenant_id,
        BusinessMetricDefinitionVersion.company_id == access.company.id,
        BusinessMetricDefinitionVersion.metric_key == metric_key,
    ]
    if version is not None:
        filters.append(BusinessMetricDefinitionVersion.version == version)
    if version is not None:
        row = session.scalar(select(BusinessMetricDefinitionVersion).where(*filters))
    else:
        row = _latest_published_definition(session, tenant_id, access.company.id, metric_key)
        latest = _latest_definition(session, tenant_id, access.company.id, metric_key)
        if latest is not None and latest.status == "deprecated":
            row = latest
    if row is None:
        raise _error(404, "metric_definition_not_found", "The metric definition was not found.")
    if metric_definition_requires_finance_read(session, row):
        require_company_context_access(session, user, tenant_id, "finance:read")
    return _definition_response(row)


def _definition_snapshot(row: BusinessMetricDefinitionVersion) -> dict[str, object]:
    return _definition_response(row).model_dump(mode="json")


_NON_SCALAR_M5_METRICS = frozenset(
    {"recorded_assignment_overload", "recorded_skill_inventory_coverage"}
)


def _validate_formula_dependency_shape(
    *,
    calculation_spec: dict[str, object],
    dependency_dimensions: list[str],
    dependency_time_granularity: str,
    formula_dimensions: list[str],
) -> None:
    calculation_kind = calculation_spec.get("kind")
    if (
        calculation_kind == "m5_metric"
        and calculation_spec.get("metric_id") in _NON_SCALAR_M5_METRICS
    ):
        raise _error(
            422,
            "metric_dependency_not_scalar",
            "Structured M5 observations cannot be used as arithmetic formula dependencies.",
        )
    selected_dimensions = set(formula_dimensions)
    if not selected_dimensions <= set(dependency_dimensions) or (
        dependency_time_granularity != "none"
        and dependency_time_granularity not in selected_dimensions
    ):
        raise _error(
            422,
            "metric_dependency_dimensions_incompatible",
            "Formula dimensions must be supported by every pinned dependency, including its "
            "required time granularity.",
        )


def _resolve_dependencies(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    metric_key: str,
    body: MetricDefinitionCreateRequest,
) -> list[dict[str, object]]:
    expressions = [body.formula, body.numerator, body.denominator]
    references: set[str] = set()
    for expression in expressions:
        references.update(formula_metric_references(expression))
    if metric_key in references:
        raise _error(
            422,
            "metric_dependency_cycle",
            "A metric definition cannot depend on another version of itself.",
        )
    result: list[dict[str, object]] = []
    for dependency_key in sorted(references):
        system = SYSTEM_METRIC_BY_KEY.get(dependency_key)
        if system is not None:
            system_calculation = system.calculation
            system_metric_id = system_calculation.get("metric_id")
            system_dimensions = (
                ["week"]
                if system_calculation.get("kind") == "m5_metric"
                and system_metric_id != "recorded_skill_inventory_coverage"
                else []
            )
            _validate_formula_dependency_shape(
                calculation_spec=system_calculation,
                dependency_dimensions=system_dimensions,
                dependency_time_granularity="week" if system_dimensions else "none",
                formula_dimensions=list(body.dimensions),
            )
            system_finance_sensitive = definition_requires_finance_read(
                {"domain": system.domain, "calculation": system_calculation}
            )
            result.append(
                {
                    "metric_key": dependency_key,
                    "version": None,
                    "version_id": None,
                    "system_version": system.version,
                    "definition_hash": system_metric_definition_hash(system),
                    "sensitivity_scope": "finance"
                    if system_finance_sensitive
                    else "company_context",
                }
            )
            continue
        latest = _latest_published_definition(session, tenant_id, company_id, dependency_key)
        if latest is None:
            raise _error(
                422,
                "metric_dependency_unavailable",
                "Formula dependencies must reference a published Company metric or "
                "supported system metric.",
            )
        _validate_formula_dependency_shape(
            calculation_spec=latest.calculation_spec,
            dependency_dimensions=latest.dimensions,
            dependency_time_granularity=latest.time_granularity,
            formula_dimensions=list(body.dimensions),
        )
        dependency_finance_sensitive = metric_definition_requires_finance_read(session, latest)
        result.append(
            {
                "metric_key": latest.metric_key,
                "version": latest.version,
                "version_id": str(latest.id),
                "system_version": None,
                "definition_hash": latest.definition_hash,
                "sensitivity_scope": "finance"
                if dependency_finance_sensitive
                else "company_context",
            }
        )
    return result


def create_metric_definition(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: MetricDefinitionCreateRequest,
    request_id: UUID,
) -> MetricDefinitionResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    if body.metric_key in SYSTEM_METRIC_BY_KEY:
        raise _error(
            409, "metric_key_reserved", "The requested metric key is reserved for a system metric."
        )
    _lock(
        session,
        f"business-metric-definition:{tenant_id}:{access.company.id}:{body.metric_key}",
    )
    latest = _latest_definition(session, tenant_id, access.company.id, body.metric_key)
    actual_version = latest.version if latest is not None else 0
    if body.expected_version != actual_version:
        raise _error(
            409,
            "metric_definition_version_conflict",
            "The metric definition changed. Reload the registry and retry with the "
            "current version.",
        )
    if latest is not None and latest.definition_type != body.definition_type:
        raise _error(
            409,
            "metric_definition_type_conflict",
            "A metric key cannot change its Company or custom definition type.",
        )
    if latest is not None and latest.status == "deprecated":
        raise _error(
            409,
            "metric_definition_deprecated",
            "A deprecated metric key cannot be revised; create a new metric key instead.",
        )
    dependencies = _resolve_dependencies(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        metric_key=body.metric_key,
        body=body,
    )
    if definition_requires_finance_read(
        {
            "domain": body.domain,
            "calculation": metric_calculation_dict(body.calculation),
            "dependencies": dependencies,
        }
    ):
        require_company_context_access(session, user, tenant_id, "finance:read")
    owner_id = body.metric_owner_user_id or user.id
    owner_membership = session.scalar(
        select(TenantMembership).where(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == owner_id,
            TenantMembership.status == "active",
        )
    )
    if owner_membership is None:
        raise _error(
            422,
            "metric_owner_not_active_member",
            "The metric owner must be an active member of this tenant.",
        )
    payload = body.model_dump(mode="json", exclude={"expected_version", "metric_owner_user_id"})
    payload["metric_owner_user_id"] = str(owner_id)
    payload["dependencies"] = dependencies
    payload["registry_version"] = METRIC_REGISTRY_VERSION
    definition_hash = content_hash(payload)
    row = BusinessMetricDefinitionVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        metric_key=body.metric_key,
        version=actual_version + 1,
        definition_type=body.definition_type,
        status="draft",
        name=body.name.strip(),
        description=body.description.strip(),
        domain=body.domain,
        category=body.category,
        business_meaning=body.business_meaning.strip(),
        calculation_key=body.calculation.kind,
        calculation_spec=metric_calculation_dict(body.calculation),
        formula=body.formula.model_dump(mode="json", exclude_none=True) if body.formula else None,
        numerator=body.numerator.model_dump(mode="json", exclude_none=True)
        if body.numerator
        else None,
        denominator=body.denominator.model_dump(mode="json", exclude_none=True)
        if body.denominator
        else None,
        dependencies=dependencies,
        aggregation_method=body.aggregation_method,
        dimensions=list(body.dimensions),
        required_source_datasets=[
            item.model_dump(mode="json") for item in body.required_source_datasets
        ],
        required_fields=[item.model_dump(mode="json") for item in body.required_fields],
        required_relationships=list(body.required_relationships),
        filters=[item.model_dump(mode="json", exclude_none=True) for item in body.filters],
        exclusions=[item.model_dump(mode="json", exclude_none=True) for item in body.exclusions],
        unit=body.unit.strip(),
        currency_code=body.currency_code,
        time_granularity=body.time_granularity,
        period_boundary=body.period_boundary,
        timezone_rule=body.timezone_rule,
        policy=body.policy.model_dump(mode="json"),
        completeness_requirements=[value.strip() for value in body.completeness_requirements],
        limitations=list(body.limitations),
        precision_scale=body.precision_scale,
        rounding_mode=body.rounding_mode,
        metric_owner_user_id=owner_id,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        definition_hash=definition_hash,
        supersedes_metric_definition_version_id=latest.id if latest is not None else None,
        author_user_id=user.id,
        created_by_user_id=user.id,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "business_intelligence.metric_definition_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_metric_definition_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "metric_key": row.metric_key,
            "version": row.version,
            "definition_hash": row.definition_hash,
            "status": row.status,
        },
    )
    return _definition_response(row)


def transition_metric_definition(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    metric_key: str,
    version: int,
    body: MetricDefinitionLifecycleRequest,
    request_id: UUID,
) -> MetricDefinitionResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _lock(session, f"business-metric-definition:{tenant_id}:{access.company.id}:{metric_key}")
    row = session.scalar(
        select(BusinessMetricDefinitionVersion)
        .where(
            BusinessMetricDefinitionVersion.tenant_id == tenant_id,
            BusinessMetricDefinitionVersion.company_id == access.company.id,
            BusinessMetricDefinitionVersion.metric_key == metric_key,
            BusinessMetricDefinitionVersion.version == version,
        )
        .with_for_update()
    )
    if row is None:
        raise _error(404, "metric_definition_not_found", "The metric definition was not found.")
    if metric_definition_requires_finance_read(session, row):
        require_company_context_access(session, user, tenant_id, "finance:read")
    if body.expected_version != row.version:
        raise _error(
            409, "metric_definition_version_conflict", "Reload the metric definition and retry."
        )
    allowed = {
        "draft": {"review"},
        "review": {"draft", "published"},
        "published": {"deprecated"},
        "deprecated": set(),
    }
    if body.target_status not in allowed[row.status]:
        raise _error(
            409,
            "metric_definition_transition_invalid",
            "The requested metric definition lifecycle transition is not allowed.",
        )
    if body.target_status == "published":
        if row.author_user_id == user.id:
            raise _error(
                403,
                "metric_definition_independent_review_required",
                "A different authorized owner or admin must publish the reviewed definition.",
            )
        row.reviewed_by_user_id = user.id
        row.review_note = body.note
    row.status = body.target_status
    session.flush()
    record_event(
        session,
        "business_intelligence.metric_definition_status_changed",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_metric_definition_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "metric_key": row.metric_key,
            "version": row.version,
            "status": row.status,
            "note": body.note,
        },
    )
    return _definition_response(row)


def _latest_config(
    session: Session, tenant_id: UUID, company_id: UUID
) -> BusinessCompanySemanticConfigVersion | None:
    return session.scalar(
        select(BusinessCompanySemanticConfigVersion)
        .where(
            BusinessCompanySemanticConfigVersion.tenant_id == tenant_id,
            BusinessCompanySemanticConfigVersion.company_id == company_id,
            BusinessCompanySemanticConfigVersion.status == "published",
        )
        .order_by(BusinessCompanySemanticConfigVersion.version.desc())
        .limit(1)
    )


def _config_precedence_rows(
    session: Session,
    config: BusinessCompanySemanticConfigVersion,
) -> list[
    tuple[
        BusinessCompanySemanticSourcePrecedence, CompanyDataSource, CompanyDataSourceProfileVersion
    ]
]:
    return list(
        session.execute(
            select(
                BusinessCompanySemanticSourcePrecedence,
                CompanyDataSource,
                CompanyDataSourceProfileVersion,
            )
            .join(
                CompanyDataSource,
                (CompanyDataSource.id == BusinessCompanySemanticSourcePrecedence.source_id)
                & (CompanyDataSource.tenant_id == BusinessCompanySemanticSourcePrecedence.tenant_id)
                & (
                    CompanyDataSource.company_id
                    == BusinessCompanySemanticSourcePrecedence.company_id
                ),
            )
            .join(
                CompanyDataSourceProfileVersion,
                CompanyDataSourceProfileVersion.id
                == BusinessCompanySemanticSourcePrecedence.source_profile_version_id,
            )
            .where(
                BusinessCompanySemanticSourcePrecedence.tenant_id == config.tenant_id,
                BusinessCompanySemanticSourcePrecedence.company_id == config.company_id,
                BusinessCompanySemanticSourcePrecedence.configuration_version_id == config.id,
            )
            .order_by(
                BusinessCompanySemanticSourcePrecedence.dataset_type,
                BusinessCompanySemanticSourcePrecedence.priority,
            )
        ).all()
    )


def _config_response(
    session: Session,
    row: BusinessCompanySemanticConfigVersion,
) -> CompanySemanticConfigurationResponse:
    sources = [
        SourcePrecedenceResponse(
            dataset_type=precedence.dataset_type,
            source_id=source.id,
            source_name=source.display_name,
            source_profile_version_id=profile.id,
            source_profile_version=profile.version,
            priority=precedence.priority,
            source_status=profile.source_status,
            authority_classification=profile.authority_classification,
            freshness_max_age_hours=profile.freshness_max_age_hours,
        )
        for precedence, source, profile in _config_precedence_rows(session, row)
    ]
    return CompanySemanticConfigurationResponse(
        id=row.id,
        version=row.version,
        status="published",
        base_currency=row.base_currency,
        timezone_name=row.timezone_name,
        fiscal_year_start_month=row.fiscal_year_start_month,
        fiscal_year_label=cast(Any, row.fiscal_year_label),
        week_start_day=row.week_start_day,
        fallback_policy=cast(Any, row.fallback_policy),
        semantic_policy=row.semantic_policy,
        source_precedence=sources,
        configuration_hash=row.configuration_hash,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
    )


def create_company_semantic_configuration(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    body: CompanySemanticConfigurationCreateRequest,
    request_id: UUID,
) -> CompanySemanticConfigurationResponse:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    _lock(session, f"business-company-semantic-config:{tenant_id}:{access.company.id}")
    latest = _latest_config(session, tenant_id, access.company.id)
    latest_version = latest.version if latest is not None else 0
    if body.expected_version != latest_version:
        raise _error(
            409,
            "company_semantic_config_version_conflict",
            "Company configuration changed. Reload it and retry with the current version.",
        )
    try:
        ZoneInfo(access.company.timezone)
    except ZoneInfoNotFoundError:
        raise _error(
            409, "company_timezone_invalid", "The Company time zone is not supported."
        ) from None

    selected_sources: list[tuple[CompanyDataSource, CompanyDataSourceProfileVersion, int]] = []
    priorities: dict[str, int] = {}
    for item in body.source_precedence:
        source = session.scalar(
            select(CompanyDataSource).where(
                CompanyDataSource.id == item.source_id,
                CompanyDataSource.tenant_id == tenant_id,
                CompanyDataSource.company_id == access.company.id,
                CompanyDataSource.dataset_type == item.dataset_type,
            )
        )
        if source is None:
            raise _error(
                422,
                "company_semantic_config_source_invalid",
                "Every source precedence entry must reference a source in this Company "
                "and dataset.",
            )
        profile = session.scalar(
            select(CompanyDataSourceProfileVersion)
            .where(
                CompanyDataSourceProfileVersion.tenant_id == tenant_id,
                CompanyDataSourceProfileVersion.company_id == access.company.id,
                CompanyDataSourceProfileVersion.source_id == source.id,
            )
            .order_by(CompanyDataSourceProfileVersion.version.desc())
            .limit(1)
        )
        if profile is None:
            raise _error(
                409,
                "company_semantic_config_source_profile_missing",
                "A configured source has no versioned source profile.",
            )
        priorities[item.dataset_type] = priorities.get(item.dataset_type, 0) + 1
        selected_sources.append((source, profile, priorities[item.dataset_type]))

    source_snapshot = [
        {
            "dataset_type": source.dataset_type,
            "source_id": str(source.id),
            "source_name": source.display_name,
            "source_profile_version_id": str(profile.id),
            "source_profile_version": profile.version,
            "source_status": profile.source_status,
            "authority_classification": profile.authority_classification,
            "freshness_max_age_hours": profile.freshness_max_age_hours,
            "priority": priority,
        }
        for source, profile, priority in selected_sources
    ]
    snapshot = {
        "company_id": str(access.company.id),
        "base_currency": access.company.base_currency,
        "timezone_name": access.company.timezone,
        "fiscal_year_start_month": body.fiscal_year_start_month,
        "fiscal_year_label": body.fiscal_year_label,
        "week_start_day": body.week_start_day,
        "fallback_policy": body.fallback_policy,
        "source_precedence": source_snapshot,
        "semantic_policy": SEMANTIC_POLICY,
    }
    row = BusinessCompanySemanticConfigVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        version=latest_version + 1,
        status="draft",
        base_currency=access.company.base_currency,
        timezone_name=access.company.timezone,
        fiscal_year_start_month=body.fiscal_year_start_month,
        fiscal_year_label=body.fiscal_year_label,
        week_start_day=body.week_start_day,
        fallback_policy=body.fallback_policy,
        semantic_policy=SEMANTIC_POLICY,
        configuration_hash=content_hash(snapshot),
        created_by_user_id=user.id,
    )
    session.add(row)
    session.flush()
    for source, profile, priority in selected_sources:
        session.add(
            BusinessCompanySemanticSourcePrecedence(
                id=uuid4(),
                tenant_id=tenant_id,
                company_id=access.company.id,
                configuration_version_id=row.id,
                dataset_type=source.dataset_type,
                source_id=source.id,
                source_profile_version_id=profile.id,
                priority=priority,
            )
        )
    session.flush()
    row.status = "published"
    session.flush()
    record_event(
        session,
        "business_intelligence.company_semantic_config_published",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="business_company_semantic_config_version",
        target_id=row.id,
        request_id=request_id,
        details={
            "version": row.version,
            "configuration_hash": row.configuration_hash,
            "source_count": len(selected_sources),
            "fallback_policy": row.fallback_policy,
        },
    )
    return _config_response(session, row)


def get_company_semantic_configuration(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    version: int | None = None,
) -> CompanySemanticConfigurationResponse:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    statement = select(BusinessCompanySemanticConfigVersion).where(
        BusinessCompanySemanticConfigVersion.tenant_id == tenant_id,
        BusinessCompanySemanticConfigVersion.company_id == access.company.id,
        BusinessCompanySemanticConfigVersion.status == "published",
    )
    if version is not None:
        statement = statement.where(BusinessCompanySemanticConfigVersion.version == version)
    row = session.scalar(
        statement.order_by(BusinessCompanySemanticConfigVersion.version.desc()).limit(1)
    )
    if row is None:
        raise _error(
            404,
            "company_semantic_config_not_found",
            "No published Company semantic configuration was found.",
        )
    return _config_response(session, row)


def list_company_semantic_configurations(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> CompanySemanticConfigurationPage:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    filters = (
        BusinessCompanySemanticConfigVersion.tenant_id == tenant_id,
        BusinessCompanySemanticConfigVersion.company_id == access.company.id,
        BusinessCompanySemanticConfigVersion.status == "published",
    )
    total = (
        session.scalar(
            select(func.count()).select_from(BusinessCompanySemanticConfigVersion).where(*filters)
        )
        or 0
    )
    rows = list(
        session.scalars(
            select(BusinessCompanySemanticConfigVersion)
            .where(*filters)
            .order_by(BusinessCompanySemanticConfigVersion.version.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return CompanySemanticConfigurationPage(
        items=[_config_response(session, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_scoped_configuration(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    version: int,
) -> BusinessCompanySemanticConfigVersion | None:
    return session.scalar(
        select(BusinessCompanySemanticConfigVersion).where(
            BusinessCompanySemanticConfigVersion.tenant_id == tenant_id,
            BusinessCompanySemanticConfigVersion.company_id == company_id,
            BusinessCompanySemanticConfigVersion.version == version,
            BusinessCompanySemanticConfigVersion.status == "published",
        )
    )


def get_scoped_metric_definition(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    metric_key: str,
    version: int | None,
) -> BusinessMetricDefinitionVersion | None:
    if version is None:
        return _latest_published_definition(session, tenant_id, company_id, metric_key)
    return session.scalar(
        select(BusinessMetricDefinitionVersion).where(
            BusinessMetricDefinitionVersion.tenant_id == tenant_id,
            BusinessMetricDefinitionVersion.company_id == company_id,
            BusinessMetricDefinitionVersion.metric_key == metric_key,
            BusinessMetricDefinitionVersion.version == version,
        )
    )


def definition_snapshot(row: BusinessMetricDefinitionVersion) -> dict[str, object]:
    return _definition_snapshot(row)


def dependency_by_key(dependencies: list[dict[str, object]]) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    for dependency in dependencies:
        key = dependency.get("metric_key")
        if isinstance(key, str):
            result[key] = dependency
    return result
