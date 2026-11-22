from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Header, Query, Request, UploadFile
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.config import Settings
from businessflow.data_imports.schemas import (
    ImportApplyResult,
    ImportConfirmRequest,
    ImportDataset,
    ImportInspectionResponse,
    ImportMappingRequest,
    ImportMappingResponse,
    ImportPreviewPage,
    ImportPurgeResult,
    ImportReport,
    ImportSummary,
    ImportSummaryPage,
)
from businessflow.data_imports.service import (
    apply_import,
    cancel_import,
    confirm_import,
    get_import,
    get_import_report,
    inspect_import_mapping,
    list_imports,
    preview_import,
    purge_expired_imports,
    set_import_mapping,
    stage_import,
    validate_import,
)
from businessflow.models import UserAccount

router = APIRouter(prefix="/tenants/{tenant_id}/imports", tags=["data imports"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]


@router.post("", response_model=ImportInspectionResponse, status_code=201)
def post_import(
    tenant_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ImportInspectionResponse:
    staged = stage_import(
        session,
        user,
        tenant_id,
        file.file,
        file.filename,
        idempotency_key,
        request.state.request_id,
        settings,
    )
    return get_import(session, user, tenant_id, staged.id)


@router.get("", response_model=ImportSummaryPage)
def get_import_history(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportSummaryPage:
    return list_imports(session, user, tenant_id, limit=limit, offset=offset)


@router.post("/purge-expired", response_model=ImportPurgeResult)
def post_expired_import_purge(
    tenant_id: UUID,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportPurgeResult:
    return purge_expired_imports(session, user, tenant_id, request.state.request_id, limit=limit)


@router.get("/{import_id}", response_model=ImportInspectionResponse)
def read_import(
    tenant_id: UUID,
    import_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportInspectionResponse:
    return get_import(session, user, tenant_id, import_id)


@router.get("/{import_id}/mapping", response_model=ImportMappingResponse)
def get_import_mapping(
    tenant_id: UUID,
    import_id: UUID,
    dataset_type: ImportDataset,
    worksheet_name: Annotated[str, Query(min_length=1, max_length=128)],
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportMappingResponse:
    return inspect_import_mapping(session, user, tenant_id, import_id, dataset_type, worksheet_name)


@router.put("/{import_id}/mapping", response_model=ImportMappingResponse)
def put_import_mapping(
    tenant_id: UUID,
    import_id: UUID,
    body: ImportMappingRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportMappingResponse:
    return set_import_mapping(session, user, tenant_id, import_id, body, request.state.request_id)


@router.post("/{import_id}/validate", response_model=ImportReport)
def post_import_validation(
    tenant_id: UUID,
    import_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportReport:
    return validate_import(session, user, tenant_id, import_id, request.state.request_id)


@router.get("/{import_id}/preview", response_model=ImportPreviewPage)
def get_import_preview(
    tenant_id: UUID,
    import_id: UUID,
    worksheet_name: Annotated[str | None, Query(min_length=1, max_length=128)] = None,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportPreviewPage:
    return preview_import(
        session,
        user,
        tenant_id,
        import_id,
        worksheet_name=worksheet_name,
        limit=limit,
        offset=offset,
    )


@router.post("/{import_id}/confirm", response_model=ImportSummary)
def post_import_confirmation(
    tenant_id: UUID,
    import_id: UUID,
    body: ImportConfirmRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportSummary:
    return confirm_import(session, user, tenant_id, import_id, body, request.state.request_id)


@router.post("/{import_id}/apply", response_model=ImportApplyResult)
def post_import_apply(
    tenant_id: UUID,
    import_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportApplyResult:
    return apply_import(session, user, tenant_id, import_id, request.state.request_id)


@router.post("/{import_id}/cancel", response_model=ImportSummary)
def post_import_cancellation(
    tenant_id: UUID,
    import_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportSummary:
    return cancel_import(session, user, tenant_id, import_id, request.state.request_id)


@router.get("/{import_id}/report", response_model=ImportReport)
def read_import_report(
    tenant_id: UUID,
    import_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ImportReport:
    return get_import_report(session, user, tenant_id, import_id)
