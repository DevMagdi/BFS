from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_session
from businessflow.models import UserAccount
from businessflow.project_decisions.schemas import (
    ProjectDecisionApprovalCreate,
    ProjectDecisionApprovalResponse,
    ProjectDecisionCreate,
    ProjectDecisionPage,
    ProjectDecisionResponse,
)
from businessflow.project_decisions.service import (
    approve_project_decision,
    create_project_decision,
    get_project_decision,
    list_project_decisions,
)

router = APIRouter(
    prefix="/tenants/{tenant_id}/project-intakes/{intake_id}/decisions",
    tags=["project decisions"],
)
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


@router.post("", response_model=ProjectDecisionResponse, status_code=201)
def post_project_decision(
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectDecisionCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectDecisionResponse:
    _case, decision, _approval = create_project_decision(
        session,
        user,
        tenant_id,
        intake_id,
        body,
        request.state.request_id,
        request.state.now,
    )
    return get_project_decision(session, user, tenant_id, intake_id, decision.id)


@router.get("", response_model=ProjectDecisionPage)
def get_project_decisions(
    tenant_id: UUID,
    intake_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectDecisionPage:
    return list_project_decisions(session, user, tenant_id, intake_id, limit=limit, offset=offset)


@router.get("/{decision_id}", response_model=ProjectDecisionResponse)
def get_project_decision_detail(
    tenant_id: UUID,
    intake_id: UUID,
    decision_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectDecisionResponse:
    return get_project_decision(session, user, tenant_id, intake_id, decision_id)


@router.post(
    "/{decision_id}/approval",
    response_model=ProjectDecisionApprovalResponse,
    status_code=201,
)
def post_project_decision_approval(
    tenant_id: UUID,
    intake_id: UUID,
    decision_id: UUID,
    body: ProjectDecisionApprovalCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectDecisionApprovalResponse:
    _case, _decision, approval = approve_project_decision(
        session,
        user,
        tenant_id,
        intake_id,
        decision_id,
        body,
        request.state.request_id,
        request.state.now,
    )
    return ProjectDecisionApprovalResponse(
        id=approval.id,
        decision_case_id=approval.decision_case_id,
        decision_version_id=approval.decision_version_id,
        decision_hash=approval.decision_hash,
        evidence_hash=approval.evidence_hash,
        assessment_id=approval.assessment_id,
        assessment_version=approval.assessment_version,
        assessment_input_hash=approval.assessment_input_hash,
        assessment_result_hash=approval.assessment_result_hash,
        intake_version=approval.intake_version,
        intake_version_hash=approval.intake_version_hash,
        metric_run_id=approval.metric_run_id,
        metric_catalog_version=approval.metric_catalog_version,
        metric_calculation_version=approval.metric_calculation_version,
        metric_input_hash=approval.metric_input_hash,
        metric_result_hash=approval.metric_result_hash,
        outcome=approval.outcome,
        rationale=approval.rationale,
        approver_user_id=approval.approver_user_id,
        approval_hash=approval.approval_hash,
        created_at=approval.created_at,
    )
