# Milestones 2–3 backend implementation baseline

## Status and decision boundary

This document describes the backend code currently in the repository. Milestone 2's identity, tenancy, authorization and audit foundation and the bounded Milestone 3 company-context slice were implemented under explicit delivery authorization. This does **not** approve every proposal in the architecture or technical-baseline documents, establish a production architecture, or make product, accounting, or finance-policy decisions. The product definition remains provisional, and [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) remains blocked because the exact original 24 requirements were not supplied.

Milestone 3 stores organization, employee, skill, capacity, project and assignment facts; it does not calculate feasibility, utilization, financial outcomes or resource optimization. The implementation intentionally excludes CRM, finance/accounting, HR/payroll, imports, AI/LLM/RAG and semantic engines, analytics engines, workflow automation, external ERP integrations, a large frontend, microservices, Kubernetes, and unrelated features.

## Implemented components

The code targets Python 3.11–3.13 under the current package metadata and uses a modular-monolith structure with FastAPI, SQLAlchemy 2, Alembic, psycopg 3, and PostgreSQL. PostgreSQL is required; SQLite does not exercise the RLS guarantees and is not an integration-test substitute. Compose is configured for PostgreSQL 18.

### Schema and tenancy

The Alembic history is `0001_backend_foundation` followed by `0002_company_context`. The first revision creates:

- `users`, `auth_sessions`, `refresh_tokens`, and `login_throttles` for account and authentication state;
- `tenants`, `companies`, and `tenant_memberships` for the tenant boundary and access roles;
- `tenant_onboarding_requests` for hashed idempotency keys and canonical request digests; and
- append-only `audit_events`.

The Milestone 3 revision adds `business_units`, `departments`, `employees`, `skills`, `employee_skills`, `employee_weekly_capacity`, `projects`, and `resource_assignments`. Every table carries explicit `tenant_id`; company-context records also carry `company_id` where appropriate. Composite foreign keys bind references to the same tenant and, for company-scoped records, the same company. Skills are a tenant-owned catalog. Case/whitespace-normalized unique keys prevent duplicate unit, department, skill, employee and project names/codes within their scope.

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

| Role | Tenant read/update | Company read/update | Membership read/create/manage | Audit read | Company-context read/write |
| --- | --- | --- | --- | --- | --- |
| Owner | Yes / Yes | Yes / Yes | Yes / Yes / Yes | Yes | Yes / Yes |
| Admin | Yes / No | Yes / Yes | Yes / Member-only / Member-only | Yes | Yes / Yes |
| Member | Yes / No | Yes / No | No | No | Yes / No |

An administrator may add or manage member-role accounts but cannot grant or change owner/admin privileges. Accounts must already exist and be active before they can be added as tenant members. A user cannot modify their own membership; the final active owner cannot be revoked or demoted.

### PostgreSQL roles and RLS

The local initialization script creates separate roles:

- `bf_migrator` owns the application databases/schema objects and is the only role permitted to run the Alembic migration;
- `bf_runtime` is the API's non-superuser, non-owner, non-`BYPASSRLS` role, with no schema `CREATE` privilege and only the table privileges required by the application; and
- the bootstrap/admin role is used only to initialize local PostgreSQL and, in tests, inspect/reset the dedicated test database.

The migrations fail closed unless `bf_runtime` exists, is not superuser/`BYPASSRLS`, cannot assume the migration role, does not own the database, and cannot create schema objects. They enable and force RLS on `tenants`, `companies`, `tenant_memberships`, `tenant_onboarding_requests`, `audit_events`, and all eight Milestone 3 tables. Each new table has a tenant-context policy. Request code sets verified user and membership-authorized tenant context with transaction-local PostgreSQL settings. Missing context returns no tenant rows and rejects tenant writes. Context is discarded with the transaction, including when a pooled connection is reused.

The M3 runtime role can select, insert and update the company-context tables, but cannot physically delete them. `employee_weekly_capacity` is append-only for the runtime role (select/insert only) and also has a database trigger rejecting update/delete. Soft deactivation/cancellation and versioned capacity records preserve the current facts and history.

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

All collection and history endpoints enforce bounded `limit`/`offset`, use Pydantic request/response models and return safe errors. Development/test environments expose OpenAPI at `/openapi.json` and Swagger UI at `/docs`; these are disabled when `APP_ENV=production`. Errors use a generic JSON envelope; validation responses do not echo submitted fields. Responses include `X-Request-ID`.

## Verification status for this delivery

The last local verification used a real embedded PostgreSQL 18.6.3 instance with separate `bf_runtime`, `bf_migrator` and admin roles because Docker/Compose and system PostgreSQL were unavailable in the environment. The clean upgrade and populated M2-to-M3 upgrade test passed; `alembic check` reported no new operations. The complete suite passed **26 tests**, including the unchanged M2 authentication, tenancy, authorization, audit and RLS regression tests. Ruff format/lint, mypy and `pip check` passed. The health live/readiness, registration/login and onboarding paths are exercised by the integration suite. Pytest emitted one Starlette `TestClient` deprecation warning about using `httpx`; it did not fail the checks. These are local results; GitHub Actions and Docker/Compose results are reported separately and must not be inferred from them.

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

This is a bounded backend implementation, not a claim of production readiness. There is no frontend, CORS policy, email service, account recovery, MFA, secret-rotation mechanism, or operational monitoring/backup design here. The company-context model is limited to the entities and states described above; job-role catalogs, non-employee resources, HR/payroll, compensation, project time/cost/revenue, contracts, customers/suppliers, imports, financial facts, decision cases, analytics and feasibility calculations remain out of scope. If deployed behind a reverse proxy, configure trusted proxy handling so login throttling receives the intended client IP; do not trust arbitrary forwarded headers. TLS termination, production secret management, backup/recovery, capacity tuning, and broader threat-model review are deployment gates outside this code delivery.

**B.I.A.S.E.D. reuse decision:** The reviewed repository snapshot was `75bbf77df8bf49bd3d9e332cd3202f2d6c043a60`; its declared license is Apache-2.0. The inspected API models, route declarations and organization/resource/project-related source search did not reveal a canonical M3 company-context model or component suitable for reuse. Its API package declares Python `>=3.14`, incompatible with this backend's supported Python `>=3.11,<3.14`. No B.I.A.S.E.D. code or dependency was copied. This is a deliberate no-reuse decision based on fit/runtime compatibility, not a license restriction; no additional license obligations are introduced.
