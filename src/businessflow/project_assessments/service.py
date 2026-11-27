from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID, uuid4

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.errors import ApiError
from businessflow.metrics.service import create_metric_run
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.project_assessments.calculation import (
    ASSESSMENT_CALCULATION_VERSION,
    calculate_capacity_assessment,
)
from businessflow.project_assessments.models import (
    CapacityAssessment,
    ProjectIntake,
    ProjectIntakeVersion,
)
from businessflow.project_assessments.schemas import (
    CapacityAssessmentCreate,
    ProjectIntakeData,
    ProjectIntakeUpdate,
)
from businessflow.services.company_context_access import require_company_context_access

INTAKE_SNAPSHOT_SCHEMA_VERSION = 1
MAX_HISTORY_PAGE_SIZE = 100
MAX_HISTORY_OFFSET = 100_000


def _not_found() -> ApiError:
    return ApiError(404, "project_intake_not_found", "Project intake was not found.")


def _data_snapshot(data: ProjectIntakeData) -> dict[str, object]:
    return {
        "snapshot_schema_version": INTAKE_SNAPSHOT_SCHEMA_VERSION,
        "data": cast(dict[str, object], data.model_dump(mode="json")),
    }


def _get_intake(
    session: Session,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    *,
    lock: bool = False,
) -> ProjectIntake:
    query = select(ProjectIntake).where(
        ProjectIntake.id == intake_id,
        ProjectIntake.tenant_id == tenant_id,
        ProjectIntake.company_id == company_id,
    )
    if lock:
        query = query.with_for_update()
    intake = session.scalar(query)
    if intake is None:
        raise _not_found()
    return intake


def _get_version(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    version: int,
) -> ProjectIntakeVersion:
    row = session.scalar(
        select(ProjectIntakeVersion).where(
            ProjectIntakeVersion.tenant_id == tenant_id,
            ProjectIntakeVersion.company_id == company_id,
            ProjectIntakeVersion.project_intake_id == intake_id,
            ProjectIntakeVersion.version == version,
        )
    )
    if row is None:
        raise _not_found()
    return row


def _validate_saved_intake(version: ProjectIntakeVersion) -> ProjectIntakeData:
    try:
        if (
            version.snapshot.get("snapshot_schema_version") != INTAKE_SNAPSHOT_SCHEMA_VERSION
            or content_hash(version.snapshot) != version.content_hash
        ):
            raise ValueError("project-intake snapshot version or hash is invalid")
        return ProjectIntakeData.model_validate(version.snapshot.get("data"))
    except (ValidationError, AttributeError, TypeError, ValueError):
        raise ApiError(
            409,
            "project_intake_version_invalid",
            "The saved project-intake version cannot be used reliably.",
        ) from None


def create_project_intake(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    data: ProjectIntakeData,
    request_id: UUID,
) -> tuple[ProjectIntake, ProjectIntakeVersion]:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    snapshot = _data_snapshot(data)
    version = ProjectIntakeVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_intake_id=uuid4(),
        version=1,
        created_by_user_id=user.id,
        content_hash=content_hash(snapshot),
        snapshot=snapshot,
    )
    intake = ProjectIntake(
        id=version.project_intake_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        created_by_user_id=user.id,
        project_code=data.project_code,
        current_version=1,
    )
    session.add(intake)
    session.add(version)
    session.flush()
    record_event(
        session,
        "project_intake.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project_intake",
        target_id=intake.id,
        request_id=request_id,
        details={
            "project_code": data.project_code,
            "version": 1,
            "version_hash": version.content_hash,
        },
    )
    return intake, version


def list_project_intakes(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[tuple[ProjectIntake, ProjectIntakeVersion]], int]:
    if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE or not 0 <= offset <= MAX_HISTORY_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    base_query = select(ProjectIntake).where(
        ProjectIntake.tenant_id == tenant_id,
        ProjectIntake.company_id == access.company.id,
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    intakes = list(
        session.scalars(
            base_query.order_by(ProjectIntake.updated_at.desc(), ProjectIntake.id)
            .limit(limit)
            .offset(offset)
        )
    )
    if not intakes:
        return [], total
    intake_ids = [row.id for row in intakes]
    versions = list(
        session.scalars(
            select(ProjectIntakeVersion).where(
                ProjectIntakeVersion.tenant_id == tenant_id,
                ProjectIntakeVersion.company_id == access.company.id,
                ProjectIntakeVersion.project_intake_id.in_(intake_ids),
                ProjectIntakeVersion.version.in_([row.current_version for row in intakes]),
            )
        )
    )
    by_intake_version = {(row.project_intake_id, row.version): row for row in versions}
    items: list[tuple[ProjectIntake, ProjectIntakeVersion]] = []
    for intake in intakes:
        current = by_intake_version.get((intake.id, intake.current_version))
        if current is None:
            raise ApiError(
                409,
                "project_intake_version_missing",
                "The current project-intake version could not be loaded.",
            )
        items.append((intake, current))
    return items, total


def get_project_intake(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
) -> tuple[ProjectIntake, ProjectIntakeVersion]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    version = _get_version(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        version=intake.current_version,
    )
    return intake, version


def get_project_intake_version(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    version_number: int,
) -> tuple[ProjectIntake, ProjectIntakeVersion]:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    version = _get_version(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        version=version_number,
    )
    return intake, version


def update_project_intake(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectIntakeUpdate,
    request_id: UUID,
) -> tuple[ProjectIntake, ProjectIntakeVersion]:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id, lock=True)
    if intake.current_version != body.expected_version:
        raise ApiError(
            409,
            "intake_version_conflict",
            "The project intake changed; reload the current version before updating.",
        )
    next_version = intake.current_version + 1
    snapshot = _data_snapshot(body.data)
    version = ProjectIntakeVersion(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_intake_id=intake.id,
        version=next_version,
        created_by_user_id=user.id,
        content_hash=content_hash(snapshot),
        snapshot=snapshot,
    )
    intake.project_code = body.data.project_code
    intake.current_version = next_version
    intake.updated_at = datetime.now(UTC)
    session.add(version)
    session.flush()
    record_event(
        session,
        "project_intake.version_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project_intake",
        target_id=intake.id,
        request_id=request_id,
        details={
            "project_code": body.data.project_code,
            "version": next_version,
            "version_hash": version.content_hash,
        },
    )
    return intake, version


def create_capacity_assessment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    body: CapacityAssessmentCreate,
    request_id: UUID,
    attested_at: datetime,
) -> CapacityAssessment:
    access = require_company_context_access(
        session, user, tenant_id, "company_context:write", request_id=request_id
    )
    require_company_context_access(
        session, user, tenant_id, "workforce:read", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    version_number = body.intake_version or intake.current_version
    if version_number > intake.current_version:
        raise _not_found()
    intake_version = _get_version(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        version=version_number,
    )
    data = _validate_saved_intake(intake_version)

    scenario: dict[str, object] | None = None
    if body.scenario is not None:
        expected_weeks = {
            data.start_week_date + timedelta(days=7 * index) for index in range(data.week_count)
        }
        scenario_weeks = {item.week_start_date for item in body.scenario.weekly_demand}
        if scenario_weeks != expected_weeks:
            raise ApiError(
                422,
                "scenario_period_mismatch",
                "A scenario must provide exactly one demand value for every intake week.",
            )
        scenario = {
            "hypothetical": True,
            "name": body.scenario.name,
            "weekly_demand": [item.model_dump(mode="json") for item in body.scenario.weekly_demand],
        }

    metric_run = create_metric_run(
        session,
        user,
        tenant_id,
        data.start_week_date,
        data.week_count,
        request_id,
    )
    if (
        content_hash(metric_run.input_snapshot) != metric_run.input_hash
        or content_hash(metric_run.result_snapshot) != metric_run.result_hash
    ):
        raise ValueError("metric run snapshot hash verification failed")
    assumptions: dict[str, object] = {
        **cast(dict[str, object], body.assumptions.model_dump(mode="json")),
        "attested_by_user_id": str(user.id),
        "attested_at": attested_at.astimezone(UTC).isoformat(timespec="microseconds"),
    }
    intake_snapshot = intake_version.snapshot
    evidence_input, result_snapshot = calculate_capacity_assessment(
        tenant_id=str(tenant_id),
        company_id=str(access.company.id),
        intake_id=str(intake.id),
        intake_version=intake_version.version,
        intake_version_id=str(intake_version.id),
        intake_version_hash=intake_version.content_hash,
        intake_snapshot=intake_snapshot,
        metric_run_id=str(metric_run.id),
        metric_catalog_version=metric_run.metric_catalog_version,
        metric_calculation_version=metric_run.calculation_version,
        metric_input_hash=metric_run.input_hash,
        metric_result_hash=metric_run.result_hash,
        metric_input_snapshot=metric_run.input_snapshot,
        metric_result_snapshot=metric_run.result_snapshot,
        assumptions=assumptions,
        scenario=scenario,
    )
    input_hash = content_hash(evidence_input)
    result_hash = content_hash(result_snapshot)
    end_week_date = data.start_week_date + timedelta(days=data.week_count * 7 - 1)
    assessment = CapacityAssessment(
        id=uuid4(),
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_intake_id=intake.id,
        intake_version=intake_version.version,
        requested_by_user_id=user.id,
        metric_run_id=metric_run.id,
        assessment_version=ASSESSMENT_CALCULATION_VERSION,
        metric_catalog_version=metric_run.metric_catalog_version,
        metric_calculation_version=metric_run.calculation_version,
        start_week_date=data.start_week_date,
        end_week_date=end_week_date,
        week_count=data.week_count,
        capacity_status=cast(str, result_snapshot["capacity_status"]),
        business_feasibility_status="incomplete",
        metric_input_hash=metric_run.input_hash,
        metric_result_hash=metric_run.result_hash,
        input_hash=input_hash,
        result_hash=result_hash,
        input_snapshot=evidence_input,
        result_snapshot=result_snapshot,
    )
    session.add(assessment)
    session.flush()
    record_event(
        session,
        "capacity_assessment.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="capacity_assessment",
        target_id=assessment.id,
        request_id=request_id,
        details={
            "project_intake_id": str(intake.id),
            "intake_version": intake_version.version,
            "metric_run_id": str(metric_run.id),
            "assessment_version": ASSESSMENT_CALCULATION_VERSION,
            "input_hash": input_hash,
            "result_hash": result_hash,
            "capacity_status": assessment.capacity_status,
            "business_feasibility_status": assessment.business_feasibility_status,
        },
    )
    return assessment


def list_capacity_assessments(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    limit: int,
    offset: int,
) -> tuple[list[CapacityAssessment], int]:
    if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE or not 0 <= offset <= MAX_HISTORY_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    require_company_context_access(session, user, tenant_id, "workforce:read")
    _get_intake(session, tenant_id, access.company.id, intake_id)
    base_query = select(CapacityAssessment).where(
        CapacityAssessment.tenant_id == tenant_id,
        CapacityAssessment.company_id == access.company.id,
        CapacityAssessment.project_intake_id == intake_id,
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = list(
        session.scalars(
            base_query.order_by(CapacityAssessment.created_at.desc(), CapacityAssessment.id.desc())
            .limit(limit)
            .offset(offset)
        )
    )
    return rows, total


def get_capacity_assessment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
) -> CapacityAssessment:
    access = require_company_context_access(session, user, tenant_id, "company_context:read")
    require_company_context_access(session, user, tenant_id, "workforce:read")
    assessment = session.scalar(
        select(CapacityAssessment).where(
            CapacityAssessment.id == assessment_id,
            CapacityAssessment.tenant_id == tenant_id,
            CapacityAssessment.company_id == access.company.id,
            CapacityAssessment.project_intake_id == intake_id,
        )
    )
    if assessment is None:
        raise ApiError(404, "capacity_assessment_not_found", "Capacity assessment was not found.")
    return assessment
