# M13 — Bounded Metric Comparisons

## Status and scope

M13 implements a versioned, read-only comparison over three existing M5 metrics, using M5 run snapshots and M12-backed evidence for the selected M4 inputs. It adds no new business metric, KPI catalog, finance calculation, target, threshold, causal claim, ranking, forecast, recommendation, or canonical-data write. It does not reinterpret an increase or decrease as good or bad.

This is a bounded engineering contract for descriptive arithmetic. The API catalog intentionally leaves `owner_approved_question.status` as `unavailable`: the implementation does not claim a separate owner approval or supply an owner-approved business interpretation. The explicit comparison behavior below is not business-policy approval.

## Supported M5 metrics

M13 reuses and hash-checks the exact selected M5 metric definition. It supports only:

- `available_capacity_hours`;
- `recorded_committed_hours`; and
- `capacity_minus_recorded_commitments_hours`.

The latter remains an arithmetic difference between capacity and recorded assignment commitments, not evidence of free capacity, actual work, utilization, or assignment completeness. M5's overload metric is not aggregated or compared without an approved aggregation rule. M5's current skill-inventory snapshot is not treated as a time series.

## Comparison contract

### Periods and completion

- M5 periods consist of inclusive Monday–Sunday weeks.
- Baseline and comparison runs must cover the same number of weeks; the comparison must start on the Monday immediately after the baseline ends.
- At comparison creation, both period end dates must be before the Company-local current date.
- Each selected M5 run must have been created at or after local midnight following its own period end. A run made during an open period is not accepted as a completed-period snapshot.
- The Company timezone used for each M5 run must match across both runs and the Company's current timezone. Invalid timezone data or incompatible scope fails safely; it does not result in guessed period boundaries.
- The completion check, timezone, as-of date, and selected run boundaries are retained in the hashed comparison input and result.

### Metric and data-scope compatibility

- Both M5 runs must pass content-hash checks and deterministic recalculation under the supported M5 catalog and calculation versions.
- The selected metric's exact M5 definition snapshots must match.
- Tenant, Company, and Company-timezone scope must match. Cross-tenant/company runs are not selectable as one comparison.
- For each metric, M13 applies the same declared M4 input groups and exact M12/M4 contract versions to both runs. It verifies M5 row lineage against retained M4 import/row/link hashes and M12 ingestion evidence.
- A source import must have succeeded, have `quality_status=passed`, match its required contract, and be fresh at the pinned evidence-evaluation time. Missing or conflicting lineage, incompatible contract, failed/warning quality, stale/unknown freshness, or missing required input evidence causes abstention rather than inferred values.
- M12 `source_as_of_at` is user-reported evidence; it is not independently authenticated. The result preserves freshness, quality, lineage, and evidence limitations.

### Values and percentage change

Each matched week retains its baseline value, comparison value, basis, neutral direction, and arithmetic delta. The delta is `comparison_value - baseline_value`; it is not rounded again beyond the underlying M5 Decimal values.

Percentage change is calculated as:

```text
((comparison_value - baseline_value) / baseline_value) * 100
```

It is emitted only when both numeric values are present and non-conflicting, their metric bases match, and the baseline is nonzero. Estimated values may be compared only when their bases match and remain labeled estimated; mixed capacity bases are not differenced. A negative nonzero baseline uses the signed denominator exactly as written; the result is arithmetic only and has no evaluative meaning.

The percentage is a Decimal value rounded to two decimal places with `ROUND_HALF_EVEN` and serialized as a decimal string; its unit is percent. A zero baseline produces `percentage_change: null`, status `unavailable`, and reason `zero_baseline_percent_change_undefined`, while a valid absolute delta may still be shown. Missing, conflicting, or incompatible values also produce a null percentage with an explicit reason and retain the existing metric/evidence abstentions. Missing numeric values are never converted to zero.

“Increased”, “decreased”, and “unchanged” are neutral arithmetic labels. They do not state whether a result is desirable. The catalog and result disclose that no owner-approved interpretation is configured; no causation, target, threshold, ranking, forecast, or recommendation is produced.

## API

All routes use `/api/v1/tenants/{tenant_id}/metrics`. The catalog requires `company_context:read`; comparison creation, saved-result reads, and replay additionally require both `company_context:read` and `workforce:read` because they use or expose M5 employee-capacity/assignment evidence. Comparison creation stores an immutable observation and does not modify M4/M5 source facts.

| Method and path | Purpose |
| --- | --- |
| `GET /insights/catalog` | Read versioned supported comparisons, exact source contracts, percentage-change rules, period convention, neutral direction semantics, and limitations. |
| `POST /insights/runs` | Create an immutable comparison from distinct `baseline_metric_run_id`, `comparison_metric_run_id`, and supported `metric_id`. |
| `GET /insights/runs/{insight_run_id}` | Read a same-Company saved result and its retained snapshots/hashes. |
| `GET /insights/runs/{insight_run_id}/replay` | Revalidate selected M5 runs and deterministically replay the saved result from its retained evidence inputs. Replay preserves the evidence/freshness state evaluated at creation; it does not refresh M12 freshness. |

Incomplete periods and incompatible period/scope requests fail with a safe 4xx response rather than creating a comparison. Hash, version, lineage, evidence, and replay mismatches fail closed. The catalog and run result carry their own version identifiers: catalog `businessflow.metric-insight-catalog.v2`, engine `businessflow.metric-insight.v2`, comparison definition `businessflow.metric-comparison.completed-adjacent-weekly.v2`, and snapshot schema version `2`.

A result reports `available`, `partial`, or `abstained`; includes per-week value status and abstention reasons; and keeps interpretation unavailable. When shared required source evidence is not verified, no comparison values are emitted. Every persisted run has content hashes over its input and output snapshots for integrity checks and replay.

## Persistence and security

Migration `0010_metric_insight_runs` adds append-only `metric_insight_runs` with tenant/company scope, requester, baseline/comparison M5 foreign keys, metric/version identifiers, availability state, immutable JSON snapshots, and SHA-256 hashes. Composite foreign keys bind both M5 runs to the same tenant and Company. Forced RLS requires the transaction's verified tenant and company context. The runtime role has `SELECT`/`INSERT` only; database triggers reject updates and deletes. API authorization, company scoping, append-only storage, and hash/replay checks preserve the existing M5/M12 safeguards.

## Validation

The focused coverage is in `tests/unit/test_metric_insights.py`, `tests/test_metric_insights.py`, and `tests/test_metric_insights_rls.py`. It covers the percentage formula and rounding (including half-even ties), zero and negative baselines, missing/incompatible/conflicting values, neutral semantics, completed-period boundaries and Company-local timezone handling, M5/M12 evidence gating, API create/read/replay behavior, tenant/company isolation, forced RLS, grants, and append-only protection. Local verification is summarized in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md); the final review reports the PR CI result alongside the PR link.

Run the backend validation commands in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md) against the dedicated PostgreSQL test database. SQLite does not substitute for the RLS, migration, or append-only integration tests.
