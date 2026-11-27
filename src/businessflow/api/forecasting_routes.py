from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.forecasting.contracts import (
    ForecastCatalogResponse,
    ForecastDatasetCreate,
    ForecastDatasetDetail,
    ForecastDatasetPage,
    ForecastEvaluationCreate,
    ForecastEvaluationPage,
    ForecastEvaluationProtocolCreate,
    ForecastEvaluationReplayResponse,
    ForecastEvaluationResponse,
    ForecastModelCreate,
    ForecastModelPage,
    ForecastModelSummary,
    ForecastPromotionCreate,
    ForecastPromotionPage,
    ForecastPromotionResponse,
    ForecastProtocolPage,
    ForecastProtocolSummary,
    ForecastRunCreate,
    ForecastRunPage,
    ForecastRunReplayResponse,
    ForecastRunResponse,
)
from businessflow.forecasting.service import (
    create_forecast_dataset,
    create_forecast_evaluation,
    create_forecast_model,
    create_forecast_promotion,
    create_forecast_protocol,
    create_forecast_run,
    ensure_forecasting_enabled,
    get_forecast_catalog,
    get_forecast_dataset,
    get_forecast_evaluation,
    get_forecast_model,
    get_forecast_protocol,
    get_forecast_run,
    list_forecast_datasets,
    list_forecast_evaluations,
    list_forecast_models,
    list_forecast_promotions,
    list_forecast_protocols,
    list_forecast_runs,
    verify_forecast_evaluation,
    verify_forecast_run,
)
from businessflow.models import UserAccount


def require_forecasting_enabled(settings: Settings = Depends(get_settings)) -> None:
    ensure_forecasting_enabled(settings)


router = APIRouter(
    prefix="/tenants/{tenant_id}/forecasts",
    tags=["forecasting and model evaluation"],
    dependencies=[Depends(require_forecasting_enabled)],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


@router.get("/catalog", response_model=ForecastCatalogResponse)
def read_forecasting_catalog(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastCatalogResponse:
    return get_forecast_catalog(session, user, tenant_id, settings=settings)


@router.get("/datasets", response_model=ForecastDatasetPage)
def read_forecast_datasets(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastDatasetPage:
    return list_forecast_datasets(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post(
    "/datasets",
    response_model=ForecastDatasetDetail,
    status_code=201,
    summary="Create an immutable version of a company-scoped time-series dataset",
    description=(
        "Source references and data origin are caller assertions, not independent source "
        "verification. Use synthetic_fixture only for deterministic tests; results from synthetic "
        "data are not customer validation or accuracy evidence. Dataset and protocol policy are "
        "not treated as company-approved merely because they are persisted."
    ),
)
def post_forecast_dataset(
    tenant_id: UUID,
    body: ForecastDatasetCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastDatasetDetail:
    return create_forecast_dataset(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/datasets/{dataset_version_id}", response_model=ForecastDatasetDetail)
def read_forecast_dataset(
    tenant_id: UUID,
    dataset_version_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastDatasetDetail:
    return get_forecast_dataset(session, user, tenant_id, dataset_version_id, settings=settings)


@router.get("/models", response_model=ForecastModelPage)
def read_forecast_models(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastModelPage:
    return list_forecast_models(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post("/models", response_model=ForecastModelSummary, status_code=201)
def post_forecast_model(
    tenant_id: UUID,
    body: ForecastModelCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastModelSummary:
    return create_forecast_model(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/models/{model_version_id}", response_model=ForecastModelSummary)
def read_forecast_model(
    tenant_id: UUID,
    model_version_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastModelSummary:
    return get_forecast_model(session, user, tenant_id, model_version_id, settings=settings)


@router.get("/protocols", response_model=ForecastProtocolPage)
def read_forecast_protocols(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastProtocolPage:
    return list_forecast_protocols(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post("/protocols", response_model=ForecastProtocolSummary, status_code=201)
def post_forecast_protocol(
    tenant_id: UUID,
    body: ForecastEvaluationProtocolCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastProtocolSummary:
    return create_forecast_protocol(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/protocols/{protocol_version_id}", response_model=ForecastProtocolSummary)
def read_forecast_protocol(
    tenant_id: UUID,
    protocol_version_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastProtocolSummary:
    return get_forecast_protocol(session, user, tenant_id, protocol_version_id, settings=settings)


@router.get("/evaluations", response_model=ForecastEvaluationPage)
def read_forecast_evaluations(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastEvaluationPage:
    return list_forecast_evaluations(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post("/evaluations", response_model=ForecastEvaluationResponse, status_code=201)
def post_forecast_evaluation(
    tenant_id: UUID,
    body: ForecastEvaluationCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastEvaluationResponse:
    return create_forecast_evaluation(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/evaluations/{evaluation_id}", response_model=ForecastEvaluationResponse)
def read_forecast_evaluation(
    tenant_id: UUID,
    evaluation_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastEvaluationResponse:
    return get_forecast_evaluation(session, user, tenant_id, evaluation_id, settings=settings)


@router.post(
    "/evaluations/{evaluation_id}/verify",
    response_model=ForecastEvaluationReplayResponse,
    summary="Replay one immutable evaluation against its original versioned evidence",
    description=(
        "Requires forecast:write. Replay proves deterministic agreement with persisted snapshots; "
        "it is not source refresh, independent audit, or company approval."
    ),
)
def replay_forecast_evaluation(
    tenant_id: UUID,
    evaluation_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastEvaluationReplayResponse:
    return verify_forecast_evaluation(
        session,
        user,
        tenant_id,
        evaluation_id,
        request_id=request.state.request_id,
        settings=settings,
    )


@router.get("/runs", response_model=ForecastRunPage)
def read_forecast_runs(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastRunPage:
    return list_forecast_runs(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post("/runs", response_model=ForecastRunResponse, status_code=201)
def post_forecast_run(
    tenant_id: UUID,
    body: ForecastRunCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastRunResponse:
    return create_forecast_run(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/runs/{run_id}", response_model=ForecastRunResponse)
def read_forecast_run(
    tenant_id: UUID,
    run_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastRunResponse:
    return get_forecast_run(session, user, tenant_id, run_id, settings=settings)


@router.post(
    "/runs/{run_id}/verify",
    response_model=ForecastRunReplayResponse,
    summary="Replay one future forecast against its original versioned evidence",
)
def replay_forecast_run(
    tenant_id: UUID,
    run_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastRunReplayResponse:
    return verify_forecast_run(
        session,
        user,
        tenant_id,
        run_id,
        request_id=request.state.request_id,
        settings=settings,
    )


@router.get("/promotions", response_model=ForecastPromotionPage)
def read_forecast_promotions(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastPromotionPage:
    return list_forecast_promotions(
        session, user, tenant_id, limit=limit, offset=offset, settings=settings
    )


@router.post(
    "/promotions",
    response_model=ForecastPromotionResponse,
    status_code=201,
    summary="Record an authorized human non-production decision",
    description=(
        "Requires forecast:review and a different reviewer from the evaluation/model author. "
        "Approval is restricted to complete holdout evidence; synthetic fixtures cannot be "
        "approved. Every decision is immutable and non-production-only. Production activation "
        "remains blocked pending separately established governance and approvals."
    ),
)
def post_forecast_promotion(
    tenant_id: UUID,
    body: ForecastPromotionCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ForecastPromotionResponse:
    return create_forecast_promotion(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )
