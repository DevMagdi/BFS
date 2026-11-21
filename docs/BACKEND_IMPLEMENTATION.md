# Milestone 2 backend implementation baseline

## Status and decision boundary

This document describes the backend code currently in the repository. The implementation was authorized as a limited Milestone 2 baseline; it does **not** approve every proposal in the architecture or technical-baseline documents, establish a production architecture, or make product, accounting, or finance-policy decisions. The product definition remains provisional, and [`REQUIREMENTS_TRACEABILITY.md`](REQUIREMENTS_TRACEABILITY.md) remains blocked because the exact original 24 requirements were not supplied.

The baseline intentionally excludes product/business calculations, resource or finance engines, AI/LLM/RAG and semantic engines, workflow automation, external ERP integrations, a large frontend, microservices, Kubernetes, and unrelated features.

## Implemented components

The code targets Python 3.11–3.13 under the current package metadata and uses a modular-monolith structure with FastAPI, SQLAlchemy 2, Alembic, psycopg 3, and PostgreSQL. PostgreSQL is required; SQLite does not exercise the RLS guarantees and is not an integration-test substitute. Compose is configured for PostgreSQL 18.

### Schema and tenancy

The initial Alembic revision is `0001_backend_foundation`. It creates:

- `users`, `auth_sessions`, `refresh_tokens`, and `login_throttles` for account and authentication state;
- `tenants`, `companies`, and `tenant_memberships` for the tenant boundary and access roles;
- `tenant_onboarding_requests` for hashed idempotency keys and canonical request digests; and
- append-only `audit_events`.

Each tenant has one primary company. A unique company `tenant_id` enforces one company per tenant; the deferred composite tenant-to-primary-company foreign key ensures the primary company belongs to that tenant. Company creation and the initial active owner membership are committed in the same database transaction as the tenant and idempotency record. An identical retry with the same user/key and request returns the original tenant; reusing that key with changed request data is rejected.

Memberships are soft-revoked rather than physically deleted. The application serializes owner changes using a tenant lock and rechecks authorization under that lock. A deferred PostgreSQL constraint trigger also rejects a commit that would leave an active tenant with no active owner.

### Authentication and authorization

Registration, login, refresh, logout, and `/auth/me` are implemented. Passwords use Argon2id (the initial configured parameters are 19 MiB memory, two iterations, one lane). Access tokens are short-lived HS256 JWTs with issuer, audience, token-use, user, session, and token identifiers. Requests validate both the token and the corresponding unrevoked database session. Refresh credentials are random opaque values; only SHA-256 digests are stored. Refresh rotates the token, detects reuse, and revokes the session and its refresh-token family. Logout revokes the session. Disabled accounts cannot log in, refresh, or continue using an existing access session.

Login throttling uses atomic PostgreSQL upserts for HMAC-hashed email and source-IP buckets. Defaults are five failed attempts per email bucket and twenty per source-IP bucket in a fifteen-minute lockout window; all are configurable. Raw passwords and bearer/refresh tokens are not written to audit metadata or application error logs. Validation and authentication errors return a bounded safe error shape with a request ID.

The API returns access and refresh tokens in JSON, not cookies. Clients must protect them and use TLS. This baseline does not implement email verification, MFA, password recovery, or invitation emails.

The service layer and API enforce this permission matrix:

| Role | Tenant read/update | Company read/update | Membership read/create/manage | Audit read |
| --- | --- | --- | --- | --- |
| Owner | Yes / Yes | Yes / Yes | Yes / Yes / Yes | Yes |
| Admin | Yes / No | Yes / Yes | Yes / Member-only / Member-only | Yes |
| Member | Yes / No | Yes / No | No | No |

An administrator may add or manage member-role accounts but cannot grant or change owner/admin privileges. Accounts must already exist and be active before they can be added as tenant members. A user cannot modify their own membership; the final active owner cannot be revoked or demoted.

### PostgreSQL roles and RLS

The local initialization script creates separate roles:

- `bf_migrator` owns the application databases/schema objects and is the only role permitted to run the Alembic migration;
- `bf_runtime` is the API's non-superuser, non-owner, non-`BYPASSRLS` role, with no schema `CREATE` privilege and only the table privileges required by the application; and
- the bootstrap/admin role is used only to initialize local PostgreSQL and, in tests, inspect/reset the dedicated test database.

The migration fails closed unless `bf_runtime` exists, is not superuser/`BYPASSRLS`, cannot assume the migration role, does not own the database, and cannot create schema objects. It enables and forces RLS on `tenants`, `companies`, `tenant_memberships`, `tenant_onboarding_requests`, and `audit_events`. Request code sets verified user and membership-authorized tenant context with transaction-local PostgreSQL settings. Missing context returns no tenant rows and rejects tenant writes. Context is discarded with the transaction, including when a pooled connection is reused.

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

Development/test environments expose OpenAPI at `/openapi.json` and Swagger UI at `/docs`; these are disabled when `APP_ENV=production`. Errors use a generic JSON envelope; validation responses do not echo submitted fields. Responses include `X-Request-ID`.

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

## Known limits and deployment notes

This is a small, reviewed implementation baseline, not a claim of production readiness. There is no frontend, CORS policy, email service, account recovery, MFA, secret-rotation mechanism, or operational monitoring/backup design here. If deployed behind a reverse proxy, configure trusted proxy handling so login throttling receives the intended client IP; do not trust arbitrary forwarded headers. TLS termination, production secret management, backup/recovery, capacity tuning, and broader threat-model review are deployment gates outside this code delivery.
