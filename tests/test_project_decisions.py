from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from starlette.testclient import TestClient

from businessflow.database import set_user_context
from businessflow.models import UserAccount
from businessflow.project_decisions.schemas import ProjectDecisionCreate
from businessflow.project_decisions.service import create_project_decision
from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK = "2026-11-16"


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _create_employee_and_capacity(
    client: TestClient,
    base: str,
    headers: dict[str, str],
    code: str,
    weekly_hours: str = "40.00",
) -> dict:
    response = client.post(
        f"{base}/employees",
        json={
            "employee_code": code,
            "full_name": f"{code} Test",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    employee = response.json()
    capacity = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": weekly_hours,
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
        },
        headers=headers,
    )
    assert capacity.status_code == 201, capacity.text
    return employee


def _create_intake(
    client: TestClient,
    base: str,
    headers: dict[str, str],
    code: str,
    demand: str = "20.00",
) -> dict:
    response = client.post(
        f"{base}/project-intakes",
        json={
            "project_code": code,
            "name": f"Proposed project {code}",
            "start_week_date": WEEK,
            "week_count": 1,
            "total_demand_hours": demand,
            "demand_unit": "team_hours",
            "weekly_demand": [{"week_start_date": WEEK, "hours": demand}],
            "demand_distribution_confirmed": True,
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _assess(
    client: TestClient,
    base: str,
    headers: dict[str, str],
    intake_id: str,
) -> dict:
    response = client.post(
        f"{base}/project-intakes/{intake_id}/assessments",
        json={
            "assumptions": {
                "capacity_data_current_confirmed": True,
                "recorded_commitments_complete_confirmed": True,
            }
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _setup(
    client: TestClient,
    slug: str,
    *,
    demand: str = "20.00",
    capacity: str = "40.00",
) -> tuple[dict, dict, str, dict[str, str], dict, dict]:
    owner = register_user(client, f"{slug}-owner@example.com", display_name=f"{slug} Owner")
    tenant = create_tenant(
        client,
        owner,
        name=f"{slug} Tenant",
        company_name=f"{slug} Company",
        key=f"{slug}-onboard-key-0001",
    )
    base = _base(tenant["id"])
    headers = auth_headers(owner)
    _create_employee_and_capacity(client, base, headers, f"{slug.upper()}-EMP", capacity)
    intake = _create_intake(client, base, headers, f"{slug.upper()}-PROPOSAL", demand)
    assessment = _assess(client, base, headers, intake["id"])
    return owner, tenant, base, headers, intake, assessment


def _decision_body(
    assessment: dict,
    outcome: str = "proceed_to_financial_evaluation",
    *,
    expected_version: int | None = None,
    **overrides: object,
) -> dict[str, object]:
    body: dict[str, object] = {
        "assessment_id": assessment["id"],
        "outcome": outcome,
        "rationale": "A human reviewed the linked weekly capacity evidence.",
    }
    if expected_version is not None:
        body["expected_version"] = expected_version
    body.update(overrides)
    return body


def _submit(
    client: TestClient,
    base: str,
    intake_id: str,
    headers: dict[str, str],
    payload: dict[str, object],
):
    return client.post(
        f"{base}/project-intakes/{intake_id}/decisions", json=payload, headers=headers
    )


def _approve(
    client: TestClient,
    base: str,
    intake_id: str,
    decision: dict,
    headers: dict[str, str],
    *,
    outcome: str = "approved",
    rationale: str = "Reviewed this exact decision version and evidence.",
):
    return client.post(
        f"{base}/project-intakes/{intake_id}/decisions/{decision['id']}/approval",
        json={
            "expected_decision_hash": decision["decision_hash"],
            "expected_evidence_hash": decision["evidence_hash"],
            "outcome": outcome,
            "rationale": rationale,
        },
        headers=headers,
    )


def test_decision_approval_binds_exact_evidence_and_enforces_separation_of_duties(
    client, admin_engine
):
    owner, tenant, base, owner_headers, intake, assessment = _setup(client, "m7approve")
    admin = register_user(client, "m7approve-admin@example.com", display_name="Review Admin")
    create_member(client, owner, tenant["id"], "m7approve-admin@example.com", "admin")

    created = _submit(
        client,
        base,
        intake["id"],
        owner_headers,
        _decision_body(assessment),
    )
    assert created.status_code == 201, created.text
    decision = created.json()
    assert decision["status"] == "pending_approval"
    assert decision["is_current"] is True
    assert decision["evidence_freshness"] == "fresh"
    assert decision["assessment_id"] == assessment["id"]
    assert decision["assessment_input_hash"] == assessment["input_hash"]
    assert decision["assessment_result_hash"] == assessment["result_hash"]
    assert decision["metric_run_id"] == assessment["metric_run_id"]
    assert decision["metric_input_hash"] == assessment["metric_input_hash"]
    assert decision["metric_result_hash"] == assessment["metric_result_hash"]
    assert decision["business_feasibility_status"] == "incomplete"
    assert decision["workflow_scope"] == "capacity_stage"

    self_review = _approve(client, base, intake["id"], decision, owner_headers)
    assert self_review.status_code == 403
    assert self_review.json()["error"]["code"] == "decision_separation_of_duties"

    wrong_hash = client.post(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}/approval",
        json={
            "expected_decision_hash": "0" * 64,
            "expected_evidence_hash": decision["evidence_hash"],
            "outcome": "approved",
            "rationale": "This request used a stale decision hash.",
        },
        headers=auth_headers(admin),
    )
    assert wrong_hash.status_code == 409
    assert wrong_hash.json()["error"]["code"] == "decision_version_conflict"

    approved = _approve(client, base, intake["id"], decision, auth_headers(admin))
    assert approved.status_code == 201, approved.text
    approval = approved.json()
    assert approval["decision_version_id"] == decision["id"]
    assert approval["decision_hash"] == decision["decision_hash"]
    assert approval["evidence_hash"] == decision["evidence_hash"]
    assert approval["assessment_id"] == assessment["id"]
    assert approval["assessment_input_hash"] == assessment["input_hash"]
    assert approval["assessment_result_hash"] == assessment["result_hash"]
    assert approval["metric_run_id"] == assessment["metric_run_id"]
    assert approval["metric_input_hash"] == assessment["metric_input_hash"]
    assert approval["metric_result_hash"] == assessment["metric_result_hash"]

    detail = client.get(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}",
        headers=owner_headers,
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["status"] == "approved"
    assert detail.json()["approval"]["approval_hash"] == approval["approval_hash"]
    assert detail.json()["evidence_freshness"] == "fresh"

    duplicate = _approve(client, base, intake["id"], decision, auth_headers(admin))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "decision_already_reviewed"

    audit = client.get(f"{base}/audit-events", headers=owner_headers)
    assert audit.status_code == 200, audit.text
    event_types = {item["event_type"] for item in audit.json()["items"]}
    assert {
        "project_decision.submitted",
        "project_decision.approval_blocked",
        "project_decision.approved",
    } <= event_types
    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM project_decision_approvals "
                    "WHERE decision_version_id = :decision_id"
                ),
                {"decision_id": decision["id"]},
            )
            == 1
        )


def test_information_response_and_hold_resume_append_versions_without_rewriting_history(client):
    _owner, _tenant, base, headers, intake, assessment = _setup(client, "m7resume")
    decision_url = f"{base}/project-intakes/{intake['id']}/decisions"

    missing_request = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, "request_information"),
    )
    assert missing_request.status_code == 422

    requested = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(
            assessment,
            "request_information",
            requested_information=["Confirm the requested delivery start date."],
        ),
    )
    assert requested.status_code == 201, requested.text
    first = requested.json()
    assert first["version"] == 1
    assert first["requested_information"] == ["Confirm the requested delivery start date."]

    no_response = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(
            assessment,
            "hold",
            expected_version=1,
            conditions=["Review the sponsor response before resuming."],
        ),
    )
    assert no_response.status_code == 422
    assert no_response.json()["error"]["code"] == "information_response_required"

    held = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(
            assessment,
            "hold",
            expected_version=1,
            conditions=["Review the sponsor response before resuming."],
            information_response="The sponsor confirmed the requested delivery start date.",
        ),
    )
    assert held.status_code == 201, held.text
    second = held.json()
    assert second["version"] == 2
    assert second["supersedes_decision_id"] == first["id"]
    assert second["information_response_to_id"] == first["id"]
    assert second["information_response"]

    resumed = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, expected_version=2),
    )
    assert resumed.status_code == 201, resumed.text
    third = resumed.json()
    assert third["version"] == 3
    assert third["supersedes_decision_id"] == second["id"]
    assert third["information_response_to_id"] is None
    assert third["is_current"] is True

    history = client.get(decision_url, headers=headers)
    assert history.status_code == 200, history.text
    payload = history.json()
    assert payload["current_version"] == 3
    assert [item["version"] for item in payload["items"]] == [3, 2, 1]
    assert [item["is_current"] for item in payload["items"]] == [True, False, False]
    assert [item["id"] for item in payload["items"]][-1] == first["id"]


def test_capacity_declined_is_capacity_stage_only_and_proceed_requires_supported_capacity(client):
    _owner, _tenant, base, headers, intake, assessment = _setup(
        client, "m7decline", demand="50.00", capacity="40.00"
    )
    assert assessment["capacity_status"] == "not_feasible"

    unsupported = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, "proceed_to_financial_evaluation"),
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "decision_outcome_not_supported"

    declined = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, "capacity_declined"),
    )
    assert declined.status_code == 201, declined.text
    body = declined.json()
    assert body["outcome"] == "capacity_declined"
    assert body["capacity_status"] == "not_feasible"
    assert body["workflow_scope"] == "capacity_stage"
    assert body["business_feasibility_status"] == "incomplete"

    final_commercial_rejection = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, "commercially_rejected", expected_version=1),
    )
    assert final_commercial_rejection.status_code == 422


def test_newer_capacity_assessment_marks_old_history_stale_and_blocks_approval(client):
    owner, tenant, base, owner_headers, intake, assessment = _setup(client, "m7stale")
    admin = register_user(client, "m7stale-admin@example.com", display_name="Review Admin")
    create_member(client, owner, tenant["id"], "m7stale-admin@example.com", "admin")
    first = _submit(client, base, intake["id"], owner_headers, _decision_body(assessment)).json()

    newer_assessment = _assess(client, base, owner_headers, intake["id"])
    assert newer_assessment["id"] != assessment["id"]
    blocked = _approve(client, base, intake["id"], first, auth_headers(admin))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "capacity_assessment_stale"

    stale_detail = client.get(
        f"{base}/project-intakes/{intake['id']}/decisions/{first['id']}",
        headers=owner_headers,
    )
    assert stale_detail.status_code == 200
    assert stale_detail.json()["evidence_freshness"] == "stale"
    assert "a_newer_capacity_assessment_exists" in stale_detail.json()["staleness_reasons"]
    assert stale_detail.json()["approval"] is None

    refreshed = _submit(
        client,
        base,
        intake["id"],
        owner_headers,
        _decision_body(newer_assessment, expected_version=1),
    )
    assert refreshed.status_code == 201, refreshed.text
    assert refreshed.json()["version"] == 2
    assert refreshed.json()["supersedes_decision_id"] == first["id"]
    assert refreshed.json()["evidence_freshness"] == "fresh"
    audit = client.get(f"{base}/audit-events", headers=owner_headers)
    assert "project_decision.approval_blocked" in {
        item["event_type"] for item in audit.json()["items"]
    }


def test_intake_and_newer_metric_evidence_are_detected_as_stale(client):
    owner, tenant, base, headers, intake, assessment = _setup(client, "m7metricstale")
    submitted = _submit(client, base, intake["id"], headers, _decision_body(assessment))
    assert submitted.status_code == 201, submitted.text
    decision = submitted.json()
    employee_id = client.get(f"{base}/employees", headers=headers).json()["items"][0]["id"]
    changed_capacity = client.post(
        f"{base}/employees/{employee_id}/weekly-capacity/{WEEK}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": "35.00",
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
        },
        headers=headers,
    )
    assert changed_capacity.status_code == 201, changed_capacity.text
    latest_metric = client.post(
        f"{base}/metrics/runs",
        json={"start_week_date": WEEK, "week_count": 1},
        headers=headers,
    )
    assert latest_metric.status_code == 201, latest_metric.text
    assert latest_metric.json()["input_hash"] != assessment["metric_input_hash"]

    admin = register_user(client, "m7metricstale-admin@example.com")
    create_member(client, owner, tenant["id"], "m7metricstale-admin@example.com", "admin")
    blocked = _approve(client, base, intake["id"], decision, auth_headers(admin))
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "capacity_assessment_stale"
    detail = client.get(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}", headers=headers
    )
    assert detail.status_code == 200
    assert detail.json()["evidence_freshness"] == "stale"
    assert "a_newer_metric_run_has_different_evidence" in detail.json()["staleness_reasons"]

    intake_data = dict(intake["data"])
    intake_data["notes"] = "Updated after the previous assessment."
    updated = client.put(
        f"{base}/project-intakes/{intake['id']}",
        json={"expected_version": 1, "data": intake_data},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    old_evidence = _submit(
        client,
        base,
        intake["id"],
        headers,
        _decision_body(assessment, expected_version=1),
    )
    assert old_evidence.status_code == 409
    assert old_evidence.json()["error"]["code"] == "capacity_assessment_stale"


def test_tenant_company_authorization_and_member_read_policy(client):
    owner, tenant, base, owner_headers, intake, assessment = _setup(client, "m7authz")
    decision = _submit(client, base, intake["id"], owner_headers, _decision_body(assessment)).json()
    member = register_user(client, "m7authz-member@example.com")
    create_member(client, owner, tenant["id"], "m7authz-member@example.com", "member")
    member_headers = auth_headers(member)

    history = client.get(f"{base}/project-intakes/{intake['id']}/decisions", headers=member_headers)
    assert history.status_code == 403
    denied_member_detail = client.get(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}",
        headers=member_headers,
    )
    assert denied_member_detail.status_code == 403
    denied_write = _submit(
        client,
        base,
        intake["id"],
        member_headers,
        _decision_body(assessment, expected_version=1),
    )
    assert denied_write.status_code == 403

    denied_approve = _approve(client, base, intake["id"], decision, member_headers)
    assert denied_approve.status_code == 403

    other_owner = register_user(client, "m7authz-other@example.com")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other M7 Tenant",
        company_name="Other M7 Company",
        key="m7authz-other-tenant-001",
    )
    cross_scope = client.get(
        f"/api/v1/tenants/{other_tenant['id']}/project-intakes/{intake['id']}/decisions",
        headers=auth_headers(other_owner),
    )
    assert cross_scope.status_code == 404

    client_supplied_company = _submit(
        client,
        base,
        intake["id"],
        owner_headers,
        {**_decision_body(assessment, expected_version=1), "company_id": tenant["company"]["id"]},
    )
    assert client_supplied_company.status_code == 422


def test_decision_rls_fk_immutability_and_atomic_rollback(
    client, app, runtime_engine, admin_engine
):
    _owner, tenant, base, headers, intake, assessment = _setup(client, "m7db")
    decision = _submit(client, base, intake["id"], headers, _decision_body(assessment)).json()
    other_owner, _other_tenant, other_base, other_headers, other_intake, _other_assessment = _setup(
        client, "m7dbother"
    )

    with runtime_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM project_decision_cases")) == 0
        assert connection.scalar(text("SELECT count(*) FROM project_decision_versions")) == 0
        assert connection.scalar(text("SELECT count(*) FROM project_decision_approvals")) == 0

    with admin_engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity "
                "FROM pg_class AS c WHERE c.relname IN "
                "('project_decision_cases', 'project_decision_versions', "
                "'project_decision_approvals') ORDER BY c.relname"
            )
        ).all()
        assert len(rows) == 3
        assert all(row.relrowsecurity and row.relforcerowsecurity for row in rows)
        assert (
            connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', 'project_decision_versions', "
                    "'UPDATE')"
                )
            )
            is False
        )
        assert (
            connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', 'project_decision_approvals', "
                    "'DELETE')"
                )
            )
            is False
        )
        company_a = connection.scalar(
            text("SELECT primary_company_id FROM tenants WHERE id = :id"), {"id": tenant["id"]}
        )
        owner_id = connection.scalar(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": "m7db-owner@example.com"},
        )

    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE project_decision_versions SET rationale = 'rewritten by test' "
                "WHERE id = :id"
            ),
            {"id": decision["id"]},
        )

    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO project_decision_cases "
                "(id, tenant_id, company_id, project_intake_id, created_by_user_id, "
                "current_version, current_decision_id) "
                "VALUES (:id, :tenant_id, :company_id, :intake_id, :creator_id, 1, :head_id)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant["id"],
                "company_id": company_a,
                "intake_id": other_intake["id"],
                "creator_id": owner_id,
                "head_id": uuid4(),
            },
        )

    rollback_intake = _create_intake(client, base, headers, "M7DB-ROLLBACK-PROJECT", "15.00")
    rollback_assessment = _assess(client, base, headers, rollback_intake["id"])
    session = app.state.session_factory()
    try:
        with pytest.raises(RuntimeError, match="rollback decision transaction"), session.begin():
            set_user_context(session, owner_id)
            user = session.scalar(select(UserAccount).where(UserAccount.id == owner_id))
            assert user is not None
            create_project_decision(
                session,
                user,
                UUID(tenant["id"]),
                UUID(rollback_intake["id"]),
                ProjectDecisionCreate.model_validate(_decision_body(rollback_assessment)),
                uuid4(),
                datetime.now(UTC),
            )
            raise RuntimeError("rollback decision transaction")
    finally:
        session.close()

    with admin_engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT count(*) FROM project_decision_cases WHERE project_intake_id = :id"),
                {"id": rollback_intake["id"]},
            )
            == 0
        )
        assert (
            connection.scalar(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE event_type = 'project_decision.submitted' AND tenant_id = :tenant_id"
                ),
                {"tenant_id": tenant["id"]},
            )
            == 1
        )
    assert other_owner and other_base and other_headers


def test_concurrent_superseding_submissions_serialize_on_expected_version(client, app):
    _owner, _tenant, base, headers, intake, assessment = _setup(client, "m7race")
    first = _submit(client, base, intake["id"], headers, _decision_body(assessment)).json()

    def submit(index: int):
        with TestClient(app, raise_server_exceptions=False) as concurrent_client:
            body = _decision_body(
                assessment,
                "hold",
                expected_version=1,
                rationale=f"A human requested a hold for concurrent review {index}.",
                conditions=[f"Review condition {index} before resuming."],
            )
            return _submit(concurrent_client, base, intake["id"], headers, body)

    with ThreadPoolExecutor(max_workers=2) as executor:
        responses = list(executor.map(submit, (1, 2)))
    assert sorted(response.status_code for response in responses) == [201, 409]
    conflict = next(response for response in responses if response.status_code == 409)
    assert conflict.json()["error"]["code"] == "decision_version_conflict"

    history = client.get(f"{base}/project-intakes/{intake['id']}/decisions", headers=headers)
    assert history.status_code == 200, history.text
    assert history.json()["total"] == 2
    assert history.json()["current_version"] == 2
    assert first["id"] in {item["id"] for item in history.json()["items"]}
