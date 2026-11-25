# Core Financials — shared six-measure workflow

**Status: experimental, default-disabled, incomplete, and not production-ready.** The persisted workflow can calculate one selected measure per run from version-bound ledger or bank evidence. This is management-reporting infrastructure, not an approved accounting policy, authoritative source, finance-owner approval, accounting advice, or authorization to process customer data.

## Scope and accounting boundaries

| Measure | Required evidence | Definition source | Important limits |
|---|---|---|---|
| Revenue | Complete general-ledger import | Versioned account map, normal balances, category coverage, and caller-supplied Revenue formula | Refunds follow the signed source entry and explicit account mapping. |
| Expenses | Complete general-ledger import | Versioned category/account mappings and caller-supplied Expenses formula | The selected categories and signs are not universal expense definitions. |
| Gross Profit | Complete general-ledger import | Versioned caller-supplied formula and coverage | No gross-margin presentation or inventory accounting is inferred. |
| Operating Profit | Complete general-ledger import | Versioned caller-supplied formula and coverage | “Operating” classification is caller policy, not inferred. |
| Net Profit | Complete general-ledger import | Versioned caller-supplied formula and coverage | Taxes, non-operating items, and other categories are not universally classified. |
| Cash Flow | Complete bank-activity import | Versioned movement, refund, reversal, adjustment, transfer, and balance-treatment rules | This is only net observed bank movement, not a cash-flow statement; no P&L rows, opening balance, or closing balance are inferred. |

All six measures are selected independently at `POST /runs`; one immutable run records one measure and its matching source adapter. P&L runs require `ledger_import_id`; Cash Flow requires `bank_import_id` and rejects a ledger import. The policy and import versions must match exactly. A run cannot synthesize Cash Flow from ledger transactions.

The default catalog is an editable illustration. It provides example account codes, normal balances, formulas, and explicit Cash Flow rules, but it marks every P&L category as `unknown`. Unknown or contradictory category coverage withholds the affected measure. A caller must create a new immutable policy version to assert coverage or revise the definitions. Even a `complete` classification is caller-declared and is not an accounting approval or independently validated chart of accounts. Do not treat illustrative formulas as GAAP/IFRS or universal management-reporting definitions.

## Signed facts, source quality, currency, and time

General-ledger debit/credit pairs are normalized to a signed canonical amount relative to the selected account's declared normal balance: normal-side postings are positive and opposite-side postings negative. Bank rows normalize `cash_in` as positive and `cash_out` as negative. Formula coefficients are explicit policy values; e.g. a selected contra-revenue account may be subtracted by its formula, while a refund posted directly to a revenue account follows the credit-normal signed entry. Neither category assignment nor formula semantics are inferred from account numbers.

Both source adapters require an explicit header mapping, source identity, bank account identity for bank rows, date, currency, and exactly one positive movement side. Supported events are `normal`, `refund`, `reversal`, and `adjustment`; `transfer` is accepted only for bank activity. Reversals must link to an existing accepted entry under the same policy version and match its account, currency, and exact opposite amount. A duplicate source record ID, rejected row, or failed reversal makes the import quality incomplete and blocks calculation. Adjustments are accepted only through the explicit selected mapping/rules. The run uses the inclusive transaction-date boundaries; the imported source period must fully cover the report period.

A numeric value is returned only when the source is caller-asserted `complete`, has no rejected/duplicate rows, covers the requested dates, and every in-window accepted row is in the selected reporting currency. The policy is `one_currency_no_fx`: mismatched in-window currency withholds the measure; there is no silent exclusion, exchange-rate lookup, conversion, or consolidation. Missing policy/evidence, incomplete coverage, or source mismatch returns `incomplete`/`missing_source` with reason codes rather than a fabricated amount. Source identity and completeness remain caller assertions and are not authenticated against a source system.

Cash Flow uses only accepted rows in a bank-activity import. Its immutable `cash_flow_policy` makes the movement rule, refund and adjustment treatment, exact reversal rule, and treatment of marked transfers explicit. The shipped policy example excludes rows marked as transfers; a separately versioned policy may include them as observed movements. The CSV contract has no opening/closing balance fields, so a policy must explicitly say that those balances are not reported and are not inferred. Marked transfers are caller-classified and not independently validated. This bounded output must not be represented as a cash-flow statement or cash balance.

## Persistence, lineage, replay, and tenant boundaries

The persisted workflow reuses the existing canonical import model and separate adapters, mappings, and immutable policy versions. Company-scoped append-only tables retain policy/mapping versions, imports, normalized records, runs, and line evidence. Runs hash the exact request, policy, source imports, accepted facts, result, and evidence; `POST /runs/{run_id}/verify` recomputes those values from the persisted immutable inputs. Responses expose mapping/policy hashes, import lineage, row hashes, result hashes, and a trace hash. The engine identifier is now `businessflow.core-financials.v3-shared-measures`; v2 Revenue-only snapshots retain their legacy version and remain verifiable for compatible Revenue runs.

All service lookups are scoped by tenant and Company and use the existing `finance:read` / `finance:write` Company authorization. Migration `0016_core_financials` creates the six forced-RLS tables, composite scope foreign keys, append-only triggers, and restricted `bf_runtime` grants. Migration `0017_core_financial_transfers` extends the accepted event constraint for explicitly marked bank transfers. The original uploaded CSV is not archived, but mapped values, normalized amounts/identifiers, source row hashes, run snapshots, and evidence are retained. Hashes are integrity references, not encryption or source authentication. No retention/deletion workflow, legal hold, key rotation, finance-owner approval, or production accounting controls are included.

## Integrated financial overview (M13)

`GET /overview` returns exactly six entries in catalog order. For each measure it selects the latest persisted run for that measure independently; this is not a synthesized six-measure calculation or an implicit common reporting period. A missing run is `missing`; a saved calculated value is `available`; an incomplete run is `incomplete`; a saved `missing_source` result is `withheld`. Values are never filled from another run. The response retains each run/evidence reference, exact and reported values, period/currency/basis, engine and policy versions/hashes, policy formulas/category coverage, source import identity and type, file/input/result/mapping/policy hashes, caller-asserted completeness, quality, evidence count, reconciliation result, reason codes, limitations, and assumptions.

The four P&L checks are deliberately policy-scoped: (1) Gross Profit against Revenue plus the category adjustments in the saved formula; (2) `Gross Profit = Revenue - Expenses` only when the three saved formula vectors establish that identity; (3) Operating Profit against Gross Profit plus its saved category adjustments; and (4) Net Profit against Operating Profit plus its saved category adjustments. `Expenses` is not presumed to mean cost of sales. Unsupported formula identities or unknown category coverage are `unverifiable`; missing/incomplete results are not imputed. Fully populated runs are compared only when source import, source type, reporting period, currency, basis, policy ID/version/hash, and calculation-engine version agree. Period/currency/source/version mismatches are `non_comparable`; no FX, cross-period arithmetic, or tolerance is applied. A consistent result means only that the saved illustrative formulas and compatible persisted runs agree; it is not accounting validation, an audit, or policy approval.

The overview reads the existing append-only runs, imports, policies, and evidence; it adds no persistence table or migration. It uses the same `finance:read` tenant/company access path and scoped queries as run history, so existing Company RLS and runtime grants remain unchanged. The authenticated UI renders six cards with in-app links to inspect each persisted run, source import, and paginated evidence.

## API and UI

All routes are under `/api/v1/tenants/{tenant_id}/core-financials`. The feature flag is checked before route work; it defaults to `false` and production is hard-blocked even if the flag is set.

| Method and path | Purpose |
|---|---|
| `GET /template` | Illustrative policy and canonical ledger/bank header contracts |
| `GET /policies` / `POST /policies` | List or append an immutable versioned, non-authoritative policy |
| `GET /mappings` / `POST /mappings` | List or append a source-adapter-specific mapping (ledger and bank remain separate) |
| `POST /imports` | Bounded UTF-8 CSV import for either general ledger or bank activity (`metadata_json`, `file`, `Idempotency-Key`) |
| `GET /imports` / `GET /imports/{import_id}` | List or recover an import and its original versions/hashes |
| `GET /imports/{import_id}/records` | Paginated normalized records and validation issues |
| `POST /runs` | Persist one selected measure; P&L requires ledger evidence, Cash Flow requires bank evidence |
| `GET /runs` / `GET /runs/{run_id}` | List or inspect persisted measure results and hashes |
| `GET /overview` | Return the latest persisted run for each of all six measures plus policy-supported consistency checks |
| `GET /runs/{run_id}/evidence` | Paginated measure-to-source-row contributions |
| `POST /runs/{run_id}/verify` | Replay persisted inputs, outputs, evidence, and trace |

`src/businessflow/web/core-financials.html` and `src/businessflow/web/assets/core-financials.js` provide a small authenticated workflow for editing/reviewing versioned policy JSON, selecting either source adapter, mapping/importing CSV, choosing a measure, inspecting results/evidence, recovering saved imports, and replay-verifying runs. The M13 overview appears above that workflow and keeps run periods/currencies separate while displaying status, source, policy, reconciliation, and evidence links for each measure. It is intentionally not a UI redesign. Use synthetic or explicitly authorized test data only.

## Verification

- Database-independent golden/unit coverage is in `tests/unit/test_core_financials.py`: independent cases for all six measures, cross-measure P&L identities, cash/profit divergence, exact signs/precision, source quality/period/currency gaps, explicit cash transfer rules, and policy/source abstention. M13's `tests/unit/test_core_financial_overview.py` exercises supported identities, incomplete and incompatible runs, inconsistent residuals, and a policy where Expenses is not a cost-of-sales bridge.
- PostgreSQL/API integration coverage is in `tests/test_core_financials.py`: Revenue recovery and replay regression, all-six-measure ledger/bank workflow and replay, evidence lineage, authorization/tenant isolation, Company RLS, runtime grants, and immutability. M13 adds overview status/link assertions and a `finance:read` authorization/tenant-scope check. Migration head remains `0017_core_financial_transfers` because the overview derives from existing persisted data. The complete PostgreSQL suite passed locally on a fresh disposable 18.6 cluster (**273 passed**) before M13, and PR #22's Backend workflow passed with PostgreSQL 18.7, migration upgrade/model-drift, and the full integration suite ([run 45573830944](https://github.com/DevMagdi/BFS/actions/runs/45573830944)).
- Static route/UI assertions are in `tests/test_position_review_web.py`.
- Browser-driven E2E has **not** been run for this milestone; static served-UI tests do not count as Browser E2E.
- PostgreSQL 18.7 migration/API/RLS integration was verified by PR #22's Backend checks ([run 45573830944](https://github.com/DevMagdi/BFS/actions/runs/45573830944)); local unit-only tests or offline migration generation alone are not substitutes for that evidence.

The disposable Compose test profile is destructive to its selected test database. Use only a fresh project and volume as described in the repository's backend testing documentation; never point it at a shared or non-disposable database.
