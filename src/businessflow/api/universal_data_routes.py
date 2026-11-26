from __future__ import annotations

from typing import Annotated, Literal
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.models import UserAccount
from businessflow.universal_data.schemas import (
    CanonicalEntityType,
    CanonicalRecordPage,
    UniversalConnectorSyncRequest,
    UniversalEntityCatalog,
    UniversalFileIngestionResponse,
    UniversalIngestionDetail,
    UniversalIngestionPage,
    UniversalMappingApplyRequest,
    UniversalMappingCreateRequest,
    UniversalMappingProfileList,
    UniversalMappingProfileVersionResponse,
    UniversalPromotionRequest,
    UniversalPromotionResult,
    UniversalRowPage,
    UniversalSourceCreateRequest,
    UniversalSourcePage,
    UniversalSourceProfileHistory,
    UniversalSourceProfileSummary,
    UniversalSourceProfileUpdateRequest,
    UniversalValidationResult,
)
from businessflow.universal_data.service import (
    apply_existing_mapping,
    create_mapping_and_apply,
    create_source,
    get_evidence,
    get_ingestion_detail,
    get_source,
    list_canonical_records,
    list_entity_catalog,
    list_ingestions,
    list_mapping_profiles,
    list_record_lineage,
    list_rows,
    list_source_profile_history,
    list_sources,
    promote_ingestion,
    stage_connector_ingestion,
    stage_file_ingestion,
    update_source_profile,
    validate_ingestion,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/company-data/universal",
    tags=["universal data"],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=200)]


@router.get("/entities", response_model=UniversalEntityCatalog)
def get_universal_entity_catalog(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalEntityCatalog:
    return list_entity_catalog(session, user, tenant_id)


@router.post("/sources", response_model=UniversalSourceProfileSummary, status_code=201)
def post_universal_source(
    tenant_id: UUID,
    body: UniversalSourceCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalSourceProfileSummary:
    return create_source(session, user, tenant_id, body, request.state.request_id)


@router.get("/sources", response_model=UniversalSourcePage)
def get_universal_sources(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalSourcePage:
    return list_sources(session, user, tenant_id, limit=limit, offset=offset)


@router.get("/sources/{source_id}", response_model=UniversalSourceProfileSummary)
def read_universal_source(
    tenant_id: UUID,
    source_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalSourceProfileSummary:
    return get_source(session, user, tenant_id, source_id)


@router.patch("/sources/{source_id}/profile", response_model=UniversalSourceProfileSummary)
def patch_universal_source_profile(
    tenant_id: UUID,
    source_id: UUID,
    body: UniversalSourceProfileUpdateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalSourceProfileSummary:
    return update_source_profile(
        session, user, tenant_id, source_id, body, request.state.request_id
    )


@router.get(
    "/sources/{source_id}/profile-history",
    response_model=UniversalSourceProfileHistory,
)
def get_universal_source_profile_history(
    tenant_id: UUID,
    source_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalSourceProfileHistory:
    return list_source_profile_history(
        session, user, tenant_id, source_id, limit=limit, offset=offset
    )


@router.post(
    "/sources/{source_id}/ingestions",
    response_model=UniversalFileIngestionResponse,
    status_code=201,
)
def post_universal_file_ingestion(
    tenant_id: UUID,
    source_id: UUID,
    request: Request,
    file: Annotated[UploadFile, File()],
    idempotency_key: IdempotencyKey,
    csv_delimiter: Annotated[str, Form()] = ",",
    csv_encoding: Annotated[Literal["utf-8-sig", "utf-16", "cp1252"], Form()] = "utf-8-sig",
    header_row: Annotated[int, Form(ge=1, le=25)] = 1,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UniversalFileIngestionResponse:
    content = file.file.read(settings.import_max_file_bytes + 1)
    return stage_file_ingestion(
        session,
        user,
        tenant_id,
        source_id,
        file_name=file.filename,
        content=content,
        idempotency_key=idempotency_key,
        max_rows=settings.import_max_rows,
        max_file_bytes=settings.import_max_file_bytes,
        csv_delimiter=csv_delimiter,
        csv_encoding=csv_encoding,
        header_row=header_row,
        request_id=request.state.request_id,
    )


@router.post(
    "/sources/{source_id}/sync",
    response_model=UniversalFileIngestionResponse,
    status_code=201,
)
def post_universal_local_sync(
    tenant_id: UUID,
    source_id: UUID,
    body: UniversalConnectorSyncRequest,
    request: Request,
    idempotency_key: IdempotencyKey,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> UniversalFileIngestionResponse:
    return stage_connector_ingestion(
        session,
        user,
        tenant_id,
        source_id,
        relative_path=body.relative_path,
        idempotency_key=idempotency_key,
        local_root=settings.universal_local_connector_root,
        max_rows=settings.import_max_rows,
        max_file_bytes=settings.import_max_file_bytes,
        csv_delimiter=body.csv_delimiter,
        csv_encoding=body.csv_encoding,
        header_row=body.header_row,
        request_id=request.state.request_id,
    )


@router.get("/sources/{source_id}/ingestions", response_model=UniversalIngestionPage)
def get_universal_ingestions(
    tenant_id: UUID,
    source_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalIngestionPage:
    return list_ingestions(session, user, tenant_id, source_id, limit=limit, offset=offset)


@router.get(
    "/sources/{source_id}/mapping-profiles",
    response_model=UniversalMappingProfileList,
)
def get_universal_mapping_profiles(
    tenant_id: UUID,
    source_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalMappingProfileList:
    return list_mapping_profiles(session, user, tenant_id, source_id, limit=limit, offset=offset)


@router.post(
    "/sources/{source_id}/ingestions/{ingestion_id}/mapping",
    response_model=UniversalMappingProfileVersionResponse,
)
def post_universal_mapping(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    body: UniversalMappingCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalMappingProfileVersionResponse:
    return create_mapping_and_apply(
        session, user, tenant_id, source_id, ingestion_id, body, request.state.request_id
    )


@router.put(
    "/sources/{source_id}/ingestions/{ingestion_id}/mapping",
    response_model=UniversalMappingProfileVersionResponse,
)
def put_universal_mapping(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    body: UniversalMappingApplyRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalMappingProfileVersionResponse:
    return apply_existing_mapping(
        session,
        user,
        tenant_id,
        source_id,
        ingestion_id,
        body.mapping_profile_version_id,
        request.state.request_id,
    )


@router.get(
    "/sources/{source_id}/ingestions/{ingestion_id}",
    response_model=UniversalIngestionDetail,
)
def read_universal_ingestion(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalIngestionDetail:
    return get_ingestion_detail(session, user, tenant_id, source_id, ingestion_id)


@router.get(
    "/sources/{source_id}/ingestions/{ingestion_id}/rows",
    response_model=UniversalRowPage,
)
def get_universal_ingestion_rows(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalRowPage:
    return list_rows(session, user, tenant_id, source_id, ingestion_id, limit=limit, offset=offset)


@router.get("/sources/{source_id}/ingestions/{ingestion_id}/evidence")
def get_universal_ingestion_evidence(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> Response:
    content, file_name, content_type = get_evidence(
        session, user, tenant_id, source_id, ingestion_id
    )
    fallback = (
        "source-evidence.xlsx" if file_name.casefold().endswith(".xlsx") else "source-evidence.csv"
    )
    content_disposition = (
        f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(file_name, safe='')}"
    )
    return Response(
        content=content,
        media_type=content_type,
        headers={
            "Content-Disposition": content_disposition,
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post(
    "/sources/{source_id}/ingestions/{ingestion_id}/validate",
    response_model=UniversalValidationResult,
)
def post_universal_validation(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalValidationResult:
    return validate_ingestion(
        session, user, tenant_id, source_id, ingestion_id, request.state.request_id
    )


@router.post(
    "/sources/{source_id}/ingestions/{ingestion_id}/promote",
    response_model=UniversalPromotionResult,
)
def post_universal_promotion(
    tenant_id: UUID,
    source_id: UUID,
    ingestion_id: UUID,
    body: UniversalPromotionRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> UniversalPromotionResult:
    return promote_ingestion(
        session, user, tenant_id, source_id, ingestion_id, body, request.state.request_id
    )


@router.get("/records", response_model=CanonicalRecordPage)
def get_universal_records(
    tenant_id: UUID,
    entity_type: CanonicalEntityType | None = None,
    source_id: UUID | None = None,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CanonicalRecordPage:
    return list_canonical_records(
        session,
        user,
        tenant_id,
        entity_type=entity_type,
        source_id=source_id,
        limit=limit,
        offset=offset,
    )


@router.get("/records/{record_id}/lineage")
def get_universal_record_lineage(
    tenant_id: UUID,
    record_id: UUID,
    limit: PageLimit = 100,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    return list_record_lineage(session, user, tenant_id, record_id, limit=limit, offset=offset)
