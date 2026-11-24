# M15 — Deterministic Financial Calculation Prototype

## Status and decision boundary

This branch contains a **bounded, experimental M15 engineering prototype**, not an approved M15 milestone or an accounting system. Its only executable entry is the exact mechanical subtotal of one complete M14 reconciliation group. It does not assert what the amount represents. It is not revenue, expense, gross profit, cash, an account balance, or accounting truth.

The prototype does not close M14's open governance gates: no accountable finance owner, authoritative source/access decision, correction or retention policy, or independently governed control has been approved. Production and customer accounting data remain unauthorized. The roadmap continues to treat M15 as conditional; this prototype is not owner approval, milestone acceptance, a delivery commitment, or authorization to expand the formula scope. See [`roadmap/PRODUCT_ROADMAP.md`](roadmap/PRODUCT_ROADMAP.md).

Only `businessflow.finance.source-group-subtotal` version `1.0.0` is executable in M15, and its catalog state is `executable_mechanical_only` / `mechanical_only_not_accounting_policy`. Gross profit, margin ratios, multi-period/source rollups, and M15 cash-flow formulas remain deferred with no executable M15 formula. A separate M16 prototype now implements only a caller-asserted direct 13-week known-item cash roll-forward; it does not convert M15 accrual amounts to cash or authenticate cash meaning, timing, completeness, or accuracy. See [`M16_DIRECT_CASH_FORECAST.md`](M16_DIRECT_CASH_FORECAST.md). It requires distinct owner decisions and independent expected examples before any broader proposal to enable finance calculations.

## Default-disabled activation

`FINANCIAL_CALCULATIONS_ENABLED` defaults to `false` in `Settings`, `.env.example`, and the Docker Compose API service. M15 routes also require `FINANCE_SOURCE_INTAKE_ENABLED=true`; both flags are checked at route and service boundaries. Only a deployment operator can pass both settings through the operator-managed environment and restart the API for an isolated, controlled test. `APP_ENV=production` always blocks M15, even if both flags are true. The flags are activation controls, not a production/customer-data authorization gate. No request field, query parameter, or header can enable them.

## M14 evidence and keyed amount commitments

M14 intentionally does not retain raw finance source rows or amount text. Migration `0012_financial_calculations` adds a nullable `amount_commitment_hash` and `amount_commitment_key_version` to `finance_source_row_evidence`. For each valid M14 row with an amount and reconciliation group, M14 stores an HMAC-SHA-256 commitment using the server's `APP_SECRET_KEY` and key label `app-secret.v1`. The HMAC binds canonical fixed-point Decimal text to tenant, Company, intake, source key, uploaded-file hash, row number and row hashes, group hash, and exact contract/mapping identities and hashes. Invalid or ungrouped rows receive no commitment.

The commitment lets M15 reject a caller-submitted amount that does not match the amount M14 validated, without persisting that amount a second time. Intakes recorded before migration `0012` retain null commitment fields; because M14 does not retain raw amounts, those rows cannot be backfilled and are ineligible for M15 calculation. The HMAC does **not** authenticate the original file/source, establish source authority, prove control independence, or approve accounting semantics. It is not encryption. The prototype has no key-ring or rotation workflow: changing `APP_SECRET_KEY` makes older commitments unverifiable unless the original key remains available. Key custody, rotation, backup, retention, and any production use remain open security/governance decisions.

M15 re-verifies the M14 intake/result/input hashes and exact contract/mapping references, then checks row-evidence counts, the requested group, every row-bound amount commitment, passed M14 row quality, and freshness both as recorded at intake and recalculated at the M15 capture time. The caller resubmits the exact amount strings. Their lexical form is retained in the request/input hash, even when distinct strings represent the same Decimal value; HMAC verification uses canonical Decimal text. M14's caller-supplied control comparison status is retained as context but is not treated as an independent approval or an accounting-policy gate.

The entire group must be represented by all and only its unique valid rows. A missing, invalid, duplicate, conflicting, mismatched, stale, uncommitted, or incomplete input produces an `incomplete` trace with reason codes and **no output**; no value is filled with zero. A group is limited to 10,000 rows. Mixed currencies, FX, minor-unit rounding, quantization, fiscal-calendar interpretation, source precedence, cross-group/period rollups, and account classification are not performed.

## Formula and calculation contract

The enabled formula is a one-group sum of canonical M14-valid `amount` values, preserving the source-reported period and one ISO 4217 currency. M15 delegates the arithmetic to M11's `MechanicalSumFormulaDefinition` and exact Decimal sum. The engine uses bounded plain Decimal inputs, an isolated 256-digit context, up to 10,000 inputs, and bounded fixed-point output; precision loss or unsupported output fails closed. Output is not rounded to the currency's minor-unit exponent. An independent `1.005` expected result in unit tests is a software golden vector only, not a finance-owner-approved accounting example.

The M15 trace binds the formula/catalog and engine versions, M14 intake and mapping evidence, requested group, row evidence references/status, exact request inputs, M11 trace, state/reasons, result and replay hashes. It is deterministic for the same run ID, captured time, request, M14 evidence, and HMAC key.

## Persistence and immutability

Migration `0012_financial_calculations` adds:

- row-bound M14 HMAC commitment columns and a check that commitment hash/key-version are both null or valid as a pair; and
- Company-scoped `financial_calculation_runs`, with same-tenant/company foreign keys to the intake, mapping, contract, and requesting membership, hash/version/state constraints, forced company-context RLS, runtime `SELECT`/`INSERT` grants only, and an append-only database trigger.

Downgrading revision `0012_financial_calculations` is destructive: it drops all M15 run records and both M14 commitment columns. A later re-upgrade cannot restore runs or commitments; retained M14 rows return with null commitments and remain ineligible for M15 because their raw amounts were never stored.

The persisted M15 record contains the formula/input/result/trace hashes and a hash-checked, redacted evidence snapshot of M14 references, row numbers/statuses, and hash/version metadata. It deliberately omits raw amount strings and the derived output value. The create response returns the trace for the caller's submitted request, but the database does not store those values in the M15 run row. These SHA-256 values are integrity markers, **not encryption or confidentiality controls**; a low-entropy amount or total may be guessable from an unkeyed hash, and the M14 intake already retains caller-supplied control values/results. Replaying a run therefore requires the caller to resubmit the original `snapshot_id`, group hash, row numbers, and exact amount strings; the API rebuilds and hashes the snapshot from current immutable M14 evidence at the saved capture time. Saved run details expose persisted metadata/evidence, not raw values or output. This prototype has no deletion, retention, or secret-rotation procedure.

## API

Routes are under `/api/v1/tenants/{tenant_id}/financial-calculations`. All are default-disabled and use the existing `finance:read` / `finance:write` authorization (owner/admin in the current role matrix).

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /formulas` | `finance:read` | Return the versioned catalog, including explicit deferred entries. |
| `POST /finance-source-intakes/{intake_id}/runs` | `finance:write` | Submit a group hash and bounded list of source-row numbers/amount strings; create a calculated or incomplete immutable run. |
| `GET /finance-source-intakes/{intake_id}/runs/{run_id}` | `finance:read` | Read hash-verified persisted metadata and redacted evidence. |
| `POST /finance-source-intakes/{intake_id}/runs/{run_id}/verify` | `finance:write` | Resubmit raw amount strings and verify M14 HMACs, rebuild M11/M15 traces, and compare against stored hashes. The stricter capability limits the commitment-verification oracle. |

Replay is a verification operation, not an approval, source refresh, or independent audit. It returns a report with passed/failed state and reason codes; changed inputs are reported as a mismatch rather than silently replacing the immutable run.

## Verification boundary

The database-free unit suite covers M11 exact Decimal behavior and M15 catalog, golden result, lexical input hashing, HMAC binding, evidence eligibility, stale-at-calculation abstention, missing/invalid/duplicate/conflicting rows, limits, feature flags, and replay/tamper detection. PostgreSQL integration tests are authored for M15 API behavior, auth/tenant isolation, RLS, grants, append-only triggers, M14 commitment persistence, the direct `0011`↔`0012` upgrade/downgrade/re-upgrade path, and rejection of an unauthorized migration role without partial schema changes. GitHub Actions is configured to run the full suite with separate PostgreSQL runtime, migration, and admin test roles on PostgreSQL 18.7; offline Alembic SQL is not a substitute for that run.

### M15-scope verification at its delivery — 2026-11-21 (historical)

The results below describe the M15 delivery snapshot before M16 migration `0013_direct_cash_forecast`; they are not the current Alembic head or M16 verification. The current-head regression, full-suite, and M16 verification results are recorded in [`M16_DIRECT_CASH_FORECAST.md`](M16_DIRECT_CASH_FORECAST.md).

- Ruff lint and format checks pass (**151 files**); mypy passes for **105 source files**.
- The isolated unit suite passes (**105 passed**). `compileall` and `git diff --check` pass.
- Against an ephemeral, role-separated PostgreSQL **18.6** instance, `alembic upgrade head` and `alembic check` pass with no model drift. The full PostgreSQL-backed test suite passes (**192 passed**), including M15 API, authorization/tenant isolation, RLS/grants/append-only behavior, and migration downgrade/re-upgrade and rejected-role safety.
- Alembic reports `0012_financial_calculations` as the sole head. OpenAPI generation exposes all **four M15 route paths** among 89 paths; the catalog contains five entries (one executable mechanical subtotal and four deferred formula candidates).
- The first PR CI run on the previous head, using PostgreSQL 18.7, caught an overlong Alembic revision ID and strict JSON wire parsing of UUID/list fields. Both issues are corrected; the PostgreSQL 18.7 workflow must pass on the updated PR head before merge.

Software tests can establish implementation behavior only. They do not demonstrate accounting correctness, source authority, independent-control governance, owner approval, production readiness, or permission to process customer data. Until those gates are separately decided, M15 remains a conditional prototype with one mechanical formula.
