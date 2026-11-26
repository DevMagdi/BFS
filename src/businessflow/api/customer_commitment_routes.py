from __future__ import annotations

from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.company_data.commitment_schemas import (
    CommitmentStatus,
    CustomerCommitmentCreateRequest,
    CustomerCommitmentDetailResponse,
    CustomerCommitmentHistoryPage,
    CustomerCommitmentPage,
    CustomerCommitmentReviewRequest,
    CustomerCommitmentSubmitRequest,
    CustomerCommitmentUpdateRequest,
    ManualCustomerCommitmentEvidenceResponse,
)
from businessflow.company_data.customer_commitments import (
    MAX_HISTORY_OFFSET,
    MAX_HISTORY_PAGE,
    add_evidence,
    create_draft,
    download_evidence,
    get_commitment,
    list_commitments,
    list_history,
    review_commitment,
    save_draft,
    submit_for_review,
)
from businessflow.models import UserAccount

router = APIRouter(
    prefix="/tenants/{tenant_id}/company-data/customer-commitments",
    tags=["manual customer commitments"],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
HistoryLimit = Annotated[int, Query(ge=1, le=MAX_HISTORY_PAGE)]
HistoryOffset = Annotated[int, Query(ge=0, le=MAX_HISTORY_OFFSET)]


@router.get("", response_model=CustomerCommitmentPage)
def get_customer_commitments(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    status: CommitmentStatus | None = Query(default=None),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentPage:
    return list_commitments(session, user, tenant_id, limit=limit, offset=offset, status=status)


@router.post("", response_model=CustomerCommitmentDetailResponse, status_code=201)
def post_customer_commitment(
    tenant_id: UUID,
    body: CustomerCommitmentCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentDetailResponse:
    return create_draft(session, user, tenant_id, body, request.state.request_id)


@router.get("/{commitment_id}", response_model=CustomerCommitmentDetailResponse)
def read_customer_commitment(
    tenant_id: UUID,
    commitment_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentDetailResponse:
    return get_commitment(session, user, tenant_id, commitment_id)


@router.put("/{commitment_id}", response_model=CustomerCommitmentDetailResponse)
def put_customer_commitment_draft(
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentUpdateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentDetailResponse:
    return save_draft(session, user, tenant_id, commitment_id, body, request.state.request_id)


@router.post(
    "/{commitment_id}/submit",
    response_model=CustomerCommitmentDetailResponse,
)
def submit_customer_commitment(
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentSubmitRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentDetailResponse:
    return submit_for_review(
        session, user, tenant_id, commitment_id, body, request.state.request_id
    )


@router.post(
    "/{commitment_id}/review",
    response_model=CustomerCommitmentDetailResponse,
)
def review_customer_commitment(
    tenant_id: UUID,
    commitment_id: UUID,
    body: CustomerCommitmentReviewRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentDetailResponse:
    return review_commitment(
        session, user, tenant_id, commitment_id, body, request.state.request_id
    )


@router.get(
    "/{commitment_id}/history",
    response_model=CustomerCommitmentHistoryPage,
)
def get_customer_commitment_history(
    tenant_id: UUID,
    commitment_id: UUID,
    limit: HistoryLimit = 50,
    offset: HistoryOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CustomerCommitmentHistoryPage:
    return list_history(
        session,
        user,
        tenant_id,
        commitment_id,
        limit=limit,
        offset=offset,
    )


@router.post(
    "/{commitment_id}/evidence",
    response_model=ManualCustomerCommitmentEvidenceResponse,
    status_code=201,
)
def post_customer_commitment_evidence(
    tenant_id: UUID,
    commitment_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File(...)],
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ManualCustomerCommitmentEvidenceResponse:
    return add_evidence(
        session,
        user,
        tenant_id,
        commitment_id,
        filename=file.filename,
        declared_type=file.content_type,
        upload=file.file,
        request_id=request.state.request_id,
    )


@router.get("/{commitment_id}/evidence/{evidence_id}")
def read_customer_commitment_evidence(
    tenant_id: UUID,
    commitment_id: UUID,
    evidence_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    evidence = download_evidence(session, user, tenant_id, commitment_id, evidence_id)
    encoded_filename = quote(evidence.file_name, safe="")
    content_disposition = f"attachment; filename*=UTF-8''{encoded_filename}"
    return Response(
        content=evidence.content,
        media_type=evidence.media_type,
        headers={
            "Content-Disposition": content_disposition,
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "X-Evidence-SHA256": evidence.file_hash,
        },
    )
