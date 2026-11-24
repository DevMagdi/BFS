# M16 — Direct Cash Forecast Engineering Prototype

## Status and decision boundary

This is a **bounded M16 engineering prototype**, not an approved finance forecast, accounting system, complete cash view, or production capability. The route and service perform deterministic arithmetic over one M14 intake and one exact M14 reconciliation-group hash, using opening cash and movements that a caller explicitly designates. The result is a **known-item roll-forward** of submitted, M14-amount-committed rows. It does not establish that those rows are cash, that the source is authoritative, or that the cash population is complete or accurate.

M14 records generic source observations, reconciliation grouping and source-reported period bounds. It does not authenticate an opening balance, a receipt/disbursement classification, an expected cash date, actual-versus-forecast status, or completeness. M16 never treats M14 `period_start`/`period_end` as cash dates, and generic M14 fields are not proof of cash semantics or timing. Flow type, local cash date, actual/forecast basis, opening-balance meaning, and any supersession are explicit **caller assertions**; the response labels them as not authenticated by M14.

M14's finance-owner, authoritative-source/access, correction, retention, and independent-control gates remain unresolved. No finance-owner or accounting approval, forecast-accuracy claim, source-authentication claim, production readiness, or production/customer-data authorization is implied by this code, its feature flags, migration, API response, or passing tests. No payment, facility draw, posting, or external action is initiated. OpenFPA was design inspiration only; no OpenFPA code or float-based arithmetic was copied.

## Default-disabled activation and production boundary

`DIRECT_CASH_FORECASTS_ENABLED` defaults to `false` in `Settings`, `.env.example`, and the Docker Compose API service. M16 also requires both `FINANCIAL_CALCULATIONS_ENABLED=true` and `FINANCE_SOURCE_INTAKE_ENABLED=true`. All three controls are checked at the router and service boundaries; no request field, query parameter, header, or M14 metadata can enable the feature. `APP_ENV=production` always blocks it with `503 direct_cash_forecasts_disabled`, even when all flags are true.

A deployment flag is only an activation guard. It is **not** approval to enable production/customer financial data, source access, an accounting policy, or a forecast. The current tests explicitly enable the flags for their isolated fixtures; that does not change the deployment defaults or the production block.

## Contract, source binding, and caller assertions

M16 has an independent versioned contract/formula catalog:

- Contract/formula ID: `businessflow.finance.direct-cash-forecast`
- Contract/formula version: `1.0.0`
- Engine: `businessflow.direct-cash-forecast.v1`
- Catalog: `1.0.0`
- Horizon: exactly 13 consecutive seven-calendar-day weeks

A create request names one M14 intake and the SHA-256-shaped hash of exactly one reconciliation group. It includes:

| Request field | Requirement and interpretation |
| --- | --- |
| `snapshot_id` | Caller-supplied UUID that identifies this input assertion snapshot. It does not authenticate or authorize anything. |
| `reconciliation_group_hash` | One exact group in the selected M14 intake. No cross-group, cross-intake, or cross-currency aggregation is performed. |
| `as_of_date` | Explicit canonical `YYYY-MM-DD` calendar date; this becomes the inclusive first day of week 1. Future local dates are unresolved. |
| `opening_cash` | One explicitly designated M14 row number and submitted `amount_text`, or `null`/missing. Caller assertion that this amount is opening cash at the start of `as_of_date`; M14 does not prove that meaning. A missing/invalid opening amount leaves all closing roll-forwards unknown. |
| `movements[]` | Each entry names one M14 source row and `amount_text`, and explicitly asserts `cash_flow_type` (`receipt`, `disbursement`, `unknown`, or `unsupported`), `basis` (`actual`, `forecast`, `unknown`, or `unsupported`), and a local calendar `cash_date` or `null`. |
| `supersedes_source_row_number` | Optional explicit correction/supersession link to another actual movement row in the same intake/group. It is a caller assertion, not an M14 correction record or authority decision. |

Every submitted source row must resolve to row evidence in the selected M14 intake and exact group. M16 parses each resubmitted amount and validates it against the row-bound M14 `app-secret.v1` HMAC commitment. Commitment verification binds the canonical Decimal value to the tenant, company, intake, source/file, row, group, contract, and mapping evidence. M14 retained no raw row amounts; old M14 rows without a valid commitment cannot contribute. The commitment proves only that the submitted numeric value matches the earlier M14 commitment—it does not authenticate the uploaded source or validate the caller's cash interpretation.

M16 additionally stores a keyed fingerprint of the exact submitted amount text (including absent/invalid inputs) in its hash-bound input snapshot. Thus lexical variants that represent the same canonical Decimal may verify against the M14 numeric commitment, but produce a different M16 request fingerprint and do not replay as the same exact request. Raw request amount strings are not included in M16 run input snapshots or audit metadata. The result does expose derived numeric subtotals/roll-forwards to authorized finance readers; those values remain sensitive finance data.

The M14 group contributes its one reported currency code only. M14 period bounds and generic `amount`/`currency`/key fields are retained as evidence references; they are not reinterpreted as cash timing or classification. M14's caller-supplied control comparison, source identity, source-as-of time, and freshness policy are not independent cash controls or source authentication.

## Fixed 13-week arithmetic and unknown behavior

Week 1 covers `as_of_date` through `as_of_date + 6 days`; each next week starts the day after the prior week ends. Week 13 ends at `as_of_date + 90 days`, inclusive. Weeks are anchored to the caller's `as_of_date`, not shifted to Monday or inferred from M14 periods. The Company IANA timezone is recorded from authorized Company context; cash dates are date-only local calendar assertions, not timestamps.

The only arithmetic is exact bounded Python `Decimal` arithmetic with no currency rounding or quantization:

```text
closing_cash[n] = opening_cash[n] + receipts[n] - disbursements[n]
opening_cash[n] = closing_cash[n - 1]  # n > 1; unknown carries forward as unknown
```

Actual and forecast receipts and disbursements remain separate subtotals. The combined receipts/disbursements totals are also explicit known subtotals; each weekly closing is labeled `closing_cash_known_item_rollforward`, never an authoritative bank balance.

No absent category is defaulted to zero. A numeric category value is reported only as the subtotal of one or more explicit, usable rows. A category with no rows is `unknown`; if some values are known but another item that could belong to that category is unresolved, it is `partial` with reason codes and unresolved row numbers. A source-bound explicit zero amount is an allowed known input; it is not inferred from absence. Unknown/unsupported classifications, missing dates, missing rows, uncommitted or mismatched amounts, duplicate/conflicting row references, invalid evidence, or invalid supersession are visible and prevent a complete arithmetic schedule. Negative movement magnitudes are invalid rather than silently sign-flipped; no absolute-value or direction inference is used. Opening cash is not normalized or rounded.

Boundary behavior is deliberately explicit:

- A missing/invalid opening value leaves the first opening and all subsequent cash roll-forwards unknown.
- An actual item dated before `as_of_date` is retained as excluded historical context; it is not brought into the horizon.
- A forecast item dated before `as_of_date` is overdue and unresolved; M16 does not invent a replacement date or move it to a future week.
- A row dated after the inclusive 13-week end is retained as out-of-horizon context and excluded.
- A row with unknown/unsupported classification, missing date, missing amount, duplicate/invalid M14 evidence, or a missing caller assertion is not summed.
- The entire M14 group must be accounted for by the explicit opening row and movement assertions. Omitted M14 rows are represented as missing/unclassified inputs, never assumed irrelevant or zero.
- M14 quality, reconciliation, freshness-at-intake and freshness-at-calculation, group row-count limits, amount commitments, and evidence integrity are checked. A failed/unverifiable gate makes the result incomplete and prevents known closing roll-forwards, while safe known subtotals and reason codes may remain visible.

The returned `calculation_state="calculated"` means only that the supported arithmetic rules produced all thirteen known-item weekly roll-forwards from explicit, verified inputs without an unresolved implementation gate. The result still carries `forecast_completeness_status="not_established_by_m14"`, `source_authority="not_established"`, `cash_population_completeness="not_established"`, `forecast_accuracy="not_established"`, and `production_customer_data_authorization="not_authorized"`. A calculated run is not a complete, accurate, approved, or source-authenticated cash forecast.

### Actual supersession

Supersession is never inferred from row order, date, amount, or source ID. A caller may explicitly link an actual movement to another actual movement row only. The implementation requires unique row references, verified amounts and valid evidence in the same M14 group, and matching receipt/disbursement direction. Multiple replacements, cross-direction links, duplicate references, unresolved links, or cycles remain explicit and make the calculation incomplete. Valid explicit replacement chains exclude each superseded actual once. This bounded mechanism does not establish an approved correction policy and does not link corrections across M14 intakes.

## API

The M16 routes are under `/api/v1/tenants/{tenant_id}/direct-cash-forecasts`:

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `GET /contract` | `finance:read` | Read the immutable versioned M16 formula contract and limitations. |
| `POST /finance-source-intakes/{intake_id}/runs` | `finance:write` | Submit opening cash and item-level assertions; persist a calculated or incomplete run and trace. |
| `GET /finance-source-intakes/{intake_id}/runs/{run_id}` | `finance:read` | Read hash-verified immutable input references, results, statuses, and limitations. Raw amount text is not stored in the input snapshot. |
| `POST /finance-source-intakes/{intake_id}/runs/{run_id}/verify` | `finance:write` | Resubmit the exact request and rebuild it against the same immutable M14 evidence; compare hashes and report mismatch reason codes. |

Finance permissions remain the existing owner/admin-only `finance:read` and `finance:write`; generic company-context membership alone does not grant finance access. Service authorization resolves Company scope from verified membership, and database access is additionally restricted by tenant/company context RLS. Replay accepts raw amount strings and could act as a commitment-verification oracle, so it requires `finance:write`. Replay is not an approval, source refresh, independent audit, or replacement of the immutable record. Sensitive input values are not echoed in validation errors or audit metadata.

## Persistence, integrity, and migration

Migration `0013_direct_cash_forecast` adds append-only `direct_cash_forecast_runs`, binding each run to the same tenant/company, requester membership, M14 intake, mapping version/hash and global source contract. It records the fixed horizon, as-of date, Company timezone, currency, formula/engine/catalog versions, reason codes, redacted input evidence, result snapshot, capture time, and input/result/trace/evidence-snapshot hashes. M16 run snapshots do not store raw submitted amount text or raw M14 rows; result snapshots do contain derived cash subtotals and known-item roll-forwards.

The run table uses composite company-scope foreign keys, forced RLS against transaction-local `app.tenant_id` and `app.company_id`, and a database trigger rejecting update/delete. The least-privilege `bf_runtime` role receives `SELECT` and `INSERT` only (no `UPDATE`, `DELETE`, or `TRUNCATE`); migrations must run as dedicated `bf_migrator`, with checks rejecting a superuser/BYPASSRLS runtime or runtime membership in the migrator role. API permission checks and RLS complement one another.

The immutable record contains:

- `input_hash`: canonical redacted caller assertions and M14 row-evidence references, including keyed amount fingerprints;
- `result_hash`: the exact versioned 13-week result snapshot;
- `formula_hash`: the canonical M16 formula contract definition;
- `evidence_snapshot_hash`: the formula definition plus input evidence snapshot;
- `trace_hash`: run identity, engine/catalog/formula versions, state/reasons and result/input hashes.

Read/replay verifies saved hashes and scope/version consistency. Replay also rebuilds the input and result using current supported M16 code, the caller's resubmitted raw strings, the same M14 evidence, and the saved capture time. A mismatch is reported, not used to mutate history. SHA-256 hashes are integrity markers, not encryption or confidentiality controls. The keyed fingerprints depend on `APP_SECRET_KEY`; key custody/rotation is not implemented. There is no M16 retention/deletion workflow or legal-hold policy. Downgrading `0013_direct_cash_forecast` drops its run table and M16 run evidence, so rollback is destructive.

## Limits and unresolved governance

The prototype has one source group and one currency per run. It does not implement a bank connector, bank-balance authentication, AR/AP subledger, cash application, accrual-to-cash bridge, tax/payroll/debt schedules, credit facility, probability, scenario model, AI/ML prediction, FX, rounding, workflow approval, or external action. No general completeness claim is made even if each submitted M14 row is accounted for: M14 cannot establish that the selected group or intake covers the Company's cash population.

Finance ownership, source authority and access, date/classification meaning, opening-balance semantics, freshness and timing policy, correction/supersession, retention, independent controls, forecast evaluation/accuracy, and production use remain open. Those gates need accountable owner decisions and independent evidence; they cannot be closed by a caller field, passing test, M14 control status, hash, or deployment flag. Keep customer/production finance data unauthorized unless and until a separate decision approves the necessary policy, access, retention, security and operations.

## Verification — 2026-11-21

On the current M16 branch, local checks passed:

- Ruff lint and format checks across the repository; mypy over all **113** `src/businessflow` source files; and `compileall` for source, migrations and tests.
- M16 database-independent tests: **12 passed**.
- M16 PostgreSQL-backed API/authorization/RLS tests: **4 passed** against an ephemeral PostgreSQL **18.6** instance with separate `bf_runtime`, `bf_migrator`, and test-admin roles. Coverage includes exact 13-week API results, redacted persistence, hash/replay mismatch handling, missing/unknown/stale abstention, default-off production guard, finance permissions, cross-company isolation, forced company RLS, least-privilege grants, and append-only database behavior.
- Full repository suite: **208 passed**. This includes the M16 migration upgrade/downgrade/re-upgrade and unauthorized migrator rejection test, plus existing PostgreSQL integration coverage.
- `alembic upgrade head --sql` generated migration SQL through `0013_direct_cash_forecast`; Alembic head resolution and source/test syntax checks passed.

One existing non-blocking Starlette `TestClient`/httpx deprecation warning was reported. Local software checks do not establish financial or accounting correctness, source authority, independent controls, forecast accuracy, milestone approval, or production readiness. PostgreSQL 18.6 local verification is separate from the repository's configured PostgreSQL 18.7 CI; do not claim CI passed until it runs on the final branch head.
