# M11 — Calculation & Verification Core

## Status and scope

M11 adds a small, pure-Python Decimal calculation and verification package at
`src/businessflow/calculation_core/`. It is not an API, policy catalog, database
feature, forecast model, or approval service. No financial formula is registered,
activated, or connected to M8. The only executable formulas are generic binary
arithmetic operations supplied explicitly by a caller; the tests use synthetic
vectors, not business rules.

No migration, model, M10 implementation, API route, or runtime dependency was
changed. The core does not read or write a database. Tenant/company identifiers
are embedded in input snapshots and source references are rejected if their scope
does not match. Any future persistence must use BFS's existing tenant access,
RLS/forced-RLS policies, and database role/privilege model; this slice does not
replace those controls.

## Architecture and contracts

The core imports BFS's existing canonical `content_hash` implementation from
`businessflow.metrics.snapshot`; it does not import OpenFPA or add a dependency.

| Module | Responsibility |
| --- | --- |
| `calculation_core/decimal.py` | Bounded plain-decimal parsing, ISO 4217 exponent lookup, explicit quantization and rendering. |
| `calculation_core/contracts.py` | Frozen, strict, extra-forbidden version-1 source, observation, assumption, snapshot, formula, policy-binding, output and trace contracts. |
| `calculation_core/engine.py` | One deterministic binary operation (`add`, `subtract`, `multiply`, or `divide`), output rounding and abstention trace construction. |
| `calculation_core/verification.py` | Exact Decimal reconciliation, independent expected-result checking and replay/hash checking. |
| `calculation_core/__init__.py` | Public exports. |

Contract rules:

- Input values are strings matching a plain base-10 grammar, with at most 64
  characters. Numeric JSON/Python floats, `Decimal` objects passed as input,
  exponent notation, NaN/infinity, surrounding whitespace, leading-zero forms,
  and unbounded values are rejected. There is no conversion through `float`.
  Original input text is retained in the snapshot.
- An observation must include a source reference: tenant and company, source ID,
  source/file hash, record locator, and optional record hash, dataset type and
  mapping version. Each source is scope-checked against the snapshot. The core
  preserves and hashes these references but does not retrieve source bytes or
  authenticate that a caller-supplied source hash is genuine. Assumptions carry
  an explicit version, rationale, user attribution and timezone-aware
  timestamp; an external source reference is optional because the attestation
  itself may be the evidence.
- Formula definitions carry a formula ID/version, operation, named input keys
  and kinds, output key/label, and an explicit rounding policy. Input, assumption,
  output, formula, source, snapshot and trace schemas are versioned. Inputs and
  assumptions are sorted by key before snapshot hashing and trace construction,
  so list order alone cannot change the result.
- Calculation requires a formula explicitly bound to an approved policy. An
  absent binding or pending binding yields an `abstained` trace with no output.
  An approved binding must identify the approver, timestamp and approval
  reference and bind to the exact formula contract hash. The binding is a
  caller-supplied assertion and does **not** prove that the named approver is
  authorized or that the policy is in force. Production callers must authorize
  the binding through an authorized policy catalog before supplying it. No such
  catalog is added here, and M8 continues to report `no_approved_policy` with an
  empty formula catalog.
- A required observation/assumption that is absent, a wrong input kind, or an
  absent/pending policy approval yields an `abstained` trace with reason codes
  and **no output**. Invalid contracts, unsupported currency arithmetic,
  division by zero, and non-terminating division without an output-rounding rule
  raise an explicit calculation error; they are not defaulted to zero.
- The trace includes the full versioned input snapshot and source references,
  assumptions and attribution, formula plus formula/contract hashes, input hash,
  engine version, tenant/company scope, rounding details, output or abstention,
  and a deterministic trace hash. The caller supplies IDs and timestamps; the
  core does not read the clock or create random run identifiers.

## Decimal, rounding and currency behavior

- Calculation operands are base-10 `Decimal` values created only from validated
  text. Each formula executes one operation using a fresh Decimal context with
  explicit precision, rounding, exponent bounds and traps, independent of the
  caller's ambient settings. With the 64-character input bound, add/subtract/
  multiply remain exact. The 256-digit context also leaves guard precision for
  the largest bounded input ratio before rounding to the maximum scale of 18.
  Division records whether its intermediate was inexact; an inexact division
  without a final rounding target raises `CalculationPrecisionError` rather
  than presenting an unqualified result.
- `RoundingPolicy` must name a target (`none`, `fixed_scale`, or
  `currency_minor_unit`) and a mode (`half_even`, `half_up`, `half_down`,
  `down`, `up`, `floor`, `ceiling`, or `05up`). `fixed_scale` requires a scale
  from 0 to 18; the public Decimal helpers enforce that same bound. Canonical
  decimal rendering is capped at 512 characters and rejects oversized values
  before allocating their fixed-point representation. Currency rounding uses the
  existing `iso4217` package's minor-unit exponent (e.g. USD/EGP 2, JPY 0,
  KWD 3). A currency output may use an explicit `fixed_scale` when its formula
  contract requires it. There is no implicit two-decimal rule and no rounding
  on input.
- Output quantization uses a 256-digit context and the same named mode. The trace
  records the intermediate precision, rounding precision/mode/target, currency
  exponent or fixed scale, whether quantization occurred, whether the numeric
  value changed, the unrounded value and the output text. Currency-rounded
  outputs retain the declared minor-unit scale. Rounded negative zero is rendered
  as positive zero; original input text remains unchanged in the snapshot.
- Add/subtract require both inputs to have the same currency (or both be
  currency-free). Multiply rejects two currency-bearing operands. Divide rejects
  cross-currency operands and a scalar divided by a currency-bearing value;
  dividing like currencies produces a currency-free ratio. No FX conversion or
  implied exchange rate exists. The core is not a dimensional-units engine.
- Reconciliation has its own explicit 256-digit Decimal context. Callers must
  supply absolute and relative tolerances; the effective tolerance is
  `max(absolute_tolerance, abs(expected) * relative_tolerance)`. This uses an
  absolute tolerance for zero expected values, does not divide by zero, and does
  not round the variance or tolerance.

## Verification capabilities

- Golden test: exact output, input-snapshot hash and trace hash for a synthetic
  source-plus-assumption calculation.
- Numeric/invariant tests: maximum bounded multiplication, input-order
  independence, exact terminating division, explicit inexact-division behavior,
  currency exponent and rounding vectors, currency mismatch rejection and
  policy-gate behavior.
- Missing/invalid-evidence tests: floats, invalid decimal syntax/currencies,
  missing input, wrong input kind, missing/pending approval and cross-tenant
  source references.
- Reconciliation: exact variance and explicit absolute/relative thresholds,
  including zero expected values.
- Independent expected result: the expected value must be in the trace's
  tenant/company scope and its source hash, record hash, and source-ID/locator
  pair must not overlap input evidence. Currency mismatch, overlapping evidence,
  or an abstained/tampered trace is `not_verifiable`, never a numeric pass. This
  check does not authenticate the external source or prove organizational
  independence; callers must verify those facts.
- Replay: recomputes from the same formula and snapshot, checks the recorded
  trace's self-hash, formula hash, input hash, engine version and replayed trace
  hash. It detects stale self-hashes and a self-consistently re-hashed altered
  output that no longer follows the formula. A fully altered snapshot with a
  correspondingly recomputed output and hashes is a valid new replay; detecting
  replacement requires comparison with a trusted, externally stored trace hash
  and authentic source evidence, neither of which this pure package provides.

These are software verification checks, not evidence that a business formula is
correct or financially accurate.

## Targeted OpenFPA audit

**Audited source:** `JeffBrines/openfpa`, commit
`ddac4e90edc7b93392d94f33c8f579100fe3f7e7` (shallow-clone HEAD at audit time).
Its `LICENSE` is MIT, copyright Guiderail 2026. Its `pyproject.toml` declares
runtime dependencies `pandas>=2.0`, `pydantic>=2.5`, `pyyaml>=6.0`, and
`openpyxl>=3.1`; `formulas>=1.2` is development-only. OpenFPA source/tests were
inspected, not run in this BFS environment. No OpenFPA code was copied, no
OpenFPA dependency was added, and no license file is needed for this
reimplementation.

### 1. Financial modeling primitives

- **Files/functions/classes:** `pyfpa/config/schemas.py` (`Channel`,
  `OpexLine`, `DebtInstrument`, `WorkingCapitalConfig`, `OpeningBalances`,
  `EntityConfig`); `pyfpa/models/revenue.py:revenue_from_config`,
  `cogs.py:cogs_from_config`, `opex.py:opex_from_config`,
  `debt.py:debt_from_config`, `working_capital.py:working_capital_from_config`,
  and `cashflow.py:_tax_series, cashflow_from_config`.
- **Behavior:** validated YAML config is composed into monthly revenue, COGS,
  operating expenses, debt, working capital, P&L and cash-flow data frames.
  Formulas are embedded in the Python functions; the config uses float-valued
  fields.
- **Tests/dependencies:** `tests/test_revenue.py`, `test_cogs.py`, `test_opex.py`,
  `test_debt.py`, `test_working_capital.py`, `test_cashflow.py`,
  `test_cashflow_fcf.py`; uses pandas and Pydantic. Tests verify selected
  examples/golden totals but use float rounding/approximation.
- **Precision/correctness and BFS fit:** binary float arithmetic, no formula
  approval/version catalog, no currency contract, no evidence lineage and
  company-specific assumptions make direct reuse unsafe. Some behavior is an
  explicit modeling choice (e.g. 30 days/month; opening NOL is consumed but
  current-period losses do not create new NOL), not a universal BFS policy.
- **Recommendation:** **reimplement** numeric/contracts in BFS; **adapt** the
  separation of inputs and calculation layers as an architecture idea; **reject**
  the sample business formulas as BFS policy.

### 2. Decimal/float precision, currency and rounding

- **Files/functions:** the model and config files above use `float`; notably
  `pyfpa/io/pl_csv.py:_parse_amount` converts to `float` and maps blank/`-` to
  `0.0`. `pyfpa/analysis/reconcile.py:reconcile` converts both sides to float,
  defaults a missing modeled line to zero, and defines a zero-actual percentage
  variance as zero. `pyfpa/excel/verify.py:verify_workbook` also compares floats
  using a hybrid tolerance.
- **Tests/dependencies:** `tests/test_reconcile.py` explicitly tests missing-model
  default-to-zero and zero actual; finance model tests round to six decimal
  places or use float tolerances. pandas/Pydantic are runtime dependencies;
  `formulas` is a dev extra for Excel verification.
- **Precision/correctness and BFS fit:** no Decimal handling or monetary rounding
  policy was found in the audited calculation paths; `SourceRecord.currency` in
  `pyfpa/memory/lineage.py` only uppercases a three-character string. OpenFPA's
  scale/float behavior must not be transferred to M8, which preserves exact
  decimal text and separate ISO currencies.
- **Recommendation:** **reject** the numeric implementations/defaults;
  **reimplement** strict decimal text, explicit rounding modes and currency
  exponents in BFS. M11 does not alter M5's existing `decimal_text()` behavior.

### 3. Reconciliation, mappings and lineage

- **Files/functions/classes:** `pyfpa/memory/lineage.py` (`SourceRecord`,
  `SourceRegistry`, `MappingRule`, `MappingRegistry`, `register_mapping`,
  `reconcile_account_table`) plus `pyfpa/analysis/reconcile.py:reconcile` and
  `pyfpa/io/pl_csv.py:_parse_amount`.
- **Behavior:** local YAML registries map exact source labels to targets; source
  profiling reports blanks/duplicates; account reconciliation reports duplicate,
  unmapped and ignored labels plus mapped totals and expected variances.
- **Tests/dependencies:** `tests/test_memory_lineage.py` covers registry
  round-trip, duplicate rules, ignored rationale, duplicate/unmapped accounts
  and a variance; `tests/test_reconcile.py` covers float comparisons. Uses
  Pydantic, PyYAML, pandas and CSV helpers.
- **Precision/correctness and BFS fit:** mappings are not tenant-scoped or
  persisted behind BFS RLS in this reference. Totals use float; omitted expected
  or mapped targets become zero, and with no expected dict the variance check is
  treated as passed (although duplicate/unmapped checks still apply). Its
  useful ideas are explicit source-to-target rules and reporting unmapped rows;
  BFS M5/M10 already carry richer import/file/row hashes and mapping versions.
- **Recommendation:** **adapt** explicit mapping identity, versions and
  unmapped-evidence reporting where a BFS-owned mapping feature is approved;
  **reimplement** reconciliation with Decimal and missing-value abstention;
  **reject** file/YAML persistence and float aggregation for tenant data.

### 4. Formula/assumption organization and approval

- **Files/functions/classes:** `pyfpa/config/loader.py:load_config` and
  `config/schemas.py:EntityConfig`; `pyfpa/backtest/snapshot.py:Snapshot,
  snapshot_forecast, save_snapshot, load_snapshot`; `pyfpa/research/objective.py:
  ResearchObjective`; `research/epochs.py:ResearchEpoch, evaluate_challenger`;
  `research/registry.py:ModelRegistry, promote_challenger`.
- **Behavior:** model assumptions and predictions are serialized to YAML;
  research epochs store training/holdout labels, checks, metrics and promotion
  eligibility; registry promotion records a human approver. With an objective,
  promotion recomputes part of evaluation. Without it,
  `promote_challenger` trusts stored `promotion_eligible`.
- **Tests/dependencies:** `tests/test_backtest_snapshot.py`,
  `test_research_objective.py`, `test_research_epochs.py`,
  `test_research_registry.py` cover round-trips, required checks and promotion
  gates. Uses Pydantic, PyYAML and float-valued models.
- **Precision/correctness and BFS fit:** snapshot files are mutable local YAML,
  not immutable tenant-scoped, hash-bound BFS evidence; evaluated scores/metrics
  are floats. The explicit version/check/approval concepts are useful, but
  approval is not tied to a BFS-authorized policy service in this code.
- **Recommendation:** **adapt** versioned contract, explicit assumptions,
  hard-check and approval-binding concepts; **reimplement** as immutable,
  source-bound BFS contracts; **reject** local file storage and the assumption
  that a stored eligible flag is itself authorization.

### 5. Model verification and forecast evaluation

- **Files/functions:** `pyfpa/excel/model_workbook.py:model_to_excel,
  _build_assumptions, _build_model`; `pyfpa/excel/verify.py:VerifyReport,
  verify_workbook`; `pyfpa/backtest/score.py:extract_lines,
  aggregate_periods, score_forecast`; `pyfpa/backtest/holdout.py:holdout_backtest`.
- **Behavior:** a config is compiled into Excel formulas and optionally evaluated
  with the `formulas` dev dependency against the pandas engine; backtesting
  aggregates flow/stock/ratio fields, scores weighted MAPE and uses a caller
  callback to fit before a final holdout.
- **Tests/dependencies:** `tests/test_excel_verify.py`,
  `test_excel_equivalence.py`, `test_excel_model_structure.py`,
  `test_backtest_score.py`, `test_backtest_holdout.py` and
  `test_backtest_snapshot.py`. Dependencies: openpyxl and pandas at runtime;
  `formulas` is optional/dev-only.
- **Precision/correctness and BFS fit:** the verifier's independent evaluator and
  fail-on-Excel-error behavior are good verification patterns, but comparison is
  float/tolerance-based and not a separate independent financial oracle. Scoring
  excludes zero actuals, silently omits non-matching lines, and period aggregation
  defaults absent fields to zero; the holdout uses mapping insertion order rather
  than validating date chronology. The scoring objective is business policy,
  not an approved BFS formula.
- **Recommendation:** **adapt** independent implementation, hard failure on
  unevaluated outputs, holdout separation and replay checks; **reimplement**
  verification with exact Decimal/evidence contracts; **reject** workbook/formula
  runtime dependencies and OpenFPA scoring/forecast rules as BFS policy.

## BFS compatibility and decisions

- **M5:** `metrics/types.py` already carries `Decimal`; `metrics/calculation.py`
  calculates observational workforce metrics; `metrics/snapshot.py` canonicalizes
  and hashes JSON. `decimal_text()` quantizes to `0.01` without passing a rounding
  argument, so it relies on the active Decimal context rather than a formula-level
  rounding contract. M5's catalog remains `implementation_draft` and
  observational-only. This M11 slice does not change M5.
- **M8:** `financial_assessments/schemas.py` accepts exact decimal strings and
  ISO 4217 codes; `financial_assessments/catalog.py` deliberately has no approved
  policy/formulas; `calculation.py:evaluate_financial_input_snapshot` preserves
  directly supported observations, identifies missing evidence and emits
  `incomplete`, with no financial metric. M11 does not activate or change M8
  formulas/policy.
- **M10:** `position_reviews/service.py:_validate_financial_rows` preserves
  source-native values, currency, file/row hashes and validation; M10
  `calculation.py:_financial_observations` explicitly abstains from financial
  comparison. M10 remains synthetic-only, incomplete and unchanged.
- **No database migration** was appropriate: the new package is not wired to
  storage or API. Existing M5/M8/M10 tenant isolation, RLS and DB privileges are
  not relaxed. The existing pull-request CI job runs its PostgreSQL 18 integration
  and RLS tests against a disposable service database; those checks pass for M11.

## Deferred capabilities and limits

No formula catalog or administration workflow; no policy/approver authorization;
no persistence/API integration; no multi-step formula language; no dimensional
unit algebra; no FX rates or currency conversion; no forecasts, backtests, model
selection, optimization, AI agents, recommendations or external actions; no
production authorization. The generic operator contract is not a substitute for
an accountant- or company-approved formula. Software tests establish only
contract behavior and determinism for test vectors. They do **not** establish
financial accuracy, accounting treatment, policy approval or production readiness.
