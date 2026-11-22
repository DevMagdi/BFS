from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from threading import Event
from typing import Any
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from businessflow.database import set_company_context, set_tenant_context, set_user_context
from businessflow.financial_assessments.calculation import evaluate_financial_input_snapshot
from businessflow.financial_assessments.schemas import ProjectFinancialInputCreate
from businessflow.financial_assessments.service import create_project_financial_input
from businessflow.metrics.snapshot import content_hash
from businessflow.models import UserAccount
from tests.helpers import auth_headers, create_member, create_tenant, register_user

WEEK = "2026-11-16"


def _base(tenant_id: str) -> str:
    return f"/api/v1/tenants/{tenant_id}"


def _admin_scalar(admin_engine, statement, params=None):
    with admin_engine.connect() as connection:
        return connection.scalar(statement, params or {})


def _source(
    reference: str,
    *,
    source_kind: str = "user_attestation",
    assumption: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "source_kind": source_kind,
        "source_reference": reference,
        "observed_at": "2026-11-20T12:00:00Z",
        "effective_from": "2026-11-01",
    }
    if assumption is not None:
        result["assumption"] = {"rationale": assumption}
    return result


def _capacity_project(client, slug: str) -> tuple[dict, dict, str, dict, dict, dict]:
    owner = register_user(client, f"{slug}-owner@example.com", display_name=f"{slug} Owner")
    tenant = create_tenant(
        client,
        owner,
        name=f"{slug} Tenant",
        company_name=f"{slug} Company",
        key=f"{slug}-onboard-key-0001",
    )
    base = _base(tenant["id"])
    owner_headers = auth_headers(owner)
    employee_response = client.post(
        f"{base}/employees",
        json={
            "employee_code": f"{slug.upper()}-EMP",
            "full_name": f"{slug} Employee",
            "job_title": "Engineer",
            "employment_status": "active",
            "employment_start_date": "2020-01-06",
            "standard_weekly_capacity_hours": "40.00",
        },
        headers=owner_headers,
    )
    assert employee_response.status_code == 201, employee_response.text
    employee = employee_response.json()
    capacity_response = client.post(
        f"{base}/employees/{employee['id']}/weekly-capacity/{WEEK}",
        json={
            "availability_status": "confirmed",
            "scheduled_hours": "40.00",
            "non_project_hours": "0.00",
            "unavailable_hours": "0.00",
        },
        headers=owner_headers,
    )
    assert capacity_response.status_code == 201, capacity_response.text
    intake_response = client.post(
        f"{base}/project-intakes",
        json={
            "project_code": f"{slug.upper()}-PROJECT",
            "name": f"Proposed project for {slug}",
            "start_week_date": WEEK,
            "week_count": 1,
            "total_demand_hours": "20.00",
            "demand_unit": "team_hours",
            "weekly_demand": [{"week_start_date": WEEK, "hours": "20.00"}],
            "demand_distribution_confirmed": True,
        },
        headers=owner_headers,
    )
    assert intake_response.status_code == 201, intake_response.text
    intake = intake_response.json()
    assessment_response = client.post(
        f"{base}/project-intakes/{intake['id']}/assessments",
        json={
            "assumptions": {
                "capacity_data_current_confirmed": True,
                "recorded_commitments_complete_confirmed": True,
            }
        },
        headers=owner_headers,
    )
    assert assessment_response.status_code == 201, assessment_response.text
    return owner, tenant, base, owner_headers, intake, assessment_response.json()


def _approve_m7_handoff(
    client,
    owner: dict,
    tenant: dict,
    base: str,
    intake: dict,
    capacity_assessment: dict,
    slug: str,
) -> tuple[dict, dict]:
    reviewer = register_user(
        client, f"{slug}-reviewer@example.com", display_name=f"{slug} Reviewer"
    )
    create_member(client, owner, tenant["id"], f"{slug}-reviewer@example.com", "admin")
    decision_response = client.post(
        f"{base}/project-intakes/{intake['id']}/decisions",
        json={
            "assessment_id": capacity_assessment["id"],
            "outcome": "proceed_to_financial_evaluation",
            "rationale": "A human reviewed the exact linked weekly capacity evidence.",
        },
        headers=auth_headers(owner),
    )
    assert decision_response.status_code == 201, decision_response.text
    decision = decision_response.json()
    approval_response = client.post(
        f"{base}/project-intakes/{intake['id']}/decisions/{decision['id']}/approval",
        json={
            "expected_decision_hash": decision["decision_hash"],
            "expected_evidence_hash": decision["evidence_hash"],
            "outcome": "approved",
            "rationale": "Reviewed this exact decision version and evidence.",
        },
        headers=auth_headers(reviewer),
    )
    assert approval_response.status_code == 201, approval_response.text
    return decision, approval_response.json()


def _financial_input_body(
    intake_version: int,
    *,
    expected_version: int | None = None,
    cost_amount: str = "125.005",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "intake_version": intake_version,
        "commercial_amounts": [
            {
                "kind": "contract_value",
                "amount": "1000.005",
                "currency": "USD",
                "source": _source(
                    "proposal:version-4",
                    source_kind="proposal",
                    assumption="The proposal amount remains subject to customer confirmation.",
                ),
            },
            {
                "kind": "reported_revenue",
                "amount": "75.2500",
                "currency": "USD",
                "source": _source("ledger:observation-12", source_kind="invoice"),
            },
        ],
        "cost_observations": [
            {
                "label": "quoted implementation support",
                "amount": cost_amount,
                "currency": "EUR",
                "source": _source("quote:vendor-8", source_kind="supplier_quote"),
            }
        ],
        "effort_hours": {
            "hours": "4.0050",
            "source": _source("estimate:work-package-2", source_kind="estimate"),
        },
        "payment_terms": {
            "value": "40 percent on signature; remaining milestones are not assessed.",
            "source": _source("proposal:terms-4", source_kind="proposal"),
        },
        "known_risks": [
            {
                "description": "Supplier quote expires before the proposed start date.",
                "source": _source("quote:vendor-8", source_kind="supplier_quote"),
            }
        ],
    }
    if expected_version is not None:
        body["expected_version"] = expected_version
    return body


def test_m8_inputs_observations_replay_versioning_and_staleness(client):
    owner, tenant, base, owner_headers, intake, capacity_assessment = _capacity_project(
        client, "m8lifecycle"
    )
    decision, approval = _approve_m7_handoff(
        client, owner, tenant, base, intake, capacity_assessment, "m8lifecycle"
    )
    financial_path = f"{base}/project-intakes/{intake['id']}"

    readiness = client.get(
        f"{financial_path}/financial-assessments/readiness", headers=owner_headers
    )
    assert readiness.status_code == 200, readiness.text
    assert readiness.json()["can_run_assessment"] is True
    assert readiness.json()["handoff_status"] == "ready"
    assert readiness.json()["input_freshness"] == "missing"
    assert "financial_input_version" in readiness.json()["missing_inputs"]
    assert "approved_financial_decision_policy" in readiness.json()["missing_inputs"]

    no_inputs_response = client.post(
        f"{financial_path}/financial-assessments", headers=owner_headers
    )
    assert no_inputs_response.status_code == 201, no_inputs_response.text
    no_inputs = no_inputs_response.json()
    assert no_inputs["financial_verdict"] == "incomplete"
    assert no_inputs["financial_input_version_id"] is None
    assert no_inputs["financial_input_hash"] is None
    assert no_inputs["calculated_metrics"] == []
    assert "financial_input_version" in no_inputs["missing_inputs"]
    assert "approved_financial_decision_policy" in no_inputs["missing_inputs"]
    assert any("costs_not_provided" in warning for warning in no_inputs["warnings"])
    assert no_inputs["observations"]["cost_observations"] == []
    assert no_inputs["evidence_freshness"] == "fresh"

    created = client.post(
        f"{financial_path}/financial-inputs",
        json=_financial_input_body(intake["current_version"]),
        headers=owner_headers,
    )
    assert created.status_code == 201, created.text
    input_v1 = created.json()
    assert input_v1["version"] == 1
    assert input_v1["is_current"] is True
    snapshot_v1 = input_v1["input_snapshot"]
    assert snapshot_v1["commercial_amounts"][0]["amount"] == "1000.005"
    assert snapshot_v1["commercial_amounts"][1]["amount"] == "75.2500"
    assert snapshot_v1["cost_observations"][0]["amount"] == "125.005"
    assert snapshot_v1["effort_hours"]["hours"] == "4.0050"
    assert snapshot_v1["commercial_amounts"][0]["source"]["assumption"]["rationale"]

    catalog = client.get(f"{financial_path}/financial-policies", headers=owner_headers)
    assert catalog.status_code == 200, catalog.text
    assert catalog.json()["status"] == "no_approved_policy"
    assert catalog.json()["approved_policies"] == []
    assert catalog.json()["formula_definitions"] == []
    assert catalog.json()["policy_approval"] is None
    assert catalog.json()["administration_available"] is False
    assert "no rounding" in catalog.json()["decimal_handling"]

    assessed = client.post(f"{financial_path}/financial-assessments", headers=owner_headers)
    assert assessed.status_code == 201, assessed.text
    assessment_v1 = assessed.json()
    assert assessment_v1["financial_verdict"] == "incomplete"
    assert assessment_v1["policy_status"] == "no_approved_policy"
    assert assessment_v1["approved_policy_id"] is None
    assert assessment_v1["approved_policy_version"] is None
    assert assessment_v1["calculated_metrics"] == []
    assert assessment_v1["currency_codes"] == ["EUR", "USD"]
    assert assessment_v1["observations"]["commercial_amounts"][0]["amount"] == "1000.005"
    assert assessment_v1["observations"]["cost_observations"][0]["amount"] == "125.005"
    assert "approved_financial_decision_policy" in assessment_v1["missing_inputs"]
    assert any("no FX conversion" in warning for warning in assessment_v1["warnings"])
    assert assessment_v1["evidence_freshness"] == "fresh"
    assert assessment_v1["financial_input_version_id"] == input_v1["id"]
    assert assessment_v1["financial_input_hash"] == input_v1["content_hash"]
    assert assessment_v1["capacity_assessment_id"] == capacity_assessment["id"]
    assert assessment_v1["metric_run_id"] == capacity_assessment["metric_run_id"]
    assert assessment_v1["decision_version_id"] == decision["id"]
    assert assessment_v1["decision_hash"] == decision["decision_hash"]
    assert assessment_v1["decision_approval_id"] == approval["id"]
    assert assessment_v1["decision_approval_hash"] == approval["approval_hash"]
    assert assessment_v1["input_hash"] == content_hash(assessment_v1["input_snapshot"])
    assert assessment_v1["result_hash"] == content_hash(assessment_v1["result_snapshot"])

    replay = evaluate_financial_input_snapshot(assessment_v1["input_snapshot"])
    assert replay == assessment_v1["result_snapshot"]
    assert content_hash(replay) == assessment_v1["result_hash"]
    evidence = assessment_v1["input_snapshot"]["evidence"]
    assert evidence["m5_metric_run"]["id"] == assessment_v1["metric_run_id"]
    assert evidence["m5_metric_run"]["input_hash"] == assessment_v1["metric_input_hash"]
    assert evidence["m6_capacity_assessment"]["id"] == assessment_v1["capacity_assessment_id"]
    assert evidence["m7_decision"]["decision_id"] == assessment_v1["decision_version_id"]
    assert assessment_v1["input_snapshot"]["financial_input"]["snapshot"] == snapshot_v1
    assert assessment_v1["assumptions"][0]["rationale"] == (
        "The proposal amount remains subject to customer confirmation."
    )
    assert (
        assessment_v1["assumptions"][0]["attributed_to_user_id"]
        == (snapshot_v1["created_by_user_id"])
    )

    detail = client.get(
        f"{financial_path}/financial-assessments/{assessment_v1['id']}", headers=owner_headers
    )
    assert detail.status_code == 200, detail.text
    assert detail.json()["evidence_freshness"] == "fresh"
    assert detail.json()["staleness_reasons"] == []

    updated = client.post(
        f"{financial_path}/financial-inputs",
        json=_financial_input_body(
            intake["current_version"], expected_version=1, cost_amount="126.005"
        ),
        headers=owner_headers,
    )
    assert updated.status_code == 201, updated.text
    input_v2 = updated.json()
    assert input_v2["version"] == 2
    assert input_v2["supersedes_version_id"] == input_v1["id"]

    old_assessment = client.get(
        f"{financial_path}/financial-assessments/{assessment_v1['id']}", headers=owner_headers
    )
    assert old_assessment.status_code == 200, old_assessment.text
    assert old_assessment.json()["evidence_freshness"] == "stale"
    assert "a_newer_financial_input_version_exists" in old_assessment.json()["staleness_reasons"]
    assert old_assessment.json()["financial_verdict"] == "incomplete"

    stale_write = client.post(
        f"{financial_path}/financial-inputs",
        json=_financial_input_body(intake["current_version"], expected_version=1),
        headers=owner_headers,
    )
    assert stale_write.status_code == 409
    assert stale_write.json()["error"]["code"] == "financial_input_version_conflict"
    assert "126.005" not in stale_write.text

    input_history = client.get(
        f"{financial_path}/financial-inputs/history?limit=1&offset=0", headers=owner_headers
    )
    assert input_history.status_code == 200, input_history.text
    assert input_history.json()["total"] == 2
    assert input_history.json()["current_version"] == 2
    assert input_history.json()["items"][0]["id"] == input_v2["id"]
    old_input = client.get(f"{financial_path}/financial-inputs/versions/1", headers=owner_headers)
    assert old_input.status_code == 200, old_input.text
    assert old_input.json()["is_current"] is False

    assessed_v2 = client.post(f"{financial_path}/financial-assessments", headers=owner_headers)
    assert assessed_v2.status_code == 201, assessed_v2.text
    assert assessed_v2.json()["financial_input_version"] == 2
    assert assessed_v2.json()["evidence_freshness"] == "fresh"
    assessment_history = client.get(
        f"{financial_path}/financial-assessments?limit=10&offset=0", headers=owner_headers
    )
    assert assessment_history.status_code == 200, assessment_history.text
    assert assessment_history.json()["total"] == 3

    audit = client.get(f"{base}/audit-events?limit=100&offset=0", headers=owner_headers)
    assert audit.status_code == 200, audit.text
    financial_events = [
        item for item in audit.json()["items"] if item["event_type"].startswith("financial_")
    ]
    assert {item["event_type"] for item in financial_events} == {
        "financial_input.version_created",
        "financial_assessment.created",
    }
    assert all("1000.005" not in str(item["metadata"]) for item in financial_events)

    updated_intake_data = {**intake["data"], "name": "Revised project scope"}
    intake_update = client.put(
        f"{base}/project-intakes/{intake['id']}",
        json={"expected_version": intake["current_version"], "data": updated_intake_data},
        headers=owner_headers,
    )
    assert intake_update.status_code == 200, intake_update.text
    assert intake_update.json()["current_version"] == 2
    prior_scope_assessment = client.get(
        f"{financial_path}/financial-assessments/{assessed_v2.json()['id']}",
        headers=owner_headers,
    )
    assert prior_scope_assessment.status_code == 200, prior_scope_assessment.text
    assert prior_scope_assessment.json()["evidence_freshness"] == "stale"
    assert (
        "project_intake_has_a_newer_version" in prior_scope_assessment.json()["staleness_reasons"]
    )
    stale_readiness = client.get(
        f"{financial_path}/financial-assessments/readiness", headers=owner_headers
    )
    assert stale_readiness.status_code == 200, stale_readiness.text
    assert stale_readiness.json()["input_freshness"] == "stale"
    assert stale_readiness.json()["handoff_status"] == "stale"
    assert stale_readiness.json()["can_run_assessment"] is False
    stale_intake_input = client.post(
        f"{financial_path}/financial-inputs",
        json=_financial_input_body(1, expected_version=2),
        headers=owner_headers,
    )
    assert stale_intake_input.status_code == 409
    assert stale_intake_input.json()["error"]["code"] == "financial_input_intake_version_conflict"

    openapi = client.get("/openapi.json").json()
    paths = openapi["paths"]
    assert "/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/financial-inputs" in paths
    assert (
        "/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/financial-assessments/readiness"
        in paths
    )
    input_schema = openapi["components"]["schemas"]["CommercialAmountInput"]["properties"]["amount"]
    assert input_schema["type"] == "string"
    assert "pattern" in input_schema


def test_m8_requires_current_approved_m7_handoff(client):
    _owner, _tenant, base, owner_headers, intake, _capacity = _capacity_project(client, "m8handoff")
    path = f"{base}/project-intakes/{intake['id']}"
    readiness = client.get(f"{path}/financial-assessments/readiness", headers=owner_headers)
    assert readiness.status_code == 200
    assert readiness.json()["handoff_status"] == "missing"
    assert readiness.json()["can_run_assessment"] is False

    attempt = client.post(f"{path}/financial-assessments", headers=owner_headers)
    assert attempt.status_code == 409
    assert attempt.json()["error"]["code"] == "financial_handoff_unavailable"
    history = client.get(f"{path}/financial-assessments", headers=owner_headers)
    assert history.status_code == 200
    assert history.json()["total"] == 0


def test_financial_input_validation_preserves_exact_decimal_text():
    valid = ProjectFinancialInputCreate.model_validate(
        {
            "intake_version": 1,
            "cost_observations": [
                {
                    "label": "Explicit zero observation",
                    "amount": "0",
                    "currency": "USD",
                    "source": _source("attestation:zero"),
                }
            ],
        }
    )
    assert valid.cost_observations is not None
    assert valid.cost_observations[0].amount == "0"
    assert Decimal(valid.cost_observations[0].amount) == Decimal("0")

    invalid_payloads = (
        {
            "intake_version": 1,
            "commercial_amounts": [
                {
                    "kind": "contract_value",
                    "amount": 1.005,
                    "currency": "USD",
                    "source": _source("proposal:numeric"),
                }
            ],
        },
        {
            "intake_version": 1,
            "commercial_amounts": [
                {
                    "kind": "contract_value",
                    "amount": "1e2",
                    "currency": "USD",
                    "source": _source("proposal:exponent"),
                }
            ],
        },
        {
            "intake_version": 1,
            "commercial_amounts": [
                {
                    "kind": "contract_value",
                    "amount": "1.005",
                    "currency": "ZZZ",
                    "source": _source("proposal:unknown-currency"),
                }
            ],
        },
        {
            "intake_version": 1,
            "effort_hours": {
                "hours": "1.5",
                "source": {
                    "source_kind": "estimate",
                    "source_reference": "estimate:naive-date",
                    "observed_at": "2026-11-20T12:00:00",
                },
            },
        },
        {"intake_version": 1},
    )
    for invalid in invalid_payloads:
        with pytest.raises(ValidationError):
            ProjectFinancialInputCreate.model_validate(invalid)


def test_finance_permissions_rls_and_runtime_grants(client, admin_engine, runtime_engine):
    owner, tenant, base, owner_headers, intake, capacity_assessment = _capacity_project(
        client, "m8security"
    )
    decision, approval = _approve_m7_handoff(
        client, owner, tenant, base, intake, capacity_assessment, "m8security"
    )
    project_path = f"{base}/project-intakes/{intake['id']}"
    input_response = client.post(
        f"{project_path}/financial-inputs",
        json=_financial_input_body(intake["current_version"]),
        headers=owner_headers,
    )
    assert input_response.status_code == 201, input_response.text
    assessment_response = client.post(
        f"{project_path}/financial-assessments", headers=owner_headers
    )
    assert assessment_response.status_code == 201, assessment_response.text

    member = register_user(client, "m8security-member@example.com", display_name="Finance Member")
    create_member(client, owner, tenant["id"], "m8security-member@example.com", "member")
    member_headers = auth_headers(member)
    assert (
        client.get(f"{project_path}/financial-policies", headers=member_headers).status_code == 403
    )
    assert client.get(f"{project_path}/financial-inputs", headers=member_headers).status_code == 403
    assert (
        client.get(
            f"{project_path}/financial-assessments/{assessment_response.json()['id']}",
            headers=member_headers,
        ).status_code
        == 403
    )
    forbidden_write = client.post(
        f"{project_path}/financial-inputs",
        json=_financial_input_body(intake["current_version"]),
        headers=member_headers,
    )
    assert forbidden_write.status_code == 403

    other_owner = register_user(client, "m8security-other@example.com", display_name="Other Owner")
    other_tenant = create_tenant(
        client,
        other_owner,
        name="Other Tenant",
        company_name="Other Company",
        key="m8security-other-key-0001",
    )
    cross_tenant_path = (
        f"/api/v1/tenants/{other_tenant['id']}/project-intakes/{intake['id']}/financial-inputs"
    )
    assert client.get(cross_tenant_path, headers=auth_headers(other_owner)).status_code == 404

    table_names = (
        "project_financial_input_cases",
        "project_financial_input_versions",
        "financial_assessments",
    )
    with admin_engine.connect() as connection:
        role = connection.execute(
            text("SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = 'bf_runtime'")
        ).one()
        assert role == (False, False)
        assert (
            connection.scalar(
                text(
                    "SELECT NOT pg_has_role('bf_runtime', 'bf_migrator', 'MEMBER') "
                    "AND datdba <> (SELECT oid FROM pg_roles WHERE rolname = 'bf_runtime') "
                    "FROM pg_database WHERE datname = current_database()"
                )
            )
            is True
        )
        for table_name in table_names:
            security = connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE oid = to_regclass(:table_name)"
                ),
                {"table_name": f"public.{table_name}"},
            ).one()
            assert security == (True, True)
        assert connection.scalar(
            text("SELECT count(*) FROM pg_policies WHERE tablename = ANY(:table_names)"),
            {"table_names": list(table_names)},
        ) == len(table_names)
        assert (
            connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'financial_assessments', 'UPDATE')")
            )
            is False
        )
        assert (
            connection.scalar(
                text("SELECT has_table_privilege('bf_runtime', 'financial_assessments', 'DELETE')")
            )
            is False
        )
        assert (
            connection.scalar(
                text(
                    "SELECT has_table_privilege('bf_runtime', "
                    "'project_financial_input_versions', 'UPDATE')"
                )
            )
            is False
        )
        assert (
            connection.scalar(
                text(
                    "SELECT has_column_privilege('bf_runtime', 'project_financial_input_cases', "
                    "'current_version', 'UPDATE')"
                )
            )
            is True
        )
        assert (
            connection.scalar(
                text(
                    "SELECT has_column_privilege('bf_runtime', 'project_financial_input_cases', "
                    "'created_by_user_id', 'UPDATE')"
                )
            )
            is False
        )

    input_case_id = UUID(input_response.json()["input_case_id"])
    input_version_id = UUID(input_response.json()["id"])
    assessment_id = UUID(assessment_response.json()["id"])
    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE project_financial_input_versions SET content_hash = :new_hash "
                "WHERE id = :input_id"
            ),
            {"new_hash": "0" * 64, "input_id": input_version_id},
        )
    with pytest.raises(DBAPIError), admin_engine.begin() as connection:
        connection.execute(
            text("DELETE FROM financial_assessments WHERE id = :assessment_id"),
            {"assessment_id": assessment_id},
        )
    with runtime_engine.connect() as connection, connection.begin():
        assert connection.scalar(text("SELECT count(*) FROM financial_assessments")) == 0
        assert connection.scalar(text("SELECT count(*) FROM project_financial_input_cases")) == 0

    user_id = UUID(
        _admin_scalar(
            admin_engine, text("SELECT id FROM users WHERE email = 'm8security-owner@example.com'")
        ).__str__()
    )
    with Session(runtime_engine) as session, session.begin():
        set_user_context(session, user_id)
        set_tenant_context(session, UUID(tenant["id"]))
        company_id = UUID(
            str(
                _admin_scalar(
                    admin_engine,
                    text("SELECT id FROM companies WHERE tenant_id = :tenant_id"),
                    {"tenant_id": tenant["id"]},
                )
            )
        )
        set_company_context(session, company_id)
        assert (
            session.scalar(
                text("SELECT count(*) FROM project_financial_input_cases WHERE id = :case_id"),
                {"case_id": input_case_id},
            )
            == 1
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM financial_assessments WHERE id = :assessment_id"),
                {"assessment_id": assessment_id},
            )
            == 1
        )

    other_user_id = UUID(
        str(
            _admin_scalar(
                admin_engine,
                text("SELECT id FROM users WHERE email = 'm8security-other@example.com'"),
            )
        )
    )
    other_company_id = UUID(
        str(
            _admin_scalar(
                admin_engine,
                text("SELECT id FROM companies WHERE tenant_id = :tenant_id"),
                {"tenant_id": other_tenant["id"]},
            )
        )
    )
    with Session(runtime_engine) as session, session.begin():
        set_user_context(session, other_user_id)
        set_tenant_context(session, UUID(other_tenant["id"]))
        set_company_context(session, other_company_id)
        assert (
            session.scalar(
                text("SELECT count(*) FROM project_financial_input_cases WHERE id = :case_id"),
                {"case_id": input_case_id},
            )
            == 0
        )
        assert (
            session.scalar(
                text("SELECT count(*) FROM financial_assessments WHERE id = :assessment_id"),
                {"assessment_id": assessment_id},
            )
            == 0
        )

    # Separate M7 approval is still the only path to a run; M8 only reads that evidence.
    assert assessment_response.json()["decision_version_id"] == decision["id"]
    assert assessment_response.json()["decision_approval_id"] == approval["id"]


def test_financial_input_concurrency_and_transaction_rollback(client, admin_engine, runtime_engine):
    _owner, tenant, base, owner_headers, intake, _assessment = _capacity_project(
        client, "m8concurrency"
    )
    project_path = f"{base}/project-intakes/{intake['id']}"
    initial = client.post(
        f"{project_path}/financial-inputs",
        json=_financial_input_body(intake["current_version"]),
        headers=owner_headers,
    )
    assert initial.status_code == 201, initial.text
    assert initial.json()["version"] == 1
    user_id = UUID(
        _admin_scalar(
            admin_engine,
            text("SELECT id FROM users WHERE email = 'm8concurrency-owner@example.com'"),
        ).__str__()
    )
    direct_body = ProjectFinancialInputCreate.model_validate(
        _financial_input_body(intake["current_version"], expected_version=1, cost_amount="200.0001")
    )
    try:
        with Session(runtime_engine) as session, session.begin():
            set_user_context(session, user_id)
            user = session.scalar(select(UserAccount).where(UserAccount.id == user_id))
            assert user is not None
            create_project_financial_input(
                session,
                user,
                UUID(tenant["id"]),
                UUID(intake["id"]),
                direct_body,
                uuid4(),
                datetime.now(UTC),
            )
            raise RuntimeError("force a rollback after both rows have been flushed")
    except RuntimeError as exc:
        assert str(exc).startswith("force a rollback")

    assert (
        _admin_scalar(
            admin_engine,
            text(
                "SELECT current_version FROM project_financial_input_cases "
                "WHERE project_intake_id = :intake_id"
            ),
            {"intake_id": intake["id"]},
        )
        == 1
    )
    assert (
        _admin_scalar(
            admin_engine,
            text(
                "SELECT count(*) FROM project_financial_input_versions "
                "WHERE project_intake_id = :intake_id"
            ),
            {"intake_id": intake["id"]},
        )
        == 1
    )

    start = Event()

    def append_version(cost_amount: str):
        start.wait(timeout=5)
        return client.post(
            f"{project_path}/financial-inputs",
            json=_financial_input_body(
                intake["current_version"], expected_version=1, cost_amount=cost_amount
            ),
            headers=owner_headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(append_version, "201.001")
        second = pool.submit(append_version, "202.002")
        start.set()
        responses = [first.result(timeout=15), second.result(timeout=15)]
    assert sorted(response.status_code for response in responses) == [201, 409]
    success = next(response for response in responses if response.status_code == 201)
    conflict = next(response for response in responses if response.status_code == 409)
    assert success.json()["version"] == 2
    assert conflict.json()["error"]["code"] == "financial_input_version_conflict"
    assert (
        _admin_scalar(
            admin_engine,
            text(
                "SELECT count(*) FROM project_financial_input_versions "
                "WHERE project_intake_id = :intake_id"
            ),
            {"intake_id": intake["id"]},
        )
        == 2
    )
