from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.financial_calculations.contracts import (
    FinancialCalculationCreateResponse,
    FinancialCalculationFormulaCatalogResponse,
    FinancialCalculationReplayVerificationReport,
    FinancialCalculationRequest,
    FinancialCalculationRunResponse,
)
from businessflow.financial_calculations.service import (
    create_financial_calculation_run,
    ensure_financial_calculations_enabled,
    get_financial_calculation_run,
    list_financial_calculation_formulas,
    verify_financial_calculation_run,
)
from businessflow.models import UserAccount


def require_financial_calculations_enabled(
    settings: Settings = Depends(get_settings),
) -> None:
    ensure_financial_calculations_enabled(settings)


router = APIRouter(
    prefix="/tenants/{tenant_id}/financial-calculations",
    tags=["deterministic financial calculations"],
    dependencies=[Depends(require_financial_calculations_enabled)],
)


@router.get("/formulas", response_model=FinancialCalculationFormulaCatalogResponse)
def read_financial_calculation_formulas(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinancialCalculationFormulaCatalogResponse:
    return list_financial_calculation_formulas(session, user, tenant_id, settings=settings)


@router.post(
    "/finance-source-intakes/{intake_id}/runs",
    response_model=FinancialCalculationCreateResponse,
    status_code=201,
    summary="Calculate one M14 source-group exact subtotal",
    description=(
        "Default-disabled, non-production-only mechanical arithmetic. The caller resubmits every "
        "source-row amount; BFS verifies each value against an M14 keyed commitment and requires "
        "complete, unique, same-group evidence. The result is not accounting truth, a revenue or "
        "cost classification, or a finance-owner-approved value."
    ),
)
def post_financial_calculation_run(
    tenant_id: UUID,
    intake_id: UUID,
    body: FinancialCalculationRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinancialCalculationCreateResponse:
    return create_financial_calculation_run(
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
    response_model=FinancialCalculationRunResponse,
)
def read_financial_calculation_run(
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinancialCalculationRunResponse:
    return get_financial_calculation_run(
        session, user, tenant_id, intake_id, run_id, settings=settings
    )


@router.post(
    "/finance-source-intakes/{intake_id}/runs/{run_id}/verify",
    response_model=FinancialCalculationReplayVerificationReport,
    summary="Replay an M15 run against its exact evidence and inputs",
    description=(
        "Resubmits raw source amount strings. Requires finance:write because this endpoint can "
        "otherwise act as a commitment-verification oracle."
    ),
)
def replay_financial_calculation_run(
    tenant_id: UUID,
    intake_id: UUID,
    run_id: UUID,
    body: FinancialCalculationRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinancialCalculationReplayVerificationReport:
    return verify_financial_calculation_run(
        session,
        user,
        tenant_id,
        intake_id,
        run_id,
        body,
        settings=settings,
        request_id=request.state.request_id,
    )
