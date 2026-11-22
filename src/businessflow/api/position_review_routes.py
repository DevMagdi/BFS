from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.models import UserAccount
from businessflow.position_reviews.schemas import (
    DemoTemplateDataset,
    FinancialImportConfirmRequest,
    FinancialImportListResponse,
    FinancialImportResponse,
    PositionDemoSeedResponse,
    PositionDemoWorkspaceResponse,
    PositionReviewCreateRequest,
    PositionReviewDispositionRequest,
    PositionReviewDispositionResponse,
    PositionReviewListResponse,
    PositionReviewResponse,
)
from businessflow.position_reviews.service import (
    confirm_financial_import,
    create_demo_workspace,
    create_position_review,
    get_demo_template,
    get_demo_workspace,
    get_financial_import,
    get_position_review,
    list_financial_imports,
    list_position_reviews,
    record_position_disposition,
    stage_financial_import,
)

router = APIRouter(prefix="/tenants/{tenant_id}/position-reviews", tags=["monthly position review"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


@router.get("/demo", response_model=PositionDemoWorkspaceResponse)
def read_demo_workspace(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PositionDemoWorkspaceResponse:
    return get_demo_workspace(session, user, tenant_id)


@router.post("/demo/seed", response_model=PositionDemoSeedResponse, status_code=201)
def seed_demo_workspace(
    tenant_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> PositionDemoSeedResponse:
    return create_demo_workspace(
        session,
        user,
        tenant_id,
        request.state.request_id,
        settings,
    )


@router.get("/demo/templates/{dataset_type}")
def download_demo_template(
    tenant_id: UUID,
    dataset_type: DemoTemplateDataset,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    content, filename = get_demo_template(session, user, tenant_id, dataset_type)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Synthetic-Demo-Data": "true",
        },
    )


@router.post("/financial-imports", response_model=FinancialImportResponse, status_code=201)
def post_financial_import(
    tenant_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinancialImportResponse:
    return stage_financial_import(
        session,
        user,
        tenant_id,
        file.file,
        file.filename,
        idempotency_key,
        request.state.request_id,
        settings,
    )


@router.get("/financial-imports", response_model=FinancialImportListResponse)
def get_financial_import_history(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialImportListResponse:
    return list_financial_imports(session, user, tenant_id, limit=limit, offset=offset)


@router.get("/financial-imports/{import_id}", response_model=FinancialImportResponse)
def read_financial_import(
    tenant_id: UUID,
    import_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialImportResponse:
    return get_financial_import(session, user, tenant_id, import_id)


@router.post("/financial-imports/{import_id}/confirm", response_model=FinancialImportResponse)
def confirm_finance_import(
    tenant_id: UUID,
    import_id: UUID,
    body: FinancialImportConfirmRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialImportResponse:
    return confirm_financial_import(
        session,
        user,
        tenant_id,
        import_id,
        body,
        request.state.request_id,
    )


@router.post("/briefs", response_model=PositionReviewResponse, status_code=201)
def post_position_review(
    tenant_id: UUID,
    body: PositionReviewCreateRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PositionReviewResponse:
    return create_position_review(
        session,
        user,
        tenant_id,
        body,
        idempotency_key,
        request.state.request_id,
    )


@router.get("/briefs", response_model=PositionReviewListResponse)
def get_position_review_history(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PositionReviewListResponse:
    return list_position_reviews(session, user, tenant_id, limit=limit, offset=offset)


@router.get("/briefs/{review_id}", response_model=PositionReviewResponse)
def read_position_review(
    tenant_id: UUID,
    review_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PositionReviewResponse:
    return get_position_review(session, user, tenant_id, review_id)


@router.post(
    "/briefs/{review_id}/dispositions",
    response_model=PositionReviewDispositionResponse,
    status_code=201,
)
def post_position_review_disposition(
    tenant_id: UUID,
    review_id: UUID,
    body: PositionReviewDispositionRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> PositionReviewDispositionResponse:
    return record_position_disposition(
        session,
        user,
        tenant_id,
        review_id,
        body,
        request.state.request_id,
    )
