# M7 — Human decision and approval workflow

## Status and boundary

M7 adds a bounded, human-controlled decision record and review workflow on top of M6 capacity assessments. It does not implement the broader “Should we accept this project?” product decision. The workflow records one of four explicit capacity-stage outcomes; it does not calculate financial feasibility, make a commercial accept/reject decision, create a canonical project, assign resources, execute an action, or call an AI/LLM.

`proceed_to_financial_evaluation` records a human choice to take a project to a separate future financial evaluation. It does not run or approve that evaluation and does not authorize an external handoff. `capacity_declined` means only that a human ended the capacity-review stage based on a `not_feasible` M6 assessment; it is not a final commercial rejection. Every decision and response continues to report `business_feasibility_status = "incomplete"` because no approved finance model exists.

The bounded API/database contract is implemented in migration `0006_decision_workflow`, package `businessflow.project_decisions`, and `project_decision_routes.py`.

## Data model and immutable history

There is at most one `project_decision_cases` stream per company/project intake. A case stores a mutable head pointer only. Its `project_decision_versions` rows are append-only and carry a monotonically increasing version, the exact prior version they supersede, the human outcome and rationale, outcome-specific conditions/requested information, and a hash-bound evidence reference. A deferred composite FK and database guard ensure the case pointer identifies the exact current version and that each new version appends to the previous head; runtime grants do not allow versions to be updated, deleted or truncated.

Every decision binds to the exact M6 `capacity_assessment` ID, assessment calculation version, input/result hashes, capacity status, and the exact M6 intake version ID/number/hash. It also records the linked M5 metric-run ID, catalog/calculation versions and input/result hashes. A composite FK binds assessment and intake to the same tenant, company and project. The `evidence_hash` and `decision_hash` reuse the existing canonical `content_hash` helper; there is no second hashing implementation. Stored snapshots are checked against their row fields and referenced immutable M6/M5 snapshots before an approval is accepted.

A `project_decision_approvals` row is an immutable review of one exact decision version. It stores the decision/evidence hashes and all assessment/intake/M5 identifiers and hashes. A uniqueness constraint permits one review result per decision version (`approved` or `changes_requested`); a changed submission must be a new decision version. Database insert guards verify the bound hashes/IDs match the referenced version, the version is still the case head, and the approver differs from the submitter. Approval does not change the submitted decision row.

All M7 tables use forced PostgreSQL RLS requiring the verified transaction-local tenant and company contexts. Composite FKs, server-side membership/permission checks and row-level locks supplement RLS. A database trigger on M6 assessment inserts locks the corresponding intake row so assessment refresh and M7 decision/approval operations serialize on the same project.

## Outcomes and required content

All decisions require a nonblank rationale of at least ten characters. Text and list lengths are bounded. Requests reject unsupported or extra fields.

| Outcome | Conditions and eligibility |
|---|---|
| `proceed_to_financial_evaluation` | Requires a feasible or conditionally feasible M6 capacity status. A conditional assessment must include at least one human-recorded condition. This is a workflow record only; finance remains incomplete. |
| `hold` | Requires at least one explicit condition for resuming review. A later submission with the current `expected_version` supersedes the held version and resumes the human workflow. |
| `request_information` | Requires at least one specific requested-information item. The next version must include a nonblank `information_response`; that response is stored in the new version and points back to the exact request being answered. |
| `capacity_declined` | Allowed only when the linked M6 capacity status is `not_feasible`. It closes only the capacity-review stage and never asserts commercial rejection or financial infeasibility. |

A decision version may include conditions where useful; `requested_information` is accepted only for `request_information`. The service and database both enforce outcome/status constraints. Decisions do not update intake, M6 evidence, capacity, projects or assignments.

## Lifecycle and API

All routes are under `/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}/decisions`; company scope is resolved from the authorized tenant and is never caller-selected.

- `POST /decisions` — submit the first decision for an intake, or append a superseding version with the current `expected_version` and a new assessment ID. When the prior outcome requested information, supply `information_response`; the service derives the response-to link from the current version.
- `GET /decisions?limit=&offset=` — bounded, paginated decision history, newest first, with current-version, approval and evidence-freshness fields.
- `GET /decisions/{decision_id}` — retrieve an exact immutable version and its review record.
- `POST /decisions/{decision_id}/approval` — append one review result for the current version. The caller must submit the `expected_decision_hash` and `expected_evidence_hash` returned for that exact version. The result is `approved` or `changes_requested`; both require an approver rationale.

Each submission uses a database lock and `expected_version` check. Concurrent attempts to supersede the same head cannot both succeed. Only the current case version can be reviewed. A superseded, stale, already-reviewed or hash-mismatched version cannot receive an approval. A `changes_requested` result is preserved; the submitter must create a new version rather than edit the old one.

## Authorization and approval policy

Authorization is enforced in the service layer and PostgreSQL RLS; URL IDs alone do not grant access. The current M2–M7 policy is explicit:

- **Owner and admin:** `decision:read`, `decision:write` and `decision:approve` within their active tenant/company. They may submit; either may review a decision submitted by a different user.
- **Member:** `decision:read` only. Members cannot submit or approve.
- **Separation of duties:** Supported and mandatory for this workflow. The authenticated submitter cannot approve the same version. The rule is enforced in the service and by a database trigger. At least two active owner/admin principals are needed to complete a submission/review cycle; no self-approval exception exists.
- One approval record is allowed per decision version. A change or new evidence requires a superseding decision version and a new review. Approvals are never transferred to a superseding version.

Denied self-review and stale-evidence review attempts emit audit events. Successful submissions, supersessions, approvals and requests for changes record actor, target IDs, versions, outcomes and hashes without copying rationale or evidence payloads into audit metadata.

## Stale evidence and integrity behavior

Before submission and approval, the service validates the stored M6 assessment/result hashes, the linked intake-version hash, M5 metric-run hashes and scope/period references. It marks evidence stale when the intake has advanced, a newer capacity assessment exists for the intake, or a newer M5 run for the same company and exact period has different input/result hashes. It marks evidence invalid when a stored snapshot/hash or its cross-reference does not agree with its immutable source rows. Stale or invalid evidence blocks new submissions/approvals; existing decision/approval history is not rewritten. Read responses expose `evidence_freshness` and stable `staleness_reasons` so an earlier approval remains auditable even if its evidence later becomes stale.

M6 currentness and commitment completeness remain human attestations, not an arbitrary freshness window or independent truth check. A source change is detectable here when represented by a newer M6 assessment or a newer persisted M5 metric run. This workflow does not independently prove that source systems are current when no refreshed evidence run exists; a human must still attest currentness under M6.

## Verification and remaining limits

The M7 PostgreSQL integration tests cover the API lifecycle, role policy, exact hash binding, stale assessment and M5 evidence, RLS, company/tenant composite-FK rejection, append-only guards, transactional rollback, optimistic concurrency, and OpenAPI paths. Migration downgrade/re-upgrade and Alembic drift are verified separately as part of the delivery evidence.

No finance engine, accounting policy, commercial acceptance/rejection, recommendation, resource matching/assignment, workflow automation, notification delivery, frontend, AI/LLM, external action, outcome measurement, or final business-feasibility result is included. These remain separate product, finance and security decisions.
