from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_metrics_session, get_session
from businessflow.errors import ApiError
from businessflow.metrics.models import MetricRun
from businessflow.metrics.schemas import (
    MetricCatalogResponse,
    MetricRunCreate,
    MetricRunResponse,
)
from businessflow.metrics.service import (
    create_metric_run,
    get_metric_run,
    read_metric_catalog,
)
from businessflow.models import UserAccount

router = APIRouter(prefix="/tenants/{tenant_id}/metrics", tags=["metrics"])


def _run_response(run: MetricRun) -> MetricRunResponse:
    return MetricRunResponse(
        id=run.id,
        tenant_id=run.tenant_id,
        company_id=run.company_id,
        requested_by_user_id=run.requested_by_user_id,
        start_week_date=run.start_week_date,
        end_week_date=run.end_week_date,
        week_count=run.week_count,
        metric_catalog_version=run.metric_catalog_version,
        calculation_version=run.calculation_version,
        reliability_status=run.reliability_status,
        input_hash=run.input_hash,
        result_hash=run.result_hash,
        input_snapshot=run.input_snapshot,
        result_snapshot=run.result_snapshot,
        created_at=run.created_at,
    )


@router.get("/catalog", response_model=MetricCatalogResponse)
def get_metrics_catalog(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricCatalogResponse:
    payload = read_metric_catalog(session, user, tenant_id)
    return MetricCatalogResponse.model_validate(payload)


@router.post("/runs", response_model=MetricRunResponse, status_code=201)
def post_metric_run(
    tenant_id: UUID,
    body: MetricRunCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_metrics_session),
) -> MetricRunResponse:
    try:
        run = create_metric_run(
            session,
            user,
            tenant_id,
            body.start_week_date,
            body.week_count,
            request.state.request_id,
        )
    except ValueError:
        raise ApiError(
            409,
            "metric_input_invalid",
            "Metric inputs are inconsistent and cannot be calculated reliably.",
        ) from None
    return _run_response(run)


@router.get("/runs/{run_id}", response_model=MetricRunResponse)
def get_saved_metric_run(
    tenant_id: UUID,
    run_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> MetricRunResponse:
    return _run_response(get_metric_run(session, user, tenant_id, run_id))
