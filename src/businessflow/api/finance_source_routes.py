from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.errors import ApiError
from businessflow.finance_sources.schemas import (
    FinanceSourceContractPage,
    FinanceSourceIntakeMetadata,
    FinanceSourceIntakePage,
    FinanceSourceIntakeResponse,
    FinanceSourceRowEvidencePage,
)
from businessflow.finance_sources.service import (
    create_finance_source_intake,
    get_finance_source_contracts,
    get_finance_source_intake,
    list_finance_source_intakes,
    list_finance_source_row_evidence,
)
from businessflow.models import UserAccount

router = APIRouter(prefix="/tenants/{tenant_id}/finance-sources", tags=["finance source intake"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
MetadataJson = Annotated[str, Form(min_length=2, max_length=1_048_576)]


@router.get("/contracts", response_model=FinanceSourceContractPage)
def read_finance_source_contracts(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinanceSourceContractPage:
    return FinanceSourceContractPage(items=get_finance_source_contracts(session, user, tenant_id))


@router.post(
    "/intakes",
    response_model=FinanceSourceIntakeResponse,
    status_code=201,
    summary="Profile and compare one finance-source CSV",
    description=(
        "Runs the versioned finance source contract, explicit mapping, profiling, row-quality "
        "checks, and caller-supplied control comparison in one request. It does not create "
        "canonical finance facts or establish accounting correctness."
    ),
)
def post_finance_source_intake(
    tenant_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    metadata_json: MetadataJson,
    file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> FinanceSourceIntakeResponse:
    try:
        metadata = FinanceSourceIntakeMetadata.model_validate_json(metadata_json)
    except ValidationError:
        raise ApiError(
            422,
            "invalid_finance_source_metadata",
            "Finance source intake metadata failed strict validation.",
        ) from None
    return create_finance_source_intake(
        session,
        user,
        tenant_id,
        file.file,
        file.filename,
        idempotency_key,
        metadata,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/intakes", response_model=FinanceSourceIntakePage)
def read_finance_source_intakes(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinanceSourceIntakePage:
    return list_finance_source_intakes(session, user, tenant_id, limit=limit, offset=offset)


@router.get("/intakes/{intake_id}", response_model=FinanceSourceIntakeResponse)
def read_finance_source_intake(
    tenant_id: UUID,
    intake_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinanceSourceIntakeResponse:
    return get_finance_source_intake(session, user, tenant_id, intake_id)


@router.get("/intakes/{intake_id}/rows", response_model=FinanceSourceRowEvidencePage)
def read_finance_source_row_evidence(
    tenant_id: UUID,
    intake_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinanceSourceRowEvidencePage:
    return list_finance_source_row_evidence(
        session,
        user,
        tenant_id,
        intake_id,
        limit=limit,
        offset=offset,
    )
