from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.business_reports.contracts import (
    ReportArtifactPurgeResponse,
    ReportExportCreateRequest,
    ReportExportDetail,
    ReportExportPage,
    ReportExportRevocationResponse,
    ReportExportRevokeRequest,
)
from businessflow.business_reports.rendering import (
    CSV_MEDIA_TYPE,
    JSON_MEDIA_TYPE,
    XLSX_MEDIA_TYPE,
)
from businessflow.business_reports.service import (
    create_report_export,
    download_report_export,
    get_report_export,
    list_report_exports,
    purge_report_artifacts,
    revoke_report_export,
)
from businessflow.config import Settings
from businessflow.models import UserAccount

router = APIRouter(prefix="/tenants/{tenant_id}/reports", tags=["reporting and export"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
PurgeLimit = Annotated[int, Query(ge=1, le=100)]


@router.post("", response_model=ReportExportDetail, status_code=201)
def post_report_export(
    tenant_id: UUID,
    body: ReportExportCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReportExportDetail:
    return create_report_export(
        session,
        user,
        tenant_id,
        body,
        request_id=request.state.request_id,
        now=request.state.now,
        settings=settings,
    )


@router.get("", response_model=ReportExportPage)
def read_report_exports(
    tenant_id: UUID,
    request: Request,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReportExportPage:
    return list_report_exports(
        session,
        user,
        tenant_id,
        limit=limit,
        offset=offset,
        now=request.state.now,
        settings=settings,
    )


@router.get("/{report_id}", response_model=ReportExportDetail)
def read_report_export(
    tenant_id: UUID,
    report_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> ReportExportDetail:
    return get_report_export(
        session, user, tenant_id, report_id, now=request.state.now, settings=settings
    )


@router.get("/{report_id}/download")
def download_report(
    tenant_id: UUID,
    report_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> Response:
    summary, content = download_report_export(
        session,
        user,
        tenant_id,
        report_id,
        request_id=request.state.request_id,
        now=request.state.now,
        settings=settings,
    )
    media_type = {
        "json": JSON_MEDIA_TYPE,
        "csv": CSV_MEDIA_TYPE,
        "xlsx": XLSX_MEDIA_TYPE,
    }[summary.format]
    extension = summary.format
    filename = f"businessflow-report-{summary.id}.{extension}"
    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "private, no-store, max-age=0",
            "Pragma": "no-cache",
            "X-Content-Type-Options": "nosniff",
            "X-Report-Mode": summary.mode,
            "ETag": f'"{summary.artifact_sha256}"',
        },
    )


@router.post("/{report_id}/revoke", response_model=ReportExportRevocationResponse)
def revoke_report(
    tenant_id: UUID,
    report_id: UUID,
    body: ReportExportRevokeRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ReportExportRevocationResponse:
    return revoke_report_export(
        session,
        user,
        tenant_id,
        report_id,
        body,
        request_id=request.state.request_id,
        now=request.state.now,
    )


@router.post("/retention/purge", response_model=ReportArtifactPurgeResponse)
def purge_retained_report_artifacts(
    tenant_id: UUID,
    request: Request,
    limit: PurgeLimit = 100,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ReportArtifactPurgeResponse:
    return purge_report_artifacts(
        session,
        user,
        tenant_id,
        now=request.state.now,
        request_id=request.state.request_id,
        limit=limit,
    )
