from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, StringConstraints, model_validator

from businessflow.schemas import StrictModel

DecisionOutcome = Literal[
    "proceed_to_financial_evaluation",
    "hold",
    "request_information",
    "capacity_declined",
]
ApprovalOutcome = Literal["approved", "changes_requested"]
CapacityStatus = Literal["feasible", "feasible_with_conditions", "not_feasible", "incomplete"]
FreshnessStatus = Literal["fresh", "stale", "invalid"]
DecisionStatus = Literal["pending_approval", "approved", "changes_requested"]

DecisionRationale = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=10, max_length=4000),
]
DecisionDetail = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
]
InformationResponse = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
]
Hash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


class ProjectDecisionCreate(StrictModel):
    assessment_id: UUID
    expected_version: Annotated[int, Field(ge=1, le=1_000_000)] | None = None
    outcome: DecisionOutcome
    rationale: DecisionRationale
    conditions: Annotated[list[DecisionDetail], Field(max_length=30)] = Field(default_factory=list)
    requested_information: Annotated[list[DecisionDetail], Field(max_length=30)] = Field(
        default_factory=list
    )
    information_response: InformationResponse | None = None

    @model_validator(mode="after")
    def validate_outcome_details(self) -> ProjectDecisionCreate:
        if self.outcome == "hold" and not self.conditions:
            raise ValueError("a hold requires at least one condition for resuming review")
        if self.outcome == "request_information" and not self.requested_information:
            raise ValueError("request_information requires at least one requested item")
        if self.outcome != "request_information" and self.requested_information:
            raise ValueError("requested_information is only valid for request_information")
        return self


class ProjectDecisionApprovalCreate(StrictModel):
    expected_decision_hash: Hash
    expected_evidence_hash: Hash
    outcome: ApprovalOutcome
    rationale: DecisionRationale


class ProjectDecisionApprovalResponse(StrictModel):
    id: UUID
    decision_case_id: UUID
    decision_version_id: UUID
    decision_hash: str
    evidence_hash: str
    assessment_id: UUID
    assessment_version: str
    assessment_input_hash: str
    assessment_result_hash: str
    intake_version: int
    intake_version_hash: str
    metric_run_id: UUID
    metric_catalog_version: str
    metric_calculation_version: str
    metric_input_hash: str
    metric_result_hash: str
    outcome: ApprovalOutcome
    rationale: str
    approver_user_id: UUID
    approval_hash: str
    created_at: datetime


class ProjectDecisionResponse(StrictModel):
    id: UUID
    decision_case_id: UUID
    project_intake_id: UUID
    tenant_id: UUID
    company_id: UUID
    version: int
    supersedes_decision_id: UUID | None
    information_response_to_id: UUID | None
    assessment_id: UUID
    assessment_version: str
    assessment_input_hash: str
    assessment_result_hash: str
    intake_version: int
    intake_version_hash: str
    metric_run_id: UUID
    metric_catalog_version: str
    metric_calculation_version: str
    metric_input_hash: str
    metric_result_hash: str
    capacity_status: CapacityStatus
    business_feasibility_status: Literal["incomplete"]
    workflow_scope: Literal["capacity_stage"]
    outcome: DecisionOutcome
    rationale: str
    conditions: list[str]
    requested_information: list[str]
    information_response: str | None
    created_by_user_id: UUID
    evidence_hash: str
    decision_hash: str
    status: DecisionStatus
    is_current: bool
    evidence_freshness: FreshnessStatus
    staleness_reasons: list[str]
    created_at: datetime
    approval: ProjectDecisionApprovalResponse | None


class ProjectDecisionPage(StrictModel):
    items: list[ProjectDecisionResponse]
    total: int
    limit: int
    offset: int
    current_version: int | None
