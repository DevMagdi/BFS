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
    CoreFinancialComparisonRequest,
    CoreFinancialComparisonResponse,
    CoreFinancialImportMetadata,
    CoreFinancialImportPage,
    CoreFinancialImportResponse,
    CoreFinancialInsightRequest,
    CoreFinancialInsightsResponse,
    CoreFinancialMappingCreateRequest,
    CoreFinancialMappingPage,
    CoreFinancialMappingResponse,
    CoreFinancialOverviewResponse,
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
    FinancialCategory,
    FinancialMeasure,
    SourceType,
)
from businessflow.core_financials.service import (
    analyze_core_financial_insights,
    compare_core_financial_periods,
    create_core_financial_import,
    create_core_financial_mapping,
    create_core_financial_policy,
    create_core_financial_run,
    ensure_core_financials_enabled,
    get_core_financial_import,
    get_core_financial_overview,
    get_core_financial_policy,
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
    summary="Create an immutable, versioned source-adapter header mapping",
)
def post_core_financial_mapping(
    tenant_id: UUID,
    body: CoreFinancialMappingCreateRequest,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialMappingResponse:
    return create_core_financial_mapping(
        session,
        user,
        tenant_id,
        body,
        request.state.request_id,
        request.state.now,
        settings,
    )


@router.get("/policies/{policy_id}", response_model=CoreFinancialPolicyResponse)
def read_core_financial_policy(
    tenant_id: UUID,
    policy_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialPolicyResponse:
    return get_core_financial_policy(
        session,
        user,
        tenant_id,
        policy_id,
        settings=settings,
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
    summary="Normalize and retain one bounded general-ledger or bank-activity CSV import",
    description=(
        "Accepts one UTF-8 general-ledger or bank-activity CSV sheet using a caller-declared "
        "policy, source period, mapping, and completeness assertion. The completeness assertion "
        "and source identity are not independently verified. Imported facts support illustrative "
        "management reporting only and are not a canonical ledger, accounting conclusion, or "
        "authoritative cash-flow statement."
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
    summary="Calculate and persist one immutable, source-backed financial measure",
    description=(
        "Calculates one selected measure using the matching general-ledger or bank-activity "
        "import and an explicit immutable policy version. P&L measures require ledger evidence; "
        "Cash Flow requires bank evidence and an explicit versioned movement/transfer policy. "
        "The result is illustrative management reporting, not approved accounting, and the API "
        "withholds values when source completeness, quality, period, currency, or policy coverage "
        "is insufficient."
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


@router.get(
    "/overview",
    response_model=CoreFinancialOverviewResponse,
    summary="Show all six measures and policy-supported cross-measure consistency checks",
    description=(
        "Returns the latest persisted run for each measure. It preserves each run's independent "
        "period, currency, source, policy and evidence context, and abstains from arithmetic when "
        "the saved formulas or run scopes do not support a comparison. Results remain illustrative "
        "and non-authoritative."
    ),
)
def read_core_financial_overview(
    tenant_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialOverviewResponse:
    return get_core_financial_overview(session, user, tenant_id, settings=settings)


@router.post(
    "/comparisons",
    response_model=CoreFinancialComparisonResponse,
    summary="Compare two selected periods using persisted financial runs",
    description=(
        "Select two non-overlapping period windows. The API selects the latest exact-period run "
        "for each of the six measures, then calculates only when period shape, currency, basis, "
        "policy, engine, source completeness, and saved measure status are compatible. It does "
        "not recalculate, impute, convert currencies, or infer causal explanations."
    ),
)
def compare_core_financial_run_periods(
    tenant_id: UUID,
    body: CoreFinancialComparisonRequest,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialComparisonResponse:
    return compare_core_financial_periods(session, user, tenant_id, body, settings=settings)


@router.post(
    "/insights",
    response_model=CoreFinancialInsightsResponse,
    summary="Analyze profitability, evidence-backed drivers, movements, and potential outliers",
    description=(
        "Select two to six ordered, non-overlapping periods. The endpoint reuses M14's latest "
        "exact-period persisted runs, scoped evidence verification, and Decimal-safe comparisons. "
        "It separates observed facts, deterministic calculations, hypotheses, and recommendations; "
        "it does not invent root causes, forecast, budget, or estimate unsupported impact."
    ),
)
def read_core_financial_insights(
    tenant_id: UUID,
    body: CoreFinancialInsightRequest,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
    settings: Settings = Depends(get_settings),
) -> CoreFinancialInsightsResponse:
    return analyze_core_financial_insights(session, user, tenant_id, body, settings=settings)


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
    category: FinancialCategory | None = None,
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
        category=category,
        settings=settings,
    )
