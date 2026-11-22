# BusinessFlow AI — Initial Technical Assessment

- **Status:** Historical technical assessment for the earlier project-feasibility hypothesis. At the time of this report, no technical-baseline approval or implementation authorization was recorded. Bounded M2–M8 backend slices have since been delivered under separate task authorizations; that work does not approve this full technical proposal. The M9 product direction and current gaps are documented in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md) and [`architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md). The 24-item traceability section remains blocked pending exact original wording.
- **As of:** 2026-11-21 (UTC).
- **Scope:** Repository/environment inspection, external-repository source and license review, revised architecture/domain/database baseline, identity/authorization and deployment architecture, initial MVP boundary, roadmap, risks and remaining decisions. This is not a product implementation or a completed milestone.

## 1. Repository and environment inspected

- Repository: `DevMagdi/BFS`; initial checkout contained only `README.md` (`# BFS`) and Git metadata.
- Session branch: `arena/bcf45ac4-bfs`, based on `dafac37c260c5ea0ea67b4570c19218e360bdb54`. The repository was clean at inspection time.
- `origin` is `https://github.com/DevMagdi/BFS.git`; GitHub CLI authentication is available.
- Available: Git 2.39.5, GitHub CLI 2.23.0, Python 3.11.2, Node 22.22.3, npm 10.9.8, GCC 12.2, Make, OpenSSL.
- Not installed: `uv`, `pnpm`, pytest, Ruff, mypy, Docker/Podman, `psql`/`pg_ctl`, Rust/Cargo, Go, CMake.
- GitHub API and Git transport access succeeded for all eight supplied repositories. Exact source pins and license findings are recorded in [`OPEN_SOURCE_INVENTORY.md`](OPEN_SOURCE_INVENTORY.md).

**Implication:** there is no local PostgreSQL or container runtime for migration/security integration tests at present. PostgreSQL acceptance tests must run against a real PostgreSQL service (for example, a CI service container) and must not be replaced with SQLite tests for RLS behavior. Milestone 0 has not been authorized.

### Evidence and confidence labels

| Label | What it means in this assessment |
|---|---|
| **Verified — workspace** | Directly observed in the current checkout or by a command run here. The repository initially contained only `README.md`; environment/tool availability and current branch are recorded above. |
| **Verified — static source** | A source file, manifest, license file or repository document was inspected at the pinned commit listed in `OPEN_SOURCE_INVENTORY.md`. This verifies what that file says, not runtime behavior or a complete security property. |
| **Verified — focused test** | At the time of this historical assessment, only EMBMIS Gateway's selected v1 API suite was executed: 75 assertions passed. This did not validate human approval, production security or its other execution paths; later BusinessFlow M2–M8 test evidence is in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md). |
| **Proposal / assumption** | At the time of this assessment, architecture, domain, RLS, identity/session, deployment and MVP behaviors in this package were design recommendations, not implemented or production-validated controls. Subsequent bounded M2–M8 implementation is documented separately in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md). |
| **Unverified** | Candidate-project claims outside the inspected files, complete dependency/license scope, full security posture, all upstream suites, actual customer data suitability, production deployment and PostgreSQL/RLS behavior. No BusinessFlow product tests had been run at the time of this assessment. |

## 2. Executive recommendation — for owner review only

1. **Recommend, not decide, a modular monolith** with a single PostgreSQL transactional/canonical store, Python/FastAPI backend, and Next.js UI introduced after domain/API authorization. Versioned migrations and explicit module/service boundaries remain proposals.
2. Preserve the owner-confirmed **separate Tenant and Company, strict 1:1 MVP relationship**, with Company-scoped facts kept explicit for future 1:N extensibility. Do not consolidate or add multi-company selection in the MVP.
3. Recommend backend authorization plus PostgreSQL RLS, a non-owner runtime role, transaction-local tenant context, tenant-aware FKs and real PostgreSQL tests. At this assessment point these controls were not yet implemented or validated; subsequent M2–M8 code and PostgreSQL evidence are documented in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md).
4. Keep BusinessFlow authoritative for canonical model/facts, approved formulas, metrics, decisions, permissions, approvals and outcomes. An LLM or semantic/BI tool may not become the numeric or policy authority.
5. Keep WrenAI/Cube as a **fair but use-case-separated** M5 benchmark: WrenAI question/intent-to-query and clarification; Cube semantic-model/join/security-context API. DuckDB/Polars remain optional analytics/import candidates. No candidate is adopted; selected upstream tests/benchmarks were not run, except the previously documented limited Gateway test.
6. Define project acceptance as abstention-first. Required inputs, candidate calculations, finance-owner approval gates, versioned scenarios and a reversible BusinessFlow-internal follow-up action are proposed in `MVP_DECISION_MODEL.md`; do not invent accounting policies, FX, cash probabilities or universal thresholds.
7. Concrete technologies, stable versions, compatibility metadata, license observations, support-range limits, operations, alternatives and open approvals are in `TECHNICAL_BASELINE_PROPOSAL.md`. This recommendation has **not** been accepted as a full baseline; no packages/deployment configuration were added by this assessment. Later M2–M8 implementation used separate task-scoped choices.
8. The owner-confirmed directions at that review were on-prem/self-hosted first with cloud portability and hybrid first-party/OIDC identity with MFA, account linking/recovery, sessions and authorization. This assessment did not authorize Milestone 0 or implementation; later M2–M8 slices were separately authorized, and M9 does not authorize further code.

The logical MVP entity/key/isolation catalog is `MVP_ENTITY_CATALOG.md`; decision and action contract is `MVP_DECISION_MODEL.md`; OSS pin/license corrections are `OPEN_SOURCE_INVENTORY.md`; exact 24-item mapping is `REQUIREMENTS_TRACEABILITY.md`, currently **BLOCKED — awaiting the exact original Part 2 requirements**. See `FINAL_REVIEW_CORRECTIONS.md` for the complete documentation change log and current readiness/status distinctions. Existing product directions remain owner-confirmed; this assessment, ADRs and technical choices remain proposals for owner review.

## 3. Source-review conclusions

| Repository | Preliminary disposition | Main reason |
|---|---|---|
| B.I.A.S.E.D. | Build our own core; use UI flows as references only | Useful import/investigation/scenario UX patterns, but the inspected FastAPI routes have no auth dependency or tenant context; the schema and logic are pharmacy/INR/demo-specific. |
| WrenAI | Keep as one candidate in a fair, use-case-separated M5 benchmark; not selected | Evaluate question/intent interpretation, clarification behavior and read-only query correctness. Its structured MDL/AST controls do not prove BusinessFlow authorization, tenant isolation, join correctness or financial correctness. |
| DuckDB | Benchmark as an isolated analytical/import worker candidate | Suitable class of tool for file analytics, not a transactional multi-tenant primary database. The supplied current source tree does not contain Python binding source; the Python distribution needs its own package/license/dependency review. |
| Polars | Benchmark as a data-preparation candidate | Useful typed/lazy transformations; it is not a canonical data-quality/reconciliation or decision engine. Decimal/null/type behavior and memory must be measured on our inputs. |
| Cube | Keep as the other candidate in a fair, use-case-separated M5 benchmark; not selected | Evaluate semantic model/join behavior and security-context propagation through its metrics/query API. It overlaps WrenAI only partly, adds a separate Node service, and requires production-mode auth/tenant tests; never expose `CUBEJS_DEV_MODE`. |
| oc8 | Reject as the MVP foundation | Its active agent runtime, Redis/PostgreSQL services, Docker provisioning/sandbox boundary and LGPL-3.0-or-later license are much broader than this modular-monolith MVP. Its own limitations describe the sandbox as not full multi-tenant isolation. |
| EMBMIS Gateway | Reject for reuse unless materially remediated and independently re-audited | The executed v1 API test suite accepts an approval authenticated with an agent key while trusting a caller-supplied `by` string; this violates the human-approval requirement. Source also logs the canonical execution ticket and returns `status: APPROVED` on execution-plane failure. Its Apache license does not grant the EMBMIS trademark. |
| Temporal | Replace for the MVP with a PostgreSQL-backed outbox/action state machine; revisit if evidence requires it | The supplied repo is the Temporal server, not a Python SDK. A durable workflow server is not justified for the initial scope and would add substantial operations. |

No source code or runtime dependency from these repositories has been added to BusinessFlow AI. Per-repository decisions, source paths, dependencies, observed license scope, security findings, integration boundaries, required tests and acceptance gates are in [`OPEN_SOURCE_INVENTORY.md`](OPEN_SOURCE_INVENTORY.md). Upstream marketing/README claims are not treated as verified capabilities.

### Additional candidates considered (selective review only)

| Repository | Intended use / disposition | License / adoption concern |
|---|---|---|
| EMBMIS ERP | External system or reference only; no BusinessFlow code reuse | Root Apache-2.0 with a NOTICE reserving EMBMIS branding; no tests. |
| ERPNext | Possible external ERP connector target only, not canonical model | Root GPL-3.0; framework/apps and distribution boundary require separate legal review; no tests. |
| n8n | No MVP workflow-engine use; revisit only for separately justified operator workflow | Sustainable Use/Enterprise license scope, not blanket OSI open-source permission; no tests. |
| Airbyte | Defer broad ingestion platform; revisit only if connector scale justifies operations | Root Elastic License 2.0, connector paths/dependencies need review; no tests. |
| Apache Superset | Possible later read-only dashboard comparison only | PyPI `apache-superset 6.1.0` maps to source tag commit `c83fb2bb1dcfac41ac51bcebd82471f4a7180d18`; root/inspected core Apache-2.0. Release not tested; row security is not BusinessFlow authorization. |
| Metabase | Possible later BI/embedding comparison only | Core AGPL-3.0 outside `enterprise/`, commercial/embedding terms separate; legal review required; no tests. |

Exact branch snapshots, stable release tags/SHAs, license paths, intended use and adoption gates are in `OPEN_SOURCE_INVENTORY.md`. The six repositories received selective source/license checks only; no tests, benchmarks, installs or reuse occurred.

## 4. Executed validation and limitations

### Repository access and tooling

- Queried GitHub metadata, default branches, current commit SHAs, top-level license files and relevant source paths for all eight repositories.
- Shallow-cloned B.I.A.S.E.D., oc8 and EMBMIS Gateway into a temporary directory outside the BusinessFlow repository for source inspection.
- No external dependency or source checkout was added to the BusinessFlow working tree.

### EMBMIS Gateway focused test

The only external test suite executed was the supplied Gateway v1 API suite, in the temporary checkout:

```text
npm_config_nodedir=/usr/local npm ci --no-audit --no-fund
npm test -- v1-api
```

- Dependency install: passed, 171 packages installed. The first install attempt could not fetch Node headers from `nodejs.org` (not an allowed outbound host); using the already-installed local Node headers succeeded.
- Test result: **1/1 selected suite passed; 75 assertions passed, 0 failed.**
- Scope limitation: this suite uses a temporary SQLite database and generated development keys. It did not exercise PostgreSQL, the worker/gRPC execution plane, Docker isolation, or the full repository test suite.
- Security interpretation: the passing test is **not** an approval-control pass. `tests/v1-api.test.js` itself approves an escalation using an agent API key and `by: 'user:u_81'`; `src/v1-api.js` authenticates that route with `loadAgent` and accepts `by` from the request body. The test passes while documenting a control gap.

### Not run

No WrenAI, Cube, DuckDB, Polars, B.I.A.S.E.D. or oc8 test suite or benchmark was run. No PostgreSQL migration, RLS test, frontend test, or BusinessFlow product test exists yet. The reasons include missing project dependencies/toolchains, missing PostgreSQL/container runtime, and the fact that no component is approved for reuse. The repository-specific test/acceptance work required before any future reuse is listed in the inventory.

## 5. Preliminary risk register

Qualitative prioritization only; risks are not validated or accepted. “Control” below is a proposed mitigation, not evidence that the risk is closed.

| Priority | Risk | Proposed control / acceptance gate |
|---|---|---|
| Critical | Cross-tenant disclosure or mutation through an API, background job, pooled connection, foreign key or privileged DB role | Verify membership and object scope in backend services; fail-closed PostgreSQL RLS, transaction-local tenant binding, composite tenant-aware keys and non-owner runtime role; prove isolation with real PostgreSQL tests including pooled reuse and background jobs. Not validated yet. |
| High | Incomplete, stale, duplicated or mis-mapped source data produces a plausible but wrong profitability/capacity/cash recommendation | Stage and version mappings; preserve raw provenance; reconcile explicitly; show source freshness, assumptions and quality; reject unsupported conclusions with an insufficient-data result; finance-owner-reviewed Decimal golden tests and a realistic pilot dataset. |
| High | Human approval is forged, stale or detached from the action actually executed; retries duplicate writes | Separate recommendation, authenticated human approval, immutable approved payload hash, idempotent outbox attempts, least-privilege connectors and independent read-after-write verification. Keep high-impact irreversible actions disabled. |
| High | Employee/financial data is overexposed or retained/resident contrary to customer or legal expectations | Minimize data, enforce company/department/compensation permissions server-side, encrypt in transit/at rest, redact logs, and obtain owner/legal decisions for residency, retention, deletion and backups before production. |
| High | Model prompt injection or fabricated explanation influences calculations, policy, access or execution | Keep calculations/permissions server-owned and deterministic; model has no DB credentials or privileged direct execution; use narrow re-authorized tools, treat imported text as untrusted, evaluate grounding and refuse unsupported claims. |
| High | Customer-operated deployments are misconfigured, unpatched or lack recoverable backups/monitoring | Define one supported reference environment, secure defaults, upgrade/patch responsibility, secret/TLS setup, backup/restore verification, observability and incident-response boundaries before production; validate with deployment and restore exercises. |
| Medium | Future tenant-level aggregation is mistaken for valid group/accounting consolidation | Keep source-company facts separate; when multi-company support is considered, add reviewed ownership, FX, fiscal-period and intercompany-elimination rules with golden tests. No group consolidation is in MVP. |
| Medium | OSS license, trademark, dependency or supply-chain obligations are missed | No candidate is adopted now; pin exact versions, audit selected package/file notices and full transitive SBOM/advisories, record attribution, and stop for legal review when scope is unclear. |
| Medium | Premature analytics/workflow infrastructure increases operational attack surface and cost | Start with PostgreSQL and a modular monolith; defer optional engines and services until representative measurements establish a need. |

## 6. Owner selections and remaining review gates

The owner confirmed the following directions. The owner also selected “review or revise first,” so the architecture baseline remains unaccepted and no milestone is authorized to start.

| Area | Owner-confirmed direction | Still needs review/definition |
|---|---|---|
| Tenant/company model | Confirmed: separate `Tenant` and `Company`, strict 1:1 in MVP | Preserve `tenant_id` and `company_id` on the relevant facts so a later 1:N relationship is possible. Future group consolidation needs separate accounting/FX/ownership rules and remains out of MVP. |
| Deployment | Confirmed: self-hosted/on-prem first, portable architecture for later cloud deployment | Select the supported reference platform, one-vs-many tenants per installation, installation/upgrade method, secret/TLS/backup/restore/monitoring responsibilities, support model and whether air-gapped operation is required. |
| Identity | Confirmed: first-party authentication plus OIDC/enterprise SSO; proper MFA, account linking, recovery, session management and authorization policies are required | Keycloak 26.8.2/Authlib 1.9.10 is a recommendation, not an owner decision. Approve or replace it and define providers, MFA, registration/linking, recovery/bootstrap, sessions and authorization in `IDENTITY_AND_AUTHORIZATION.md`. |
| Overall assessment | Review or revise first | Await explicit acceptance. Do not begin Milestone 0 or add infrastructure/product code. |

Other policies—data residency, retention periods, deletion/backups, employee compensation visibility, base-currency/FX source, and action approval thresholds—remain explicit later gates. No geography, currency or legal retention period has been inferred from the requester's approximate location.

## 7. Current delivery state

- This is a **pre-Milestone 0 design review**, not Milestone 0 or Milestone 1 completion.
- No product code, frontend screen, migration, production schema, external dependency, or secret was created.
- At the time of this assessment no commit or merge had been made. A later documentation-only commit/merge does not change the fact that the architecture and ADRs remain proposals.
- The owner confirmed separate Tenant and Company entities with strict 1:1 MVP cardinality, self-hosted/on-prem first with future cloud portability, and hybrid first-party + OIDC identity with proper MFA/session/access controls. The owner also directed **review first**. No Milestone 0, code, dependency, migration or infrastructure work is authorized.
- `REQUIREMENTS_TRACEABILITY.md` remains **BLOCKED — awaiting the exact original Part 2 requirements**. No reconstructed, paraphrased or substitute 24-item list is accepted as traceability.
- The provisional requirements package has been reviewed for documentation delivery; it does not approve the technical assessment, architecture, implementation, product testing or independent verification. `FINAL_REVIEW_CORRECTIONS.md` records the earlier technical/documentation review and open gates.
- The provisional requirements documentation has since been reviewed for delivery. Exact requirement text is still needed for traceability; explicit technical-baseline acceptance and separate implementation authorization are still absent. Resolve finance, identity, deployment and security gates before any related implementation.
