from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.company_data.schemas import (
    CompanyDataContractListResponse,
    CompanyDataIngestionCreateRequest,
    CompanyDataIngestionListResponse,
    CompanyDataIngestionResponse,
    CompanyDataLineagePage,
    CompanyDataSourceCreateRequest,
    CompanyDataSourceListResponse,
    CompanyDataSourceSummary,
)
from businessflow.company_data.service import (
    create_ingestion,
    create_source,
    get_ingestion,
    get_source,
    list_contracts,
    list_ingestions,
    list_lineage,
    list_sources,
)
from businessflow.models import UserAccount

router = APIRouter(prefix="/tenants/{tenant_id}/company-data", tags=["company data"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


@router.get("/contracts", response_model=CompanyDataContractListResponse)
def get_company_data_contracts(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataContractListResponse:
    return list_contracts(session, user, tenant_id)


@router.post("/sources", response_model=CompanyDataSourceSummary, status_code=201)
def post_company_data_source(
    tenant_id: UUID,
    body: CompanyDataSourceCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataSourceSummary:
    return create_source(session, user, tenant_id, body, request.state.request_id)


@router.get("/sources", response_model=CompanyDataSourceListResponse)
def get_company_data_sources(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataSourceListResponse:
    return list_sources(session, user, tenant_id, limit=limit, offset=offset)


@router.get("/sources/{source_id}", response_model=CompanyDataSourceSummary)
def read_company_data_source(
    tenant_id: UUID,
    source_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataSourceSummary:
    return get_source(session, user, tenant_id, source_id)


@router.post(
    "/sources/{source_id}/ingestions",
    response_model=CompanyDataIngestionResponse,
    status_code=201,
)
def post_company_data_ingestion(
    tenant_id: UUID,
    source_id: UUID,
    body: CompanyDataIngestionCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataIngestionResponse:
    return create_ingestion(
        session,
        user,
        tenant_id,
        source_id,
        body,
        request.state.request_id,
    )


@router.get(
    "/sources/{source_id}/ingestions",
    response_model=CompanyDataIngestionListResponse,
)
def get_company_data_ingestions(
    tenant_id: UUID,
    source_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataIngestionListResponse:
    return list_ingestions(
        session,
        user,
        tenant_id,
        source_id,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/sources/{source_id}/ingestions/{ingestion_id}",
    response_model=CompanyDataIngestionResponse,
)
def read_company_data_ingestion(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataIngestionResponse:
    return get_ingestion(session, user, tenant_id, source_id, ingestion_id)


@router.get(
    "/sources/{source_id}/ingestions/{ingestion_id}/lineage",
    response_model=CompanyDataLineagePage,
)
def get_company_data_ingestion_lineage(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CompanyDataLineagePage:
    return list_lineage(
        session,
        user,
        tenant_id,
        source_id,
        ingestion_id,
        limit=limit,
        offset=offset,
    )
