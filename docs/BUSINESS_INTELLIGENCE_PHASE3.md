# Phase 3 Business Intelligence increment

This document covers the bounded, backend-only Business Intelligence API and its company-wide
Step 2 expansion on top of the Business Semantic Layer. It reuses Company Context, Universal Data,
M5, Core Financials, and existing authorization services. Step 2 adds source-scoped operational and
sales observations, required-field projections, period/dimension-aware readiness, explicit
customer/workforce authorization boundaries, and governance status metadata. It does not add another
ingestion path, infer identities, turn Universal Data amounts into finance facts, or replace any
existing calculation engine.

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
  the check; optional domain, paired period bounds, and repeated `dimensions` query parameters scope
  readiness to the requested analysis shape.
- `GET /analyses/{analysis_id}/readiness` reports integrity and result-state readiness for one exact
  saved analysis, including its pinned Company, period, governance blockers, and retention state.
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
serialized guard that also keeps seeded-demo workspaces read-only. Analysis creation requires Company
Context read permission plus the applicable `finance:read`, `workforce:read`, `universal_data:read`,
and customer-data governance checks. The raw-data permission does not substitute for customer-data
processing authorization. There is no Business Intelligence browser workspace in this increment.

## Bounded calculation and evidence contracts

The registry exposes five existing M5 metrics, six existing Core Financials measures, and bounded
Universal Data source observations for sales transactions, customers, business activities, and
operational records. M5 and Core Financials selections reference an exact existing saved run; BI
rejects an M5 version or Core Financials measure mismatch and does not recalculate capacity or
financial measures. M5 and Core Financials definition units must match the selected system metric,
and Universal Data counts/distinct values/source amounts use their declared canonical units. M5
snapshots are validated with the existing M5 replay service. A selected Core Financials run must pass
the existing verification service and retain its original policy, trace, currency, and period. M5's
structured overload and skill-inventory observations cannot be consumed as scalar formula
dependencies.

Company definitions use an allow-listed calculation schema:

- Universal Data record counts and non-identity distinct-text counts remain source-scoped.
- Universal Data amount sums preserve the source currency and explicitly choose a single-currency or
  by-currency result. These sums are source observations, never recognized revenue, expense, profit,
  cash, or Core Financials facts. No FX conversion, refund/reversal policy, or accounting recognition
  is introduced.
- Saved Universal Data inputs project only the fields required by the calculation, declared field
  requirements, filters/exclusions, selected dimensions, and applicable date bounds. Unrequested
  source attributes are omitted from the BI input snapshot while exact source-row lineage and hashes
  remain pinned.
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

Governance is explicit and fail-closed. The registry, readiness, analysis, replay, and evidence
responses carry unresolved source-authority, completeness, independent-control, finance-policy,
retention, and customer-processing status. These are not approvals. Customer-domain calculations,
customer entities/fields, customer grouping/filtering, and the `customer_has_sales_transaction`
relationship return a blocker or `customer_data_processing_authorization_required`; no user role is
treated as that approval. Customer retention/churn and revenue inferred from record volume remain
unsupported. Workforce-sensitive M5/People analyses, employee-level dimensions, and definitions
using employee-linked semantic relationships require `workforce:read`. The default owner/admin roles
have this permission; other roles cannot read employee-linked semantic catalog entries or saved
analyses. Their broad lists omit sensitive entries and their explicit sensitive requests fail closed.

Readiness requires paired, ordered inclusive period dates, unique dimensions, and no more than two
grouping dimensions. It reports unsupported dimension/date shapes as blockers and applies the same
period and dimensions when evaluating source evidence. M5 period checks require Monday–Sunday bounds;
analysis still binds the exact existing M5 run rather than calculating a new one.

Analysis snapshots pin the immutable metric-definition snapshot/hash, immutable Company
configuration snapshot/hash, period/dimensions, calculation inputs, source row lineage, saved-run or
dependency references, and calculation-engine version. Every saved analysis has input/result hashes
and a deterministic replay path. Evidence pages contain typed references to Universal Data rows,
current source-verified semantic relationship versions, saved M5 runs, verified Core Financials
runs, and exact saved dependency analyses; they do not copy full run payloads into the evidence page.

## Authorization and persistence

- Reads and Company registry access use the existing Company Context read permission; metric-definition
  and configuration writes use Company Context write permission. Configuration reads that include
  Universal Data source precedence, Universal Data-backed metric definitions, readiness, analyses,
  saved results, replay, and evidence require `universal_data:read`. This permission protects source
  observations and is granted to owner/admin roles by default; without it, lists omit Universal
  Data-backed entries and direct sensitive reads fail closed. The static calculation catalog remains
  data-free. Analysis creation uses Company Context read permission and creates only an immutable
  snapshot; it does not mutate source records. Workforce-sensitive definitions/runs/analyses
  additionally require `workforce:read`. The default owner/admin roles have it; member lists omit
  workforce-sensitive catalog and analysis entries, and direct sensitive reads fail closed. Company
  Context employee, skills, projects, assignments, workforce summaries, and M6 capacity-assessment
  evidence use the same permission boundary.
- Finance-domain definitions, Universal Data source-amount sums, and finance-sensitive analyses
  additionally require `finance:read`; Universal Data-backed metrics also require `universal_data:read`,
  so source-amount sums require both. Formula analyses inherit these scopes from pinned dependencies.
  Registry/catalog listings hide finance-sensitive definitions and counts from callers
  without that permission, while direct reads require it. Listing analyses likewise hides
  finance-scoped rows and counts. Selecting a Core Financials run requires both `finance:read` and
  the existing verifier's stricter `finance:write` permission. The current owner/admin role matrix
grants both together; BI does not weaken the verifier boundary for any future read-only finance role.
- Customer-data processing remains unapproved. Customer-domain analyses, customer entities/fields,
  customer dimensions/filters, and customer-linked semantic relationships are blocked with an
  explicit governance blocker. Customer approval is not inferred from Company roles, source profiles,
  record counts, or a metric definition. The registry names customer-retention/churn and
  revenue-from-record-volume calculations as unsupported.
- Core Financials remains controlled by its existing feature flag and hard production block.
- Tenant/company access is resolved from authenticated membership; clients cannot choose a Company.
  New tables use forced Company-context RLS, same-scope composite foreign keys, restricted runtime
  grants, immutable analysis/configuration history, and lifecycle-only definition updates.
- Migration `0021_bi_registry` adds Company-scoped versioned metric definitions.
  Migration `0022_bi_config_analysis` adds published Company configurations, exact source
  precedence/profile pins, and immutable analysis runs. Migration `0023_core_financial_guardrails`
  retains exact Core Financials upload bytes for new imports and binds immutable idempotency keys.
  Migration `0024_company_wide_intelligence` adds the project-manager relationship type and a
  Company-scoped `workforce_sensitive` analysis
  marker, backfilled from People/M5 definitions, pinned dependencies, and employee-linked
  relationships. It does not rewrite legacy immutable input snapshots; schema-v1 analysis snapshots
  remain verifiable. Downgrades refuse to discard retained semantic relationship or workforce
  analysis evidence.

## Verification boundary

Historical Phase 3 candidate verification snapshot (2026-11-21; it predates the current Step 2
migration): the database-independent suite passed (**228 passed** with
`pytest --noconftest -q tests/unit`); Ruff lint passed and format check reported **228 files** already
formatted; mypy reported no issues in **150 source files**; Python compileall and `git diff --check`
passed. The full suite collected (**384 tests**) using syntactically valid placeholder PostgreSQL
URLs; collection was not execution. The Business Intelligence OpenAPI surface registered **12 paths
/ 15 operations**. Offline `alembic upgrade head --sql` rendered through
`0022_bi_config_analysis` (**5,446 lines**), which checked migration generation only. These counts
and results are retained as a historical snapshot and are not current verification for migration
`0024` or the access-control changes described above.

The GitHub record for run **45879884607** reports a successful conclusion on branch
`arena/5a13e381-bfs`, commit `890e528af0100e5358a262a7f9588d7c76d994a0`. Its committed tree does not
contain `0024_company_wide_intelligence`; consequently, it is not evidence that the current `0024`
migration or this branch's workforce-authorization changes passed PostgreSQL CI. The absence of a
local PostgreSQL server means live migration, RLS/grant/trigger, and integration verification for
this Step 2 change set remains a separate gate. Software checks do not approve source authority,
finance policy, production use, or customer-data authorization.
