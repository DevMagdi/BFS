# AUVRA Intelligence Core — Phase 1 Requirements Baseline

- **Baseline date:** 2026-11-21 (UTC).
- **Status:** Authoritative, newly defined Phase 1 engineering baseline derived from the approved owner-directed product vision and the existing Operations-first implementation direction. It preserves all four company-wide pillars while bounding this delivery to the shared Intelligence Core proof and first Operations vertical slice. The scope decision is recorded in [`../architecture/PHASE1_SCOPE_ACCEPTANCE.md`](../architecture/PHASE1_SCOPE_ACCEPTANCE.md); technical completion and evidence are recorded separately in [`../architecture/PHASE1_ACCEPTANCE_REPORT.md`](../architecture/PHASE1_ACCEPTANCE_REPORT.md).
- **Requirements authority:** This baseline—not the supplemental evidence matrix—is the source of Phase 1 requirements. The requirements are newly defined from the approved vision; no historical checklist is reconstructed or required for this phase.
- **Repository identity:** This repository is `DevMagdi/BFS`; existing package, API, database, and historical documentation names remain BusinessFlow/BFS. This Phase 1 baseline does not authorize a repository-wide rename.
- **Evidence:** [Repository assessment](../architecture/AUVRA_PHASE1_REPOSITORY_ASSESSMENT.md), [scope acceptance](../architecture/PHASE1_SCOPE_ACCEPTANCE.md), and [technical acceptance report](../architecture/PHASE1_ACCEPTANCE_REPORT.md).

## Product definition

**AUVRA is a company-wide Intelligence Core for evidence-grounded business understanding and human-owned decisions.** It is intended to let each company choose which business domains it enables, connect only facts it is authorized to use, preserve provenance and correction history, compute reviewed deterministic observations, and keep human hypotheses, forecasts, recommendations, approvals, and decisions explicit and separate.

“Company-wide” describes extensibility and scope across a company's selected domains. It does not mean every company must adopt every domain, one universal organizational chart, common processes/statuses, one industry model, or one source-of-truth policy.

### Four company-wide product pillars

The approved product direction retains four first-class intelligence pillars:

1. **Business Intelligence** — governed, traceable interpretation of selected business facts and definitions.
2. **Financial Intelligence** — optional financial evidence and calculations only under separately approved finance/accounting definitions and controls.
3. **Operational Intelligence** — evidence about the company's selected operations.
4. **Decision Intelligence** — evidence-bound, human-owned choices and approvals, with any action or outcome process separately authorized.

These are company-wide product pillars, not a claim that the current repository implements all four end-to-end. Operations is accepted as the first implementation proof, **not** the permanent product boundary. Business, Financial, and Decision Intelligence remain first-class and are not subordinated to Operations.

## Basis and status distinctions

| Item | Status |
|---|---|
| Four-pillar product direction and evidence principles | Owner-directed in [`PRODUCT_VISION.md`](PRODUCT_VISION.md). |
| API-only, source-scoped Operations observation lifecycle below | Accepted bounded Phase 1 engineering scope; see the [scope acceptance record](../architecture/PHASE1_SCOPE_ACCEPTANCE.md). |
| `operations_intelligence` package, API router, migration `0029_operations_intelligence`, role wiring, and focused test files | Implemented and merged by PR #41 at `366649e36b9d6cb5eaab53a4050b588cee4efeb6`. Local PostgreSQL-backed, static/type, migration-parity, PR/push CI, and post-merge CI evidence is recorded in the [technical acceptance report](../architecture/PHASE1_ACCEPTANCE_REPORT.md). External domain, customer-data, and production approvals remain separate. |
| Customer/source authority, completeness, freshness, accuracy, retention, production use, or domain-owner approval | Not established by this engineering baseline, source registrations, code, or test files. |

## Design constraints for this baseline

The following constraints are part of the accepted Phase 1 baseline and come from the approved product direction and existing BFS architecture:

- Preserve the existing Python, FastAPI, SQLAlchemy, Alembic, PostgreSQL, Pydantic, and modular-monolith shape unless a measured need justifies change.
- Preserve Tenant/Company authorization, PostgreSQL forced row-level security (RLS), runtime/migrator separation, immutable evidence/version history, correction lineage, and existing finance safety gates.
- Do not add a frontend, dashboard, workspace, visualization, infrastructure platform, microservice, message broker, or workflow engine to the accepted Phase 1 slice.
- Do not add an external dependency without a concrete need and exact release, license, security, compatibility, and transitive-footprint review. No new dependency is added or required for this baseline.
- Do not assume an ERP, CRM, accounting package, source authority, company process, status vocabulary, unit, currency, organization, metric target, SLA, accounting rule, or customer approval.
- Do not run arbitrary SQL or ask an LLM to calculate metrics. Missing, invalid, or contradictory evidence must not be silently converted to zero, merged by name, a target, or a preferred answer.
- Any automated acceptance fixture must be synthetic operational data and labeled as such. Synthetic results are not customer/company results.

## Trust and interpretation contract

These states are deliberately non-interchangeable:

| Concept | Phase 1 meaning | Must not be interpreted as |
|---|---|---|
| **Evidence** | A source-scoped manual assertion or an exact linked Universal Data record revision, with actor, source, event key, and version provenance. | Proof that a source is authoritative, complete, accurate, or approved. |
| **Structural validation** | Pydantic/database contract checks, same-scope references, active actor/source checks, and immutable-chain checks. | Business correctness or independent verification of an underlying event. |
| **Deterministic calculation** | Versioned, replayable counts of recorded operational observations for one selected source and explicit time range. | Throughput, productivity, service quality, causality, completeness, or an owner-approved KPI unless separately defined. |
| **Hypothesis** | A human-authored proposition to be tested; no hypothesis engine is proposed in this slice. | A measured fact or forecast. |
| **Forecast** | A separately governed estimate about a future period; none is proposed in this slice. | Observed evidence or a target. |
| **Recommendation** | A supported, authorized, human-reviewable option; none is proposed in this slice. | A count, calculation, forecast, or automatic choice. |
| **Human-approved decision** | A decision/approval record bound to authorized people and exact evidence under a separately defined policy. | An event record, calculation, generated recommendation, or external action. No generic Operations approval policy is proposed here. |

An analysis may return observations while explicitly marking freshness, completeness, source authority, accuracy, approval, hypothesis, forecast, recommendation, and human decision as unassessed/not produced. A successful API response would not establish customer-data authorization or production readiness.

## Phase 1 — Shared Intelligence Core with an Operations-first proof

### Accepted technical outcome

The bounded slice provides an end-to-end **source-scoped operational-observation lifecycle**:

1. Bind an event to an existing, Company-scoped Universal Data source; preserve source identity rather than merge across sources.
2. Capture a structurally validated observation with an opaque source event key, source subject reference, optional source-defined process reference, source-defined event type, explicit time, optional before/after labels, and an optional bounded description.
3. Distinguish a deliberate manual assertion from a link to an exact Universal Data `business_activity` or `operational_record` revision. A linked revision is supporting evidence, not a verified interpretation of that record.
4. Correct by appending a new event version that names the prior version and a non-empty correction reason. Preserve prior values, actor, timestamps, source reference, and hash chain; never overwrite history.
5. Save a deterministic, versioned observation run over the latest non-superseded event versions for one source and a caller-specified, timezone-aware, half-open time interval.
6. Report only observed counts grouped by exact source-defined event/state labels. Bind the complete bounded input snapshot and result hash; support replay from that snapshot without rereading live source data.
7. Return explicit `no_matching_evidence` when no event matches. Do not emit zero-valued business measures for empty evidence, and do not silently truncate an oversized analysis scope.

This scope establishes a technical evidence-to-observation path. It does **not** establish coverage of every company's operations, a universal workflow engine, a provider connector, a process template, or customer-accepted Operations policy.

### Authoritative Phase 1 requirements

These requirements are newly defined from the approved product vision and accepted Operations-first engineering scope. This baseline is the source of truth for Phase 1; the supplemental evidence matrix in [`../REQUIREMENTS_TRACEABILITY.md`](../REQUIREMENTS_TRACEABILITY.md) maps implementation and test evidence but does not define or replace these requirements.

| ID | Authoritative requirement |
|---|---|
| **AUV-CORE-01** | Retain Business, Financial, Operational, and Decision Intelligence as separate, company-wide capability pillars; the Operations slice shall not become the product boundary. |
| **AUV-CORE-02** | Domains and organizational dimensions shall be optional; no universal process, status taxonomy, organization hierarchy, unit, currency, source, or industry model shall be assumed. |
| **AUV-CORE-03** | Evidence, structural validation, deterministic calculation, hypothesis, forecast, recommendation, and human-approved decision shall remain separately named and represented. |
| **AUV-OPS-01** | An operational observation shall bind to a source registered within the authenticated Tenant's canonical Company and to a source-scoped event key; same-name records shall never be merged. |
| **AUV-OPS-02** | Each observation shall retain the source subject reference, exact event time, event type, optional source-defined process/state labels, authenticated creator, source profile version, and exact evidence reference when supplied. |
| **AUV-OPS-03** | Manual assertions and exact linked Universal Data record revisions shall be explicitly distinguished; neither shall be labeled verified or authoritative by implication. |
| **AUV-OPS-04** | Corrections shall append a new version with the exact prior-version link and correction reason; source facts and earlier evidence versions shall remain immutable. |
| **AUV-OPS-05** | Operations calculations shall be deterministic and versioned, operate on an explicit source/time scope, expose observed input count and exact grouping labels, and bind inputs/results to hashes. |
| **AUV-OPS-06** | Empty evidence shall be reported as `no_matching_evidence`; no missing or empty value shall be silently zero-filled, truncated, or converted to a preferred answer. |
| **AUV-OPS-07** | Replay shall use the saved input snapshot and calculation version, verify both stored hashes, and report a mismatch rather than silently changing a prior result. |
| **AUV-OPS-08** | The slice shall not infer event semantics, workflow transitions, cycle time, throughput, productivity, service levels, targets, forecast, recommendation, approval, or external action. |
| **AUV-SEC-01** | Every read/write shall resolve Company scope from verified membership/permissions; event and analysis tables shall use forced tenant-and-company RLS, same-scope references, and least-privilege runtime grants. |
| **AUV-SEC-02** | New event/analysis history shall be append-only at application and database privilege/trigger boundaries. A correction is a new version, not an update/delete. |
| **AUV-SEC-03** | Default Operations read/write shall remain owner/admin-only under the existing fixed role model; member/steward access is not inferred and requires an explicit policy decision. |
| **AUV-NFR-01** | The Phase 1 slice shall not add UI/UX, new runtime/development packages, services, data stores, or background workers. |
| **AUV-QA-01** | Automated acceptance scenarios shall use synthetic operational data only and identify it as test-fixture data. |
| **AUV-RETENTION-01** | Preserve exact linked Universal Data revisions with `ON DELETE RESTRICT`; do not infer or add an Operations retention/purge policy; the existing Universal Data purge-plan/procedure must keep referenced revisions and Operations evidence intact. |

### Explicit non-goals

- UI/UX, dashboards, visualization, general-purpose BI workspace, or public metrics endpoints.
- Any remote/live connector, scheduled sync, generic connector framework, arbitrary SQL, or arbitrary user formula.
- A fixed process, lifecycle, status/transition graph, SLA, target, benchmark, service-quality score, cycle-time/throughput/productivity rule, or causal claim.
- Forecasting, optimization, generated hypotheses, recommendations, approval, decision execution, external writeback, or a universal decision workflow.
- Customer, employee, supplier, or other identity resolution/merging by name or inferred attributes.
- Any change to existing financial safety gates, financial policy, or Core Financials enablement.
- A new generic retention period or purge policy. Operations-event retention remains an owner decision; the exact-revision foreign key uses `ON DELETE RESTRICT`, and the existing Universal Data purge path blocks canonical-revision dependencies without purging Operations history. Any future retention change needs explicit owner policy and regression evidence.

## Technical acceptance criteria

Every criterion below must have current-branch evidence for technical closure. The scope is accepted in [`../architecture/PHASE1_SCOPE_ACCEPTANCE.md`](../architecture/PHASE1_SCOPE_ACCEPTANCE.md); technical completion, CI status, and external business/data-use decisions are reported separately in [`../architecture/PHASE1_ACCEPTANCE_REPORT.md`](../architecture/PHASE1_ACCEPTANCE_REPORT.md). Technical closure is not customer validation, a complete security audit, source-authority approval, or production authorization.

| AC | Acceptance evidence |
|---|---|
| **AC-01** | New Company-scoped tables have forced RLS, same-scope foreign keys, and migration-owner checks. Event streams, versions, and analyses are append-only with runtime SELECT/INSERT-only grants. The derived current-version pointer is trigger-maintained, with only the required INSERT and two projection-column UPDATE privileges; a database guard must reject pointers whose version or occurrence timestamp does not match the latest version. |
| **AC-02** | Unauthorized users, members without Operations permission, other Tenants/Companies, and pooled database sessions cannot read/write events or analyses outside authorized scope. |
| **AC-03** | A manual observation is accepted only for an enabled manual-entry source explicitly classified as `manual_assertion`; a linked observation binds to an exact same-source operational/business-activity revision. |
| **AC-04** | Duplicate idempotency keys replay only an identical request; key reuse with changed content conflicts; duplicate source event keys cannot create parallel first versions. |
| **AC-05** | Corrections append, preserve prior hashes/versions, require the current leaf and correction reason, and reject stale/branched/invalid references. |
| **AC-06** | Analysis includes only the selected source, time range, and current leaf event versions; an oversized input scope fails closed rather than producing a partial count. |
| **AC-07** | Empty input is explicit `no_matching_evidence`; type/state counts are exact labels; no target, assumed status, zero-fill, forecast, recommendation, or source-authority claim appears. |
| **AC-08** | Saved analysis replay verifies snapshot/result hashes and reproduces the stored result exactly; evidence pagination identifies event versions and linked source revisions. |
| **AC-09** | Synthetic unit and PostgreSQL acceptance tests cover success, correction and full-chain/current-pointer tamper detection, replay and saved-snapshot hash tamper detection, empty/over-limit input, source/evidence mismatch, authorization, RLS/pool isolation, append-only behavior, and migration upgrade/drift. |
| **AC-10** | Ruff format/lint, mypy, database-free unit tests, isolated PostgreSQL-backed tests, migration drift, complete diff review, and current-branch CI pass for technical closure. Run destructive fixtures only against uniquely named disposable databases with separate runtime/migrator/admin roles; verify migration-target isolation. |
| **AC-11** | Exact linked Universal Data revision retention is explicit: the Operations reference cannot be silently cascaded/deleted, and the existing purge plan/procedure blocks its dependencies without changing retention policy or deleting Operations events. |

## Open governance decisions (do not assume)

These policy-dependent decisions do not block technical closure of the accepted Phase 1 baseline; they block the corresponding business, customer-data, KPI, or production use. Before such use or richer Operations Intelligence, an accountable company/domain owner must decide source(s), permitted fields, source authority, completeness, freshness, accuracy/correction semantics, retention and purge treatment, user/steward roles, required human review, process/state meaning, units, and specific operational questions/measures. Forecasts, targets, SLAs, recommendations, approval, and action each require their own policy/owner gate. No approval is inferred from a source registration, a passing test, or a human-entered field. The exact open decisions and required owners are recorded in [`../architecture/PHASE1_SCOPE_ACCEPTANCE.md`](../architecture/PHASE1_SCOPE_ACCEPTANCE.md).
