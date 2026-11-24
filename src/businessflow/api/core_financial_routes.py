from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Header, Query, Request, UploadFile
from pydantic import ValidationError
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session, get_settings
from businessflow.api.finance_source_limits import FINANCE_SOURCE_MAX_METADATA_BYTES
from businessflow.config import Settings
from businessflow.core_financials.contracts import (
    CoreFinancialImportMetadata,
    CoreFinancialImportPage,
    CoreFinancialImportResponse,
    CoreFinancialMappingCreateRequest,
    CoreFinancialMappingPage,
    CoreFinancialMappingResponse,
    CoreFinancialPolicyCreateRequest,
    CoreFinancialPolicyPage,
    CoreFinancialPolicyResponse,
    CoreFinancialRecordPage,
    CoreFinancialRunCreateRequest,
    CoreFinancialRunEvidencePage,
    CoreFinancialRunPage,
    CoreFinancialRunResponse,
    CoreFinancialRunVerificationResponse,
    CoreFinancialTemplateResponse,
    FinancialMeasure,
    SourceType,
)
from businessflow.core_financials.service import (
    create_core_financial_import,
    create_core_financial_mapping,
    create_core_financial_policy,
    create_core_financial_run,
    ensure_core_financials_enabled,
    get_core_financial_import,
    get_core_financial_run,
    get_core_financial_template,
    list_core_financial_imports,
    list_core_financial_mappings,
    list_core_financial_policies,
    list_core_financial_records,
    list_core_financial_run_evidence,
    list_core_financial_runs,
    verify_core_financial_run,
)
from businessflow.errors import ApiError
from businessflow.models import UserAccount


def require_core_financials_enabled(settings: Settings = Depends(get_settings)) -> None:
    ensure_core_financials_enabled(settings)


router = APIRouter(
    prefix="/tenants/{tenant_id}/core-financials",
    tags=["core financials"],
    dependencies=[Depends(require_core_financials_enabled)],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=16, max_length=128)]
MetadataJson = Annotated[
    str,
    Form(min_length=2, max_length=FINANCE_SOURCE_MAX_METADATA_BYTES),
]
ImportRowStatus = Literal["accepted", "rejected", "duplicate"]


@router.get("/template", response_model=CoreFinancialTemplateResponse)
def read_core_financial_template(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialTemplateResponse:
    return get_core_financial_template(session, user, tenant_id, settings=settings)


@router.post(
    "/policies",
    response_model=CoreFinancialPolicyResponse,
    status_code=201,
    summary="Create an immutable, versioned management-reporting policy",
    description=(
        "A policy is illustrative and non-authoritative. Coverage classifications and formula "
        "definitions are caller-supplied; this endpoint does not establish GAAP/IFRS treatment, "
        "finance-owner approval, or accounting correctness."
    ),
)
def post_core_financial_policy(
    tenant_id: UUID,
    body: CoreFinancialPolicyCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialPolicyResponse:
    return create_core_financial_policy(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/policies", response_model=CoreFinancialPolicyPage)
def read_core_financial_policies(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialPolicyPage:
    return list_core_financial_policies(
        session,
        user,
        tenant_id,
        limit=limit,
        offset=offset,
        settings=settings,
    )


@router.post(
    "/mappings",
    response_model=CoreFinancialMappingResponse,
    status_code=201,
    summary="Create an immutable, versioned general-ledger header mapping",
)
def post_core_financial_mapping(
    tenant_id: UUID,
    body: CoreFinancialMappingCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialMappingResponse:
    if body.source_type != "general_ledger":
        raise ApiError(
            422,
            "unsupported_source_type",
            "The first integrated workflow accepts general-ledger mappings only.",
        )
    return create_core_financial_mapping(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/mappings", response_model=CoreFinancialMappingPage)
def read_core_financial_mappings(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    source_key: str | None = Query(default=None, min_length=2, max_length=64),
    source_type: SourceType | None = None,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialMappingPage:
    return list_core_financial_mappings(
        session,
        user,
        tenant_id,
        limit=limit,
        offset=offset,
        source_key=source_key,
        source_type=source_type,
        settings=settings,
    )


@router.post(
    "/imports",
    response_model=CoreFinancialImportResponse,
    status_code=201,
    summary="Normalize and retain one bounded general-ledger CSV import",
    description=(
        "Accepts one UTF-8 general-ledger CSV sheet using a caller-declared policy, source period, "
        "mapping, and "
        "completeness assertion. The completeness assertion and source identity are not "
        "independently verified. Imported facts support management reporting only and are not a "
        "canonical ledger, accounting conclusion, or authoritative cash-flow statement."
    ),
)
def post_core_financial_import(
    tenant_id: UUID,
    request: Request,
    idempotency_key: IdempotencyKey,
    metadata_json: MetadataJson,
    file: UploadFile = File(...),
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialImportResponse:
    try:
        metadata = CoreFinancialImportMetadata.model_validate_json(metadata_json)
    except ValidationError:
        raise ApiError(
            422,
            "invalid_core_financial_metadata",
            "Core Financials import metadata failed strict validation.",
        ) from None
    if metadata.source_type != "general_ledger":
        raise ApiError(
            422,
            "unsupported_source_type",
            "The first integrated workflow accepts general-ledger imports only.",
        )
    return create_core_financial_import(
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


@router.get("/imports", response_model=CoreFinancialImportPage)
def read_core_financial_imports(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialImportPage:
    return list_core_financial_imports(
        session,
        user,
        tenant_id,
        limit=limit,
        offset=offset,
        settings=settings,
    )


@router.get("/imports/{import_id}", response_model=CoreFinancialImportResponse)
def read_core_financial_import(
    tenant_id: UUID,
    import_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialImportResponse:
    return get_core_financial_import(
        session,
        user,
        tenant_id,
        import_id,
        settings=settings,
    )


@router.get("/imports/{import_id}/records", response_model=CoreFinancialRecordPage)
def read_core_financial_records(
    tenant_id: UUID,
    import_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    row_status: ImportRowStatus | None = None,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRecordPage:
    return list_core_financial_records(
        session,
        user,
        tenant_id,
        import_id,
        limit=limit,
        offset=offset,
        row_status=row_status,
        settings=settings,
    )


@router.post(
    "/runs",
    response_model=CoreFinancialRunResponse,
    status_code=201,
    summary="Calculate and persist an immutable revenue-only report run",
    description=(
        "This first integrated workflow calculates Revenue only from one accepted general-ledger "
        "import and an explicit policy version. Other measures and bank activity are outside this "
        "increment. The exact result is illustrative management reporting, not approved "
        "accounting. "
        "It abstains when source completeness, quality, period coverage, or revenue category "
        "coverage is insufficient."
    ),
)
def post_core_financial_run(
    tenant_id: UUID,
    body: CoreFinancialRunCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRunResponse:
    return create_core_financial_run(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/runs", response_model=CoreFinancialRunPage)
def read_core_financial_runs(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRunPage:
    return list_core_financial_runs(
        session,
        user,
        tenant_id,
        limit=limit,
        offset=offset,
        settings=settings,
    )


@router.get("/runs/{run_id}", response_model=CoreFinancialRunResponse)
def read_core_financial_run(
    tenant_id: UUID,
    run_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRunResponse:
    return get_core_financial_run(
        session,
        user,
        tenant_id,
        run_id,
        settings=settings,
    )


@router.post(
    "/runs/{run_id}/verify",
    response_model=CoreFinancialRunVerificationResponse,
    summary="Replay a persisted calculation against its immutable inputs",
    description=(
        "Recomputes persisted inputs, results, line evidence, and trace hashes. Requires "
        "finance:write; replay is not independent audit, source refresh, or approval."
    ),
)
def replay_core_financial_run(
    tenant_id: UUID,
    run_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRunVerificationResponse:
    return verify_core_financial_run(
        session,
        user,
        tenant_id,
        run_id,
        settings=settings,
        request_id=request.state.request_id,
    )


@router.get("/runs/{run_id}/evidence", response_model=CoreFinancialRunEvidencePage)
def read_core_financial_run_evidence(
    tenant_id: UUID,
    run_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    metric: FinancialMeasure | None = None,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialRunEvidencePage:
    return list_core_financial_run_evidence(
        session,
        user,
        tenant_id,
        run_id,
        limit=limit,
        offset=offset,
        metric=metric,
        settings=settings,
    )
