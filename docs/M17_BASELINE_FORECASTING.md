# M17 — Baseline Forecasting and Model Evaluation

**Status:** bounded experimental prototype; not a production forecasting capability, business-policy approval, accounting conclusion, or customer-validated model.

M17 adds a small deterministic forecasting engine for explicitly supplied time-series data. It is intentionally separate from M14–M16 finance-source workflows: no bank, ledger, M14 intake, or M16 cash-schedule integration is performed. The service does not authenticate the business meaning, completeness, freshness, authority, or customer-data permission of submitted observations.

## Safety and scope

- `FORECASTING_ENABLED=false` and `FORECAST_PROMOTION_ENABLED=false` by default. Forecasting and promotion remain blocked whenever `APP_ENV=production`, even if an operator sets either flag to true.
- Enabling a flag is only a code-path switch for an isolated, authorized non-production test deployment. It does not authorize customer data, production finance data, or production activation.
- Dataset origins and per-record references are caller assertions. Hashes bind the submitted snapshot; they do not authenticate the cited source or prove its authority.
- A `synthetic_fixture` is clearly labeled and is not customer evidence, customer validation, or accuracy evidence. Synthetic holdout results cannot be approved.
- Evaluation comparison uses the same complete-case population for each model. The engine never automatically ranks, selects, recommends, deploys, or authorizes a model.
- Promotion records are immutable human decisions limited to `nonproduction_only`. An authorized reviewer must differ from the dataset, protocol, evaluation, and model authors. `production_activation_status` is permanently recorded as `blocked_pending_governance`.
- Validation-derived intervals use a nearest-rank empirical absolute-error width. Their output explicitly states that time-series coverage guarantees are not established.
- Missing values are not imputed or converted to zero. Missing and unobserved periods are reported; sparse models abstain when their input requirements are not met.
- Forecast input and output decimals use plain base-10 text and Python `Decimal`, with technical rendering at 18 fractional places using `ROUND_HALF_EVEN`. This is not currency rounding or FX conversion.

## Bounded contracts

Datasets are immutable, company-scoped versions. Each observation binds a calendar period, a declared dimension tuple, a decimal-text value or explicit missing reason, and a caller-supplied record reference. Daily, Monday-start weekly, and first-of-month monthly frequencies are supported. Dataset keys, model keys, and protocol keys use optimistic `expected_version` checks when a later version is created.

The prototype enforces these ceilings:

| Resource | Limit |
| --- | ---: |
| Observations per dataset version | 20,000 |
| Dimensions per dataset | 8 |
| Timeline periods assessed | 10,000 |
| Dense timeline-by-series panel cells | 100,000 |
| Models per evaluation | 8 |
| Evaluation points | 20,000 |
| Validation rolling-origin blocks | 256 |
| Future forecast points | 20,000 |
| Future horizon per protocol | 366 periods |
| Dataset HTTP request body | 32 MiB |

Technical dataset eligibility is returned separately from business approval. Over-budget and zero-observed-value datasets are blocked from calculation. Incomplete panels and unverified source claims are reported as warnings; an eligibility result never means a dataset is trusted or company-approved.

## Deterministic models and evaluation

Only three declarative baselines are implemented; arbitrary code, external libraries, learned parameters, automated training, and AI/ML are not supported:

- `naive_last_value` — repeats the latest observed value, abstaining if the latest training period is missing.
- `seasonal_naive` — repeats the value one configured number of periods earlier; unavailable lags produce an explicit abstention.
- `moving_average` — averages the configured number of immediately preceding periods; incomplete windows produce an explicit abstention.

Protocols are immutable and bind the target, frequency, horizon, chronological split lengths, requested metrics, missing-actual policy, optional flag-only Tukey-IQR treatment, and optional validation-calibrated interval configuration. Supported metrics are MAE, RMSE, MAPE, and sMAPE. MAPE is undefined when any scored actual is zero; sMAPE is undefined when an actual and its forecast are both zero. Undefined metrics are not zero-filled.

Validation uses chronological expanding-window rolling origins. Each validation block is forecast from history strictly before its origin; no future actual is used to make that block's predictions. The holdout uses one fixed origin, forecasts the whole holdout without training or calibration on holdout actuals, and is consumed at most once per dataset version. A complete validation evaluation is required before it can supply holdout calibration. An incomplete or failed holdout still consumes the one-time holdout; create a new dataset version to conduct a fresh holdout.

When `missing_actual_policy=exclude_and_report`, missing actuals are excluded from the common scoring sample and reported; an incomplete result is marked `partial` or `not_evaluable`, not eligible for promotion. When `block_evaluation_if_missing_actuals` is selected, any missing/unobserved actual in the evaluation window blocks scoring. Outlier handling is opt-in and flag-only; flagged observations remain in the sample and are never silently removed.

## API

Routes are under `/api/v1/tenants/{tenant_id}/forecasts`. Company scope is resolved from authenticated membership; the client cannot choose a different company ID in the body. Existing owner/admin roles receive the forecast permissions. Other roles do not receive forecast read/write/review access by default.

| Method and path | Permission | Purpose |
| --- | --- | --- |
| `GET /catalog` | `forecast:read` | Read supported capabilities, limits, defaults, and governance status. |
| `GET /datasets` | `forecast:read` | List company-scoped dataset versions. |
| `POST /datasets` | `forecast:write` | Create an immutable dataset version and technical eligibility report. |
| `GET /datasets/{dataset_version_id}` | `forecast:read` | Read and integrity-check a dataset snapshot and its eligibility. |
| `GET /models` / `GET /models/{model_version_id}` | `forecast:read` | List or read immutable model definitions. |
| `POST /models` | `forecast:write` | Create a version of a supported declarative baseline. |
| `GET /protocols` / `GET /protocols/{protocol_version_id}` | `forecast:read` | List or read immutable evaluation protocols. |
| `POST /protocols` | `forecast:write` | Create a versioned evaluation protocol. |
| `GET /evaluations` / `GET /evaluations/{evaluation_id}` | `forecast:read` | List or read saved evaluation evidence. |
| `POST /evaluations` | `forecast:write` | Create validation or one-time holdout evidence. |
| `POST /evaluations/{evaluation_id}/verify` | `forecast:write` | Replay the saved evaluation and compare canonical hashes. |
| `GET /runs` / `GET /runs/{run_id}` | `forecast:read` | List or read immutable future forecast runs. |
| `POST /runs` | `forecast:write` | Create a deterministic forecast from exact dataset/model/protocol versions. |
| `POST /runs/{run_id}/verify` | `forecast:write` | Replay a saved run against its original inputs. |
| `GET /promotions` | `forecast:read` | List integrity-checked human decisions. |
| `POST /promotions` | `forecast:review` | Record an immutable human decision on complete holdout evidence. |

The forecast dataset route has a 32 MiB ASGI request-body cap. Dataset, model, protocol, evaluation, run, and promotion endpoints are unavailable unless the corresponding default-off feature flags are explicitly enabled in non-production.

## Persistence and integrity

Migration `0026_forecasting_engine` creates seven append-only tables: `forecast_dataset_versions`, `forecast_model_versions`, `forecast_evaluation_protocol_versions`, `forecast_evaluations`, `forecast_evaluation_model_versions`, `forecast_runs`, and `forecast_promotion_decisions`. Composite foreign keys bind every record to the same tenant and company. Tables enforce Company-context RLS (including `FORCE ROW LEVEL SECURITY`) and append-only triggers. The runtime role receives `SELECT` and `INSERT`, not `UPDATE`, `DELETE`, or `TRUNCATE`. Evaluations, runs, and promotion decisions bind their inputs and outputs through SHA-256 content hashes; replay recomputes deterministic evidence rather than modifying history.

The hashes are integrity markers, not encryption, keyed signatures, or protection against a privileged database administrator. Snapshots retain submitted observations and derived forecasts. No M17 retention, legal-hold, customer-data authorization, or deletion workflow is implemented. Downgrade is intentionally refused while any M17 evidence remains; removing all rows and downgrading destroys the feature schema.

## Verification and remaining gates

The calculation and contract tests exercise deterministic validation/holdout behavior, missing-actual policies, source/timeline eligibility, forecast-point budgets, undefined percentage metrics, ISO date and UUID wire parsing, and feature-flag guards. The API integration test covers versioned dataset/model/protocol creation, evaluation and run replay, one-time holdout enforcement, separation-of-duties promotion, and all-seven-table RLS, runtime grants, and append-only behavior. The migration head assertion is `0026_forecasting_engine`.

Local validation passes all **259 pure unit tests** (including 10 forecasting calculation/contract tests), repository-wide Ruff format/lint, `mypy src/businessflow`, `compileall`, `git diff --check`, the standalone declared-size forecast-body test, `alembic heads` (`0026_forecasting_engine`), and offline `alembic upgrade head --sql` rendering. GitHub Actions [run 46034334027](https://github.com/DevMagdi/BFS/actions/runs/46034334027) passed on PostgreSQL 18.7 (`server_version_num=180007`), including a clean migration upgrade, `alembic check`, and the full database-backed suite with M17 API, tenant/RLS, runtime-grant, append-only, member-access, and existing migration-regression tests. The CI job used its disposable `businessflow_test` database and separate runtime, migrator, and bootstrap roles. This local workspace itself still has no PostgreSQL server or dedicated test URLs; database validation was performed in CI, not against a local or non-test database.

A passing code test, replay, content hash, synthetic result, caller-provided control, reviewer decision, or feature flag does not establish source authority, complete population, business-policy approval, forecast accuracy, independent validation, production readiness, or customer/production-data permission. Those remain separate governance decisions.
