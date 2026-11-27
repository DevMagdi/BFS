from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from businessflow.audit import record_event
from businessflow.errors import ApiError, CommitAndRaise
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
from businessflow.project_decisions.schemas import (
    ApprovalOutcome,
    CapacityStatus,
    DecisionOutcome,
    DecisionStatus,
    FreshnessStatus,
    ProjectDecisionApprovalCreate,
    ProjectDecisionApprovalResponse,
    ProjectDecisionCreate,
    ProjectDecisionPage,
    ProjectDecisionResponse,
)
from businessflow.services.company_context_access import require_company_context_access

DECISION_SNAPSHOT_SCHEMA_VERSION = 1
MAX_HISTORY_PAGE_SIZE = 100
MAX_HISTORY_OFFSET = 100_000


@dataclass(frozen=True, slots=True)
class _ResolvedEvidence:
    assessment: CapacityAssessment
    intake: ProjectIntake
    intake_version: ProjectIntakeVersion
    metric_run: MetricRun
    binding: dict[str, object]
    evidence_hash: str
    freshness: str
    reasons: tuple[str, ...]


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def _not_found() -> ApiError:
    return ApiError(404, "project_intake_not_found", "Project intake was not found.")


def _decision_not_found() -> ApiError:
    return ApiError(404, "project_decision_not_found", "Project decision was not found.")


def _assessment_not_found() -> ApiError:
    return ApiError(404, "capacity_assessment_not_found", "Capacity assessment was not found.")


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


def _get_case(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    lock: bool = False,
) -> ProjectDecisionCase | None:
    query = select(ProjectDecisionCase).where(
        ProjectDecisionCase.tenant_id == tenant_id,
        ProjectDecisionCase.company_id == company_id,
        ProjectDecisionCase.project_intake_id == intake_id,
    )
    if lock:
        query = query.with_for_update()
    return session.scalar(query)


def _get_decision(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake_id: UUID,
    decision_id: UUID,
) -> ProjectDecisionVersion:
    decision = session.scalar(
        select(ProjectDecisionVersion).where(
            ProjectDecisionVersion.id == decision_id,
            ProjectDecisionVersion.tenant_id == tenant_id,
            ProjectDecisionVersion.company_id == company_id,
            ProjectDecisionVersion.project_intake_id == intake_id,
        )
    )
    if decision is None:
        raise _decision_not_found()
    return decision


def _assessment_binding(
    assessment: CapacityAssessment,
    intake: ProjectIntake,
    intake_version: ProjectIntakeVersion,
    metric_run: MetricRun,
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tenant_id": str(assessment.tenant_id),
        "company_id": str(assessment.company_id),
        "project_intake_id": str(assessment.project_intake_id),
        "intake_version": assessment.intake_version,
        "intake_version_hash": intake_version.content_hash,
        "assessment_id": str(assessment.id),
        "assessment_version": assessment.assessment_version,
        "assessment_input_hash": assessment.input_hash,
        "assessment_result_hash": assessment.result_hash,
        "capacity_status": assessment.capacity_status,
        "business_feasibility_status": assessment.business_feasibility_status,
        "metric_run_id": str(metric_run.id),
        "metric_catalog_version": metric_run.metric_catalog_version,
        "metric_calculation_version": metric_run.calculation_version,
        "metric_input_hash": metric_run.input_hash,
        "metric_result_hash": metric_run.result_hash,
    }


def _resolve_assessment_evidence(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    intake: ProjectIntake,
    assessment_id: UUID,
    require_fresh: bool,
) -> _ResolvedEvidence:
    assessment = session.scalar(
        select(CapacityAssessment).where(
            CapacityAssessment.id == assessment_id,
            CapacityAssessment.tenant_id == tenant_id,
            CapacityAssessment.company_id == company_id,
            CapacityAssessment.project_intake_id == intake.id,
        )
    )
    if assessment is None:
        raise _assessment_not_found()

    intake_version = session.scalar(
        select(ProjectIntakeVersion).where(
            ProjectIntakeVersion.tenant_id == tenant_id,
            ProjectIntakeVersion.company_id == company_id,
            ProjectIntakeVersion.project_intake_id == intake.id,
            ProjectIntakeVersion.version == assessment.intake_version,
        )
    )
    metric_run = session.scalar(
        select(MetricRun).where(
            MetricRun.id == assessment.metric_run_id,
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == company_id,
        )
    )
    if intake_version is None or metric_run is None:
        raise ApiError(
            409,
            "capacity_assessment_evidence_invalid",
            "The capacity assessment evidence cannot be verified.",
        )

    input_snapshot = assessment.input_snapshot
    result_snapshot = assessment.result_snapshot
    intake_binding = _as_dict(input_snapshot.get("intake"))
    scope_binding = _as_dict(input_snapshot.get("scope"))
    metric_binding = _as_dict(input_snapshot.get("metric_run"))
    metric_input_reference = _as_dict(input_snapshot.get("metric_run_input_snapshot_reference"))
    result_metric_binding = _as_dict(result_snapshot.get("metric_run"))
    metric_input_scope = _as_dict(metric_run.input_snapshot.get("scope"))
    metric_input_period = _as_dict(metric_run.input_snapshot.get("period"))
    metric_result_period = _as_dict(metric_run.result_snapshot.get("period"))
    reasons: list[str] = []

    try:
        if content_hash(input_snapshot) != assessment.input_hash:
            reasons.append("assessment_input_hash_mismatch")
        if content_hash(result_snapshot) != assessment.result_hash:
            reasons.append("assessment_result_hash_mismatch")
        if content_hash(intake_version.snapshot) != intake_version.content_hash:
            reasons.append("intake_version_hash_mismatch")
        if content_hash(metric_run.input_snapshot) != metric_run.input_hash:
            reasons.append("metric_input_hash_mismatch")
        if content_hash(metric_run.result_snapshot) != metric_run.result_hash:
            reasons.append("metric_result_hash_mismatch")
    except (TypeError, ValueError):
        reasons.append("evidence_snapshot_unhashable")

    if (
        str(assessment.tenant_id) != str(tenant_id)
        or str(assessment.company_id) != str(company_id)
        or assessment.project_intake_id != intake.id
        or assessment.intake_version != intake_version.version
        or assessment.metric_run_id != metric_run.id
    ):
        reasons.append("assessment_scope_mismatch")
    if (
        str(scope_binding.get("tenant_id")) != str(tenant_id)
        or str(scope_binding.get("company_id")) != str(company_id)
        or intake_binding.get("id") != str(intake.id)
        or intake_binding.get("version") != intake_version.version
        or intake_binding.get("version_id") != str(intake_version.id)
        or intake_binding.get("version_hash") != intake_version.content_hash
        or intake_binding.get("snapshot") != intake_version.snapshot
    ):
        reasons.append("assessment_intake_binding_mismatch")
    expected_metric = {
        "id": str(metric_run.id),
        "catalog_version": metric_run.metric_catalog_version,
        "calculation_version": metric_run.calculation_version,
        "input_hash": metric_run.input_hash,
        "result_hash": metric_run.result_hash,
    }
    expected_metric_reference = {
        "metric_run_id": str(metric_run.id),
        "input_hash": metric_run.input_hash,
        "snapshot_type": "immutable_M5_metric_run_input_snapshot",
    }
    if (
        metric_binding != expected_metric
        or result_metric_binding != expected_metric
        or metric_input_reference != expected_metric_reference
        or metric_run.metric_catalog_version != assessment.metric_catalog_version
        or metric_run.calculation_version != assessment.metric_calculation_version
        or metric_run.input_hash != assessment.metric_input_hash
        or metric_run.result_hash != assessment.metric_result_hash
    ):
        reasons.append("assessment_metric_binding_mismatch")
    if (
        str(metric_input_scope.get("tenant_id")) != str(tenant_id)
        or str(metric_input_scope.get("company_id")) != str(company_id)
        or metric_input_period.get("start_week_date") != assessment.start_week_date.isoformat()
        or metric_input_period.get("week_count") != assessment.week_count
        or metric_result_period.get("start_week_date") != assessment.start_week_date.isoformat()
        or metric_result_period.get("week_count") != assessment.week_count
        or metric_run.start_week_date != assessment.start_week_date
        or metric_run.week_count != assessment.week_count
    ):
        reasons.append("metric_period_or_scope_mismatch")
    if (
        result_snapshot.get("assessment_calculation_version") != assessment.assessment_version
        or result_snapshot.get("capacity_status") != assessment.capacity_status
        or assessment.business_feasibility_status != "incomplete"
        or result_snapshot.get("business_feasibility_status") != "incomplete"
    ):
        reasons.append("assessment_result_contract_mismatch")

    binding = _assessment_binding(assessment, intake, intake_version, metric_run)
    evidence_hash = content_hash(binding)
    if reasons:
        if require_fresh:
            raise ApiError(
                409,
                "capacity_assessment_evidence_invalid",
                "The capacity assessment evidence cannot be verified.",
            )
        return _ResolvedEvidence(
            assessment=assessment,
            intake=intake,
            intake_version=intake_version,
            metric_run=metric_run,
            binding=binding,
            evidence_hash=evidence_hash,
            freshness="invalid",
            reasons=tuple(dict.fromkeys(reasons)),
        )

    stale_reasons: list[str] = []
    if intake.current_version != assessment.intake_version:
        stale_reasons.append("project_intake_has_a_newer_version")

    latest_assessment = session.scalar(
        select(CapacityAssessment)
        .where(
            CapacityAssessment.tenant_id == tenant_id,
            CapacityAssessment.company_id == company_id,
            CapacityAssessment.project_intake_id == intake.id,
        )
        .order_by(CapacityAssessment.created_at.desc(), CapacityAssessment.id.desc())
        .limit(1)
    )
    if latest_assessment is not None and latest_assessment.id != assessment.id:
        stale_reasons.append("a_newer_capacity_assessment_exists")

    latest_metric_run = session.scalar(
        select(MetricRun)
        .where(
            MetricRun.tenant_id == tenant_id,
            MetricRun.company_id == company_id,
            MetricRun.start_week_date == metric_run.start_week_date,
            MetricRun.week_count == metric_run.week_count,
        )
        .order_by(MetricRun.created_at.desc(), MetricRun.id.desc())
        .limit(1)
    )
    if latest_metric_run is not None and (
        latest_metric_run.input_hash != metric_run.input_hash
        or latest_metric_run.result_hash != metric_run.result_hash
    ):
        stale_reasons.append("a_newer_metric_run_has_different_evidence")

    if stale_reasons and require_fresh:
        raise ApiError(
            409,
            "capacity_assessment_stale",
            "Capacity evidence is stale; create a current M6 assessment before continuing.",
        )
    return _ResolvedEvidence(
        assessment=assessment,
        intake=intake,
        intake_version=intake_version,
        metric_run=metric_run,
        binding=binding,
        evidence_hash=evidence_hash,
        freshness="stale" if stale_reasons else "fresh",
        reasons=tuple(stale_reasons),
    )


def _decision_snapshot(
    *,
    decision_id: UUID,
    case_id: UUID,
    intake_id: UUID,
    version: int,
    supersedes_decision_id: UUID | None,
    information_response_to_id: UUID | None,
    created_by_user_id: UUID,
    created_at: datetime,
    outcome: str,
    rationale: str,
    conditions: list[str],
    requested_information: list[str],
    information_response: str | None,
    evidence_binding: dict[str, object],
    evidence_hash: str,
) -> dict[str, object]:
    return {
        "decision_schema_version": DECISION_SNAPSHOT_SCHEMA_VERSION,
        "decision": {
            "id": str(decision_id),
            "decision_case_id": str(case_id),
            "project_intake_id": str(intake_id),
            "version": version,
            "supersedes_decision_id": (
                str(supersedes_decision_id) if supersedes_decision_id is not None else None
            ),
            "information_response_to_id": (
                str(information_response_to_id) if information_response_to_id is not None else None
            ),
            "created_by_user_id": str(created_by_user_id),
            "created_at": _timestamp_text(created_at),
            "outcome": outcome,
            "rationale": rationale,
            "conditions": conditions,
            "requested_information": requested_information,
            "information_response": information_response,
        },
        "assessment_evidence": evidence_binding,
        "evidence_hash": evidence_hash,
    }


def _decision_binding_from_row(row: ProjectDecisionVersion) -> dict[str, object]:
    return {
        "schema_version": 1,
        "tenant_id": str(row.tenant_id),
        "company_id": str(row.company_id),
        "project_intake_id": str(row.project_intake_id),
        "intake_version": row.intake_version,
        "intake_version_hash": row.intake_version_hash,
        "assessment_id": str(row.assessment_id),
        "assessment_version": row.assessment_version,
        "assessment_input_hash": row.assessment_input_hash,
        "assessment_result_hash": row.assessment_result_hash,
        "capacity_status": row.capacity_status,
        "business_feasibility_status": row.business_feasibility_status,
        "metric_run_id": str(row.metric_run_id),
        "metric_catalog_version": row.metric_catalog_version,
        "metric_calculation_version": row.metric_calculation_version,
        "metric_input_hash": row.metric_input_hash,
        "metric_result_hash": row.metric_result_hash,
    }


def _decision_integrity_valid(row: ProjectDecisionVersion) -> bool:
    binding = _decision_binding_from_row(row)
    if row.evidence_hash != content_hash(binding):
        return False
    expected = _decision_snapshot(
        decision_id=row.id,
        case_id=row.decision_case_id,
        intake_id=row.project_intake_id,
        version=row.version,
        supersedes_decision_id=row.supersedes_decision_id,
        information_response_to_id=row.information_response_to_id,
        created_by_user_id=row.created_by_user_id,
        created_at=row.created_at,
        outcome=row.outcome,
        rationale=row.rationale,
        conditions=list(row.conditions),
        requested_information=list(row.requested_information),
        information_response=row.information_response,
        evidence_binding=binding,
        evidence_hash=row.evidence_hash,
    )
    return row.snapshot == expected and row.decision_hash == content_hash(expected)


def _approval_snapshot(
    *,
    approval_id: UUID,
    case_id: UUID,
    decision: ProjectDecisionVersion,
    outcome: str,
    rationale: str,
    approver_user_id: UUID,
    created_at: datetime,
) -> dict[str, object]:
    return {
        "approval_schema_version": 1,
        "approval": {
            "id": str(approval_id),
            "decision_case_id": str(case_id),
            "decision_version_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "evidence_hash": decision.evidence_hash,
            "outcome": outcome,
            "rationale": rationale,
            "approver_user_id": str(approver_user_id),
            "created_at": _timestamp_text(created_at),
        },
        "assessment_evidence": _decision_binding_from_row(decision),
    }


def _approval_integrity_valid(
    approval: ProjectDecisionApproval, decision: ProjectDecisionVersion
) -> bool:
    if (
        approval.decision_hash != decision.decision_hash
        or approval.evidence_hash != decision.evidence_hash
        or approval.assessment_id != decision.assessment_id
        or approval.assessment_input_hash != decision.assessment_input_hash
        or approval.assessment_result_hash != decision.assessment_result_hash
        or approval.metric_run_id != decision.metric_run_id
        or approval.metric_input_hash != decision.metric_input_hash
        or approval.metric_result_hash != decision.metric_result_hash
    ):
        return False
    expected = _approval_snapshot(
        approval_id=approval.id,
        case_id=approval.decision_case_id,
        decision=decision,
        outcome=approval.outcome,
        rationale=approval.rationale,
        approver_user_id=approval.approver_user_id,
        created_at=approval.created_at,
    )
    return approval.snapshot == expected and approval.approval_hash == content_hash(expected)


def _approval_response(row: ProjectDecisionApproval) -> ProjectDecisionApprovalResponse:
    return ProjectDecisionApprovalResponse(
        id=row.id,
        decision_case_id=row.decision_case_id,
        decision_version_id=row.decision_version_id,
        decision_hash=row.decision_hash,
        evidence_hash=row.evidence_hash,
        assessment_id=row.assessment_id,
        assessment_version=row.assessment_version,
        assessment_input_hash=row.assessment_input_hash,
        assessment_result_hash=row.assessment_result_hash,
        intake_version=row.intake_version,
        intake_version_hash=row.intake_version_hash,
        metric_run_id=row.metric_run_id,
        metric_catalog_version=row.metric_catalog_version,
        metric_calculation_version=row.metric_calculation_version,
        metric_input_hash=row.metric_input_hash,
        metric_result_hash=row.metric_result_hash,
        outcome=cast(ApprovalOutcome, row.outcome),
        rationale=row.rationale,
        approver_user_id=row.approver_user_id,
        approval_hash=row.approval_hash,
        created_at=row.created_at,
    )


def _decision_response(
    session: Session,
    row: ProjectDecisionVersion,
    case: ProjectDecisionCase,
    approval: ProjectDecisionApproval | None,
) -> ProjectDecisionResponse:
    integrity_valid = _decision_integrity_valid(row)
    try:
        evidence = _resolve_assessment_evidence(
            session,
            tenant_id=row.tenant_id,
            company_id=row.company_id,
            intake=_get_intake(session, row.tenant_id, row.company_id, row.project_intake_id),
            assessment_id=row.assessment_id,
            require_fresh=False,
        )
        if (
            evidence.evidence_hash != row.evidence_hash
            or evidence.binding != _decision_binding_from_row(row)
        ):
            integrity_valid = False
    except ApiError:
        evidence = None
        integrity_valid = False

    if approval is not None and not _approval_integrity_valid(approval, row):
        integrity_valid = False

    if not integrity_valid or evidence is None or evidence.freshness == "invalid":
        freshness = "invalid"
        reasons = ["decision_or_assessment_integrity_invalid"]
        if evidence is not None:
            reasons.extend(evidence.reasons)
    else:
        freshness = evidence.freshness
        reasons = list(evidence.reasons)

    status = "pending_approval" if approval is None else approval.outcome
    return ProjectDecisionResponse(
        id=row.id,
        decision_case_id=row.decision_case_id,
        project_intake_id=row.project_intake_id,
        tenant_id=row.tenant_id,
        company_id=row.company_id,
        version=row.version,
        supersedes_decision_id=row.supersedes_decision_id,
        information_response_to_id=row.information_response_to_id,
        assessment_id=row.assessment_id,
        assessment_version=row.assessment_version,
        assessment_input_hash=row.assessment_input_hash,
        assessment_result_hash=row.assessment_result_hash,
        intake_version=row.intake_version,
        intake_version_hash=row.intake_version_hash,
        metric_run_id=row.metric_run_id,
        metric_catalog_version=row.metric_catalog_version,
        metric_calculation_version=row.metric_calculation_version,
        metric_input_hash=row.metric_input_hash,
        metric_result_hash=row.metric_result_hash,
        capacity_status=cast(CapacityStatus, row.capacity_status),
        business_feasibility_status="incomplete",
        workflow_scope="capacity_stage",
        outcome=cast(DecisionOutcome, row.outcome),
        rationale=row.rationale,
        conditions=list(row.conditions),
        requested_information=list(row.requested_information),
        information_response=row.information_response,
        created_by_user_id=row.created_by_user_id,
        evidence_hash=row.evidence_hash,
        decision_hash=row.decision_hash,
        status=cast(DecisionStatus, status),
        is_current=case.current_decision_id == row.id,
        evidence_freshness=cast(FreshnessStatus, freshness),
        staleness_reasons=reasons,
        created_at=row.created_at,
        approval=_approval_response(approval) if approval is not None else None,
    )


def _validate_outcome_against_assessment(
    body: ProjectDecisionCreate, evidence: _ResolvedEvidence
) -> None:
    if body.outcome == "proceed_to_financial_evaluation":
        if evidence.assessment.capacity_status not in {"feasible", "feasible_with_conditions"}:
            raise ApiError(
                422,
                "decision_outcome_not_supported",
                "Only a feasible or conditionally feasible capacity assessment may proceed "
                "to financial evaluation.",
            )
        if (
            evidence.assessment.capacity_status == "feasible_with_conditions"
            and not body.conditions
        ):
            raise ApiError(
                422,
                "decision_conditions_required",
                "Conditions from a conditional capacity assessment must be recorded.",
            )
    elif (
        body.outcome == "capacity_declined"
        and evidence.assessment.capacity_status != "not_feasible"
    ):
        raise ApiError(
            422,
            "decision_outcome_not_supported",
            "capacity_declined is available only for a not_feasible capacity assessment.",
        )


def _new_decision_row(
    *,
    decision_id: UUID | None = None,
    tenant_id: UUID,
    company_id: UUID,
    case_id: UUID,
    intake_id: UUID,
    version: int,
    supersedes_decision_id: UUID | None,
    information_response_to_id: UUID | None,
    user_id: UUID,
    created_at: datetime,
    body: ProjectDecisionCreate,
    evidence: _ResolvedEvidence,
) -> ProjectDecisionVersion:
    assessment = evidence.assessment
    metric_run = evidence.metric_run
    decision_id = decision_id or uuid4()
    snapshot = _decision_snapshot(
        decision_id=decision_id,
        case_id=case_id,
        intake_id=intake_id,
        version=version,
        supersedes_decision_id=supersedes_decision_id,
        information_response_to_id=information_response_to_id,
        created_by_user_id=user_id,
        created_at=created_at,
        outcome=body.outcome,
        rationale=body.rationale,
        conditions=list(body.conditions),
        requested_information=list(body.requested_information),
        information_response=body.information_response,
        evidence_binding=evidence.binding,
        evidence_hash=evidence.evidence_hash,
    )
    return ProjectDecisionVersion(
        id=decision_id,
        tenant_id=tenant_id,
        company_id=company_id,
        decision_case_id=case_id,
        project_intake_id=intake_id,
        version=version,
        supersedes_decision_id=supersedes_decision_id,
        information_response_to_id=information_response_to_id,
        assessment_id=assessment.id,
        intake_version=assessment.intake_version,
        intake_version_hash=evidence.intake_version.content_hash,
        assessment_version=assessment.assessment_version,
        assessment_input_hash=assessment.input_hash,
        assessment_result_hash=assessment.result_hash,
        capacity_status=assessment.capacity_status,
        business_feasibility_status="incomplete",
        metric_run_id=metric_run.id,
        metric_catalog_version=metric_run.metric_catalog_version,
        metric_calculation_version=metric_run.calculation_version,
        metric_input_hash=metric_run.input_hash,
        metric_result_hash=metric_run.result_hash,
        outcome=body.outcome,
        rationale=body.rationale,
        conditions=list(body.conditions),
        requested_information=list(body.requested_information),
        information_response=body.information_response,
        created_by_user_id=user_id,
        evidence_hash=evidence.evidence_hash,
        decision_hash=content_hash(snapshot),
        snapshot=snapshot,
        created_at=created_at,
    )


def create_project_decision(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    body: ProjectDecisionCreate,
    request_id: UUID,
    now: datetime,
) -> tuple[ProjectDecisionCase, ProjectDecisionVersion, ProjectDecisionApproval | None]:
    access = require_company_context_access(
        session, user, tenant_id, "decision:write", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id, lock=True)
    evidence = _resolve_assessment_evidence(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake=intake,
        assessment_id=body.assessment_id,
        require_fresh=True,
    )
    _validate_outcome_against_assessment(body, evidence)

    case = _get_case(
        session, tenant_id=tenant_id, company_id=access.company.id, intake_id=intake.id, lock=True
    )
    previous: ProjectDecisionVersion | None = None
    if case is None:
        if body.expected_version is not None:
            raise ApiError(
                409,
                "decision_version_conflict",
                "No decision history exists; submit without expected_version.",
            )
        case_id = uuid4()
        decision_id = uuid4()
        case = ProjectDecisionCase(
            id=case_id,
            tenant_id=tenant_id,
            company_id=access.company.id,
            project_intake_id=intake.id,
            created_by_user_id=user.id,
            current_version=1,
            current_decision_id=decision_id,
            created_at=now,
            updated_at=now,
        )
        session.add(case)
        session.flush()
        version_number = 1
        supersedes_id = None
        response_to_id = None
    else:
        if body.expected_version is None or body.expected_version != case.current_version:
            raise ApiError(
                409,
                "decision_version_conflict",
                "The decision history changed; reload it and submit against the current version.",
            )
        previous = _get_decision(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            intake_id=intake.id,
            decision_id=case.current_decision_id,
        )
        if previous.decision_case_id != case.id or not _decision_integrity_valid(previous):
            raise ApiError(
                409,
                "decision_history_invalid",
                "The current decision history cannot be verified safely.",
            )
        if previous.outcome == "request_information":
            if body.information_response is None:
                raise ApiError(
                    422,
                    "information_response_required",
                    "A response is required before superseding an information request.",
                )
            response_to_id = previous.id
        else:
            if body.information_response is not None:
                raise ApiError(
                    422,
                    "information_response_not_expected",
                    "No open information request exists for this decision version.",
                )
            response_to_id = None
        version_number = case.current_version + 1
        supersedes_id = previous.id

    decision = _new_decision_row(
        decision_id=case.current_decision_id if previous is None else None,
        tenant_id=tenant_id,
        company_id=access.company.id,
        case_id=case.id,
        intake_id=intake.id,
        version=version_number,
        supersedes_decision_id=supersedes_id,
        information_response_to_id=response_to_id,
        user_id=user.id,
        created_at=now,
        body=body,
        evidence=evidence,
    )
    if case.current_version == 1 and previous is None:
        # The first case row points to its first version before the deferred composite FK
        # is checked at commit. This also allows the database trigger to validate the stream.
        if case.current_decision_id != decision.id:
            raise RuntimeError("new decision case pointer does not match its first version")
    else:
        session.add(decision)
        session.flush()
        case.current_version = version_number
        case.current_decision_id = decision.id
        case.updated_at = now
    if previous is None:
        session.add(decision)
        session.flush()

    record_event(
        session,
        "project_decision.submitted" if previous is None else "project_decision.superseded",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project_decision_version",
        target_id=decision.id,
        request_id=request_id,
        details={
            "decision_case_id": str(case.id),
            "project_intake_id": str(intake.id),
            "version": version_number,
            "outcome": decision.outcome,
            "capacity_status": decision.capacity_status,
            "decision_hash": decision.decision_hash,
            "evidence_hash": decision.evidence_hash,
        },
    )
    return case, decision, None


def _approval_from_decision(
    *,
    decision: ProjectDecisionVersion,
    outcome: str,
    rationale: str,
    approver_user_id: UUID,
    now: datetime,
) -> ProjectDecisionApproval:
    approval_id = uuid4()
    snapshot = _approval_snapshot(
        approval_id=approval_id,
        case_id=decision.decision_case_id,
        decision=decision,
        outcome=outcome,
        rationale=rationale,
        approver_user_id=approver_user_id,
        created_at=now,
    )
    return ProjectDecisionApproval(
        id=approval_id,
        tenant_id=decision.tenant_id,
        company_id=decision.company_id,
        decision_case_id=decision.decision_case_id,
        decision_version_id=decision.id,
        project_intake_id=decision.project_intake_id,
        assessment_id=decision.assessment_id,
        intake_version=decision.intake_version,
        intake_version_hash=decision.intake_version_hash,
        assessment_version=decision.assessment_version,
        assessment_input_hash=decision.assessment_input_hash,
        assessment_result_hash=decision.assessment_result_hash,
        metric_run_id=decision.metric_run_id,
        metric_catalog_version=decision.metric_catalog_version,
        metric_calculation_version=decision.metric_calculation_version,
        metric_input_hash=decision.metric_input_hash,
        metric_result_hash=decision.metric_result_hash,
        decision_hash=decision.decision_hash,
        evidence_hash=decision.evidence_hash,
        outcome=outcome,
        rationale=rationale,
        approver_user_id=approver_user_id,
        approval_hash=content_hash(snapshot),
        snapshot=snapshot,
        created_at=now,
    )


def approve_project_decision(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    decision_id: UUID,
    body: ProjectDecisionApprovalCreate,
    request_id: UUID,
    now: datetime,
) -> tuple[ProjectDecisionCase, ProjectDecisionVersion, ProjectDecisionApproval]:
    access = require_company_context_access(
        session, user, tenant_id, "decision:approve", request_id=request_id
    )
    intake = _get_intake(session, tenant_id, access.company.id, intake_id, lock=True)
    case = _get_case(
        session, tenant_id=tenant_id, company_id=access.company.id, intake_id=intake.id, lock=True
    )
    if case is None:
        raise _decision_not_found()
    decision = _get_decision(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        decision_id=decision_id,
    )
    if decision.decision_case_id != case.id:
        raise _decision_not_found()
    if case.current_decision_id != decision.id:
        raise ApiError(
            409,
            "decision_superseded",
            "Only the current decision version may be reviewed.",
        )
    if decision.created_by_user_id == user.id:
        record_event(
            session,
            "project_decision.approval_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="project_decision_version",
            target_id=decision.id,
            request_id=request_id,
            outcome="denied",
            details={"reason": "separation_of_duties", "version": decision.version},
        )
        raise CommitAndRaise(
            ApiError(
                403,
                "decision_separation_of_duties",
                "A different authorized person must review this decision version.",
            )
        )
    if not _decision_integrity_valid(decision):
        record_event(
            session,
            "project_decision.approval_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="project_decision_version",
            target_id=decision.id,
            request_id=request_id,
            outcome="failure",
            details={"reason": "decision_integrity_invalid", "version": decision.version},
        )
        raise CommitAndRaise(
            ApiError(
                409,
                "decision_evidence_invalid",
                "The submitted decision hash or evidence binding cannot be verified.",
            )
        )
    if (
        body.expected_decision_hash != decision.decision_hash
        or body.expected_evidence_hash != decision.evidence_hash
    ):
        raise ApiError(
            409,
            "decision_version_conflict",
            "The decision or evidence changed; reload the exact version before review.",
        )

    existing = session.scalar(
        select(ProjectDecisionApproval).where(
            ProjectDecisionApproval.tenant_id == tenant_id,
            ProjectDecisionApproval.company_id == access.company.id,
            ProjectDecisionApproval.decision_case_id == case.id,
            ProjectDecisionApproval.decision_version_id == decision.id,
        )
    )
    if existing is not None:
        raise ApiError(
            409,
            "decision_already_reviewed",
            "This immutable decision version already has a review record.",
        )

    try:
        evidence = _resolve_assessment_evidence(
            session,
            tenant_id=tenant_id,
            company_id=access.company.id,
            intake=intake,
            assessment_id=decision.assessment_id,
            require_fresh=False,
        )
    except ApiError:
        evidence = None
    if (
        evidence is None
        or evidence.freshness != "fresh"
        or evidence.evidence_hash != decision.evidence_hash
        or evidence.binding != _decision_binding_from_row(decision)
    ):
        reason = "assessment_evidence_invalid" if evidence is None else evidence.freshness
        record_event(
            session,
            "project_decision.approval_blocked",
            tenant_id=tenant_id,
            actor_user_id=user.id,
            target_type="project_decision_version",
            target_id=decision.id,
            request_id=request_id,
            outcome="failure",
            details={
                "reason": reason,
                "staleness_reasons": list(evidence.reasons) if evidence is not None else [],
                "version": decision.version,
                "evidence_hash": decision.evidence_hash,
            },
        )
        if evidence is not None and evidence.freshness == "stale":
            error = ApiError(
                409,
                "capacity_assessment_stale",
                "Capacity evidence is stale; create a current M6 assessment before approval.",
            )
        else:
            error = ApiError(
                409,
                "decision_evidence_invalid",
                "The exact assessment and evidence bound to this decision cannot be verified.",
            )
        raise CommitAndRaise(error)

    approval = _approval_from_decision(
        decision=decision,
        outcome=body.outcome,
        rationale=body.rationale,
        approver_user_id=user.id,
        now=now,
    )
    session.add(approval)
    session.flush()
    record_event(
        session,
        "project_decision.approved"
        if body.outcome == "approved"
        else "project_decision.changes_requested",
        tenant_id=tenant_id,
        actor_user_id=user.id,
        target_type="project_decision_approval",
        target_id=approval.id,
        request_id=request_id,
        details={
            "decision_case_id": str(case.id),
            "decision_version_id": str(decision.id),
            "decision_hash": decision.decision_hash,
            "evidence_hash": decision.evidence_hash,
            "outcome": approval.outcome,
        },
    )
    return case, decision, approval


def _approval_for_decision(
    session: Session,
    *,
    tenant_id: UUID,
    company_id: UUID,
    case_id: UUID,
    decision_id: UUID,
) -> ProjectDecisionApproval | None:
    return session.scalar(
        select(ProjectDecisionApproval).where(
            ProjectDecisionApproval.tenant_id == tenant_id,
            ProjectDecisionApproval.company_id == company_id,
            ProjectDecisionApproval.decision_case_id == case_id,
            ProjectDecisionApproval.decision_version_id == decision_id,
        )
    )


def list_project_decisions(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    *,
    limit: int,
    offset: int,
) -> ProjectDecisionPage:
    if not 1 <= limit <= MAX_HISTORY_PAGE_SIZE or not 0 <= offset <= MAX_HISTORY_OFFSET:
        raise ApiError(422, "invalid_page", "The requested page is outside supported limits.")
    access = require_company_context_access(session, user, tenant_id, "decision:read")
    # Decision history binds M5/M6 capacity evidence, so decision permission alone
    # must not expose workforce-sensitive results.
    require_company_context_access(session, user, tenant_id, "workforce:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    case = _get_case(
        session, tenant_id=tenant_id, company_id=access.company.id, intake_id=intake.id
    )
    if case is None:
        return ProjectDecisionPage(
            items=[], total=0, limit=limit, offset=offset, current_version=None
        )

    base_query = select(ProjectDecisionVersion).where(
        ProjectDecisionVersion.tenant_id == tenant_id,
        ProjectDecisionVersion.company_id == access.company.id,
        ProjectDecisionVersion.decision_case_id == case.id,
        ProjectDecisionVersion.project_intake_id == intake.id,
    )
    total = session.scalar(select(func.count()).select_from(base_query.subquery())) or 0
    decisions = list(
        session.scalars(
            base_query.order_by(ProjectDecisionVersion.version.desc()).limit(limit).offset(offset)
        )
    )
    approvals: dict[UUID, ProjectDecisionApproval] = {}
    if decisions:
        approval_rows = session.scalars(
            select(ProjectDecisionApproval).where(
                ProjectDecisionApproval.tenant_id == tenant_id,
                ProjectDecisionApproval.company_id == access.company.id,
                ProjectDecisionApproval.decision_case_id == case.id,
                ProjectDecisionApproval.decision_version_id.in_([row.id for row in decisions]),
            )
        )
        approvals = {row.decision_version_id: row for row in approval_rows}
    return ProjectDecisionPage(
        items=[_decision_response(session, row, case, approvals.get(row.id)) for row in decisions],
        total=total,
        limit=limit,
        offset=offset,
        current_version=case.current_version,
    )


def get_project_decision(
    session: Session,
    user: UserAccount,
    tenant_id: UUID,
    intake_id: UUID,
    decision_id: UUID,
) -> ProjectDecisionResponse:
    access = require_company_context_access(session, user, tenant_id, "decision:read")
    # Exact-version reads disclose the same linked M5/M6 workforce evidence as history.
    require_company_context_access(session, user, tenant_id, "workforce:read")
    intake = _get_intake(session, tenant_id, access.company.id, intake_id)
    case = _get_case(
        session, tenant_id=tenant_id, company_id=access.company.id, intake_id=intake.id
    )
    if case is None:
        raise _decision_not_found()
    decision = _get_decision(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        intake_id=intake.id,
        decision_id=decision_id,
    )
    if decision.decision_case_id != case.id:
        raise _decision_not_found()
    approval = _approval_for_decision(
        session,
        tenant_id=tenant_id,
        company_id=access.company.id,
        case_id=case.id,
        decision_id=decision.id,
    )
    return _decision_response(session, decision, case, approval)
