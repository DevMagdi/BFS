from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.errors import ApiError
from businessflow.financial_assessments.calculation import (
    evaluate_financial_input_snapshot,
    financial_observation_summary,
)
from businessflow.financial_assessments.catalog import (
    FINANCIAL_POLICY_CATALOG_STATUS,
    FINANCIAL_POLICY_CATALOG_VERSION,
    policy_catalog_payload,
)
from businessflow.financial_assessments.models import (
    FinancialAssessment,
    ProjectFinancialInputCase,
    ProjectFinancialInputVersion,
)
from businessflow.financial_assessments.schemas import (
    FinancialApprovalOutcome,
    FinancialAssessmentPage,
    FinancialAssessmentResponse,
    FinancialDecisionOutcome,
    FinancialFreshness,
    FinancialHandoffStatus,
    FinancialInputFreshness,
    FinancialInputPage,
    FinancialInputVersionResponse,
    FinancialPolicyCatalogResponse,
    FinancialPolicyStatus,
    FinancialReadinessResponse,
    FinancialVerdict,
    ProjectFinancialInputCreate,
)
from businessflow.metrics.models import MetricRun
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from businessflow.project_assessments.models import (
    CapacityAssessment,
    ProjectIntake,
    ProjectIntakeVersion,
)
from businessflow.project_decisions.models import (
    ProjectDecisionApproval,
    ProjectDecisionCase,
    ProjectDecisionVersion,
)
from businessflow.project_decisions.schemas import ProjectDecisionResponse
from businessflow.project_decisions.service import get_project_decision
from businessflow.services.company_context_access import (
    CompanyContextAccess,
    require_company_context_access,
)

INPUT_SNAPSHOT_SCHEMA_VERSION = 1
ASSESSMENT_SNAPSHOT_SCHEMA_VERSION = 1
MAX_HISTORY_PAGE_SIZE = 100
MAX_HISTORY_OFFSET = 100_000


def _not_found() -> ApiError:
    return ApiError(404, "project_intake_not_found", "Project intake was not found.")


def _financial_input_not_found() -> ApiError:
    return ApiError(
        404, "project_financial_inputs_not_found", "Project financial inputs were not found."
    )


def _assessment_not_found() -> ApiError:
    return ApiError(404, "financial_assessment_not_found", "Financial assessment was not found.")


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _as_list(value: object) -> list[object]:
    return value if isinstance(value, list) else []


def _timestamp_text(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds")


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


def _get_intake_version(
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
        raise ApiError(
            409,
            "project_intake_version_invalid",
            "The project-intake version cannot be verified.",
        )
    try:
        valid = content_hash(row.snapshot) == row.content_hash
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ApiError(
            409,
            "project_intake_version_invalid",
            "The project-intake version cannot be verified.",
        )
    return row


def _get_input_case(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    lock: bool = False,
) -> ProjectFinancialInputCase | None:
    query = select(ProjectFinancialInputCase).where(
        ProjectFinancialInputCase.tenant_id == tenant_id,
        ProjectFinancialInputCase.company_id == company_id,
        ProjectFinancialInputCase.project_intake_id == intake_id,
    )
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def _get_current_input_version(
    session: Session,
    input_case: ProjectFinancialInputCase,
) -> ProjectFinancialInputVersion:
    row = session.scalar(
        select(ProjectFinancialInputVersion).where(
            ProjectFinancialInputVersion.tenant_id == input_case.tenant_id,
            ProjectFinancialInputVersion.company_id == input_case.company_id,
            ProjectFinancialInputVersion.project_intake_id == input_case.project_intake_id,
            ProjectFinancialInputVersion.input_case_id == input_case.id,
            ProjectFinancialInputVersion.version == input_case.current_version,
            ProjectFinancialInputVersion.id == input_case.current_version_id,
        )
    )
    if row is None:
        raise ApiError(
            409,
            "financial_input_version_invalid",
            "The saved financial input version cannot be verified.",
        )
    _validate_input_version(row)
    return row


def _validate_input_version(row: ProjectFinancialInputVersion) -> None:
    try:
        valid = (
            row.input_snapshot.get("snapshot_schema_version") == INPUT_SNAPSHOT_SCHEMA_VERSION
            and row.input_snapshot.get("input_case_id") == str(row.input_case_id)
            and row.input_snapshot.get("id") == str(row.id)
            and row.input_snapshot.get("version") == row.version
            and row.input_snapshot.get("project_intake_id") == str(row.project_intake_id)
            and row.input_snapshot.get("intake_version") == row.intake_version
            and content_hash(row.input_snapshot) == row.content_hash
        )
    except (TypeError, ValueError, AttributeError):
        valid = False
    if not valid:
        raise ApiError(
            409,
            "financial_input_version_invalid",
            "The saved financial input version cannot be verified.",
        )


def _input_response(
    row: ProjectFinancialInputVersion,
    *,
    is_current: bool,
) -> FinancialInputVersionResponse:
    return FinancialInputVersionResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        project_intake_id=row.project_intake_id,
        input_case_id=row.input_case_id,
        version=row.version,
        supersedes_version_id=row.supersedes_version_id,
        intake_version=row.intake_version,
        created_by_user_id=row.created_by_user_id,
        content_hash=row.content_hash,
        input_snapshot=row.input_snapshot,
        created_at=row.created_at,
        is_current=is_current,
    )


def create_project_financial_input(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectFinancialInputCreate,
    request_id: UUID,
    now: datetime,
) -> FinancialInputVersionResponse:
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id, lock=True)
    if body.intake_version != intake.current_version:
        raise ApiError(
            409,
            "financial_input_intake_version_conflict",
            "Reload the current project-intake version before recording financial inputs.",
        )
    intake_version = _get_intake_version(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        version=intake.current_version,
    )

    input_case = _get_input_case(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        lock=True,
    )
    if input_case is None:
        if body.expected_version is not None:
            raise ApiError(
                409,
                "financial_input_version_conflict",
                "No current financial-input version exists; reload before creating it.",
            )
        case_id = uuid4()
        input_version_id = uuid4()
        version_number = 1
        supersedes_version_id = None
        input_case = ProjectFinancialInputCase(
            id=case_id,
            tenant_id=tenant_id,
            company_id=access.company.id,
            project_intake_id=intake.id,
            created_by_user_id=user.id,
            current_version=version_number,
            current_version_id=input_version_id,
            created_at=now,
            updated_at=now,
        )
        session.add(input_case)
        session.flush()
    else:
        if body.expected_version != input_case.current_version:
            raise ApiError(
                409,
                "financial_input_version_conflict",
                "Financial inputs changed; reload the current version before updating.",
            )
        current_version = _get_current_input_version(session, input_case)
        case_id = input_case.id
        input_version_id = uuid4()
        version_number = input_case.current_version + 1
        supersedes_version_id = current_version.id

    input_data = body.model_dump(mode="json", exclude={"expected_version", "intake_version"})
    input_snapshot: dict[str, object] = {
        "snapshot_schema_version": INPUT_SNAPSHOT_SCHEMA_VERSION,
        "id": str(input_version_id),
        "input_case_id": str(case_id),
        "project_intake_id": str(intake.id),
        "version": version_number,
        "supersedes_version_id": (
            str(supersedes_version_id) if supersedes_version_id is not None else None
        ),
        "intake_version": intake.current_version,
        "intake_version_hash": intake_version.content_hash,
        "created_by_user_id": str(user.id),
        "created_at": _timestamp_text(now),
        **input_data,
    }
    input_version = ProjectFinancialInputVersion(
        id=input_version_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        input_case_id=case_id,
        project_intake_id=intake.id,
        version=version_number,
        supersedes_version_id=supersedes_version_id,
        intake_version=intake.current_version,
        created_by_user_id=user.id,
        content_hash=content_hash(input_snapshot),
        input_snapshot=input_snapshot,
        created_at=now,
    )
    session.add(input_version)
    session.flush()
    if version_number > 1:
        input_case.current_version = version_number
        input_case.current_version_id = input_version_id
        input_case.updated_at = now
        session.flush()

    record_event(
        session,
        "financial_input.version_created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project_financial_input_version",
        target_id=input_version.id,
        request_id=request_id,
        details={
            "project_intake_id": intake.id,
            "input_case_id": case_id,
            "version": version_number,
            "input_hash": input_version.content_hash,
        },
    )
    return _input_response(input_version, is_current=True)


def get_current_project_financial_input(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
) -> FinancialInputVersionResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    input_case = _get_input_case(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
    )
    if input_case is None:
        raise _financial_input_not_found()
    return _input_response(_get_current_input_version(session, input_case), is_current=True)


def list_project_financial_inputs(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    limit: int,
    offset: int,
) -> FinancialInputPage:
    if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE or not 0 <= offset <= MAX_HISTORY_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    input_case = _get_input_case(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
    )
    if input_case is None:
        return FinancialInputPage(
            items=[], total=0, limit=limit, offset=offset, current_version=None
        )
    base_query = select(ProjectFinancialInputVersion).where(
        ProjectFinancialInputVersion.tenant_id == tenant_id,
        ProjectFinancialInputVersion.company_id == access.company.id,
        ProjectFinancialInputVersion.project_intake_id == intake.id,
        ProjectFinancialInputVersion.input_case_id == input_case.id,
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = list(
        session.scalars(
            base_query.order_by(
                ProjectFinancialInputVersion.version.desc(), ProjectFinancialInputVersion.id
            )
            .limit(limit)
            .offset(offset)
        )
    )
    items: list[FinancialInputVersionResponse] = []
    for row in rows:
        _validate_input_version(row)
        items.append(_input_response(row, is_current=row.id == input_case.current_version_id))
    return FinancialInputPage(
        items=items,
        total=total,
        limit=limit,
        offset=offset,
        current_version=input_case.current_version,
    )


def get_project_financial_input_version(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    version_number: int,
) -> FinancialInputVersionResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    row = session.scalar(
        select(ProjectFinancialInputVersion).where(
            ProjectFinancialInputVersion.tenant_id == tenant_id,
            ProjectFinancialInputVersion.company_id == access.company.id,
            ProjectFinancialInputVersion.project_intake_id == intake.id,
            ProjectFinancialInputVersion.version == version_number,
        )
    )
    if row is None:
        raise _financial_input_not_found()
    input_case = _get_input_case(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
    )
    _validate_input_version(row)
    return _input_response(
        row,
        is_current=input_case is not None and row.id == input_case.current_version_id,
    )


def get_financial_policy_catalog(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
) -> FinancialPolicyCatalogResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    _get_intake(session, tenant_id, access.company.id, intake_id)
    return FinancialPolicyCatalogResponse.model_validate(policy_catalog_payload())


@dataclass(frozen=True, slots=True)
class _ResolvedHandoff:
    case: ProjectDecisionCase | None
    decision: ProjectDecisionVersion | None
    response: ProjectDecisionResponse | None
    approval: ProjectDecisionApproval | None
    intake_version: ProjectIntakeVersion | None
    capacity_assessment: CapacityAssessment | None
    metric_run: MetricRun | None
    status: str
    reasons: tuple[str, ...]


def _resolve_handoff(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    company_id: UUID,
    intake: ProjectIntake,
    *,
    lock: bool = False,
) -> _ResolvedHandoff:
    case_query = select(ProjectDecisionCase).where(
        ProjectDecisionCase.tenant_id == tenant_id,
        ProjectDecisionCase.company_id == company_id,
        ProjectDecisionCase.project_intake_id == intake.id,
    )
    if lock:
        case_query = case_query.with_for_update()
    case = session.scalar(case_query)
    if case is None:
        return _ResolvedHandoff(
            None, None, None, None, None, None, None, "missing", ("m7_decision_not_found",)
        )

    decision = session.scalar(
        select(ProjectDecisionVersion).where(
            ProjectDecisionVersion.tenant_id == tenant_id,
            ProjectDecisionVersion.company_id == company_id,
            ProjectDecisionVersion.project_intake_id == intake.id,
            ProjectDecisionVersion.decision_case_id == case.id,
            ProjectDecisionVersion.id == case.current_decision_id,
            ProjectDecisionVersion.version == case.current_version,
        )
    )
    if decision is None:
        return _ResolvedHandoff(
            case, None, None, None, None, None, None, "invalid", ("m7_current_decision_missing",)
        )

    try:
        response = get_project_decision(session, user, tenant_id, intake.id, decision.id)
    except ApiError:
        return _ResolvedHandoff(
            case, decision, None, None, None, None, None, "invalid", ("m7_evidence_invalid",)
        )

    approval = session.scalar(
        select(ProjectDecisionApproval).where(
            ProjectDecisionApproval.tenant_id == tenant_id,
            ProjectDecisionApproval.company_id == company_id,
            ProjectDecisionApproval.decision_case_id == case.id,
            ProjectDecisionApproval.decision_version_id == decision.id,
        )
    )
    reasons: list[str] = []
    if not response.is_current:
        reasons.append("m7_decision_not_current")
    if response.evidence_freshness == "stale":
        reasons.extend(response.staleness_reasons or ["m7_evidence_stale"])
    elif response.evidence_freshness == "invalid":
        return _ResolvedHandoff(
            case,
            decision,
            response,
            approval,
            None,
            None,
            None,
            "invalid",
            tuple(response.staleness_reasons or ["m7_evidence_invalid"]),
        )
    if decision.outcome != "proceed_to_financial_evaluation":
        reasons.append("m7_decision_does_not_authorize_financial_evaluation")
    if response.status != "approved" or approval is None or approval.outcome != "approved":
        reasons.append("m7_decision_is_not_approved")
    if decision.intake_version != intake.current_version:
        reasons.append("m7_decision_intake_version_is_not_current")

    intake_version = session.scalar(
        select(ProjectIntakeVersion).where(
            ProjectIntakeVersion.tenant_id == tenant_id,
            ProjectIntakeVersion.company_id == company_id,
            ProjectIntakeVersion.project_intake_id == intake.id,
            ProjectIntakeVersion.version == decision.intake_version,
        )
    )
    capacity_assessment = session.scalar(
        select(CapacityAssessment).where(
            CapacityAssessment.tenant_id == tenant_id,
            CapacityAssessment.company_id == company_id,
            CapacityAssessment.project_intake_id == intake.id,
            CapacityAssessment.id == decision.assessment_id,
        )
    )
    metric_run = session.scalar(
        select(MetricRun).where(
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == company_id,
            MetricRun.id == decision.metric_run_id,
        )
    )
    if intake_version is None or capacity_assessment is None or metric_run is None:
        return _ResolvedHandoff(
            case,
            decision,
            response,
            approval,
            intake_version,
            capacity_assessment,
            metric_run,
            "invalid",
            ("m7_evidence_reference_missing",),
        )
    try:
        linked_rows_valid = (
            content_hash(intake_version.snapshot) == intake_version.content_hash
            and content_hash(capacity_assessment.input_snapshot) == capacity_assessment.input_hash
            and content_hash(capacity_assessment.result_snapshot) == capacity_assessment.result_hash
            and content_hash(metric_run.input_snapshot) == metric_run.input_hash
            and content_hash(metric_run.result_snapshot) == metric_run.result_hash
            and decision.intake_version_hash == intake_version.content_hash
            and decision.assessment_id == capacity_assessment.id
            and decision.assessment_input_hash == capacity_assessment.input_hash
            and decision.assessment_result_hash == capacity_assessment.result_hash
            and decision.metric_run_id == metric_run.id
            and decision.metric_input_hash == metric_run.input_hash
            and decision.metric_result_hash == metric_run.result_hash
            and (
                (approval is None and response.approval is None)
                or (
                    approval is not None
                    and response.approval is not None
                    and response.approval.id == approval.id
                    and response.approval.approval_hash == approval.approval_hash
                )
            )
        )
    except (TypeError, ValueError):
        linked_rows_valid = False
    if not linked_rows_valid:
        return _ResolvedHandoff(
            case,
            decision,
            response,
            approval,
            intake_version,
            capacity_assessment,
            metric_run,
            "invalid",
            ("m7_evidence_integrity_invalid",),
        )

    if reasons:
        status = (
            "stale"
            if response.evidence_freshness == "stale" or not response.is_current
            else "not_approved"
        )
        return _ResolvedHandoff(
            case,
            decision,
            response,
            approval,
            intake_version,
            capacity_assessment,
            metric_run,
            status,
            tuple(dict.fromkeys(reasons)),
        )
    return _ResolvedHandoff(
        case,
        decision,
        response,
        approval,
        intake_version,
        capacity_assessment,
        metric_run,
        "ready",
        (),
    )


def _current_input_binding(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    lock: bool = False,
) -> tuple[ProjectFinancialInputCase | None, ProjectFinancialInputVersion | None]:
    input_case = _get_input_case(
        session,
        tenant_id=tenant_id,
        company_id=company_id,
        intake_id=intake_id,
        lock=lock,
    )
    if input_case is None:
        return None, None
    return input_case, _get_current_input_version(session, input_case)


def _readiness_result_snapshot(
    *,
    intake: ProjectIntake,
    input_version: ProjectFinancialInputVersion | None,
) -> dict[str, object]:
    financial_binding: dict[str, object] | None = None
    if input_version is not None:
        financial_binding = {
            "case_id": str(input_version.input_case_id),
            "id": str(input_version.id),
            "version": input_version.version,
            "intake_version": input_version.intake_version,
            "content_hash": input_version.content_hash,
            "snapshot": input_version.input_snapshot,
        }
    snapshot: dict[str, object] = {
        "intake": {"version": intake.current_version},
        "financial_input": financial_binding,
    }
    return evaluate_financial_input_snapshot(snapshot)


def get_project_financial_readiness(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
) -> FinancialReadinessResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    input_case: ProjectFinancialInputCase | None = None
    input_version: ProjectFinancialInputVersion | None = None
    input_freshness = "missing"
    input_warnings: list[str] = []
    try:
        input_case, input_version = _current_input_binding(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            intake_id=intake.id,
        )
        if input_version is not None:
            input_freshness = (
                "fresh" if input_version.intake_version == intake.current_version else "stale"
            )
    except ApiError:
        input_freshness = "invalid"
        input_warnings.append("financial_input_snapshot_invalid")

    handoff = _resolve_handoff(session, user, tenant_id, access.company.id, intake)
    result_snapshot = _readiness_result_snapshot(intake=intake, input_version=input_version)
    missing_inputs = list(cast(list[str], result_snapshot["missing_inputs"]))
    warnings = list(cast(list[str], result_snapshot["warnings"]))
    if input_freshness == "invalid":
        missing_inputs.append("financial_input_version_integrity")
    if handoff.status != "ready":
        missing_inputs.append("approved_current_m7_financial_handoff")
        warnings.append("m7_handoff_" + handoff.status)
    warnings.extend(input_warnings)
    return FinancialReadinessResponse(
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_intake_id=intake.id,
        current_intake_version=intake.current_version,
        current_input_version=input_case.current_version if input_case is not None else None,
        input_intake_version=input_version.intake_version if input_version is not None else None,
        input_freshness=cast(FinancialInputFreshness, input_freshness),
        m7_decision_id=handoff.decision.id if handoff.decision is not None else None,
        m7_decision_version=handoff.decision.version if handoff.decision is not None else None,
        m7_decision_status=handoff.response.status if handoff.response is not None else None,
        m7_decision_outcome=handoff.decision.outcome if handoff.decision is not None else None,
        m7_evidence_freshness=(
            handoff.response.evidence_freshness if handoff.response is not None else None
        ),
        handoff_status=cast(FinancialHandoffStatus, handoff.status),
        can_run_assessment=handoff.status == "ready",
        policy_status=FINANCIAL_POLICY_CATALOG_STATUS,
        financial_verdict_if_run="incomplete",
        missing_inputs=list(dict.fromkeys(missing_inputs)),
        unprovided_observations=cast(list[str], result_snapshot["unprovided_observations"]),
        warnings=list(dict.fromkeys([*warnings, *handoff.reasons])),
    )


def _assessment_input_snapshot(
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake: ProjectIntake,
    intake_version: ProjectIntakeVersion,
    input_case: ProjectFinancialInputCase | None,
    financial_input: ProjectFinancialInputVersion | None,
    handoff: _ResolvedHandoff,
    user: UserAccount,
    now: datetime,
) -> dict[str, object]:
    if any(
        value is None
        for value in (
            handoff.case,
            handoff.decision,
            handoff.response,
            handoff.approval,
            handoff.capacity_assessment,
            handoff.metric_run,
            handoff.intake_version,
        )
    ):
        raise ApiError(
            409,
            "financial_handoff_unavailable",
            "A fresh, approved M7 handoff is required before an M8 assessment can be run.",
        )
    decision = cast(ProjectDecisionVersion, handoff.decision)
    decision_response = cast(ProjectDecisionResponse, handoff.response)
    approval = cast(ProjectDecisionApproval, handoff.approval)
    capacity_assessment = cast(CapacityAssessment, handoff.capacity_assessment)
    metric_run = cast(MetricRun, handoff.metric_run)
    decision_case = cast(ProjectDecisionCase, handoff.case)
    financial_binding: dict[str, object] | None = None
    if financial_input is not None and input_case is not None:
        financial_binding = {
            "case_id": str(input_case.id),
            "id": str(financial_input.id),
            "version": financial_input.version,
            "content_hash": financial_input.content_hash,
            "intake_version": financial_input.intake_version,
            "snapshot": financial_input.input_snapshot,
        }
    return {
        "snapshot_schema_version": ASSESSMENT_SNAPSHOT_SCHEMA_VERSION,
        "scope": {
            "tenant_id": str(tenant_id),
            "company_id": str(company_id),
            "project_intake_id": str(intake.id),
        },
        "captured_by_user_id": str(user.id),
        "captured_at": _timestamp_text(now),
        "intake": {
            "id": str(intake.id),
            "version_id": str(intake_version.id),
            "version": intake_version.version,
            "content_hash": intake_version.content_hash,
            "snapshot": intake_version.snapshot,
        },
        "financial_input": financial_binding,
        "evidence": {
            "m5_metric_run": {
                "id": str(metric_run.id),
                "catalog_version": metric_run.metric_catalog_version,
                "calculation_version": metric_run.calculation_version,
                "input_hash": metric_run.input_hash,
                "result_hash": metric_run.result_hash,
                "input_snapshot_hash": content_hash(metric_run.input_snapshot),
                "result_snapshot_hash": content_hash(metric_run.result_snapshot),
            },
            "m6_capacity_assessment": {
                "id": str(capacity_assessment.id),
                "assessment_version": capacity_assessment.assessment_version,
                "intake_version": capacity_assessment.intake_version,
                "input_hash": capacity_assessment.input_hash,
                "result_hash": capacity_assessment.result_hash,
                "metric_run_id": str(capacity_assessment.metric_run_id),
            },
            "m7_decision": {
                "case_id": str(decision_case.id),
                "decision_id": str(decision.id),
                "version": decision.version,
                "outcome": decision.outcome,
                "status": decision_response.status,
                "decision_hash": decision.decision_hash,
                "evidence_hash": decision.evidence_hash,
                "approval": {
                    "id": str(approval.id),
                    "outcome": approval.outcome,
                    "approval_hash": approval.approval_hash,
                    "decision_hash": approval.decision_hash,
                    "evidence_hash": approval.evidence_hash,
                },
            },
        },
        "policy_catalog": policy_catalog_payload(),
        "formula_catalog_version": FINANCIAL_POLICY_CATALOG_VERSION,
    }


def create_financial_assessment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    request_id: UUID,
    now: datetime,
) -> FinancialAssessment:
    access = require_company_context_access(
        session, user, tenant_id, "finance:write", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id, lock=True)
    handoff = _resolve_handoff(session, user, tenant_id, access.company.id, intake, lock=True)
    if handoff.status != "ready" or any(
        value is None
        for value in (
            handoff.case,
            handoff.decision,
            handoff.response,
            handoff.approval,
            handoff.intake_version,
            handoff.capacity_assessment,
            handoff.metric_run,
        )
    ):
        raise ApiError(
            409,
            "financial_handoff_unavailable",
            "A fresh, approved M7 handoff is required before an M8 assessment can be run.",
        )
    intake_version = cast(ProjectIntakeVersion, handoff.intake_version)
    decision_case = cast(ProjectDecisionCase, handoff.case)
    decision = cast(ProjectDecisionVersion, handoff.decision)
    approval = cast(ProjectDecisionApproval, handoff.approval)
    capacity_assessment = cast(CapacityAssessment, handoff.capacity_assessment)
    metric_run = cast(MetricRun, handoff.metric_run)
    if intake_version.version != intake.current_version:
        raise ApiError(
            409,
            "financial_handoff_unavailable",
            "A fresh, approved M7 handoff is required before an M8 assessment can be run.",
        )

    input_case, financial_input = _current_input_binding(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        lock=True,
    )
    input_snapshot = _assessment_input_snapshot(
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake=intake,
        intake_version=intake_version,
        input_case=input_case,
        financial_input=financial_input,
        handoff=handoff,
        user=user,
        now=now,
    )
    result_snapshot = evaluate_financial_input_snapshot(input_snapshot)
    assessment_id = uuid4()
    row = FinancialAssessment(
        id=assessment_id,
        tenant_id=tenant_id,
        company_id=access.company.id,
        project_intake_id=intake.id,
        requested_by_user_id=user.id,
        intake_version=intake_version.version,
        intake_version_hash=intake_version.content_hash,
        financial_input_case_id=input_case.id if input_case is not None else None,
        financial_input_version_id=financial_input.id if financial_input is not None else None,
        financial_input_version=financial_input.version if financial_input is not None else None,
        financial_input_hash=financial_input.content_hash if financial_input is not None else None,
        capacity_assessment_id=capacity_assessment.id,
        capacity_assessment_version=capacity_assessment.assessment_version,
        capacity_assessment_input_hash=capacity_assessment.input_hash,
        capacity_assessment_result_hash=capacity_assessment.result_hash,
        metric_run_id=metric_run.id,
        metric_catalog_version=metric_run.metric_catalog_version,
        metric_calculation_version=metric_run.calculation_version,
        metric_input_hash=metric_run.input_hash,
        metric_result_hash=metric_run.result_hash,
        decision_case_id=decision_case.id,
        decision_version_id=decision.id,
        decision_version=decision.version,
        decision_outcome="proceed_to_financial_evaluation",
        decision_hash=decision.decision_hash,
        decision_evidence_hash=decision.evidence_hash,
        decision_approval_id=approval.id,
        decision_approval_outcome="approved",
        decision_approval_hash=approval.approval_hash,
        formula_catalog_version=FINANCIAL_POLICY_CATALOG_VERSION,
        approved_policy_id=None,
        approved_policy_version=None,
        currency_codes=cast(list[str], result_snapshot["currency_codes"]),
        financial_verdict="incomplete",
        input_hash=content_hash(input_snapshot),
        result_hash=content_hash(result_snapshot),
        input_snapshot=input_snapshot,
        result_snapshot=result_snapshot,
        created_at=now,
    )
    session.add(row)
    session.flush()
    record_event(
        session,
        "financial_assessment.created",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="financial_assessment",
        target_id=row.id,
        request_id=request_id,
        details={
            "project_intake_id": intake.id,
            "intake_version": row.intake_version,
            "financial_input_version": row.financial_input_version,
            "capacity_assessment_id": row.capacity_assessment_id,
            "metric_run_id": row.metric_run_id,
            "decision_version_id": row.decision_version_id,
            "decision_approval_id": row.decision_approval_id,
            "formula_catalog_version": row.formula_catalog_version,
            "financial_verdict": row.financial_verdict,
            "input_hash": row.input_hash,
            "result_hash": row.result_hash,
        },
    )
    return row


def _get_assessment(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
) -> FinancialAssessment:
    row = session.scalar(
        select(FinancialAssessment).where(
            FinancialAssessment.id == assessment_id,
            FinancialAssessment.tenant_id == tenant_id,
            FinancialAssessment.company_id == company_id,
            FinancialAssessment.project_intake_id == intake_id,
        )
    )
    if row is None:
        raise _assessment_not_found()
    return row


def _integrity_reasons(
    session: Session,
    user: UserAccount,
    access: CompanyContextAccess,
    row: FinancialAssessment,
) -> tuple[list[str], ProjectDecisionResponse | None]:
    reasons: list[str] = []
    decision_response: ProjectDecisionResponse | None = None
    input_snapshot = row.input_snapshot
    result_snapshot = row.result_snapshot
    scope = _as_dict(input_snapshot.get("scope"))
    intake_binding = _as_dict(input_snapshot.get("intake"))
    evidence = _as_dict(input_snapshot.get("evidence"))
    m5_binding = _as_dict(evidence.get("m5_metric_run"))
    m6_binding = _as_dict(evidence.get("m6_capacity_assessment"))
    m7_binding = _as_dict(evidence.get("m7_decision"))

    try:
        if content_hash(input_snapshot) != row.input_hash:
            reasons.append("financial_assessment_input_hash_mismatch")
        if content_hash(result_snapshot) != row.result_hash:
            reasons.append("financial_assessment_result_hash_mismatch")
    except (TypeError, ValueError):
        reasons.append("financial_assessment_snapshot_invalid")

    if (
        input_snapshot.get("snapshot_schema_version") != ASSESSMENT_SNAPSHOT_SCHEMA_VERSION
        or scope.get("tenant_id") != str(row.tenant_id)
        or scope.get("company_id") != str(row.company_id)
        or scope.get("project_intake_id") != str(row.project_intake_id)
        or input_snapshot.get("captured_by_user_id") != str(row.requested_by_user_id)
        or input_snapshot.get("formula_catalog_version") != row.formula_catalog_version
        or row.formula_catalog_version != FINANCIAL_POLICY_CATALOG_VERSION
        or row.financial_verdict != "incomplete"
        or row.approved_policy_id is not None
        or row.approved_policy_version is not None
    ):
        reasons.append("financial_assessment_contract_mismatch")

    intake_version = session.scalar(
        select(ProjectIntakeVersion).where(
            ProjectIntakeVersion.tenant_id == row.tenant_id,
            ProjectIntakeVersion.company_id == row.company_id,
            ProjectIntakeVersion.project_intake_id == row.project_intake_id,
            ProjectIntakeVersion.version == row.intake_version,
        )
    )
    try:
        if (
            intake_version is None
            or intake_version.content_hash != row.intake_version_hash
            or content_hash(intake_version.snapshot) != intake_version.content_hash
            or intake_binding.get("version_id") != str(intake_version.id)
            or intake_binding.get("version") != row.intake_version
            or intake_binding.get("content_hash") != row.intake_version_hash
            or intake_binding.get("snapshot") != intake_version.snapshot
        ):
            reasons.append("project_intake_binding_invalid")
    except (TypeError, ValueError, AttributeError):
        reasons.append("project_intake_binding_invalid")

    if row.financial_input_version_id is None:
        if input_snapshot.get("financial_input") is not None:
            reasons.append("financial_input_binding_invalid")
    else:
        financial_binding = _as_dict(input_snapshot.get("financial_input"))
        financial_version = session.scalar(
            select(ProjectFinancialInputVersion).where(
                ProjectFinancialInputVersion.tenant_id == row.tenant_id,
                ProjectFinancialInputVersion.company_id == row.company_id,
                ProjectFinancialInputVersion.project_intake_id == row.project_intake_id,
                ProjectFinancialInputVersion.input_case_id == row.financial_input_case_id,
                ProjectFinancialInputVersion.id == row.financial_input_version_id,
            )
        )
        try:
            if (
                financial_version is None
                or financial_version.version != row.financial_input_version
                or financial_version.content_hash != row.financial_input_hash
                or content_hash(financial_version.input_snapshot) != financial_version.content_hash
                or financial_binding.get("case_id") != str(row.financial_input_case_id)
                or financial_binding.get("id") != str(row.financial_input_version_id)
                or financial_binding.get("version") != row.financial_input_version
                or financial_binding.get("content_hash") != row.financial_input_hash
                or financial_binding.get("snapshot") != financial_version.input_snapshot
            ):
                reasons.append("financial_input_binding_invalid")
        except (TypeError, ValueError, AttributeError):
            reasons.append("financial_input_binding_invalid")

    capacity_assessment = session.scalar(
        select(CapacityAssessment).where(
            CapacityAssessment.tenant_id == row.tenant_id,
            CapacityAssessment.company_id == row.company_id,
            CapacityAssessment.project_intake_id == row.project_intake_id,
            CapacityAssessment.id == row.capacity_assessment_id,
        )
    )
    metric_run = session.scalar(
        select(MetricRun).where(
            MetricRun.tenant_id == row.tenant_id,
            MetricRun.company_id == row.company_id,
            MetricRun.id == row.metric_run_id,
        )
    )
    try:
        if (
            capacity_assessment is None
            or capacity_assessment.assessment_version != row.capacity_assessment_version
            or capacity_assessment.input_hash != row.capacity_assessment_input_hash
            or capacity_assessment.result_hash != row.capacity_assessment_result_hash
            or content_hash(capacity_assessment.input_snapshot) != capacity_assessment.input_hash
            or content_hash(capacity_assessment.result_snapshot) != capacity_assessment.result_hash
            or m6_binding.get("id") != str(row.capacity_assessment_id)
            or m6_binding.get("assessment_version") != row.capacity_assessment_version
            or m6_binding.get("input_hash") != row.capacity_assessment_input_hash
            or m6_binding.get("result_hash") != row.capacity_assessment_result_hash
            or capacity_assessment.metric_run_id != row.metric_run_id
        ):
            reasons.append("capacity_assessment_binding_invalid")
        if (
            metric_run is None
            or metric_run.metric_catalog_version != row.metric_catalog_version
            or metric_run.calculation_version != row.metric_calculation_version
            or metric_run.input_hash != row.metric_input_hash
            or metric_run.result_hash != row.metric_result_hash
            or content_hash(metric_run.input_snapshot) != metric_run.input_hash
            or content_hash(metric_run.result_snapshot) != metric_run.result_hash
            or m5_binding.get("id") != str(row.metric_run_id)
            or m5_binding.get("catalog_version") != row.metric_catalog_version
            or m5_binding.get("calculation_version") != row.metric_calculation_version
            or m5_binding.get("input_hash") != row.metric_input_hash
            or m5_binding.get("result_hash") != row.metric_result_hash
        ):
            reasons.append("metric_run_binding_invalid")
        if (
            capacity_assessment is not None
            and metric_run is not None
            and (
                capacity_assessment.metric_catalog_version != metric_run.metric_catalog_version
                or capacity_assessment.metric_calculation_version != metric_run.calculation_version
                or capacity_assessment.metric_input_hash != metric_run.input_hash
                or capacity_assessment.metric_result_hash != metric_run.result_hash
            )
        ):
            reasons.append("capacity_metric_binding_invalid")
    except (TypeError, ValueError):
        reasons.append("capacity_or_metric_snapshot_invalid")

    decision_case = session.scalar(
        select(ProjectDecisionCase).where(
            ProjectDecisionCase.tenant_id == row.tenant_id,
            ProjectDecisionCase.company_id == row.company_id,
            ProjectDecisionCase.id == row.decision_case_id,
            ProjectDecisionCase.project_intake_id == row.project_intake_id,
        )
    )
    decision = session.scalar(
        select(ProjectDecisionVersion).where(
            ProjectDecisionVersion.tenant_id == row.tenant_id,
            ProjectDecisionVersion.company_id == row.company_id,
            ProjectDecisionVersion.decision_case_id == row.decision_case_id,
            ProjectDecisionVersion.project_intake_id == row.project_intake_id,
            ProjectDecisionVersion.id == row.decision_version_id,
        )
    )
    approval = session.scalar(
        select(ProjectDecisionApproval).where(
            ProjectDecisionApproval.tenant_id == row.tenant_id,
            ProjectDecisionApproval.company_id == row.company_id,
            ProjectDecisionApproval.decision_case_id == row.decision_case_id,
            ProjectDecisionApproval.decision_version_id == row.decision_version_id,
            ProjectDecisionApproval.id == row.decision_approval_id,
        )
    )
    if decision_case is None or decision is None or approval is None:
        reasons.append("m7_evidence_reference_missing")
    else:
        approval_binding = _as_dict(m7_binding.get("approval"))
        if (
            decision.version != row.decision_version
            or decision.outcome != row.decision_outcome
            or decision.decision_hash != row.decision_hash
            or decision.evidence_hash != row.decision_evidence_hash
            or decision.assessment_id != row.capacity_assessment_id
            or decision.metric_run_id != row.metric_run_id
            or decision_case.project_intake_id != row.project_intake_id
            or decision_case.id != row.decision_case_id
            or m7_binding.get("case_id") != str(row.decision_case_id)
            or m7_binding.get("decision_id") != str(row.decision_version_id)
            or m7_binding.get("version") != row.decision_version
            or m7_binding.get("decision_hash") != row.decision_hash
            or m7_binding.get("evidence_hash") != row.decision_evidence_hash
            or approval.id != row.decision_approval_id
            or approval.outcome != row.decision_approval_outcome
            or approval.approval_hash != row.decision_approval_hash
            or approval_binding.get("id") != str(row.decision_approval_id)
            or approval_binding.get("outcome") != row.decision_approval_outcome
            or approval_binding.get("approval_hash") != row.decision_approval_hash
        ):
            reasons.append("m7_decision_or_approval_binding_invalid")
        try:
            decision_response = get_project_decision(
                session, user, row.tenant_id, row.project_intake_id, row.decision_version_id
            )
            if (
                decision_response.decision_hash != row.decision_hash
                or decision_response.evidence_hash != row.decision_evidence_hash
                or decision_response.approval is None
                or decision_response.approval.id != row.decision_approval_id
                or decision_response.approval.approval_hash != row.decision_approval_hash
                or decision_response.evidence_freshness == "invalid"
            ):
                reasons.append("m7_evidence_integrity_invalid")
        except ApiError:
            reasons.append("m7_evidence_integrity_invalid")

    return list(dict.fromkeys(reasons)), decision_response


def _assessment_freshness(
    session: Session,
    user: UserAccount,
    access: CompanyContextAccess,
    row: FinancialAssessment,
) -> tuple[str, list[str]]:
    integrity_reasons, decision_response = _integrity_reasons(session, user, access, row)
    if integrity_reasons:
        return "invalid", integrity_reasons

    reasons: list[str] = []
    intake = session.scalar(
        select(ProjectIntake).where(
            ProjectIntake.tenant_id == row.tenant_id,
            ProjectIntake.company_id == row.company_id,
            ProjectIntake.id == row.project_intake_id,
        )
    )
    if intake is None:
        return "invalid", ["project_intake_missing"]
    if intake.current_version != row.intake_version:
        reasons.append("project_intake_has_a_newer_version")

    current_input_case = _get_input_case(
        session,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        intake_id=row.project_intake_id,
    )
    if row.financial_input_version_id is None:
        if current_input_case is not None:
            reasons.append("financial_inputs_added_after_assessment")
    elif current_input_case is None:
        reasons.append("financial_input_case_missing")
    elif (
        current_input_case.id != row.financial_input_case_id
        or current_input_case.current_version_id != row.financial_input_version_id
        or current_input_case.current_version != row.financial_input_version
    ):
        reasons.append("a_newer_financial_input_version_exists")

    if row.financial_input_version_id is not None:
        financial_version = session.scalar(
            select(ProjectFinancialInputVersion).where(
                ProjectFinancialInputVersion.tenant_id == row.tenant_id,
                ProjectFinancialInputVersion.company_id == row.company_id,
                ProjectFinancialInputVersion.project_intake_id == row.project_intake_id,
                ProjectFinancialInputVersion.id == row.financial_input_version_id,
            )
        )
        if (
            financial_version is not None
            and financial_version.intake_version != intake.current_version
        ):
            reasons.append("financial_input_is_bound_to_a_prior_intake_version")

    if decision_response is None:
        reasons.append("m7_decision_unavailable")
    else:
        if not decision_response.is_current:
            reasons.append("a_newer_m7_decision_exists")
        if decision_response.evidence_freshness == "stale":
            reasons.extend(decision_response.staleness_reasons or ["m7_evidence_is_stale"])
        elif decision_response.evidence_freshness == "invalid":
            return "invalid", ["m7_evidence_integrity_invalid"]
        if (
            decision_response.status != "approved"
            or decision_response.outcome != "proceed_to_financial_evaluation"
            or decision_response.approval is None
            or decision_response.approval.outcome != "approved"
        ):
            reasons.append("m7_financial_handoff_is_no_longer_approved")

    if row.formula_catalog_version != FINANCIAL_POLICY_CATALOG_VERSION:
        reasons.append("financial_policy_catalog_version_changed")
    unique_reasons = list(dict.fromkeys(reasons))
    return ("stale", unique_reasons) if unique_reasons else ("fresh", [])


def _assessment_response(
    session: Session,
    user: UserAccount,
    access: CompanyContextAccess,
    row: FinancialAssessment,
) -> FinancialAssessmentResponse:
    freshness, reasons = _assessment_freshness(session, user, access, row)
    result = row.result_snapshot
    return FinancialAssessmentResponse(
        id=row.id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        project_intake_id=row.project_intake_id,
        requested_by_user_id=row.requested_by_user_id,
        intake_version=row.intake_version,
        intake_version_hash=row.intake_version_hash,
        financial_input_case_id=row.financial_input_case_id,
        financial_input_version_id=row.financial_input_version_id,
        financial_input_version=row.financial_input_version,
        financial_input_hash=row.financial_input_hash,
        capacity_assessment_id=row.capacity_assessment_id,
        capacity_assessment_version=row.capacity_assessment_version,
        capacity_assessment_input_hash=row.capacity_assessment_input_hash,
        capacity_assessment_result_hash=row.capacity_assessment_result_hash,
        metric_run_id=row.metric_run_id,
        metric_catalog_version=row.metric_catalog_version,
        metric_calculation_version=row.metric_calculation_version,
        metric_input_hash=row.metric_input_hash,
        metric_result_hash=row.metric_result_hash,
        decision_case_id=row.decision_case_id,
        decision_version_id=row.decision_version_id,
        decision_version=row.decision_version,
        decision_outcome=cast(FinancialDecisionOutcome, row.decision_outcome),
        decision_hash=row.decision_hash,
        decision_evidence_hash=row.decision_evidence_hash,
        decision_approval_id=row.decision_approval_id,
        decision_approval_outcome=cast(FinancialApprovalOutcome, row.decision_approval_outcome),
        decision_approval_hash=row.decision_approval_hash,
        formula_catalog_version=row.formula_catalog_version,
        approved_policy_id=row.approved_policy_id,
        approved_policy_version=row.approved_policy_version,
        currency_codes=list(row.currency_codes),
        financial_verdict=cast(FinancialVerdict, row.financial_verdict),
        input_hash=row.input_hash,
        result_hash=row.result_hash,
        input_snapshot=row.input_snapshot,
        result_snapshot=row.result_snapshot,
        policy_status=cast(
            FinancialPolicyStatus, result.get("policy_status", FINANCIAL_POLICY_CATALOG_STATUS)
        ),
        calculated_metrics=[_as_dict(item) for item in _as_list(result.get("calculated_metrics"))],
        observations=financial_observation_summary(result),
        assumptions=[_as_dict(item) for item in _as_list(result.get("assumptions"))],
        missing_inputs=[str(item) for item in _as_list(result.get("missing_inputs"))],
        unprovided_observations=[
            str(item) for item in _as_list(result.get("unprovided_observations"))
        ],
        warnings=[str(item) for item in _as_list(result.get("warnings"))],
        evidence_freshness=cast(FinancialFreshness, freshness),
        staleness_reasons=reasons,
        created_at=row.created_at,
    )


def list_financial_assessments(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    limit: int,
    offset: int,
) -> FinancialAssessmentPage:
    if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE or not 0 <= offset <= MAX_HISTORY_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    base_query = select(FinancialAssessment).where(
        FinancialAssessment.tenant_id == tenant_id,
        FinancialAssessment.company_id == access.company.id,
        FinancialAssessment.project_intake_id == intake.id,
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    rows = list(
        session.scalars(
            base_query.order_by(
                FinancialAssessment.created_at.desc(), FinancialAssessment.id.desc()
            )
            .limit(limit)
            .offset(offset)
        )
    )
    return FinancialAssessmentPage(
        items=[_assessment_response(session, user, access, row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def get_financial_assessment(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    assessment_id: UUID,
) -> FinancialAssessmentResponse:
    access = require_company_context_access(session, user, tenant_id, "finance:read")
    _get_intake(session, tenant_id, access.company.id, intake_id)
    row = _get_assessment(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake_id,
        assessment_id=assessment_id,
    )
    return _assessment_response(session, user, access, row)
