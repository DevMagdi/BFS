from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.financial_assessments.schemas import (
    FinancialAssessmentPage,
    FinancialAssessmentResponse,
    FinancialInputPage,
    FinancialInputVersionResponse,
    FinancialPolicyCatalogResponse,
    FinancialReadinessResponse,
    ProjectFinancialInputCreate,
)
from businessflow.financial_assessments.service import (
    create_financial_assessment,
    create_project_financial_input,
    get_current_project_financial_input,
    get_financial_assessment,
    get_financial_policy_catalog,
    get_project_financial_input_version,
    get_project_financial_readiness,
    list_financial_assessments,
    list_project_financial_inputs,
)
from businessflow.models import UserAccount

router = APIRouter(
    prefix="/tenants/{tenant_id}/project-intakes/{intake_id}",
    tags=["project financial feasibility"],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]
VersionNumber = Annotated[int, Path(ge=1, le=1_000_000)]


@router.post(
    "/financial-inputs",
    response_model=FinancialInputVersionResponse,
    status_code=201,
    summary="Append a sourced financial-input version",
    description=(
        "Records exact decimal-text observations and source/assumption metadata. "
        "Input versions are immutable and tied to the specified current intake version."
    ),
)
def post_project_financial_input(
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectFinancialInputCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialInputVersionResponse:
    return create_project_financial_input(
        session,
        user,
        tenant_id,
        intake_id,
        body,
        request.state.request_id,
        request.state.now,
    )


@router.get("/financial-inputs", response_model=FinancialInputVersionResponse)
def get_current_financial_input(
    tenant_id: UUID,
    intake_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialInputVersionResponse:
    return get_current_project_financial_input(session, user, tenant_id, intake_id)


@router.get("/financial-inputs/history", response_model=FinancialInputPage)
def get_financial_input_history(
    tenant_id: UUID,
    intake_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialInputPage:
    return list_project_financial_inputs(
        session, user, tenant_id, intake_id, limit=limit, offset=offset
    )


@router.get(
    "/financial-inputs/versions/{version_number}", response_model=FinancialInputVersionResponse
)
def get_financial_input_version(
    tenant_id: UUID,
    intake_id: UUID,
    version_number: VersionNumber,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialInputVersionResponse:
    return get_project_financial_input_version(session, user, tenant_id, intake_id, version_number)


@router.get("/financial-policies", response_model=FinancialPolicyCatalogResponse)
def get_financial_policies(
    tenant_id: UUID,
    intake_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialPolicyCatalogResponse:
    # The catalog is tenant-independent today, but the scoped route applies the same finance
    # authorization boundary and verifies that the project path itself is in scope.
    return get_financial_policy_catalog(session, user, tenant_id, intake_id)


@router.get("/financial-assessments/readiness", response_model=FinancialReadinessResponse)
def get_financial_assessment_readiness(
    tenant_id: UUID,
    intake_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialReadinessResponse:
    return get_project_financial_readiness(session, user, tenant_id, intake_id)


@router.post(
    "/financial-assessments",
    response_model=FinancialAssessmentResponse,
    status_code=201,
    summary="Create an immutable financial assessment snapshot",
    description=(
        "Requires a current, fresh, approved M7 proceed-to-financial-evaluation handoff. "
        "No financial formula or verdict beyond incomplete is emitted without approved policy."
    ),
)
def post_financial_assessment(
    tenant_id: UUID,
    intake_id: UUID,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialAssessmentResponse:
    row = create_financial_assessment(
        session,
        user,
        tenant_id,
        intake_id,
        request.state.request_id,
        request.state.now,
    )
    return get_financial_assessment(session, user, tenant_id, intake_id, row.id)


@router.get("/financial-assessments", response_model=FinancialAssessmentPage)
def get_financial_assessment_history(
    tenant_id: UUID,
    intake_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialAssessmentPage:
    return list_financial_assessments(
        session, user, tenant_id, intake_id, limit=limit, offset=offset
    )


@router.get("/financial-assessments/{assessment_id}", response_model=FinancialAssessmentResponse)
def get_financial_assessment_detail(
    tenant_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> FinancialAssessmentResponse:
    return get_financial_assessment(session, user, tenant_id, intake_id, assessment_id)
