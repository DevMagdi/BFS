# Milestones 2–8 backend implementation baseline

## Status and decision boundary

This guide records the bounded M2–M8 backend implementation present in the repository and its known limits. The migration chain runs from `0001_backend_foundation` through `0007_financial_feasibility`: M2 foundation, M3 company context, M4 staged imports, M5 metric runs, M6 project capacity assessment, M7 project-stage decision/approval, and M8 project-scoped financial observations and an intentionally incomplete assessment. The repository contains no distinct M1 runtime/schema capability in that migration sequence. See [`architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md) for the M9 company-wide product assessment. Neither this guide nor M9 approves a general production architecture, customer demand, accounting policy, or finance rule. [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) remains blocked because the exact original 24 requirements were not supplied.

M3 stores a narrow set of organization, employee, skill, capacity, project and assignment facts. M4 stages only six workforce/project dataset types. M5 provides five deterministic non-financial observations labelled `implementation_draft` and `observational_only_no_recommendation`. M6 assesses project capacity only; M7 records human-controlled project-stage outcomes; M8 stores sourced financial observations for a project intake and emits only `incomplete` without an approved financial policy. These are bounded patterns—not company-wide BI, a general KPI layer, a financial calculation engine, or a reusable decision platform. The broader product target and conditional next step are documented in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md) and [`roadmap/PRODUCT_ROADMAP.md`](roadmap/PRODUCT_ROADMAP.md).

## Implemented components

The code targets Python 3.11–3.13 under the current package metadata and uses a modular-monolith structure with FastAPI, SQLAlchemy 2, Alembic, psycopg 3, and PostgreSQL. PostgreSQL is required; SQLite does not exercise the RLS guarantees and is not an integration-test substitute. Compose is configured for PostgreSQL 18.

### Schema and tenancy

The Alembic history runs from `0001_backend_foundation` through `0007_financial_feasibility`: M2 backend foundation, M3 company context, M4 staged imports, M5 metric runs, M6 capacity assessment, M7 project decision workflow, and M8 financial observations/assessment. The first revision creates:

- `users`, `auth_sessions`, `refresh_tokens`, and `login_throttles` for account and authentication state;
- `tenants`, `companies`, and `tenant_memberships` for the tenant boundary and access roles;
- `tenant_onboarding_requests` for hashed idempotency keys and canonical request digests; and
- append-only `audit_events`.

The Milestone 3 revision adds `business_units`, `departments`, `employees`, `skills`, `employee_skills`, `employee_weekly_capacity`, `projects`, and `resource_assignments`. Every table carries explicit `tenant_id`; company-context records also carry `company_id` where appropriate. Composite foreign keys bind references to the same tenant and, for company-scoped records, the same company. Skills are a tenant-owned catalog. Case/whitespace-normalized unique keys prevent duplicate unit, department, skill, employee and project names/codes within their scope.

The Milestone 4 revision adds `data_imports`, `data_import_rows`, and `data_import_record_links`. Import and row records carry both tenant and company scope, use composite foreign keys for parent/row identity, and have forced tenant RLS. Staged cell values are nullable so they can be purged while row hashes, validation results, audit history, and canonical trace links remain available. The import table records a monotonically incremented mapping version and validation/confirmation hashes; links preserve the import row, canonical type/ID, and a snapshot fingerprint.

The Milestone 5 revision adds `metric_runs`, an immutable tenant/company-scoped record with requested-by membership, Monday-week period, catalog/calculation versions, reliability state, input/result JSONB snapshots, and SHA-256 hashes. Database checks constrain the period, supported reliability values, hash shape, and snapshot object type. Forced RLS uses the transaction-local tenant context. The `bf_runtime` role can select and insert runs, but cannot update, delete or truncate them.

The bounded M6 revision adds `project_intakes`, immutable `project_intake_versions`, and immutable `capacity_assessments`. Composite tenant/company keys bind each record to its parent and requester; assessments pin the exact intake version and immutable M5 `metric_runs` evidence. Database constraints bound supported statuses, Monday-week date range, version values, hash syntax, and JSON object shapes. The service validates the saved intake schema/hash and verifies M5 catalog/calculation versions and snapshot hashes before deriving results. A deferred composite foreign key prevents a dangling current-version pointer. Forced RLS requires transaction-local tenant and company context. The runtime role can select/insert all three tables; historical versions and assessments cannot be updated, deleted or truncated. Intake updates are column-limited to project code, version pointer and timestamp, with a database guard requiring exactly one version advance.

The M7 revision adds `project_decision_cases`, append-only `project_decision_versions`, and append-only `project_decision_approvals`. Cases point to one exact current decision version; deferred composite foreign keys and database guards enforce version sequencing, head-pointer consistency, exact intake/assessment/metric evidence binding, valid outcome-specific fields, and submitter/approver separation of duties. The version and approval records bind assessment ID/version/input/result hashes, intake-version ID/number/hash, and M5 metric-run ID/catalog/calculation versions/input/result hashes. The service reuses the existing canonical content-hash helper, verifies those immutable snapshots and references before submission/review, and rejects stale or invalid evidence without modifying past decisions. Forced tenant/company RLS and composite FKs protect every table; runtime privileges are select/insert only for history rows, with append-only triggers. Intake locks serialize assessment refresh, decision submission and approval. See [`M7_DECISION_APPROVAL.md`](M7_DECISION_APPROVAL.md) for lifecycle, policy, API semantics and known limits.

The M8 revision `0007_financial_feasibility` adds `project_financial_input_cases`, append-only `project_financial_input_versions`, and append-only `financial_assessments`. Financial inputs are scoped to one project intake and store sourced observations/assumption metadata as versioned snapshots; they are not canonical company finance records. Assessment rows bind the exact current intake, M5 run, M6 capacity assessment, M7 decision/approval and M8 input evidence. The calculation module returns observations only: `calculated_metrics` stays empty, the policy/formula catalogs stay empty, and the verdict is always `incomplete`; database checks enforce the policy gate. M8 accepts exact decimal strings and ISO 4217 codes, preserves source currencies, performs no rounding or FX conversion, and requires a fresh, approved M7 handoff before creating an assessment. Forced RLS, append-only guards, restricted runtime grants, and authorization checks protect all three M8 tables. Finance read/write permissions are present for owner/admin in the fixed role matrix; members have no finance permission. M8 does not implement company-wide finance, accounting policy, financial calculations, or general decision subjects. See [`architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md`](architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md) for the detailed code-backed assessment and target governance boundary.

Each tenant has one primary company. A unique company `tenant_id` enforces one company per tenant; the deferred composite tenant-to-primary-company foreign key ensures the primary company belongs to that tenant. Company creation and the initial active owner membership are committed in the same database transaction as the tenant and idempotency record. An identical retry with the same user/key and request returns the original tenant; reusing that key with changed request data is rejected.

Memberships are soft-revoked rather than physically deleted. The application serializes owner changes using a tenant lock and rechecks authorization under that lock. A deferred PostgreSQL constraint trigger also rejects a commit that would leave an active tenant with no active owner.

### Milestone 3 company-context rules

- Business units and departments have active/inactive status. A department may have one optional business unit. Units cannot be deactivated while active departments remain; departments cannot be deactivated while active or on-leave employees remain. The API's `DELETE` operations are soft deactivations, not physical deletes.
- An `Employee` is a business record, separate from `UserAccount`; employee records do not require product logins. The implemented fields are an employee code, name, job title, optional department, employment status/dates and standard weekly hours. No compensation, payroll, manager hierarchy or employee-to-user link is implemented.
- Skills have a tenant-scoped catalog and employee-skill records. Proficiency is an integer from 1 through 5 (`beginner`, `working`, `proficient`, `advanced`, `expert`). Catalog entries and employee-skill records are soft-deactivated; historical associations are retained.
- Weekly capacity is recorded for Monday–Sunday weeks, with each record anchored by a Monday date. `confirmed` and `estimated` records require scheduled, non-project and unavailable hours; the reported known hours are their difference. `unknown` requires all hour fields to be absent and produces no available-hours value. Missing and unknown capacity are never inferred from an employee's standard-weekly-hours field. Corrections append a monotonically versioned record; the runtime role cannot update or delete capacity history.
- Project transitions are `draft` → `planned`/`cancelled`, `planned` → `active`/`on_hold`/`cancelled`, `active` → `on_hold`/`completed`/`cancelled`, and `on_hold` → `active`/`completed`/`cancelled`; completed/cancelled projects are terminal and immutable. Completion requires an actual end date, and an actual end date is only valid for a completed project. Resource assignments transition `proposed` → `committed`/`cancelled` and `committed` → `completed`/`cancelled`; completed/cancelled assignments are immutable. Only planned/active projects and active employees may receive committed assignments. A project cannot close while proposed or committed assignments remain. Manager-adjustment capacity requires a reason and manager-adjustment assignment creation requires a note. Assignment changes and cancellations are preserved as append-only audit events and exposed through a paginated history endpoint.
- The `/company-context` summary reports stored facts and coverage, not a recommendation or feasibility score. It returns counts, bounded/truncated lists, weekly capacity coverage and assignment facts, and explicit indicators for missing, unknown or estimated information. It does not treat planned assignment hours as proof that capacity is available.

Registration, login, refresh, logout, and `/auth/me` are implemented. Passwords use Argon2id (the initial configured parameters are 19 MiB memory, two iterations, one lane). Access tokens are short-lived HS256 JWTs with issuer, audience, token-use, user, session, and token identifiers. Requests validate both the token and the corresponding unrevoked database session. Refresh credentials are random opaque values; only SHA-256 digests are stored. Refresh rotates the token, detects reuse, and revokes the session and its refresh-token family. Logout revokes the session. Disabled accounts cannot log in, refresh, or continue using an existing access session.

Login throttling uses atomic PostgreSQL upserts for HMAC-hashed email and source-IP buckets. Defaults are five failed attempts per email bucket and twenty per source-IP bucket in a fifteen-minute lockout window; all are configurable. Raw passwords and bearer/refresh tokens are not written to audit metadata or application error logs. Validation and authentication errors return a bounded safe error shape with a request ID.

The API returns access and refresh tokens in JSON, not cookies. Clients must protect them and use TLS. This baseline does not implement email verification, MFA, password recovery, or invitation emails.

The service layer and API enforce this permission matrix:

| Role | Tenant read/update | Company read/update | Membership read/create/manage | Audit read | Company-context read/write | Decision read/submit/review | Finance read/write |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Owner | Yes / Yes | Yes / Yes | Yes / Yes / Yes | Yes | Yes / Yes | Yes / Yes / Yes | Yes / Yes |
| Admin | Yes / No | Yes / Yes | Yes / Member-only / Member-only | Yes | Yes / Yes | Yes / Yes / Yes | Yes / Yes |
| Member | Yes / No | Yes / No | No | No | Yes / No | Yes / No / No | No / No |

For M7, owners and admins can submit decisions and review another active owner's/admin's decision; members have read-only access. Separation of duties is mandatory and enforced both in the service and database: a submitter cannot review the same version, and every version has at most one immutable review result. At least two eligible owner/admin principals are required. There is no self-approval exception. Decision permissions are scoped by active membership, tenant, company and intake; details and stale-evidence behavior are documented in [`M7_DECISION_APPROVAL.md`](M7_DECISION_APPROVAL.md).

An administrator may add or manage member-role accounts but cannot grant or change owner/admin privileges. Accounts must already exist and be active before they can be added as tenant members. A user cannot modify their own membership; the final active owner cannot be revoked or demoted.

### Milestone 4 staged imports

The import API is mounted under `/api/v1/tenants/{tenant_id}/imports` and is company-context scoped. It supports six dataset types: employees, skills, employee skills, weekly capacity, projects, and resource assignments. Upload accepts UTF-8 CSV (optional BOM) and XLSX only. The default limits are 25 MiB per upload, 10,000 nonblank data rows, and a 24-hour staging TTL; environment settings bound configurable limits.

Inspection reports worksheet/header/row metadata and dataset suggestions. Mapping is explicit: every source column must map to a supported field or be listed as ignored. Validation produces row-level safe issues and deterministic counts, identifies in-file and canonical duplicates, and never overwrites an existing canonical record. Preview is paginated. Confirmation requires the latest validation hash, explicit warning acknowledgement when warnings exist, and explicit partial-apply permission when invalid rows exist. The apply request revalidates the mapping, source rows, referenced company records, and duplicate state before using the existing canonical create services. Audit events are written for upload, mapping, validation, confirmation, expiry, cancellation, and application; row links preserve source-to-canonical traceability. Upload retries—including concurrent retries—with the same key, user, and file hash return the original import; a changed request conflicts. Applying an already-applied import returns its prior link count with `replayed: true`.

The XLSX parser uses `openpyxl` with defused XML and rejects formula cells, hidden worksheets, macro parts, external workbook links/relationships, unsafe ZIP paths, excessive archive entries, high compression ratios, and expanded-size/worksheet/column/row limits. The API does not evaluate uploaded formulas. Raw staged values are cleared on apply, cancellation, or expiry. Expired items are purged in bounded batches by the owner/admin `POST /api/v1/tenants/{tenant_id}/imports/purge-expired` endpoint; a deployment scheduler must call this endpoint (or a future maintenance worker) to enforce unattended expiry. The implementation does not define legal retention for audit metadata or backups.

### Milestone 5 deterministic metrics

The API exposes `GET /api/v1/tenants/{tenant_id}/metrics/catalog`, `POST /api/v1/tenants/{tenant_id}/metrics/runs`, and `GET /api/v1/tenants/{tenant_id}/metrics/runs/{run_id}`. Catalog version is `businessflow.metrics.v1`; calculation version is `businessflow.capacity-weekly.v1`. The catalog contains five non-financial definitions: `available_capacity_hours`, `recorded_committed_hours`, `capacity_minus_recorded_commitments_hours`, `recorded_assignment_overload`, and `recorded_skill_inventory_coverage`. The API and persisted result explicitly label the catalog `implementation_draft` and its use `observational_only_no_recommendation`; versioning does not imply owner/finance approval. A run starts on a Monday and covers 1–52 weeks.

The calculation is pure Python, deterministic, input-order independent, and uses `Decimal` hours serialized at two decimal places. It snapshots current tenant/company-scoped canonical facts and available retained M4 source-row links, calculates the result, and persists both JSON-compatible snapshots plus canonical SHA-256 hashes in one repeatable-read transaction. The input hash covers the canonical fact/lineage snapshot and period; the result hash covers the values, metric catalog and calculation versions, per-metric versions, and a snapshot of the metric definitions and formulas. Each run records the requester, period, reliability status and audit event. The snapshot retains import ID/status, dataset, mapping version, file and row hashes, source-row identity/location, and the canonical fingerprint captured at import; it does not copy staged cell payloads. A saved input snapshot can be deserialized and replayed against the same calculation implementation/version and checked against the stored result hash.

Metric interpretation and limits:

- Weekly available capacity is `scheduled_hours - non_project_hours - unavailable_hours`, using the latest version for each active employee/week. Confirmed and estimated values are separate. Missing, explicitly unknown, invalid or conflicting values remain unknown; standard weekly hours are never a fallback. A complete company subtotal is emitted only when capacity is known for every currently active employee.
- Only committed assignments are compared with capacity; proposed hours are separate. Each assignment contributes its full planned weekly value to every Monday–Sunday week intersecting its inclusive dates; no daily proration occurs. No assignment rows means not recorded, not zero, and assignment completeness is not attested.
- Employee overload comparisons are emitted only when the employee has known capacity. Confirmed and estimated overloads are distinguished; missing, unknown or invalid capacity is reported as unresolved. Capacity minus recorded commitments is null unless all active-employee capacity is known and at least one active employee commitment is recorded. Even when available, it is an arithmetic observation—not proof of free capacity, actual work, timesheets or utilization.
- Skill output counts current active employee-skill records by active skill and proficiency (1–5). It is recorded inventory only; project-required skills, matching, practical competency and delivery capability are unsupported.
- Current active employee status is applied to every requested week because M3 has no historical employment-status model. Run-level reliability describes weekly-capacity coverage only; assignment and skill states are reported separately.

Calculation inputs are bounded to 52 weeks, 1,000 active employees, 10,000 overlapping assignments, 2,000 active skills, 20,000 active employee-skill records and 50,000 retained lineage references. Oversized inputs are rejected rather than silently truncated. The service enforces existing `company_context:read` membership authorization; saved runs are visible only within the authorized tenant/company. The database enforces forced RLS and runtime select/insert-only access. The local verification described below covers replay, source lineage, authorization, tenant isolation and append-only grants using PostgreSQL.

### PostgreSQL roles and RLS

The local initialization script creates separate roles:

- `bf_migrator` owns the application databases/schema objects and is the only role permitted to run the Alembic migration;
- `bf_runtime` is the API's non-superuser, non-owner, non-`BYPASSRLS` role, with no schema `CREATE` privilege and only the table privileges required by the application; and
- the bootstrap/admin role is used only to initialize local PostgreSQL and, in tests, inspect/reset the dedicated test database.

The migrations fail closed unless `bf_runtime` exists, is not superuser/`BYPASSRLS`, cannot assume the migration role, does not own the database, and cannot create schema objects. They enable and force RLS on `tenants`, `companies`, `tenant_memberships`, `tenant_onboarding_requests`, `audit_events`, all eight Milestone 3 tables, all three Milestone 4 import tables, M5 `metric_runs`, M6 `project_intakes`, `project_intake_versions`, and `capacity_assessments`, the three M7 decision/approval tables, and M8 `project_financial_input_cases`, `project_financial_input_versions`, and `financial_assessments`. Each tenant-owned table has a tenant-context policy; M6–M8 additionally require the verified company context. Request code sets verified user and membership-authorized tenant/company context with transaction-local PostgreSQL settings. Missing context returns no tenant rows and rejects tenant writes. Context is discarded with the transaction, including when a pooled connection is reused.

The M3 runtime role can select, insert and update the company-context tables, but cannot physically delete them. `employee_weekly_capacity` is append-only for the runtime role (select/insert only) and also has a database trigger rejecting update/delete. The M4 runtime role can select/insert/update staged imports and rows but cannot delete them; it can only select/insert canonical trace links. The M5 runtime role can select/insert `metric_runs`, with no update/delete/truncate privilege. M6 versions and assessments are select/insert only for `bf_runtime` and protected from update/delete by database triggers; intake updates are limited to the `project_code`, `current_version`, and `updated_at` columns, with a trigger requiring exactly one version advance. M7 versions and approvals are also select/insert only and guarded against update/delete/truncate; only the case's current-version pointer can advance, and database guards verify the new immutable version and approval bind the exact current evidence and satisfy the separation-of-duties policy. M8 input versions and financial assessments are select/insert only for the runtime role and protected by append-only guards; the financial-input case pointer advances only with a new version. Soft deactivation/cancellation, versioned capacity, decision and financial records, and purgeable staging payloads preserve the current facts and history.

The global `users` and authentication-state tables are not tenant-RLS tables: authentication must locate a global account before tenant membership is known. Their API access is constrained by service queries and the user/session identity checks. Tenant-owned records are separately scoped at the service/API boundary and by PostgreSQL RLS. Unscoped authentication audit events can be inserted only under the narrow policies in the migration; tenant audit reads remain tenant-scoped. Audit rows are append-only for the runtime role through grants and database triggers.

### Versioned HTTP API

Health:

- `GET /health/live`
- `GET /health/ready` (checks database connectivity)

Authentication (`/api/v1/auth`):

- `POST /register`
- `POST /login`
- `POST /refresh`
- `POST /logout`
- `GET /me`

Tenant APIs (`/api/v1/tenants`):

- `GET /` and `POST /` (initial tenant, company, and owner onboarding; `POST` requires an `Idempotency-Key` header)
- `GET /{tenant_id}` and `PATCH /{tenant_id}`
- `GET /{tenant_id}/company` and `PATCH /{tenant_id}/company`
- `GET /{tenant_id}/memberships`, `POST /{tenant_id}/memberships`, `PATCH /{tenant_id}/memberships/{membership_id}`, and `DELETE /{tenant_id}/memberships/{membership_id}`
- `GET /{tenant_id}/audit-events`

Company context is under `/api/v1/tenants/{tenant_id}`. Tenant IDs are accepted only in the path, verified against active membership; payloads cannot set `tenant_id` or `company_id`:

- Business units and departments: paginated list/detail, create, patch and soft-deactivate; status filters and business-unit filtering are supported.
- Employees: paginated list/detail, create, patch and soft-deactivate; filters include employment status, department, business unit and skill, with bounded sort options.
- Skills and employee skills: paginated catalog/association reads, create/update and soft-deactivation; proficiency is validated from 1 through 5.
- Weekly capacity: record a Monday-start version, list the latest version per week with optional week-range filters, and retrieve paginated version history.
- Projects and resource assignments: paginated/filterable lists, create/update, lifecycle transitions and soft cancellation; project assignment lists and assignment-history reads are paginated.
- `GET /{tenant_id}/company-context` returns a factual summary; an optional `week_start_date` must be a Monday. Summary lists are bounded and expose truncation and completeness indicators.
- Staged imports: `POST /{tenant_id}/imports` (multipart upload; requires `Idempotency-Key`), `GET /{tenant_id}/imports` and `GET /{tenant_id}/imports/{import_id}`, mapping inspection/save, paginated preview, validation/report, explicit confirm/apply, cancel, and bounded expiry purge. Writes require owner/admin company-context permission; members may read import summaries and reports.
- Metrics: `GET /{tenant_id}/metrics/catalog`, `POST /{tenant_id}/metrics/runs`, and `GET /{tenant_id}/metrics/runs/{run_id}`. These require `company_context:read`; all authorized tenant members may read the catalog and saved runs.
- Project capacity assessments: `GET`/`POST /{tenant_id}/project-intakes`, `GET`/`PUT /{tenant_id}/project-intakes/{intake_id}`, `GET /{tenant_id}/project-intakes/{intake_id}/versions/{version_number}`, `GET`/`POST /{tenant_id}/project-intakes/{intake_id}/assessments`, `GET /{tenant_id}/project-intakes/{intake_id}/assessments/{assessment_id}`, and `GET /{tenant_id}/project-intakes/{intake_id}/assessments/{assessment_id}/evidence`. Intake writes and assessment creation require company-context write permission; authorized company-context readers may inspect intake versions and assessment history. See [`M6_CAPACITY_ASSESSMENT.md`](M6_CAPACITY_ASSESSMENT.md) for request/response semantics and security details.
- M7 project decisions: `GET`/`POST /{tenant_id}/project-intakes/{intake_id}/decisions`, `GET /{tenant_id}/project-intakes/{intake_id}/decisions/{decision_id}`, and `POST /{tenant_id}/project-intakes/{intake_id}/decisions/{decision_id}/approval`. Owner/admin may submit and review; tenant members may read. Writes require an expected current decision version; approvals require the exact expected decision and evidence hashes. All list/history reads are bounded and company scoped. See [`M7_DECISION_APPROVAL.md`](M7_DECISION_APPROVAL.md) for outcomes, conditions, resume/information-response flow, staleness and approval policy.
- M8 financial observations and assessments: under `/api/v1/tenants/{tenant_id}/project-intakes/{intake_id}`, routes expose financial-input create/current/history/version, policy-catalog read, readiness, assessment create/history/detail. Owner/admin require `finance:read` or `finance:write`; members do not have finance access. Assessment creation requires a current fresh approved M7 `proceed_to_financial_evaluation` handoff. It always emits `financial_verdict="incomplete"`, no calculated metrics, and no approved policy. See [`architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md`](architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md) for the exact M8 boundary and future gates.

All collection and history endpoints enforce bounded `limit`/`offset`, use Pydantic request/response models and return safe errors. Development/test environments expose OpenAPI at `/openapi.json` and Swagger UI at `/docs`; these are disabled when `APP_ENV=production`. Errors use a generic JSON envelope; validation responses do not echo submitted fields. Responses include `X-Request-ID`.

## Verification status for this delivery

M8 delivery CI: the PostgreSQL integration and quality-gates workflow for PR #10 completed successfully ([run 45034257194](https://github.com/DevMagdi/BFS/actions/runs/45034257194)); the subsequent `main` workflow also completed successfully ([run 45034376997](https://github.com/DevMagdi/BFS/actions/runs/45034376997)). These are M8 delivery checks, not M9 verification. M9 changes documentation only; no business-code test run is claimed for this assessment.

M7 local verification (2026-11-21): the focused decision workflow integration file passed **8 tests** and the full PostgreSQL integration suite passed **76 tests** against isolated PostgreSQL 16.2 with separate runtime, migration and admin roles. The M7 coverage exercises all four outcome rules; approval/evidence hash binding and submitter/approver separation; information response and hold/resume versioning; M6/M5 evidence staleness; tenant/company/member authorization; forced RLS and composite-FK integrity; append-only database protections; transactional rollback and audit atomicity; and concurrent superseding submissions. Migration `0006_decision_workflow` downgraded to `0005_capacity_assessment` and re-upgraded successfully, and `alembic check` reported no new upgrade operations. Ruff format/lint and mypy pass. Local PostgreSQL 16.2 evidence is distinct from CI: the M7 PR workflow [run 45025110508](https://github.com/DevMagdi/BFS/actions/runs/45025110508) passed its runtime major-version assertion on **PostgreSQL 18.7**, as shown by the version-specific CI step, and passed formatting/lint, mypy, migration upgrade/drift, and the full integration job. After PR #8 merged at `a3fc3ed86c0587da97929f555c077a30bbef1e09`, the push-to-main workflow [run 45025427123](https://github.com/DevMagdi/BFS/actions/runs/45025427123) also passed those gates, with its version-specific step confirming PostgreSQL 18.7. One non-blocking Starlette `TestClient`/httpx deprecation warning remains.

M6 local verification (2026-11-21): the full suite passed **68 tests** against isolated PostgreSQL 18.6 with separate runtime, migration and admin roles. It includes a clean migration through `0005_capacity_assessment`, the populated legacy-schema upgrade path, M2–M6 API/calculation/history/authorization/RLS/append-only regressions, and the focused M6 evidence/hash/replay and concurrency coverage. The focused M6 integration file passed **7 tests**. An explicit downgrade to `0004_metric_runs` and re-upgrade to head also passed; `alembic check` reports no new upgrade operations. Ruff format check reports all **78 files** formatted; Ruff lint passes for `src`, `migrations` and `tests`; mypy reports no issues in **56 source files**. OpenAPI smoke checks confirmed the six M6 intake/version/assessment path patterns. One non-blocking Starlette `TestClient`/httpx deprecation warning remains. This is local verification; no M6 CI or post-merge result is claimed, and test success does not approve product or finance policy.

Historical M5 local baseline: at the M5 delivery, the complete suite passed **61 tests** against live PostgreSQL 18.6 with separate runtime, migration and admin roles, through `0004_metric_runs`. That baseline covered the populated legacy-schema upgrade path, metrics API/replay/lineage/authorization checks, forced-RLS and append-only grants, and M2–M4 regressions; its Alembic drift and static checks passed. M5 was merged as [PR #6](https://github.com/DevMagdi/BFS/pull/6), with CI and post-merge checks passing. The 61-test count is a historical M5 baseline, not a current-head verification total.

Historical M4 verification: its full suite passed **49 tests** against isolated PostgreSQL 16.2 with separate runtime, migration and admin roles; parser/catalog tests, populated M2-to-M4 migration, imports, RLS/grants, purge, rollback, concurrency and locked revalidation passed. The PostgreSQL 18 CI workflow passed formatting, lint, mypy, Alembic upgrade/drift and the full integration test on both [PR #4](https://github.com/DevMagdi/BFS/actions/runs/45009975788) and post-merge main [at commit `4724e1f`](https://github.com/DevMagdi/BFS/actions/runs/45010074104).

## Local development

### Recommended: Docker Compose

1. Install Docker Engine and the Compose plugin. Copy the safe example and replace every placeholder with an independent URL-safe random value; never use these examples as production secrets.

   ```sh
   cp .env.example .env
   python -c "import secrets; print(secrets.token_urlsafe(48))"
   ```

   Generate distinct values for `BF_BOOTSTRAP_PASSWORD`, `BF_MIGRATOR_PASSWORD`, `BF_RUNTIME_PASSWORD`, and `APP_SECRET_KEY`.

2. Initialize PostgreSQL, apply migrations as `bf_migrator`, start the API as `bf_runtime`, and check readiness:

   ```sh
   docker compose --profile tools run --build --rm migrate
   docker compose up --build --detach api
   curl --fail http://localhost:8000/health/ready
   ```

   The API listens on port 8000 by default; set `API_PORT` to change the host port. Migrations are an explicit step and are not run automatically by the API container.

3. Run the destructive, isolated PostgreSQL integration suite against `businessflow_test`:

   ```sh
   docker compose --profile test run --build --rm tests
   ```

   The test fixture downgrades and rebuilds this dedicated test database. Never point any `TEST_*_DATABASE_URL` at development, staging, production, or data that must be kept. Tests require PostgreSQL runtime, migration, and separate admin URLs; the runtime and migration URLs must use `bf_runtime` and `bf_migrator` respectively.

4. Stop services with `docker compose down`. **`docker compose down -v` deletes the local PostgreSQL volume and all data**; use it only when intentionally resetting the environment. Database-init scripts run only on an empty volume; changing `.env` passwords does not change roles in an existing volume. See [`scripts/README.md`](../scripts/README.md).

### Python tools

Create an environment and install the project plus development tools:

```sh
python3.11 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
```

For a host-run API or test suite, provision PostgreSQL and the separate roles using the same ownership/grant model as `scripts/db-init/010-create-roles.sh`, export `DATABASE_URL` for the runtime role and `MIGRATION_DATABASE_URL` for `bf_migrator`, then run:

```sh
.venv/bin/alembic upgrade head
.venv/bin/uvicorn businessflow.main:app --host 0.0.0.0 --port 8000
```

For tests, also export `TEST_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, and `TEST_ADMIN_DATABASE_URL` for the same dedicated test database, with `bf_runtime`, `bf_migrator`, and a separate test-admin role. All PostgreSQL URLs must use the `postgresql+psycopg` driver. Then run:

```sh
.venv/bin/pytest -q
.venv/bin/ruff format --check src migrations tests
.venv/bin/ruff check src migrations tests
.venv/bin/mypy src/businessflow
```

`APP_SECRET_KEY` must be a non-placeholder value of at least 32 characters; use a separately generated secret of at least 48 URL-safe characters. Production configuration rejects common `dev-`, `test-`, and `ci-` prefixes. Avoid sharing `.env`, database URLs, access tokens, refresh tokens, or test-admin credentials.

The GitHub Actions workflow at `.github/workflows/backend.yml` provisions an isolated PostgreSQL 18 service and runs formatting, lint, mypy, Alembic upgrade/drift checks, and the same integration suite. Its fixed credentials are disposable test-only values, not application secrets.

## Known limits, reuse review and deployment notes

This is a bounded backend implementation, not a claim of production readiness. There is no frontend, CORS policy, email service, account recovery, MFA, secret-rotation mechanism, or operational monitoring/backup design here. M5 is limited to the non-financial capacity, recorded-assignment and skill-inventory observations above. M6 adds versioned proposed-project intake and a human capacity-only comparison with explicit weekly team-hour demand; its sole hypothetical scenario is a demand schedule for that same comparison, not a multi-option decision engine. Job-role catalogs, non-employee resources, HR/payroll, compensation, canonical time-entry/cost/revenue/accounting records, contracts, customers/suppliers, company-wide financial fact models, approved financial calculations/policies, actual-work/utilization data, project skill requirements, a general KPI framework, non-project decision subjects, option recommendations, and broad business/commercial feasibility decisions remain out of scope. M8 accepts project-scoped sourced observations only; it does not canonicalize or calculate them. Imports are limited to the six CSV/XLSX datasets described above; there is no reusable named-source registry, template downloader, or background scheduler. If deployed behind a reverse proxy, configure trusted proxy handling so login throttling receives the intended client IP; do not trust arbitrary forwarded headers. TLS termination, production secret management, backup/recovery, capacity tuning, and broader threat-model review are deployment gates outside this code delivery.

M7 adds project-stage decision versions and their audit/history; M8 adds sourced project-level financial observation snapshots and an assessment that stays `incomplete` without approved policy. Neither implements company-wide finance, financial calculations, broad commercial acceptance/rejection, a recommended option, hiring/outsourcing, resource allocation, autonomous decisions, a UI, external actions, or outcome tracking. M6 completeness/currentness remains an explicit human assumption. M7 detects changes represented by newer M6 assessments and newer persisted M5 metric runs; it cannot prove an upstream source is current if no refreshed evidence run is recorded. Approval is limited to owner/admin review of the exact current capacity-stage decision version with matching hashes; no approval carries forward to a superseding version. Local M7 verification used embedded PostgreSQL 16.2, while configured CI uses PostgreSQL 18; these environments are reported separately.

**B.I.A.S.E.D. reuse decision:** The reviewed repository snapshot was `75bbf77df8bf49bd3d9e332cd3202f2d6c043a60`; its declared license is Apache-2.0. The inspected API models, route declarations and organization/resource/project-related source search did not reveal a canonical M3 company-context model or component suitable for reuse. Its API package declares Python `>=3.14`, incompatible with this backend's supported Python `>=3.11,<3.14`. No B.I.A.S.E.D. code or dependency was copied. This is a deliberate no-reuse decision based on fit/runtime compatibility, not a license restriction; no additional license obligations are introduced.
