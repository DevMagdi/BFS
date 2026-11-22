from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from businessflow.api.dependencies import get_current_user, get_metrics_session, get_session
from businessflow.errors import ApiError
from businessflow.models import UserAccount
from businessflow.project_assessments.models import (
    CapacityAssessment,
    ProjectIntake,
    ProjectIntakeVersion,
)
from businessflow.project_assessments.schemas import (
    CapacityAssessmentCreate,
    CapacityAssessmentPage,
    CapacityAssessmentResponse,
    CapacityAssessmentSummaryResponse,
    ProjectIntakeData,
    ProjectIntakePage,
    ProjectIntakeResponse,
    ProjectIntakeUpdate,
    ProjectIntakeVersionResponse,
)
from businessflow.project_assessments.service import (
    create_capacity_assessment,
    create_project_intake,
    get_capacity_assessment,
    get_project_intake,
    get_project_intake_version,
    list_capacity_assessments,
    list_project_intakes,
    update_project_intake,
)

router = APIRouter(prefix="/tenants/{tenant_id}/project-intakes", tags=["project assessments"])
PageLimit = Annotated[int, Query(ge=1, le=100)]
PageOffset = Annotated[int, Query(ge=0, le=100_000)]


def _intake_response(intake: ProjectIntake, version: ProjectIntakeVersion) -> ProjectIntakeResponse:
    return ProjectIntakeResponse(
        id=intake.id,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        created_by_user_id=intake.created_by_user_id,
        project_code=intake.project_code,
        current_version=intake.current_version,
        current_version_id=version.id,
        current_version_hash=version.content_hash,
        data=ProjectIntakeData.model_validate(version.snapshot.get("data")),
        created_at=intake.created_at,
        updated_at=intake.updated_at,
    )


def _version_response(
    intake: ProjectIntake, version: ProjectIntakeVersion
) -> ProjectIntakeVersionResponse:
    return ProjectIntakeVersionResponse(
        project_intake_id=intake.id,
        tenant_id=intake.tenant_id,
        company_id=intake.company_id,
        version=version.version,
        version_id=version.id,
        created_by_user_id=version.created_by_user_id,
        content_hash=version.content_hash,
        data=ProjectIntakeData.model_validate(version.snapshot.get("data")),
        created_at=version.created_at,
    )


def _assessment_summary(assessment: CapacityAssessment) -> CapacityAssessmentSummaryResponse:
    return CapacityAssessmentSummaryResponse(
        id=assessment.id,
        project_intake_id=assessment.project_intake_id,
        tenant_id=assessment.tenant_id,
        company_id=assessment.company_id,
        intake_version=assessment.intake_version,
        requested_by_user_id=assessment.requested_by_user_id,
        metric_run_id=assessment.metric_run_id,
        assessment_version=assessment.assessment_version,
        metric_catalog_version=assessment.metric_catalog_version,
        metric_calculation_version=assessment.metric_calculation_version,
        start_week_date=assessment.start_week_date,
        end_week_date=assessment.end_week_date,
        week_count=assessment.week_count,
        capacity_status=assessment.capacity_status,
        business_feasibility_status=assessment.business_feasibility_status,
        metric_input_hash=assessment.metric_input_hash,
        metric_result_hash=assessment.metric_result_hash,
        input_hash=assessment.input_hash,
        result_hash=assessment.result_hash,
        created_at=assessment.created_at,
    )


def _assessment_response(assessment: CapacityAssessment) -> CapacityAssessmentResponse:
    return CapacityAssessmentResponse(
        **_assessment_summary(assessment).model_dump(),
        input_snapshot=assessment.input_snapshot,
        result_snapshot=assessment.result_snapshot,
    )


@router.post("", response_model=ProjectIntakeResponse, status_code=201)
def post_project_intake(
    tenant_id: UUID,
    body: ProjectIntakeData,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectIntakeResponse:
    intake, version = create_project_intake(
        session, user, tenant_id, body, request.state.request_id
    )
    return _intake_response(intake, version)


@router.get("", response_model=ProjectIntakePage)
def get_project_intakes(
    tenant_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectIntakePage:
    rows, total = list_project_intakes(session, user, tenant_id, limit=limit, offset=offset)
    return ProjectIntakePage(
        items=[_intake_response(intake, version) for intake, version in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{intake_id}", response_model=ProjectIntakeResponse)
def get_project_intake_detail(
    tenant_id: UUID,
    intake_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectIntakeResponse:
    intake, version = get_project_intake(session, user, tenant_id, intake_id)
    return _intake_response(intake, version)


@router.put("/{intake_id}", response_model=ProjectIntakeResponse)
def put_project_intake(
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectIntakeUpdate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectIntakeResponse:
    intake, version = update_project_intake(
        session, user, tenant_id, intake_id, body, request.state.request_id
    )
    return _intake_response(intake, version)


@router.get("/{intake_id}/versions/{version_number}", response_model=ProjectIntakeVersionResponse)
def get_project_intake_version_detail(
    tenant_id: UUID,
    intake_id: UUID,
    version_number: int,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> ProjectIntakeVersionResponse:
    intake, version = get_project_intake_version(
        session, user, tenant_id, intake_id, version_number
    )
    return _version_response(intake, version)


@router.post(
    "/{intake_id}/assessments",
    response_model=CapacityAssessmentResponse,
    status_code=201,
)
def post_capacity_assessment(
    tenant_id: UUID,
    intake_id: UUID,
    body: CapacityAssessmentCreate,
    request: Request,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_metrics_session),
) -> CapacityAssessmentResponse:
    try:
        assessment = create_capacity_assessment(
            session,
            user,
            tenant_id,
            intake_id,
            body,
            request.state.request_id,
            request.state.now,
        )
    except ValueError:
        raise ApiError(
            409,
            "capacity_assessment_inputs_invalid",
            "Capacity inputs are inconsistent and cannot be assessed reliably.",
        ) from None
    return _assessment_response(assessment)


@router.get("/{intake_id}/assessments", response_model=CapacityAssessmentPage)
def get_capacity_assessment_history(
    tenant_id: UUID,
    intake_id: UUID,
    limit: PageLimit = 50,
    offset: PageOffset = 0,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CapacityAssessmentPage:
    rows, total = list_capacity_assessments(
        session, user, tenant_id, intake_id, limit=limit, offset=offset
    )
    return CapacityAssessmentPage(
        items=[_assessment_summary(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{intake_id}/assessments/{assessment_id}", response_model=CapacityAssessmentResponse)
def get_capacity_assessment_detail(
    tenant_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CapacityAssessmentResponse:
    assessment = get_capacity_assessment(session, user, tenant_id, intake_id, assessment_id)
    return _assessment_response(assessment)


@router.get(
    "/{intake_id}/assessments/{assessment_id}/evidence",
    response_model=CapacityAssessmentResponse,
)
def get_capacity_assessment_evidence(
    tenant_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
    user: UserAccount = Depends(get_current_user),
    session: Session = Depends(get_session),
) -> CapacityAssessmentResponse:
    assessment = get_capacity_assessment(session, user, tenant_id, intake_id, assessment_id)
    return _assessment_response(assessment)
