from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from businessflow.api.company_context_routes import _protect_seeded_demo_workspace
from businessflow.api.dependencies import (
    get_current_user,
    get_metrics_session,
    get_session,
    get_settings,
)
from businessflow.business_intelligence.analysis_service import (
    assess_metric_readiness,
    create_metric_analysis,
    get_metric_analysis,
    list_metric_analyses,
    list_metric_evidence,
    replay_metric_analysis,
)
from businessflow.business_intelligence.registry_service import (
    create_company_semantic_configuration,
    create_metric_definition,
    get_company_semantic_configuration,
    get_metric_definition,
    list_company_semantic_configurations,
    list_metric_definitions,
    registry_catalog,
    transition_metric_definition,
)
from businessflow.business_intelligence.schemas import (
    CompanySemanticConfigurationCreateRequest,
    CompanySemanticConfigurationPage,
    CompanySemanticConfigurationResponse,
    MetricAnalysisCreateRequest,
    MetricAnalysisPage,
    MetricAnalysisReplayResponse,
    MetricAnalysisResponse,
    MetricDefinitionCreateRequest,
    MetricDefinitionLifecycleRequest,
    MetricDefinitionPage,
    MetricDefinitionResponse,
    MetricEvidencePage,
    MetricReadinessResponse,
    MetricRegistryCatalog,
)
from businessflow.config import Settings
from businessflow.errors import ApiError, CommitAndRaise
from businessflow.models import Company, UserAccount
from businessflow.position_reviews.models import PositionDemoSeed
from businessflow.services.company_context_access import require_company_context_access


def _protect_seeded_demo_analysis_write(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> None:
    """Serialize analysis creation with seeding without blocking its foreign-key checks."""
    try:
        access = require_company_context_access(session, user, tenant_id, "company_context:read")
    except (ApiError, CommitAndRaise):
        # Leave unscoped/unauthorized requests to the route's normal authorization path.
        return
    # PostgreSQL renders this as FOR NO KEY UPDATE: it conflicts with the seed path's
    # FOR UPDATE lock, but remains compatible with the separate analysis transaction's
    # foreign-key KEY SHARE lock on companies.
    session.scalar(
        select(Company.id)
        .where(Company.id == access.company.id, Company.tenant_id == tenant_id)
        .with_for_update(key_share=True)
    )
    seed_exists = session.scalar(
        select(PositionDemoSeed.id).where(
            PositionDemoSeed.tenant_id == tenant_id,
            PositionDemoSeed.company_id == access.company.id,
        )
    )
    if seed_exists is not None:
        raise ApiError(
            409,
            "synthetic_demo_operational_api_read_only",
            (
                "M10 workspaces accept analytical writes only through their exact built-in "
                "synthetic workflow; direct Business Intelligence writes are disabled."
            ),
        )


router = APIRouter(
    prefix="/tenants/{tenant_id}/business-intelligence",
    tags=["business intelligence"],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
MetricStatusFilter = Literal["draft", "review", "published", "deprecated"]
MetricDomainFilter = Literal["finance", "sales", "customers", "procurement", "operations", "people"]


@router.get("/registry", response_model=MetricRegistryCatalog)
def get_business_intelligence_registry(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricRegistryCatalog:
    return registry_catalog(session, user, tenant_id)


@router.get("/metrics", response_model=MetricDefinitionPage)
def get_metric_registry_definitions(
    tenant_id: UUID,
    metric_key: str | None = None,
    domain: MetricDomainFilter | None = None,
    status: MetricStatusFilter | None = None,
    include_history: bool = False,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricDefinitionPage:
    return list_metric_definitions(
        session,
        user,
        tenant_id,
        metric_key=metric_key,
        domain=domain,
        status=status,
        include_history=include_history,
        limit=limit,
        offset=offset,
    )


@router.get("/metrics/{metric_key}", response_model=MetricDefinitionResponse)
def get_current_metric_registry_definition(
    tenant_id: UUID,
    metric_key: str,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricDefinitionResponse:
    return get_metric_definition(session, user, tenant_id, metric_key)


@router.get("/metrics/{metric_key}/versions/{version}", response_model=MetricDefinitionResponse)
def get_metric_registry_definition_version(
    tenant_id: UUID,
    metric_key: str,
    version: int,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricDefinitionResponse:
    return get_metric_definition(session, user, tenant_id, metric_key, version)


@router.post(
    "/metrics",
    response_model=MetricDefinitionResponse,
    status_code=201,
    dependencies=[Depends(_protect_seeded_demo_workspace)],
)
def post_metric_registry_definition(
    tenant_id: UUID,
    body: MetricDefinitionCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricDefinitionResponse:
    return create_metric_definition(session, user, tenant_id, body, request.state.request_id)


@router.post(
    "/metrics/{metric_key}/versions/{version}/lifecycle",
    response_model=MetricDefinitionResponse,
    dependencies=[Depends(_protect_seeded_demo_workspace)],
)
def post_metric_definition_lifecycle(
    tenant_id: UUID,
    metric_key: str,
    version: int,
    body: MetricDefinitionLifecycleRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricDefinitionResponse:
    return transition_metric_definition(
        session, user, tenant_id, metric_key, version, body, request.state.request_id
    )


@router.get(
    "/metrics/{metric_key}/readiness",
    response_model=MetricReadinessResponse,
)
def get_metric_readiness(
    tenant_id: UUID,
    metric_key: str,
    version: int | None = Query(default=None, ge=1),
    configuration_version: int | None = Query(default=None, ge=1),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_metrics_session),
    settings: Settings = Depends(get_settings),
) -> MetricReadinessResponse:
    return assess_metric_readiness(
        session,
        user,
        tenant_id,
        metric_key,
        version=version,
        configuration_version=configuration_version,
        settings=settings,
    )


@router.get("/configurations", response_model=CompanySemanticConfigurationPage)
def get_company_semantic_configurations(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanySemanticConfigurationPage:
    return list_company_semantic_configurations(
        session, user, tenant_id, limit=limit, offset=offset
    )


@router.get("/configuration", response_model=CompanySemanticConfigurationResponse)
def get_current_company_semantic_configuration(
    tenant_id: UUID,
    version: int | None = Query(default=None, ge=1),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanySemanticConfigurationResponse:
    return get_company_semantic_configuration(session, user, tenant_id, version)


@router.post(
    "/configurations",
    response_model=CompanySemanticConfigurationResponse,
    status_code=201,
    dependencies=[Depends(_protect_seeded_demo_workspace)],
)
def post_company_semantic_configuration(
    tenant_id: UUID,
    body: CompanySemanticConfigurationCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanySemanticConfigurationResponse:
    return create_company_semantic_configuration(
        session, user, tenant_id, body, request.state.request_id
    )


@router.get("/analyses", response_model=MetricAnalysisPage)
def get_metric_analyses(
    tenant_id: UUID,
    metric_key: str | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MetricAnalysisPage:
    return list_metric_analyses(
        session,
        user,
        tenant_id,
        metric_key=metric_key,
        limit=limit,
        offset=offset,
        settings=settings,
    )


@router.post(
    "/analyses",
    response_model=MetricAnalysisResponse,
    status_code=201,
    dependencies=[Depends(_protect_seeded_demo_analysis_write)],
)
def post_metric_analysis(
    tenant_id: UUID,
    body: MetricAnalysisCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_metrics_session),
    settings: Settings = Depends(get_settings),
) -> MetricAnalysisResponse:
    return create_metric_analysis(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        settings=settings,
    )


@router.get("/analyses/{analysis_id}", response_model=MetricAnalysisResponse)
def get_saved_metric_analysis(
    tenant_id: UUID,
    analysis_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MetricAnalysisResponse:
    return get_metric_analysis(session, user, tenant_id, analysis_id, settings=settings)


@router.get("/analyses/{analysis_id}/evidence", response_model=MetricEvidencePage)
def get_saved_metric_analysis_evidence(
    tenant_id: UUID,
    analysis_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> MetricEvidencePage:
    return list_metric_evidence(
        session,
        user,
        tenant_id,
        analysis_id,
        limit=limit,
        offset=offset,
        settings=settings,
    )


@router.get("/analyses/{analysis_id}/replay", response_model=MetricAnalysisReplayResponse)
def replay_saved_metric_analysis(
    tenant_id: UUID,
    analysis_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_metrics_session),
    settings: Settings = Depends(get_settings),
) -> MetricAnalysisReplayResponse:
    return replay_metric_analysis(session, user, tenant_id, analysis_id, settings=settings)
