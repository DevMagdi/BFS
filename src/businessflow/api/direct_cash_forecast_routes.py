from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.direct_cash_forecasts.contracts import (
    DirectCashForecastContractResponse,
    DirectCashForecastCreateResponse,
    DirectCashForecastReplayVerificationReport,
    DirectCashForecastRequest,
    DirectCashForecastRunResponse,
)
from businessflow.direct_cash_forecasts.service import (
    create_direct_cash_forecast_run,
    ensure_direct_cash_forecasts_enabled,
    get_direct_cash_forecast_contract,
    get_direct_cash_forecast_run,
    verify_direct_cash_forecast_run,
)
from businessflow.models import UserAccount


def require_direct_cash_forecasts_enabled(
    settings: Settings = Depends(get_settings),
) -> None:
    ensure_direct_cash_forecasts_enabled(settings)


router = APIRouter(
    prefix="/tenants/{tenant_id}/direct-cash-forecasts",
    tags=["direct cash forecasts"],
    dependencies=[Depends(require_direct_cash_forecasts_enabled)],
)


@router.get("/contract", response_model=DirectCashForecastContractResponse)
def read_direct_cash_forecast_contract(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DirectCashForecastContractResponse:
    return get_direct_cash_forecast_contract(session, user, tenant_id, settings=settings)


@router.post(
    "/finance-source-intakes/{intake_id}/runs",
    response_model=DirectCashForecastCreateResponse,
    status_code=201,
    summary="Calculate one M14-bound 13-week cash schedule",
    description=(
        "Default-disabled and non-production-only mechanical arithmetic. The caller explicitly "
        "designates opening cash and asserts flow type, date, and actual/forecast basis for each "
        "M14 group row; unknown, unsupported, or missing values remain unresolved. BFS verifies "
        "submitted amounts against row-bound M14 commitments, but M14 does not authenticate cash "
        "meaning, timing, classification, source authority, or population completeness. The "
        "persisted result is a known-item roll-forward, not an authoritative cash balance or "
        "complete/accurate forecast; no payment is initiated."
    ),
)
def post_direct_cash_forecast_run(
    tenant_id: UUID,
    intake_id: UUID,
    body: DirectCashForecastRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DirectCashForecastCreateResponse:
    return create_direct_cash_forecast_run(
        session,
        user,
        tenant_id,
        intake_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get(
    "/finance-source-intakes/{intake_id}/runs/{run_id}",
    response_model=DirectCashForecastRunResponse,
)
def read_direct_cash_forecast_run(
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DirectCashForecastRunResponse:
    return get_direct_cash_forecast_run(
        session, user, tenant_id, intake_id, run_id, settings=settings
    )


@router.post(
    "/finance-source-intakes/{intake_id}/runs/{run_id}/verify",
    response_model=DirectCashForecastReplayVerificationReport,
    summary="Replay a direct-cash run against its exact M14 evidence and inputs",
    description=(
        "Resubmits raw amount strings and can act as a commitment-verification oracle; requires "
        "finance:write. Replay is not source refresh, approval, or independent audit."
    ),
)
def replay_direct_cash_forecast_run(
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    body: DirectCashForecastRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> DirectCashForecastReplayVerificationReport:
    return verify_direct_cash_forecast_run(
        session,
        user,
        tenant_id,
        intake_id,
        run_id,
        body,
        settings=settings,
        request_id=request.state.request_id,
    )
