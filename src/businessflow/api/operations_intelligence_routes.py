from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.models import UserAccount
from businessflow.operations_intelligence.schemas import (
    OperationsAnalysisCreateRequest,
    OperationsAnalysisEvidencePage,
    OperationsAnalysisResponse,
    OperationsEventCorrectionRequest,
    OperationsEventCreateRequest,
    OperationsEventHistoryPage,
    OperationsEventPage,
    OperationsEventSummary,
)
from businessflow.operations_intelligence.service import (
    correct_operations_event,
    create_operations_analysis,
    create_operations_event,
    get_operations_analysis,
    get_operations_event,
    list_operations_analysis_evidence,
    list_operations_event_versions,
    list_operations_events,
)

router = APIRouter(prefix="/tenants/{tenant_id}/operations", tags=["operations intelligence"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


@router.post("/events", response_model=OperationsEventSummary, status_code=201)
def post_operations_event(
    tenant_id: UUID,
    body: OperationsEventCreateRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsEventSummary:
    return create_operations_event(
        session,
        user,
        tenant_id,
        body,
        idempotency_key,
        request.state.request_id,
        request.state.now,
    )


@router.get("/events", response_model=OperationsEventPage)
def get_operations_events(
    tenant_id: UUID,
    source_id: UUID | None = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsEventPage:
    return list_operations_events(
        session, user, tenant_id, source_id=source_id, limit=limit, offset=offset
    )


@router.get("/events/{event_id}", response_model=OperationsEventSummary)
def get_operations_event_detail(
    tenant_id: UUID,
    event_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsEventSummary:
    return get_operations_event(session, user, tenant_id, event_id)


@router.get("/events/{event_id}/versions", response_model=OperationsEventHistoryPage)
def get_operations_event_versions(
    tenant_id: UUID,
    event_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsEventHistoryPage:
    return list_operations_event_versions(
        session, user, tenant_id, event_id, limit=limit, offset=offset
    )


@router.post(
    "/events/{event_id}/corrections",
    response_model=OperationsEventSummary,
    status_code=201,
)
def post_operations_event_correction(
    tenant_id: UUID,
    event_id: UUID,
    body: OperationsEventCorrectionRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsEventSummary:
    return correct_operations_event(
        session,
        user,
        tenant_id,
        event_id,
        body,
        idempotency_key,
        request.state.request_id,
        request.state.now,
    )


@router.post("/analyses", response_model=OperationsAnalysisResponse, status_code=201)
def post_operations_analysis(
    tenant_id: UUID,
    body: OperationsAnalysisCreateRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsAnalysisResponse:
    return create_operations_analysis(
        session,
        user,
        tenant_id,
        body,
        idempotency_key,
        request.state.request_id,
        request.state.now,
    )


@router.get("/analyses/{analysis_id}", response_model=OperationsAnalysisResponse)
def get_operations_analysis_detail(
    tenant_id: UUID,
    analysis_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsAnalysisResponse:
    return get_operations_analysis(session, user, tenant_id, analysis_id)


@router.get("/analyses/{analysis_id}/evidence", response_model=OperationsAnalysisEvidencePage)
def get_operations_analysis_evidence(
    tenant_id: UUID,
    analysis_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> OperationsAnalysisEvidencePage:
    return list_operations_analysis_evidence(
        session, user, tenant_id, analysis_id, limit=limit, offset=offset
    )
