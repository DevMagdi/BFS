# BusinessFlow AI — Milestone Roadmap and Acceptance Gates

- **Status:** Proposed sequencing; no product milestone is declared complete by this roadmap.
- **Rule:** A milestone advances only after implementation, tests, security review, documentation and acceptance evidence are reviewed. Do not start the next milestone while the current one is unverified.

## Subsequent implementation authorization — 2026-11-21

A later, explicit delivery task authorized a limited backend foundation, documented in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md). This supersedes the earlier no-implementation direction below only for that bounded code scope. It does not approve the proposed technical baseline as a product decision, complete the full sequential Milestone 0/1 gates, accept product/finance behavior, or authorize features outside that implementation note. The historical review and gate statements below remain accurate for the documentation review at the time they were written; use the backend guide for the current code status and verified local commands.

## Pre-Milestone 0 — Initial technical assessment (this review)

- **Objective:** Inspect the repository/environment and supplied OSS projects, then present the revised architecture, domain/ERD, tenant isolation, identity/authorization, deployment, OSS reuse/licensing/security review, MVP, risks and decisions.
- **Deliverable:** The documentation package under `docs/`, including `TECHNICAL_BASELINE_PROPOSAL.md`, `MVP_ENTITY_CATALOG.md`, `MVP_DECISION_MODEL.md`, and `FINAL_REVIEW_CORRECTIONS.md`.
- **Owner-confirmed directions:** `Tenant` and `Company` remain separate with strict 1:1 MVP cardinality; self-hosted/on-prem first with future cloud portability; first-party plus OIDC/enterprise identity with proper MFA, linking, recovery, sessions and authorization. Concrete technology choices remain unapproved.
- **Traceability:** `REQUIREMENTS_TRACEABILITY.md` is **BLOCKED — awaiting the exact original Part 2 requirements**. Do not reconstruct, paraphrase or replace the original 24 requirements.
- **Acceptance gate:** Owner reviews/revises the full assessment and explicitly approves the technical baseline before Milestone 0. No milestone is authorized to start by this review or its documentation-only corrections.

## Provisional MVP definition — requirements only

The founder's direction is to define a narrow MVP now, while customer discovery continues separately. The provisional product is new-project feasibility decision support for project-based services companies, initially hypothesizing Software & IT Services. The current requirements baseline is [`BRD.md`](BRD.md), with scope boundary [`MVP_SCOPE.md`](MVP_SCOPE.md), workflows [`USER_WORKFLOWS.md`](USER_WORKFLOWS.md), screens [`SCREEN_INVENTORY.md`](SCREEN_INVENTORY.md), and acceptance criteria [`MVP_ACCEPTANCE_CRITERIA.md`](MVP_ACCEPTANCE_CRITERIA.md). These are the reviewed provisional documentation baseline, not a customer-validated Product Baseline or authorization to implement.

The core is file-based intake, data-quality/freshness review, deterministic weekly capacity and finance observations, four bounded options (current resources, manual reallocation, phase/defer, decline), a source-linked brief, and a human-recorded choice with explicit abstention. Hiring/outsourcing estimates, cash observations, internal follow-up tasks, and outcome tracking remain conditional/Should Have. No preferred option appears without the required business/finance policy approval. See [`MVP_SCOPE.md`](MVP_SCOPE.md) for the complete boundary and planning-only demonstrable slices.

Customer discovery remains the already-authorized 9–12 founder-led exploratory interviews; it may revise the hypothesis but does not block defining this provisional scope. Keep the existing technical-baseline approval, finance-policy approval, and exact-original-24 traceability gates separate. **Do not start Milestone 0 or any implementation.**

## Milestone 0 — Repository initialization and engineering standards

**Scope:** Establish project structure, language/runtime baselines, code style, dependency locks, environment configuration, secrets policy, README/development guide, ADR template, CI, lint/type/test and security/dependency checks. No business feature or frontend screen.

**Acceptance gates:**
- Clean install from documented commands and locked dependencies.
- CI runs formatting/lint/static checks and unit tests on the owner-approved language/runtime baselines.
- Secret scanning, safe configuration examples and test-data policy are in place.
- CI can provide the owner-approved database for migration/isolation test jobs. If PostgreSQL/RLS is selected after technical approval, CI must provide real PostgreSQL; local Docker absence does not excuse skipping those approved tests.
- Development and contribution documentation is accurate; no failing check is hidden.

## Milestone 1 — Product and domain architecture

**Scope:** Finalize bounded contexts, universal model, MVP boundaries, first decision use case, module ownership/interfaces, API principles and ADRs. Review the logical ERD and secure tenancy plan with the product owner.

**Acceptance gates:**
- Domain vocabulary distinguishes tenant/company, user/employee, access/job role, source fact/metric/recommendation/approval/action/verification/outcome.
- Project-acceptance inputs, provenance/freshness, explicit assumptions, abstention gates and candidate capacity/cost/revenue/contribution/collection formulas are documented in `MVP_DECISION_MODEL.md`; finance-owner approval is still required for every listed formula/policy before recommendation behavior is accepted.
- The logical entity catalog identifies MVP/deferred scope, ownership, keys/FKs, tenant/company isolation, uniqueness and integrity constraints; no external ERP schema is canonical.
- Owner approves the detailed one-company-per-tenant constraint and future 1:N extension, supported self-hosted deployment and hybrid identity/account lifecycle before database implementation.
- Exact original Part 2 requirement wording is available and traced verbatim before claiming traceability complete. Retention, residency, installation topology and other open policy decisions are separately resolved before the related implementation.

## Milestone 2 — Database and core backend foundation

**Scope:** Database connection/session boundaries, reviewed migrations, tenant/company, identity/membership, access roles/permissions, organization baseline, audit foundation, repository/service boundaries and test harness. PostgreSQL/Alembic are candidates in the technical proposal, not approved selections.

**Acceptance gates:**
- The owner-approved database migrations succeed and constraints/indexes are verified.
- The selected runtime role and tenant/company context fail closed under the approved isolation design.
- Backend authorization and the database's approved isolation controls are both exercised; cross-tenant read/write/delete, pooled-connection reuse and background-job tests pass.
- Unauthorized users cannot access company/department-scoped or compensation-restricted records.
- Unit/integration/security tests and lint/static checks pass; no critical security issue remains unresolved.
- If PostgreSQL/RLS is approved, test against real PostgreSQL with a non-owner/non-BYPASSRLS runtime role; SQLite-only tests do not validate those controls.

## Milestone 3 — Company context and canonical business model

**Scope:** Company, business units/departments, employees/job roles/resources/capacity, parties, projects, contracts, financial facts, commitments, goals/KPIs/policies as approved; data source registry and first lineage references.

**Acceptance gates:**
- Canonical relationships are constrained and tenant-aware.
- Imported source identifiers and company context are preserved.
- Company/project/resource/party operations do not depend on a particular ERP schema.
- Domain invariants and sensitive access are tested and documented.

## Milestone 4 — Data integration and data quality

**Scope:** File import foundation, source registration, versioned mapping, raw/normalized/canonical staging, validation, reconciliation, duplicate handling and quality reports.

**Acceptance gates:**
- CSV/XLSX sample data can be previewed, mapped, validated and explicitly confirmed.
- Every promoted fact can be traced to a source row/file hash and mapping version.
- Invalid/contradictory/stale data is surfaced, not silently repaired; imports are idempotent and tenant-isolated.
- Malicious files/content, size limits, formula injection and secrets/PII logging are tested.

## Milestone 5 — Metrics and analytical foundation

**Scope:** Versioned metric catalog, deterministic calculations, reproducible runs and evidence/lineage for the core case. Cash/invoice/receipt observations are conditional/Should Have, not a required release component. Benchmark DuckDB/Polars and semantic engines only after a separate owner-approved evaluation decision; none is a baseline dependency.

**Acceptance gates:**
- Finance/product owners approve versioned definitions and exact-decimal golden examples before any financial decision metric is treated as accepted. Missing owner policy means no decision recommendation.
- Calculations record input snapshot, formula/policy version, original currency and approved FX source where applicable, quality limits and lineage.
- Analytics benchmarks measure throughput, memory, concurrency, latency, failure behavior, tenant isolation and operating cost on representative inputs.
- Only if the owner separately authorizes semantic-engine evaluation: WrenAI tests intent clarification/read-only question-to-query correctness; Cube tests semantic model/join and security-context correctness. Apply financial gold values, tenant/permission controls, safety and operational cost where applicable. Test Arabic/English only if those are owner-approved product languages.
- No candidate is adopted without an exact release/commit, selected package/path and license scope, security/advisory and dependency/SBOM review, upstream tests, and BusinessFlow-specific tests. Do not make an engine canonical truth; BusinessFlow owns metrics, decisions, permissions, approvals and outcomes. If neither semantic candidate passes, keep the internal deterministic service.

## Milestone 6 — First business-intelligence vertical slice

**Scope:** Demonstrate “Should we accept this new project?” using the provisional BRD: proposed scope/effort, skill- and week-specific capacity, finance observations under approved formulas, four core options (current resources, manual reallocation, phase/defer, decline), and an evidence-linked brief. Cash, hire/outsource estimates, and outcome observations are optional/conditional; no collection probability or risk score.

**Acceptance gates:**
- Runs on structured, reconciled and authorized data, not hardcoded demo answers.
- Required inputs and abstention follow `BRD.md` and `MVP_DECISION_MODEL.md`; partial observations are not a preferred-option recommendation.
- Capacity formulas and any financial formula/policy used for a recommendation have an approved, versioned owner definition and reviewed golden examples; no universal margin, utilization or cash threshold is assumed.
- Returns evidence, input/formula/policy version, assumptions, currency, limitations and per-option impact.
- Deterministic scenario results match reviewed tests with traceable lineage and immutable scenario versions.
- Validate first with synthetic fixtures. Any later real-data pilot requires separate owner/security approval and an authorized data-handling plan.

## Milestone 7 — Decision and approval engine

**Scope:** Decision/recommendation versions, evidence, approval policy, human decision records, history, revision/rejection and separation-of-duties rules.

**Acceptance gates:**
- Recommendation never equals approval; approval identifies authenticated human, exact version/option and payload hash.
- An agent/model cannot approve itself, forge an approver identity, alter a decision after approval, or bypass role/scope checks.
- Concurrent approvals, replay, stale approvals and cross-tenant attempts are tested.

## Milestone 8 — Controlled action execution

**Scope:** Approved action queue, policy enforcement, idempotency, retry semantics, sanitized audit, verification and failure recovery. The proposed first action is a reversible BusinessFlow-internal project-assessment follow-up task only; it must not modify a customer/external system. Product owner may instead defer execution and keep the MVP recommendation-and-approval-only. See `MVP_DECISION_MODEL.md` and ADR 0008; neither option is yet approved.

**Acceptance gates:**
- Only an authenticated human-approved, unexpired exact payload hash can execute; a model/service principal cannot approve.
- First action requires `decision:read` and `action:request`, plus separately authorized human approval/execution; no external target is configured.
- Action ID/idempotency uniqueness is tenant/decision/recommendation/action scoped; retries reuse the same ID. Transient failures have bounded backoff; permanent/authorization failures are surfaced, not blindly retried.
- Requested, approved, queued, attempted, completed, verified, cancelled and failed states are distinct; cancellation is audited, not a delete.
- Action, audit and outbox write atomically; a fresh, separate read-back verifies persisted payload/state before marking verified. Failure/retry/replay/pool-isolation tests pass against the owner-approved database (real PostgreSQL if selected).
- High-impact/irreversible operations remain disabled. If no justified internal task is accepted, action execution is deferred.

## Milestone 9 — Outcome tracking and evaluation

**Scope (optional):** Expected-versus-actual measures, deviations, assumption review and quality metrics, only if separately prioritized after the core decision slice.

**Acceptance gates if included:**
- Outcomes retain the original recommendation and calculation version.
- The system can associate realized results with decision evidence and explain variance.
- Outcome measures do not claim causality without a defensible method; model/calculation errors can be audited and corrected through new versions.
- This milestone is not a blocker for the core MVP release.

## Milestone 10 — Product UX and MVP release

**Scope:** Owner/manager decision center, source management, proposed-project intake, scenario comparison, evidence/brief, user/role management, error/empty/denied states, responsive UI and the core end-to-end flow. Internal action and outcome screens are optional/Should Have.

**Acceptance gates:**
- An authorized user completes import → reconcile → project evaluation → scenario comparison → traceable brief → human decision and any policy-required approval → authorized export on an approved synthetic/reference dataset.
- The owner-approved browser/integration test stack exercises tenant/role boundaries, failure and insufficient-data states.
- UX does not present demo values as live data or a human decision as a system recommendation; task execution/verification is not claimed unless the optional action feature is separately approved and shipped.
- Security, deployment, backup/restore, observability and operating runbooks are reviewed before release.

## Standard evidence bundle for every milestone

Record the exact commit/branch and commands; environment/tool versions; test totals and failures; migrations and selected database version; lint/type/security results; known unverified areas; reviewer/acceptance decision; documentation updates; dependency/license inventory changes; and remaining risks. “Test file exists” or “endpoint returns 200” is not acceptance evidence.

## Git and sequencing note

Use the branch selected by the current Arena coding session; do not switch, rename, or create another branch in that session. A documentation or code delivery may be merged only when its own requested scope is explicitly authorized and its applicable tests, security review, documentation, and acceptance gates have verified evidence. Such a delivery does not by itself mark unrelated product or finance milestones complete.

## Phase 1 product and market validation gate (before Product Baseline)

- **Status:** The competitive sweep is complete and recorded in [`PHASE_1_COMPETITIVE_VALIDATION.md`](PHASE_1_COMPETITIVE_VALIDATION.md). A narrow provisional MVP is now specified in [`BRD.md`](BRD.md), [`MVP_SCOPE.md`](MVP_SCOPE.md), [`USER_WORKFLOWS.md`](USER_WORKFLOWS.md), [`SCREEN_INVENTORY.md`](SCREEN_INVENTORY.md), and [`MVP_ACCEPTANCE_CRITERIA.md`](MVP_ACCEPTANCE_CRITERIA.md). The approved 9–12 exploratory interviews remain separate and do not block this requirements definition; see [`PHASE_1_DISCOVERY_PROTOCOL.md`](PHASE_1_DISCOVERY_PROTOCOL.md). This is not implementation or technical-baseline approval.
- **Finding:** Kantata, Projectworks, and Microsoft Project Operations publicly describe substantial overlap with the proposed project-intake, capacity, margin, scenario, cash, and management-workflow needs. Customer stories and vendor capability claims do not independently validate results or establish BusinessFlow demand. A broad “business decision layer” is not a demonstrated differentiator.
- **Gate:** The Software & IT Services segment, buyer, willingness-to-pay, final positioning, and business need remain hypotheses. The BRD/scope are provisional product requirements, not a validated Product Baseline. The founder-authorized 9–12 exploratory interviews continue independently; use anonymized notes only and no recording, identifying quotes, confidential data, or customer-system access. No vendor outreach/demo, trial account, subscription, spend, product implementation, or Milestone 0 is authorized by this documentation.
- **Boundary:** This product-validation gate does not replace or alter the earlier technical assessment, the proposed `MVP_DECISION_MODEL.md`, the requirement-traceability hold, or technical-baseline approval gates.

## Remaining gates after the documentation baseline

The provisional documentation baseline is reviewed and records the current product direction; it does not complete a product milestone or approve implementation. Founder-led 9–12 exploratory interviews continue separately under [`PHASE_1_DISCOVERY_PROTOCOL.md`](PHASE_1_DISCOVERY_PROTOCOL.md). The exact original 24 Part 2 requirements remain blocked in `REQUIREMENTS_TRACEABILITY.md`; the BRD map is not a substitute. Technical baseline, finance formula/policy, identity/deployment/security, and exact-requirement gates remain separate. Do not begin Milestone 0 or implementation.
