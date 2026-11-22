# M6 bounded capacity-only project assessment

## Decision boundary

This is the bounded M6 backend delivery: an authorized human may assess a proposed project's explicit **weekly team-hour demand** against current company-aggregate capacity evidence. It is not the broader “Should we accept this project?” product decision. The API neither accepts nor rejects a project, recommends an option, approves a decision, assigns people, edits capacity, creates a canonical project, or takes an operational action.

The result contains two distinct statuses:

- `capacity_status` is one of `feasible`, `feasible_with_conditions`, `not_feasible`, or `incomplete`, and describes only the supported capacity comparison below.
- `business_feasibility_status` is always `incomplete`. Finance inputs and approved commercial formulas/policies are not implemented; missing financial data is not zero.

No project skill requirements, employee-to-project matching, department matching, utilization measure, or delivery-capability claim is supported. The comparison is company-aggregate and does not allocate demand among employees.

## Intake and version lifecycle

A project intake has a normalized unique company-scoped `project_code`, name, Monday-start period (`start_week_date` plus `week_count`), declared total demand in `team_hours`, an optional weekly demand list, a distribution-confirmation flag, and optional notes. Periods are bounded to 1–52 Monday–Sunday weeks. Demand values are nonnegative decimal hours with two decimal places and explicit `team_hours` units; input size and pagination are bounded.

`total_demand_hours` is **not** distributed by the service. To use base intake demand in a capacity assessment, a human must provide exactly one demand value for every week and set `demand_distribution_confirmed` to true; the values must sum to the declared total. A partial or unconfirmed distribution remains incomplete. Missing weeks are not treated as zero. Duplicate weeks, out-of-period weeks, non-Monday boundaries, inconsistent totals, unsupported units, duplicate project codes, and stale expected versions are rejected.

Each create/update writes a content-hashed immutable `project_intake_versions` snapshot. Updates require `expected_version`; a row lock and database guard allow only a one-version advance, and a deferred composite foreign key ensures the current-version pointer refers to a stored version. Stale concurrent edits receive a conflict instead of overwriting newer intake. Historical versions remain readable. An assessment may reference the current version or an explicitly requested earlier version.

## Assessment lifecycle and API

All paths below are under `/api/v1/tenants/{tenant_id}`. The tenant is accepted only in the path and verified against the authenticated principal. The company is resolved from that tenant's canonical active company; clients cannot select a `company_id` in a body or query. Owner/admin users with `company_context:write` may create/update intakes and request assessments. Authorized tenant members with `company_context:read` may read intake versions, evidence, and history.

- `POST /project-intakes` — create version 1.
- `GET /project-intakes` — bounded, paginated company intake list.
- `GET /project-intakes/{intake_id}` — current intake version.
- `PUT /project-intakes/{intake_id}` — create the next version with `expected_version` and a complete intake payload.
- `GET /project-intakes/{intake_id}/versions/{version_number}` — retrieve immutable intake evidence.
- `POST /project-intakes/{intake_id}/assessments` — create an immutable assessment and a fresh linked M5 metric run. The request may select an intake version, submit two explicit assumptions, and/or supply a hypothetical scenario.
- `GET /project-intakes/{intake_id}/assessments` — bounded, paginated assessment history.
- `GET /project-intakes/{intake_id}/assessments/{assessment_id}` — assessment result and evidence.
- `GET /project-intakes/{intake_id}/assessments/{assessment_id}/evidence` — the same immutable evidence-oriented detail representation.

The required human assumptions are `capacity_data_current_confirmed` and `recorded_commitments_complete_confirmed`. Both default to false. Without affirmative confirmation, the assessment records blockers and returns `incomplete`; the service does not infer freshness or completeness. These are requester attestations, not independent verification.

A scenario contains a bounded name and exactly one Monday-week demand value for each intake week. It is stored with `hypothetical: true`, is included in the evidence hash, and changes only that assessment's demand input. It does not mutate the intake version, metric inputs, canonical capacity, projects, or resource assignments. When all other required evidence and attestations are present, a scenario with no observed shortfall is `feasible_with_conditions`; a confirmed weekly deficit is `not_feasible` for that hypothetical schedule, not a project decision. Missing or unsupported required evidence still yields `incomplete`.

## Calculation and weekly evidence

The assessment does not implement M5 capacity or assignment logic a second time. In the same repeatable-read transaction, it creates and stores an M5 `metric_run` for the intake period using catalog `businessflow.metrics.v1` and calculation `businessflow.capacity-weekly.v1`, and verifies the run's snapshot hashes. It consumes M5's per-week values:

- `capacity.complete_available_hours` and `complete_capacity_basis`;
- `assignments.active_employee_committed_hours_per_week` and recorded commitment counts; and
- `comparison.capacity_minus_recorded_commitments_hours`.

M5's formula remains `scheduled_hours - non_project_hours - unavailable_hours`; its latest-version selection, Monday–Sunday convention, active-employee scope, and full weekly assignment treatment remain authoritative. The assessment uses M5's capacity-minus-recorded-commitments result directly. It does not subtract commitments a second time. If there are no active committed-assignment rows, M5 leaves commitment hours/residual unknown; only an explicit completeness attestation allows M6 to use zero recorded commitments. Without it, missing assignment records are not zero and prevent a conclusion. Inactive-employee commitments and unavailable capacity comparisons are surfaced as blockers.

Every assessment week preserves the M5 capacity, assignment, and comparison evidence, demand status/source, available capacity, existing active commitments, residual before proposed demand, projected residual, deficit/surplus, M5 run reference/hashes, capacity record IDs, and committed/proposed assignment source IDs. Full M5 input facts and M4 lineage remain available from the immutable referenced metric run. A deficit in any week is retained as-is; weeks are not averaged, prorated, or netted against surplus in another week. Other proposed M5 assignments are reported separately, not counted as committed, and trigger a condition rather than being silently subtracted.

The assessment input snapshot/hash binds tenant/company, the exact intake version and hash, period, M5 run ID and catalog/calculation/input/result versions and hashes, assumptions/attestation metadata, and optional scenario. The result snapshot/hash contains every week, status, warnings, blockers, limitations, and explicit non-action flags. Assessment rows and intake-version snapshots are append-only for the runtime role and protected by forced RLS. A source change results in a new M5 run and new assessment; it cannot rewrite prior evidence.

Import lineage is a trace to a source row, not proof that the current canonical value still matches the import-time fingerprint. The service does not define an arbitrary freshness window. A human must attest that evidence is current; absent that attestation, a reliable status is blocked.

## Capacity status contract

| `capacity_status` | Meaning |
|---|---|
| `feasible` | Full confirmed weekly demand; M5 has known confirmed capacity for every week; currentness and commitment completeness are explicitly attested; no unsupported/conflicting evidence or other proposed assignments; no weekly deficit. This says only that the aggregate recorded capacity comparison fits the supplied demand under those assumptions. |
| `feasible_with_conditions` | The same weekly comparison has no observed shortfall, but at least one condition remains visible—for example estimated capacity, other proposed assignments, or an explicitly hypothetical demand scenario. The conditions must be reviewed by the human. |
| `not_feasible` | With confirmed capacity, complete confirmed demand, currentness and commitment-completeness attestations, at least one individual week has a deficit after recorded existing commitments. The status describes capacity only; it does not reject or otherwise decide the project. |
| `incomplete` | Any required demand, capacity, currentness, commitment-completeness, scope/version, or conflict evidence is missing, stale/unconfirmed, unknown, or unsupported. A deficit against estimated capacity also stays incomplete pending confirmation. No unsupported conclusion is made. |

Warnings and blockers use stable codes. Regardless of capacity status, `business_feasibility_status` stays `incomplete` until finance data and approved business rules are separately implemented and reviewed.

## Security and persistence

Migration `0005_capacity_assessment` adds `project_intakes`, `project_intake_versions`, and `capacity_assessments`. All three tables have forced RLS policies requiring both transaction-local, membership-verified `app.tenant_id` and `app.company_id`. Company context is set from the canonical company resolver, not client input. The non-owner `bf_runtime` role may select/insert immutable versions and assessments but cannot update, delete, or truncate them. It may update only the intake pointer/code/timestamp columns; a database guard requires exactly one version advance. Composite foreign keys bind intake, version, metric run, requester membership, tenant and company. Audit events record create/update/assessment metadata without copying source payloads into audit details.

## Deliberate exclusions

No finance formulas, prices, costs, revenue, margin, cash-flow or collection estimates; no four-option recommendation set; no final acceptance/rejection; no approval workflow; no employee assignment, capacity mutation, hiring, outsourcing or other operational action; no AI/agent decision; and no frontend or bilingual redesign are included. The result is human capacity-only decision support and requires human interpretation.
