# Phase 3 Business Intelligence increment

This increment adds a bounded, backend-only Business Intelligence API on top of the existing
Business Semantic Layer. It reuses the existing Company Context, Universal Data, M5, Core
Financials, and authorization services. It does not add another ingestion path, infer identities,
turn Universal Data amounts into finance facts, or replace any existing calculation engine.

## API surface

All routes are under `/api/v1/tenants/{tenant_id}/business-intelligence`:

- `GET /registry` returns the versioned system-metric catalog, supported calculation capabilities,
  expression operators, and explicit unsupported capabilities.
- `GET /metrics` lists Company metric definitions, with optional `metric_key`, `domain`, `status`,
  history, and pagination filters. `GET /metrics/{metric_key}` returns the latest published
  definition while a newer draft/review version is pending; it returns the newest deprecated
  version if the key has been deprecated. `GET /metrics/{metric_key}/versions/{version}` returns
  an exact Company definition version, including draft and review states.
- `POST /metrics` creates an immutable draft definition. The lifecycle endpoint
  `POST /metrics/{metric_key}/versions/{version}/lifecycle` allows draft → review, review → draft,
  review → published, and published → deprecated. Publishing requires a different authorized
  reviewer than the author and a review note. A published version is never edited in place.
- `GET /metrics/{metric_key}/readiness` checks the selected published definition against a published
  Company configuration and current supported inputs. Optional metric/configuration versions pin
  the check.
- `GET /configuration` reads the current or exact published Company configuration;
  `GET /configurations` lists history; `POST /configurations` publishes a new immutable version.
  The configuration pins Company currency/timezone/calendar rules, source precedence, and the exact
  Universal Data source-profile versions selected for that precedence.
- `POST /analyses` creates an immutable analysis using an exact published definition and required
  Company configuration version. `GET /analyses` lists saved analyses; `GET /analyses/{id}` reads
  one; `GET /analyses/{id}/replay` deterministically replays its saved snapshots; and
  `GET /analyses/{id}/evidence` returns paginated, typed evidence references.

Metric-definition and Company-configuration writes are blocked in an explicitly seeded M10
synthetic demo Company by the existing seeded-demo API guard. Saved-analysis creation uses a separate
serialized guard that also keeps seeded-demo workspaces read-only, while regular Company Context
readers can still create allowed analyses subject to finance permissions. There is no Business
Intelligence browser workspace in this increment.

## Bounded calculation and evidence contracts

The registry exposes five existing M5 metrics and six existing Core Financials measures as system
metrics. M5 and Core Financials selections reference an exact existing saved run; BI rejects an M5
version or Core Financials measure mismatch and does not recalculate capacity or financial measures.
M5 and Core Financials definition units must match the selected system metric, and Universal Data
counts/distinct values/source amounts use their declared canonical units. M5 snapshots are validated
with the existing M5 replay service. A selected Core Financials run must pass the existing
verification service and retain its original policy, trace, currency, and period. M5's structured
overload and skill-inventory observations cannot be consumed as scalar formula dependencies.

Company definitions use an allow-listed calculation schema:

- Universal Data record counts and non-identity distinct-text counts remain source-scoped.
- Universal Data amount sums preserve the source currency and explicitly choose a single-currency or
  by-currency result. These sums are source observations, never recognized revenue, expense, profit,
  cash, or Core Financials facts. No FX conversion, refund/reversal policy, or accounting recognition
  is introduced.
- Custom formulas are bounded Decimal expression trees. They reference published metric keys and
  pin the exact Company or system metric version/hash. A formula analysis must provide the exact
  saved analysis for every pinned dependency, with matching Company configuration, period, and
  dimensions. Only scalar complete dependency values can produce a complete formula result;
  formulas do not add independent source evidence or promote an incomplete dependency.
- Arbitrary SQL, dynamic field access, user code, automatic cross-source party matching, and
  unsupported calculations are rejected. Division by zero, unit/currency mismatch, missing source
  evidence, and incomplete inputs abstain or produce a non-complete status rather than being
  silently coerced.

Readiness evaluates source precedence, source-profile state, last promoted Universal Data ingestion,
freshness, bounded record counts, required fields, and current analytically usable semantic
relationships. Candidate-source checks count at most 10,001 matching records per configured source;
only the selected source's at-most-10,000 records are materialized into the immutable analysis input.
Warnings are returned explicitly alongside blockers and limitations. A source's
`authority_classification` remains metadata, not proof of authority; an `owner_asserted_primary`
requirement checks only that same assertion in the pinned and current profiles. A
`reviewed_company_policy` requirement is not accepted because this increment has no independent
source-policy review mechanism. Free-text completeness requirements are reported as unevaluated
rather than treated as verified.

Analysis snapshots pin the immutable metric-definition snapshot/hash, immutable Company
configuration snapshot/hash, period/dimensions, calculation inputs, source row lineage, saved-run or
dependency references, and calculation-engine version. Every saved analysis has input/result hashes
and a deterministic replay path. Evidence pages contain typed references to Universal Data rows,
current source-verified semantic relationship versions, saved M5 runs, verified Core Financials
runs, and exact saved dependency analyses; they do not copy full run payloads into the evidence page.

## Authorization and persistence

- Reads and Company registry/configuration access use the existing Company Context read permission;
  metric-definition and configuration writes use Company Context write permission. Analysis creation
  uses Company Context read permission, matching the existing saved M5-run generation contract; it
  creates only an immutable analysis snapshot and does not mutate source records.
- Finance-domain definitions, Universal Data source-amount sums, and finance-sensitive analyses
  additionally require `finance:read`; formula analyses inherit this scope from finance-sensitive
  dependencies. Registry/catalog listings hide finance-sensitive definitions and counts from callers
  without that permission, while direct reads require it. Listing analyses likewise hides
  finance-scoped rows and counts. Selecting a Core Financials run requires both `finance:read` and
  the existing verifier's stricter `finance:write` permission. The current owner/admin role matrix grants both
  together; BI does not weaken the verifier boundary for any future read-only finance role.
- Core Financials remains controlled by its existing feature flag and hard production block.
- Tenant/company access is resolved from authenticated membership; clients cannot choose a Company.
  New tables use forced Company-context RLS, same-scope composite foreign keys, restricted runtime
  grants, immutable analysis/configuration history, and lifecycle-only definition updates.
- Migration `0021_bi_registry` adds Company-scoped versioned metric definitions.
  Migration `0022_bi_config_analysis` adds published Company
  configurations, exact source precedence/profile pins, and immutable analysis runs. Downgrades
  refuse to discard retained metric definitions, configuration, or analysis evidence.

## Verification boundary

Local verification (2026-11-21): the database-independent suite passes (**228 passed** with
`pytest --noconftest -q tests/unit`); Ruff lint passes and format check reports **228 files** already
formatted; mypy reports no issues in **150 source files**; Python compileall and `git diff --check`
pass. The full suite collects (**384 tests**) using syntactically valid placeholder PostgreSQL URLs;
collection is not execution. The Business Intelligence OpenAPI surface registers **12 paths / 15
operations**. Offline `alembic upgrade head --sql` renders through
`0022_bi_config_analysis` (**5,446 lines**), which checks migration
generation only. This workspace has no configured PostgreSQL test URLs or local PostgreSQL server,
so live migration, database/API integration, RLS/grant/trigger, and schema-drift checks must run in the
required PostgreSQL 18.7 candidate CI. The first pushed candidate (run **45878630379**) passed
formatting/lint, mypy, schema upgrade, and drift checks but failed five BI integration tests. Review
identified nullable formula JSONB fields being encoded as JSON `null` and a test mapping that omitted
its required decimal separator; both are corrected and regression-covered where possible. A new
PostgreSQL 18.7 candidate run is required and remains pending; `alembic upgrade head`, `alembic check`,
and full `pytest` must pass before any PR is opened. No PR or merge is present. Software checks do not
approve source authority, finance policy, production use, or customer-data authorization.
