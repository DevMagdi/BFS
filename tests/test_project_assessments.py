from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import sessionmaker

from businessflow.metrics.snapshot import content_hash
from businessflow.project_assessments.calculation import calculate_capacity_assessment
from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK_1 = "2026-11-16"
WEEK_2 = "2026-11-23"


def _tenant_path(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _scalar(engine, statement, params: dict[str, object] | None = None):
    with engine.connect() as connection:
        return connection.scalar(statement, params or {})


def _owner_tenant(client, slug: str) -> tuple[dict, dict, str, dict[str, str]]:
    owner = register_user(client, f"{slug}-owner@example.com", display_name=f"{slug} Owner")
    tenant = create_tenant(
        client,
        owner,
        name=f"{slug} Tenant",
        company_name=f"{slug} Company",
        key=f"{slug}-tenant-key-0001",
    )
    base = _tenant_path(tenant["id"])
    return owner, tenant, base, auth_headers(owner)


def _create_employee(client, base: str, headers: dict[str, str], code: str) -> dict:
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
    return response.json()


def _record_capacity(
    client,
    base: str,
    headers: dict[str, str],
    employee_id: str,
    week: str,
    *,
    status: str = "confirmed",
    available: str = "30.00",
) -> dict:
    if status == "unknown":
        payload = {"availability_status": "unknown"}
    else:
        payload = {
            "availability_status": status,
            "scheduled_hours": available,
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
        }
    response = client.post(
        f"{base}/employees/{employee_id}/weekly-capacity/{week}",
        json=payload,
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _create_assignment(
    client,
    base: str,
    headers: dict[str, str],
    employee_id: str,
    week: str,
    hours: str,
    code: str,
    *,
    assignment_status: str = "committed",
) -> dict:
    project_response = client.post(
        f"{base}/projects",
        json={
            "project_code": code,
            "name": f"Existing work {code}",
            "status": "planned",
            "start_date": week,
            "planned_end_date": "2026-11-29" if week == WEEK_2 else "2026-11-22",
        },
        headers=headers,
    )
    assert project_response.status_code == 201, project_response.text
    week_end = "2026-11-29" if week == WEEK_2 else "2026-11-22"
    assignment = client.post(
        f"{base}/projects/{project_response.json()['id']}/assignments",
        json={
            "employee_id": employee_id,
            "start_date": week,
            "end_date": week_end,
            "planned_hours_per_week": hours,
            "status": assignment_status,
        },
        headers=headers,
    )
    assert assignment.status_code == 201, assignment.text
    return assignment.json()


def _intake_payload(
    code: str,
    *,
    start: str = WEEK_1,
    weeks: int = 1,
    total: str = "20.00",
    distribution: list[dict[str, str]] | None = None,
    confirmed: bool = False,
) -> dict:
    payload = {
        "project_code": code,
        "name": f"Proposed project {code}",
        "start_week_date": start,
        "week_count": weeks,
        "total_demand_hours": total,
        "demand_unit": "team_hours",
        "demand_distribution_confirmed": confirmed,
    }
    if distribution is not None:
        payload["weekly_demand"] = distribution
    return payload


def _weekly(week: str, hours: str) -> dict[str, str]:
    return {"week_start_date": week, "hours": hours}


def _create_intake(client, base: str, headers: dict[str, str], payload: dict) -> dict:
    response = client.post(f"{base}/project-intakes", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def _assess(
    client,
    base: str,
    headers: dict[str, str],
    intake_id: str,
    *,
    assumptions: dict[str, bool] | None = None,
    scenario: dict | None = None,
    intake_version: int | None = None,
) -> dict:
    body: dict[str, object] = {
        "assumptions": assumptions
        or {
            "capacity_data_current_confirmed": True,
            "recorded_commitments_complete_confirmed": True,
        }
    }
    if scenario is not None:
        body["scenario"] = scenario
    if intake_version is not None:
        body["intake_version"] = intake_version
    response = client.post(
        f"{base}/project-intakes/{intake_id}/assessments", json=body, headers=headers
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_assessment_reuses_m5_commitment_residual_and_reports_each_week_without_averaging(
    client,
):
    _owner, _tenant, base, headers = _owner_tenant(client, "m6fit")
    employee = _create_employee(client, base, headers, "M6-FIT-EMP")
    _record_capacity(client, base, headers, employee["id"], WEEK_1, available="30.00")
    existing = _create_assignment(
        client, base, headers, employee["id"], WEEK_1, "10.00", "M6-EXISTING-FIT"
    )

    fit_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-FIT-PROPOSAL",
            total="20.00",
            distribution=[_weekly(WEEK_1, "20.00")],
            confirmed=True,
        ),
    )
    fit = _assess(client, base, headers, fit_intake["id"])
    assert fit["capacity_status"] == "feasible"
    assert fit["business_feasibility_status"] == "incomplete"
    assert fit["result_snapshot"]["business_feasibility_status"] == "incomplete"
    week = fit["result_snapshot"]["weekly"][0]
    assert week["available_capacity_hours"] == "30.00"
    assert week["existing_committed_hours"] == "10.00"
    assert week["remaining_capacity_before_project_hours"] == "20.00"
    assert week["projected_remaining_capacity_hours"] == "0.00"
    assert week["deficit_hours"] == "0.00"
    assert week["surplus_hours"] == "0.00"
    assert week["source_evidence"]["active_committed_assignment_ids"] == [existing["id"]]
    assert (
        week["m5_weekly_evidence"]["assignments"]["active_employee_committed_hours_per_week"]
        == "10.00"
    )
    assert (
        week["m5_weekly_evidence"]["comparison"]["capacity_minus_recorded_commitments_hours"]
        == "20.00"
    )
    assert fit["result_snapshot"]["canonical_capacity_changed"] is False
    assert fit["result_snapshot"]["project_or_employee_assignments_created"] is False
    assert fit["result_snapshot"]["operational_action_taken"] is False

    proposed = _create_assignment(
        client,
        base,
        headers,
        employee["id"],
        WEEK_1,
        "5.00",
        "M6-OTHER-PROPOSED",
        assignment_status="proposed",
    )
    conditional_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-CONDITION-ON-PROPOSED",
            total="20.00",
            distribution=[_weekly(WEEK_1, "20.00")],
            confirmed=True,
        ),
    )
    conditional = _assess(client, base, headers, conditional_intake["id"])
    assert conditional["capacity_status"] == "feasible_with_conditions"
    conditional_week = conditional["result_snapshot"]["weekly"][0]
    assert conditional_week["existing_committed_hours"] == "10.00"
    assert conditional_week["remaining_capacity_before_project_hours"] == "20.00"
    assert conditional_week["projected_remaining_capacity_hours"] == "0.00"
    assert conditional_week["source_evidence"]["proposed_assignment_ids"] == [proposed["id"]]
    assert any(
        item["code"] == "other_proposed_assignments_not_subtracted"
        for item in conditional["result_snapshot"]["warnings"]
    )

    overload_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-OVERLOAD-PROPOSAL",
            total="21.00",
            distribution=[_weekly(WEEK_1, "21.00")],
            confirmed=True,
        ),
    )
    overload = _assess(client, base, headers, overload_intake["id"])
    assert overload["capacity_status"] == "not_feasible"
    overload_week = overload["result_snapshot"]["weekly"][0]
    assert overload_week["projected_remaining_capacity_hours"] == "-1.00"
    assert overload_week["deficit_hours"] == "1.00"
    assert overload_week["surplus_hours"] == "0.00"
    assert len(overload_week["source_evidence"]["active_committed_assignment_ids"]) == 1

    _record_capacity(client, base, headers, employee["id"], WEEK_2, available="10.00")
    multiweek_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-NO-AVERAGING",
            start=WEEK_1,
            weeks=2,
            total="11.00",
            distribution=[_weekly(WEEK_1, "0.00"), _weekly(WEEK_2, "11.00")],
            confirmed=True,
        ),
    )
    multiweek = _assess(client, base, headers, multiweek_intake["id"])
    assert multiweek["capacity_status"] == "not_feasible"
    assert multiweek["result_snapshot"]["weekly"][0]["deficit_hours"] == "0.00"
    assert multiweek["result_snapshot"]["weekly"][1]["deficit_hours"] == "1.00"
    assert multiweek["result_snapshot"]["weekly"][1]["surplus_hours"] == "0.00"


def test_total_hours_without_distribution_is_incomplete_and_scenario_is_hypothetical(client):
    _owner, _tenant, base, headers = _owner_tenant(client, "m6scenario")
    employee = _create_employee(client, base, headers, "M6-SCENARIO-EMP")
    for week in (WEEK_1, WEEK_2):
        _record_capacity(client, base, headers, employee["id"], week, available="30.00")

    intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload("M6-UNSCHEDULED", start=WEEK_1, weeks=2, total="40.00"),
    )
    base_result = _assess(client, base, headers, intake["id"])
    assert base_result["capacity_status"] == "incomplete"
    assert base_result["result_snapshot"]["weekly"][0]["demand"]["hours"] is None
    assert base_result["result_snapshot"]["weekly"][1]["demand"]["hours"] is None
    assert all(
        item["projected_remaining_capacity_hours"] is None
        for item in base_result["result_snapshot"]["weekly"]
    )
    assert any(
        item["code"] == "demand_distribution_unconfirmed"
        for item in base_result["result_snapshot"]["blockers"]
    )

    scenario = {
        "name": "User-supplied even-week planning case",
        "weekly_demand": [_weekly(WEEK_1, "10.00"), _weekly(WEEK_2, "30.00")],
    }
    hypothetical = _assess(client, base, headers, intake["id"], scenario=scenario)
    assert hypothetical["capacity_status"] == "feasible_with_conditions"
    assert hypothetical["result_snapshot"]["hypothetical_scenario"] is True
    assert hypothetical["input_snapshot"]["scenario"]["hypothetical"] is True
    assert hypothetical["result_snapshot"]["weekly"][0]["demand"]["hours"] == "10.00"
    assert hypothetical["result_snapshot"]["weekly"][1]["demand"]["hours"] == "30.00"
    assert any(
        item["code"] == "hypothetical_scenario"
        for item in hypothetical["result_snapshot"]["warnings"]
    )
    metric_run = client.get(
        f"{base}/metrics/runs/{hypothetical['metric_run_id']}", headers=headers
    ).json()
    assessment_intake = hypothetical["input_snapshot"]["intake"]
    replayed_input, replayed_result = calculate_capacity_assessment(
        tenant_id=hypothetical["tenant_id"],
        company_id=hypothetical["company_id"],
        intake_id=assessment_intake["id"],
        intake_version=assessment_intake["version"],
        intake_version_id=assessment_intake["version_id"],
        intake_version_hash=assessment_intake["version_hash"],
        intake_snapshot=assessment_intake["snapshot"],
        metric_run_id=metric_run["id"],
        metric_catalog_version=metric_run["metric_catalog_version"],
        metric_calculation_version=metric_run["calculation_version"],
        metric_input_hash=metric_run["input_hash"],
        metric_result_hash=metric_run["result_hash"],
        metric_input_snapshot=metric_run["input_snapshot"],
        metric_result_snapshot=metric_run["result_snapshot"],
        assumptions=hypothetical["input_snapshot"]["assumptions"],
        scenario=hypothetical["input_snapshot"]["scenario"],
    )
    assert content_hash(replayed_input) == hypothetical["input_hash"]
    assert content_hash(replayed_result) == hypothetical["result_hash"]
    assert replayed_result == hypothetical["result_snapshot"]
    assigned = client.get(f"{base}/resource-assignments", headers=headers)
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["total"] == 0


def test_missing_unknown_and_estimated_capacity_block_or_condition_the_conclusion(client):
    _owner, _tenant, base, headers = _owner_tenant(client, "m6capacity")
    employee_a = _create_employee(client, base, headers, "M6-CAPACITY-A")
    employee_b = _create_employee(client, base, headers, "M6-CAPACITY-B")
    _record_capacity(client, base, headers, employee_a["id"], WEEK_1, available="30.00")
    _record_capacity(client, base, headers, employee_b["id"], WEEK_1, available="25.00")
    _record_capacity(client, base, headers, employee_a["id"], WEEK_2, status="unknown")
    # Employee B has no week-two capacity record, intentionally distinct from unknown.
    missing_and_unknown = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-MISSING-CAPACITY",
            start=WEEK_1,
            weeks=2,
            total="20.00",
            distribution=[_weekly(WEEK_1, "10.00"), _weekly(WEEK_2, "10.00")],
            confirmed=True,
        ),
    )
    unresolved = _assess(client, base, headers, missing_and_unknown["id"])
    assert unresolved["capacity_status"] == "incomplete"
    second_week = unresolved["result_snapshot"]["weekly"][1]
    assert second_week["available_capacity_hours"] is None
    assert second_week["m5_weekly_evidence"]["capacity"]["unknown_employee_count"] == 1
    assert second_week["m5_weekly_evidence"]["capacity"]["missing_employee_count"] == 1
    assert any(
        item["code"] == "capacity_incomplete" for item in unresolved["result_snapshot"]["blockers"]
    )

    estimated_employee = _create_employee(client, base, headers, "M6-ESTIMATED-CAPACITY")
    _record_capacity(
        client,
        base,
        headers,
        estimated_employee["id"],
        WEEK_1,
        status="estimated",
        available="30.00",
    )
    estimated_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-ESTIMATED-PROPOSAL",
            total="10.00",
            distribution=[_weekly(WEEK_1, "10.00")],
            confirmed=True,
        ),
    )
    estimated = _assess(client, base, headers, estimated_intake["id"])
    assert estimated["capacity_status"] == "feasible_with_conditions"
    assert estimated["result_snapshot"]["weekly"][0]["capacity_basis"] == "estimated"
    assert any(
        item["code"] == "estimated_capacity_condition"
        for item in estimated["result_snapshot"]["warnings"]
    )

    estimated_deficit_intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-ESTIMATED-DEFICIT",
            total="90.00",
            distribution=[_weekly(WEEK_1, "90.00")],
            confirmed=True,
        ),
    )
    estimated_deficit = _assess(client, base, headers, estimated_deficit_intake["id"])
    assert estimated_deficit["capacity_status"] == "incomplete"
    estimated_deficit_week = estimated_deficit["result_snapshot"]["weekly"][0]
    assert estimated_deficit_week["projected_remaining_capacity_hours"] == "-5.00"
    assert estimated_deficit_week["deficit_hours"] == "5.00"
    assert any(
        item["code"] == "estimated_capacity_shortfall_requires_confirmation"
        for item in estimated_deficit["result_snapshot"]["blockers"]
    )

    stale_or_incomplete = _assess(
        client,
        base,
        headers,
        estimated_intake["id"],
        assumptions={
            "capacity_data_current_confirmed": False,
            "recorded_commitments_complete_confirmed": False,
        },
    )
    assert stale_or_incomplete["capacity_status"] == "incomplete"
    assert {item["code"] for item in stale_or_incomplete["result_snapshot"]["blockers"]} >= {
        "capacity_currentness_unconfirmed",
        "commitment_completeness_unconfirmed",
    }


def test_intake_validation_authorization_scope_and_optimistic_versioning(client):
    owner, tenant, base, headers = _owner_tenant(client, "m6validation")
    collection = f"{base}/project-intakes"
    good = _intake_payload("M6-VALIDATION", total="10.00")
    assert client.post(collection, json=good).status_code == 401

    invalid_payloads = [
        {**good, "project_code": "M6-NOT-MONDAY", "start_week_date": "2026-11-17"},
        {**good, "project_code": "M6-TOO-LONG", "week_count": 53},
        {**good, "project_code": "M6-NEGATIVE", "total_demand_hours": "-1.00"},
        {**good, "project_code": "M6-WRONG-UNIT", "demand_unit": "person_days"},
        {
            **_intake_payload("M6-WEEK-OVERFLOW", start="9999-12-27", total="1.00"),
        },
        {
            **_intake_payload(
                "M6-NON-MONDAY-DEMAND",
                total="1.00",
                distribution=[_weekly("2026-11-17", "1.00")],
            ),
        },
        {
            **_intake_payload(
                "M6-DUP-WEEK",
                weeks=2,
                total="2.00",
                distribution=[_weekly(WEEK_1, "1.00"), _weekly(WEEK_1, "1.00")],
            ),
        },
        {
            **_intake_payload(
                "M6-SUM-MISMATCH",
                total="4.00",
                distribution=[_weekly(WEEK_1, "3.00")],
                confirmed=True,
            ),
        },
        {**good, "project_code": "M6-SPOOF-SCOPE", "company_id": tenant["company"]["id"]},
    ]
    for payload in invalid_payloads:
        response = client.post(collection, json=payload, headers=headers)
        assert response.status_code == 422, response.text

    intake = _create_intake(client, base, headers, good)
    duplicate = client.post(collection, json=good, headers=headers)
    assert duplicate.status_code == 409

    updated_data = _intake_payload("M6-VALIDATION-RENAMED", total="10.00")
    updated = client.put(
        f"{collection}/{intake['id']}",
        json={"expected_version": 1, "data": updated_data},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["current_version"] == 2
    stale_update = client.put(
        f"{collection}/{intake['id']}",
        json={"expected_version": 1, "data": good},
        headers=headers,
    )
    assert stale_update.status_code == 409
    assert stale_update.json()["error"]["code"] == "intake_version_conflict"
    version_one = client.get(f"{collection}/{intake['id']}/versions/1", headers=headers)
    assert version_one.status_code == 200
    assert version_one.json()["content_hash"] == intake["current_version_hash"]
    assert version_one.json()["data"]["project_code"] == "M6-VALIDATION"

    member = register_user(client, "m6validation-member@example.com", display_name="Read Only")
    create_member(client, owner, tenant["id"], "m6validation-member@example.com")
    member_headers = auth_headers(member)
    assert client.get(f"{collection}/{intake['id']}", headers=member_headers).status_code == 200
    assert (
        client.post(
            f"{collection}/{intake['id']}/assessments",
            json={},
            headers=member_headers,
        ).status_code
        == 403
    )
    assert (
        client.put(
            f"{collection}/{intake['id']}",
            json={"expected_version": 2, "data": good},
            headers=member_headers,
        ).status_code
        == 403
    )

    other_owner = register_user(client, "m6validation-other@example.com", display_name="Other")
    other_tenant = create_tenant(
        client, other_owner, name="Other M6 Tenant", key="m6validation-other-key-0001"
    )
    other_response = client.get(
        f"{_tenant_path(other_tenant['id'])}/project-intakes/{intake['id']}",
        headers=auth_headers(other_owner),
    )
    assert other_response.status_code == 404
    assert client.get(f"{collection}?limit=101", headers=headers).status_code == 422
    assert client.get(f"{collection}?offset=100001", headers=headers).status_code == 422


def test_assessment_history_keeps_old_intake_and_metric_evidence_after_source_changes(client):
    _owner, _tenant, base, headers = _owner_tenant(client, "m6history")
    employee = _create_employee(client, base, headers, "M6-HISTORY-EMP")
    _record_capacity(client, base, headers, employee["id"], WEEK_1, available="30.00")
    intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload(
            "M6-HISTORY-PROPOSAL",
            total="25.00",
            distribution=[_weekly(WEEK_1, "25.00")],
            confirmed=True,
        ),
    )
    original = _assess(client, base, headers, intake["id"], intake_version=1)
    assert original["capacity_status"] == "feasible"
    assert original["intake_version"] == 1
    assert original["input_hash"] == content_hash(original["input_snapshot"])
    assert original["result_hash"] == content_hash(original["result_snapshot"])
    assert original["input_snapshot"]["metric_run"]["id"] == original["metric_run_id"]
    metric_run = client.get(f"{base}/metrics/runs/{original['metric_run_id']}", headers=headers)
    assert metric_run.status_code == 200
    assert metric_run.json()["input_hash"] == original["metric_input_hash"]
    assert metric_run.json()["result_hash"] == original["metric_result_hash"]

    updated_data = _intake_payload(
        "M6-HISTORY-PROPOSAL",
        total="25.00",
        distribution=[_weekly(WEEK_1, "25.00")],
        confirmed=True,
    )
    updated_data["name"] = "Revised proposal description"
    updated = client.put(
        f"{base}/project-intakes/{intake['id']}",
        json={"expected_version": 1, "data": updated_data},
        headers=headers,
    )
    assert updated.status_code == 200, updated.text
    second_version_assessment = _assess(client, base, headers, intake["id"])
    assert second_version_assessment["intake_version"] == 2

    _record_capacity(client, base, headers, employee["id"], WEEK_1, available="20.00")
    after_source_change = _assess(client, base, headers, intake["id"], intake_version=1)
    assert after_source_change["capacity_status"] == "not_feasible"
    assert after_source_change["metric_input_hash"] != original["metric_input_hash"]
    assert after_source_change["metric_result_hash"] != original["metric_result_hash"]

    member = register_user(client, "m6history-member@example.com")
    create_member(client, _owner, _tenant["id"], "m6history-member@example.com")
    member_headers = auth_headers(member)
    assessment_collection = f"{base}/project-intakes/{intake['id']}/assessments"
    for path in (
        assessment_collection,
        f"{assessment_collection}/{original['id']}",
        f"{assessment_collection}/{original['id']}/evidence",
    ):
        assert client.get(path, headers=member_headers).status_code == 403

    old_again = client.get(
        f"{base}/project-intakes/{intake['id']}/assessments/{original['id']}/evidence",
        headers=headers,
    )
    assert old_again.status_code == 200
    assert old_again.json()["input_hash"] == original["input_hash"]
    assert old_again.json()["result_hash"] == original["result_hash"]
    assert old_again.json()["result_snapshot"] == original["result_snapshot"]
    history = client.get(f"{base}/project-intakes/{intake['id']}/assessments", headers=headers)
    assert history.status_code == 200
    assert history.json()["total"] == 3
    assert {item["intake_version"] for item in history.json()["items"]} == {1, 2}


def test_concurrent_intake_updates_use_locked_expected_versions(client):
    _owner, _tenant, base, headers = _owner_tenant(client, "m6concurrent")
    intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload("M6-CONCURRENT", total="10.00"),
    )
    first_data = _intake_payload("M6-CONCURRENT", total="10.00")
    first_data["name"] = "Concurrent edit one"
    second_data = _intake_payload("M6-CONCURRENT", total="10.00")
    second_data["name"] = "Concurrent edit two"
    url = f"{base}/project-intakes/{intake['id']}"
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(
                client.put,
                url,
                json={"expected_version": 1, "data": data},
                headers=headers,
            )
            for data in (first_data, second_data)
        ]
        responses = [future.result(timeout=20) for future in futures]
    assert sorted(response.status_code for response in responses) == [200, 409]
    current = client.get(url, headers=headers)
    assert current.status_code == 200
    assert current.json()["current_version"] == 2
    assert current.json()["data"]["name"] in {
        "Concurrent edit one",
        "Concurrent edit two",
    }
    assert client.get(f"{url}/versions/2", headers=headers).status_code == 200
    assert client.get(f"{url}/versions/3", headers=headers).status_code == 404


def test_assessment_rollback_and_forced_company_rls_append_only_privileges(
    client, runtime_engine, admin_engine
):
    _owner, tenant, base, headers = _owner_tenant(client, "m6rls")
    employee = _create_employee(client, base, headers, "M6-RLS-EMP")
    _record_capacity(client, base, headers, employee["id"], WEEK_1, available="30.00")
    intake = _create_intake(
        client,
        base,
        headers,
        _intake_payload("M6-RLS-PROPOSAL", total="10.00"),
    )
    before = _scalar(
        admin_engine,
        text("SELECT count(*) FROM metric_runs WHERE tenant_id = :tenant_id"),
        {"tenant_id": tenant["id"]},
    )
    invalid_scenario = client.post(
        f"{base}/project-intakes/{intake['id']}/assessments",
        json={
            "assumptions": {
                "capacity_data_current_confirmed": True,
                "recorded_commitments_complete_confirmed": True,
            },
            "scenario": {
                "name": "Out of period",
                "weekly_demand": [_weekly(WEEK_2, "10.00")],
            },
        },
        headers=headers,
    )
    assert invalid_scenario.status_code == 422
    assert (
        _scalar(
            admin_engine,
            text("SELECT count(*) FROM metric_runs WHERE tenant_id = :tenant_id"),
            {"tenant_id": tenant["id"]},
        )
        == before
    )

    assessment = _assess(client, base, headers, intake["id"])
    with admin_engine.connect() as connection:
        security = connection.execute(
            text(
                "SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity, r.rolname AS owner "
                "FROM pg_class c JOIN pg_roles r ON r.oid = c.relowner "
                "WHERE c.relname IN ('project_intakes', 'project_intake_versions', "
                "'capacity_assessments') ORDER BY c.relname"
            )
        ).all()
        policies = connection.execute(
            text(
                "SELECT tablename, policyname, cmd FROM pg_policies "
                "WHERE tablename IN ('project_intakes', 'project_intake_versions', "
                "'capacity_assessments') ORDER BY tablename"
            )
        ).all()
        privileges = connection.execute(
            text(
                "SELECT "
                "has_table_privilege('bf_runtime', 'project_intakes', 'SELECT') AS intake_select, "
                "has_table_privilege('bf_runtime', 'project_intakes', 'INSERT') AS intake_insert, "
                "has_column_privilege('bf_runtime', 'project_intakes', "
                "'current_version', 'UPDATE') AS intake_version_update, "
                "has_column_privilege('bf_runtime', 'project_intakes', "
                "'tenant_id', 'UPDATE') AS intake_tenant_update, "
                "has_table_privilege('bf_runtime', 'project_intake_versions', "
                "'INSERT') AS version_insert, "
                "has_table_privilege('bf_runtime', 'project_intake_versions', "
                "'UPDATE') AS version_update, "
                "has_table_privilege('bf_runtime', 'capacity_assessments', "
                "'INSERT') AS assessment_insert, "
                "has_table_privilege('bf_runtime', 'capacity_assessments', "
                "'UPDATE') AS assessment_update, "
                "has_table_privilege('bf_runtime', 'capacity_assessments', "
                "'DELETE') AS assessment_delete, "
                "has_table_privilege('bf_runtime', 'capacity_assessments', "
                "'TRUNCATE') AS assessment_truncate"
            )
        ).one()
    assert len(security) == 3
    assert all(row.relrowsecurity and row.relforcerowsecurity for row in security)
    assert all(row.owner == "bf_migrator" for row in security)
    assert len(policies) == 3
    assert all(row.cmd == "ALL" for row in policies)
    assert privileges.intake_select and privileges.intake_insert
    assert privileges.intake_version_update and not privileges.intake_tenant_update
    assert privileges.version_insert and not privileges.version_update
    assert privileges.assessment_insert and not privileges.assessment_update
    assert not privileges.assessment_delete and not privileges.assessment_truncate

    factory = sessionmaker(runtime_engine, expire_on_commit=False)
    with factory.begin() as session:
        assert session.scalar(text("SELECT count(*) FROM project_intakes")) == 0
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        assert session.scalar(text("SELECT count(*) FROM project_intakes")) == 1
        assert session.scalar(text("SELECT count(*) FROM capacity_assessments")) == 1
        assert (
            session.scalar(
                text("SELECT count(*) FROM capacity_assessments WHERE id = :id"),
                {"id": assessment["id"]},
            )
            == 1
        )

    with factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": str(uuid4())},
        )
        assert session.scalar(text("SELECT count(*) FROM project_intakes")) == 0
        assert session.scalar(text("SELECT count(*) FROM capacity_assessments")) == 0

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text(
                "INSERT INTO project_intakes "
                "(id, tenant_id, company_id, created_by_user_id, project_code, current_version) "
                "VALUES (:id, :tenant_id, :company_id, :user_id, 'M6-SPOOFED-COMPANY', 1)"
            ),
            {
                "id": uuid4(),
                "tenant_id": tenant["id"],
                "company_id": uuid4(),
                "user_id": intake["created_by_user_id"],
            },
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text("UPDATE capacity_assessments SET result_hash = :hash WHERE id = :id"),
            {"hash": "f" * 64, "id": assessment["id"]},
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text("DELETE FROM project_intake_versions WHERE project_intake_id = :id"),
            {"id": intake["id"]},
        )

    with pytest.raises(DBAPIError), factory.begin() as session:
        session.execute(
            text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
            {"tenant_id": tenant["id"]},
        )
        session.execute(
            text("SELECT set_config('app.company_id', :company_id, true)"),
            {"company_id": tenant["company"]["id"]},
        )
        session.execute(
            text(
                "UPDATE project_intakes SET current_version = current_version + 1, "
                "updated_at = now() WHERE id = :id"
            ),
            {"id": intake["id"]},
        )
