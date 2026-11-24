# Core Financials — revenue-only integrated increment

**Status: experimental, default-disabled, incomplete, and not production-ready.** The narrow backend/UI workflow is implemented in code. Browser-driven execution remains unverified; PostgreSQL verification for this revision must be read from its GitHub Actions PR check, not inferred from local or offline checks. This is not finance-owner approval, accounting advice, a release decision, or authorization to process customer data.

## Code-backed scope

| Area | Status | Implementation |
|---|---|---|
| Persistence | Implemented in code; live PostgreSQL migration status is tracked in the PR checks | Six append-only Company-scoped tables in `src/businessflow/core_financials/models.py` and `migrations/versions/0016_core_financials.py`: versioned policies/mappings, imports, normalized records, runs, and run evidence. |
| API | Implemented; PostgreSQL-backed execution status is tracked in the PR checks | `src/businessflow/api/core_financial_routes.py`: template, policy/mapping versions, CSV imports and row pages, **Revenue-only** runs, run history/detail, evidence drill-down, and replay verification. |
| Ingestion/normalization | Implemented for one API-supported path | `src/businessflow/core_financials/service.py` bounds and parses UTF-8 CSV; `adapters.py` validates headers, dates, identities, currencies, debit/credit sides, account mappings and reversals, then normalizes general-ledger journal lines. Bank-adapter helpers remain lower-level only; the API rejects bank mappings/imports in this increment. |
| Canonical model/mappings | Partial; broader governed ledger is missing | `CoreFinancialRecord` stores one signed amount per source row, mapped account/category, source/import IDs and row hashes. Header mapping and account/category policy are separate versioned objects. This is not a shared authoritative ledger or authenticated source-of-truth model. |
| Calculation | Implemented in the API for one measure | `POST /runs` only accepts `measure: "revenue"`. It calculates revenue less revenue reductions using exact Decimal arithmetic, inclusive dates, one currency, and the selected immutable policy version. Other measures are not exposed by this integrated endpoint. |
| Reconciliation | Implemented as an optional declared-control comparison | A caller-provided expected amount, control-file SHA-256, source ID and locator are compared with explicit tolerances. File/row/locator overlap is `not_verifiable`. Independence and control value remain caller-asserted, not authenticated. |
| Evidence/drill-down | Implemented in API/UI code | Source row pages expose normalized fields, issues and row hashes. Run evidence links the revenue contribution/coefficient to the canonical record, import, source file hash, row number and row hash. Run input/result/trace hashes can be replay-verified. |
| Authentication/authorization/scope | Integrated in code; live PostgreSQL RLS/grant status is tracked in the PR checks | Existing Bearer/session auth and `finance:read` / `finance:write` checks resolve a membership-authorized Company. Service queries include tenant/company filters. Migration declares forced RLS, composite scope FKs, append-only triggers and restricted runtime grants. |
| UI | Implemented; browser E2E unverified | `/core-financials.html` is a small vanilla-JS workflow linked from the existing workspace. It versions the illustrative policy, maps/imports a CSV, inspects normalized rows, creates a revenue run, optionally reconciles, drills into evidence and requests replay verification. No framework or redesign was added. |

The standalone calculation module still contains broader experimental calculation code, but the persisted HTTP workflow is intentionally constrained to one measure. The revenue policy JSON carries the existing illustrative formula catalog; only Revenue is calculated or returned by this increment. Do not treat other measures as shipped functionality.

## User journey

1. Sign in to the existing app as a tenant owner/admin, choose a Company, and open **Revenue workflow**. The API still returns `503 core_financials_disabled` unless explicitly enabled in a non-production deployment. It hard-blocks `APP_ENV=production` even when the flag is true.
2. Create/reuse the immutable `illustrative.revenue-only` policy version. It marks only `revenue` and `revenue_reduction` category coverage complete; every other category remains unknown. The account examples are 4000 (credit-normal revenue) and 4090 (debit-normal revenue).
3. Load the included synthetic example or choose a UTF-8 CSV. Map every required general-ledger field to a header; every extra column must be explicitly ignored with a rationale. Save the Company/source-key-scoped mapping version.
4. Choose the inclusive source period and make an explicit `complete` / `partial` / `unknown` caller assertion. Importing uploads the file to `POST /imports`; the server records a file hash (not the original file), validation outcomes, mapped source values and canonical rows. Rejected/duplicate rows are visible and block a calculated Revenue value.
5. For the included synthetic sample, assert the November period complete and import these rows: a 1,000 USD credit to account 4000 and a 100 USD debit to account 4090. The exact expected Revenue is **900 USD** (`1000 - 100`). This result is policy-versioned and non-authoritative.
6. Optionally load/select a separate synthetic control CSV, enter its expected Revenue and locator, and create the run. A passing comparison only means the declared exact amounts match within the selected tolerance; it does not prove the control is independent. A reused source file is not verifiable.
7. Inspect the result, normalized import rows and per-row evidence. Select a saved run and **Replay-verify** its inputs, result, evidence and trace hashes.

The UI does not create other financial measures, bank-activity imports, a cash-flow statement, FX conversions, AI explanations or recommendations. The source system, completeness, mapping and control source are not authenticated. The illustrative policy must never be represented as approved or authoritative.

## API surface

All routes are under `/api/v1/tenants/{tenant_id}/core-financials`. The flag is checked before route work; Company access is checked on each service call.

| Method and path | Purpose | Permission |
|---|---|---|
| `GET /template` | Example policy and canonical header contracts (only general ledger is accepted by this API increment) | `finance:read` |
| `GET /policies` / `POST /policies` | List or append a versioned illustrative policy | read / write |
| `GET /mappings` / `POST /mappings` | List mappings; create a general-ledger source-header mapping | read / write |
| `POST /imports` | Bounded general-ledger CSV import (`metadata_json`, `file`, `Idempotency-Key`) | `finance:write` |
| `GET /imports` / `GET /imports/{import_id}` | List or inspect imports | `finance:read` |
| `GET /imports/{import_id}/records` | Paginated normalized row and validation details | `finance:read` |
| `POST /runs` | Persist an immutable **Revenue-only** run | `finance:write` |
| `GET /runs` / `GET /runs/{run_id}` | List or inspect run result/hashes | `finance:read` |
| `GET /runs/{run_id}/evidence` | Paginated Revenue-to-source-row contributions | `finance:read` |
| `POST /runs/{run_id}/verify` | Replay stored inputs/results/evidence | `finance:write` |

A Revenue run request is version-bound through `policy_id` and the required `ledger_import_id`; the strict request schema accepts only `measure: "revenue"` and does not expose `bank_import_id` or any other measure. Bank activity is outside this workflow. An optional reconciliation control contains `metric: "revenue"`, expected decimal text, currency, tolerances, and a caller-declared source ID/hash/locator.

## Data handling, boundaries and retention

The original CSV file is not archived. Mapped source values and normalized amounts/identifiers are retained in row records and immutable snapshots; run inputs include accepted canonical facts. Migration `0016_core_financials` uses Company scope FKs, forced RLS, append-only triggers and `bf_runtime` `SELECT`/`INSERT` grants without `UPDATE`/`DELETE`/`TRUNCATE`. Hashes are integrity references, not encryption or source authentication. No customer-data retention/deletion workflow, legal hold, key rotation, finance-owner approval, or production controls are included.

`CORE_FINANCIALS_ENABLED` is `false` by default in config, `.env.example`, and Compose; set it to `true` only for an authorized isolated non-production test. No setting enables it in production. Do not upload real customer/confidential data in this prototype.

## Verification status and reproducible PostgreSQL setup

Scoped local, database-independent verification ran: `.venv/bin/pytest -q --noconftest tests/unit --ignore=tests/unit/test_calculation_analysis.py -k 'not test_m14_committed_rows_flow_to_hash_bound_analysis_envelope'` (**132 passed, 1 deselected**; the deselected M11 bridge test and its generic analysis helper are retained outside this Core Financials changeset), plus `.venv/bin/pytest -q --noconftest tests/test_position_review_web.py` (**1 passed**). Ruff format/lint, Mypy on 122 source files, Python/JavaScript syntax checks, `git diff --check`, and offline Alembic history/SQL generation also passed in the working tree. These checks do not substitute for PostgreSQL. The three PostgreSQL integration tests cover import replay, authenticated Company scoping, persisted evidence/replay, and RLS/runtime-grant/immutability. Direct local invocation is blocked during `conftest.py` loading because the three PostgreSQL URLs are unset. Read the actual Backend checks result for this revision to determine live migration, API/auth/tenant authorization, RLS/runtime grants and immutability status; offline Alembic SQL generation does not verify them. Browser E2E remains unverified; no browser automation harness was found.

The current sandbox has no `docker`/`podman`, PostgreSQL server/client binaries, or configured PostgreSQL URLs. The GitHub Actions job in `.github/workflows/backend.yml` runs on GitHub-hosted Ubuntu with a PostgreSQL 18.7 service, creates the bootstrap/migrator/runtime roles, applies the migration, runs `alembic check`, and executes the full pytest suite. It is triggered by pull requests targeting `main` or pushes to `main`, and has no `workflow_dispatch`. A verification-only Draft PR targeting `main` is the trigger for this work; use that PR's Backend checks run as the authoritative PostgreSQL result.

With Docker Compose available, the local disposable test path is also available. Run it against a fresh project/volume only:

```bash
# In the repository root. Use generated test-only credentials; do not reuse production values.
export BF_BOOTSTRAP_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BF_MIGRATOR_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export BF_RUNTIME_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
export APP_SECRET_KEY="$(python -c 'import secrets; print(secrets.token_urlsafe(48))')"
export APP_ENV=test

# Unique Compose project gives this destructive suite a fresh isolated PostgreSQL volume.
docker compose -p bfs-core-financials-test --profile test run --build --rm tests
# Remove only that isolated test project's container/network/volume after the run.
docker compose -p bfs-core-financials-test down -v
```

The `db` service uses `postgres:18.7-alpine`; `scripts/db-init/010-create-roles.sh` creates `bf_migrator` (schema owner), `bf_runtime` (non-owner, non-superuser, no `BYPASSRLS`) and the `businessflow_test` database; the test profile supplies `TEST_DATABASE_URL`, `TEST_MIGRATION_DATABASE_URL`, and a separate `TEST_ADMIN_DATABASE_URL`. Its session fixture downgrades the dedicated test schema to `base` and upgrades to `0016_core_financials`, so **never point it at any non-disposable database or use the normal Compose project/volume**. CI additionally verifies PostgreSQL 18.7 exactly, runs `alembic check`, then all integration tests.
