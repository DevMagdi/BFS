# Phase 2 Candidate Evaluation Protocol — Owner Approval Required

- **Assessment date:** 2026-11-21 (UTC).
- **Protocol status:** **RECOMMENDED** draft for the candidate in [`PREDICTIVE_USE_CASE.md`](PREDICTIVE_USE_CASE.md); not approved or run.
- **Approval tracking token:** `PENDING_EVALUATION_OWNER_APPROVAL`.
- **Dataset status:** No authorized real operational dataset or evaluation window was identified in the reviewed repository. No evaluation was performed.

## 1. Scope and target (proposed)

This protocol applies only if the accountable Operations Business Owner selects the candidate **weekly count of incoming work requests for one named workflow** and the source owner authorizes a concrete Company/source. The target is `requests/week`: the number of distinct work requests first received in each complete Monday-start week, with one next-week forecast period. A single Company, one source, and one owner-defined workflow/event cohort comprise the evaluation population.

The repository’s generic Operations event count is not this target. Before evaluation, a source contract must prove stable request identity, first-received event semantics, the Company timezone/calendar, period completeness, deduplication, and correction history. If any of these remain unknown, the target is not measurable and the protocol must abstain rather than reinterpret generic event counts.

## 2. Evaluation design

| Protocol element | Proposed rule | Status / rationale |
|---|---|---|
| **Frequency and horizon** | Weekly, Monday-start periods; one next-week forecast (`horizon_periods = 1`). Use the owner-approved Company timezone/calendar to derive the date period; do not silently assume UTC or local business hours. | Proposed and pending owner approval. M17 supports Monday-start weekly periods but does not choose the business calendar or horizon. |
| **Evaluation population** | One authorized Company, one source, one workflow and one exact request-creation cohort. No cross-Company pooling, source substitution, employee-level grouping, or post-hoc filtering. Use the same population for every baseline. | Scope control; source and business semantics are not yet approved. |
| **Observation window** | Use a source-owner-approved contiguous historical window of complete weeks. Record its exact start/end, source/profile/version IDs, timezone/calendar, extraction time, all missing intervals, duplicates, corrections, and source freshness. Do not choose calendar dates until an authorized source is available. | No such historical window is present in the repository; dates and data coverage remain unknown. |
| **Training** | At each rolling origin, train only on observations that were available as of that origin. Use an expanding chronological history. The starting training length must meet the approved business-readiness rule and each selected M17 baseline’s technical requirements. | The business minimum-history rule is not established. Do not infer it from M17 resource limits. |
| **Validation** | Use 12 consecutive one-week rolling-origin validation targets as a proposed minimum. Each origin predicts the next week using strictly prior training data; no validation actual can flow backward into that prediction. Keep each week’s source snapshot and prediction origin explicit. | Proposal only; `PENDING_EVALUATION_OWNER_APPROVAL`. Twelve non-overlapping target weeks are not statistically independent samples and do not establish seasonality or representativeness. |
| **Final holdout** | Reserve the final one complete week after validation. Lock data identity, target definition, protocol, and candidate baselines before a single fixed-origin holdout evaluation. Do not expose actuals/results for tuning before the evaluation plan is locked. | Proposed one-week holdout; it must be governed at source/time/content level, not only by dataset-version ID. |
| **Minimum valid windows** | Require all 12 proposed validation target weeks to be valid for the predeclared scoring rule and require one untouched final holdout week. A missing or source-incomplete target week is not a zero. If the proposed minimum is not met, return `not_evaluable`/abstain; do not lower the minimum after seeing scores. | Proposed minimum, not approved. There is no evidence that 13 complete periods plus sufficient training history exist. The owner/evaluation owner may revise this before any data are scored. |
| **Seasonality test** | Do not assert a seasonal pattern from the current repository. Seasonal-naive is eligible only if the authorized historical series demonstrates a repeatable cycle, the cycle length is specified before scoring, and enough prior cycles exist in training and validation. | No real series or cycle evidence exists. A baseline being available in M17 is not proof of seasonality. |
| **Incumbent-process comparison** | Capture the current manual/rule-based plan or forecast for each comparable week before actuals are known, including its timestamp, target, horizon, and source scope. Score that incumbent on the same valid weeks. If no contemporaneous incumbent outputs exist, report only the comparison against M17 baselines; do not claim improvement over today’s process. | Current Operations decision process and archived outputs are not recorded in the repository. |

The value `12` is an explicit review proposal so the protocol has a testable starting minimum. It is not a statistically justified sample-size guarantee, error threshold, or claim that 12 windows are currently available. The evaluation owner must approve, replace, or reject it before an evaluation is run.

## 3. Baselines and model comparison

Reuse the M17 deterministic baseline implementation; do not build a second engine or add a dependency:

- `naive_last_value` — one-step carry-forward baseline.
- `moving_average` — a fixed, versioned trailing-window baseline; window length must be set before evaluation.
- `seasonal_naive` — include only if Section 2’s predeclared seasonality evidence and history requirement are met.

Every candidate is evaluated on identical training origins, the same one-week target periods, source snapshot, and scoring population. Report unavailable predictions rather than dropping different weeks for different models. M17 uses common complete cases across the candidate models and does not automatically rank, choose, recommend, promote, or deploy a champion. A human reviewer must not tune against an exposed holdout and then describe that same holdout as independent evidence.

The M17 model/protocol/data hashes and replay are deterministic evidence controls; they do not authenticate source authority, prove independent evaluation, or establish forecast accuracy. See [`M17_BASELINE_FORECASTING.md`](../M17_BASELINE_FORECASTING.md) and [`forecasting/service.py`](../../src/businessflow/forecasting/service.py).

## 4. Metrics and data validity

### Recommended metrics

| Metric | Use in this candidate | Limitations / implementation status |
|---|---|---|
| **MAE** | Primary: mean absolute miss in `requests/week`, interpretable in the target’s count unit. | Implemented by M17. It is not normalized across workflows with different volumes; compare only within the locked population and horizon. |
| **RMSE** | Secondary diagnostic only if the Business Owner says large misses have disproportionately higher consequence. | Implemented by M17. It is also in count units and can be dominated by a small number of spikes; do not choose it after looking at model results. |
| **Signed bias / mean error** | Request a predeclared over-/under-forecast diagnostic; define sign as forecast minus actual. | Not currently an M17 metric. Any calculation requires an approved, deterministic P2-03 change and tests; this document does not implement it. |
| **WAPE** | Not selected for the initial protocol. | Not implemented in M17. Its denominator is the sum of actual counts and is zero for an all-zero evaluation window; low totals also make it unstable. Add only if an owner-approved comparison requires it and the denominator policy is fixed in advance. |
| **MAPE / sMAPE** | Not selected for low-volume request counts. | MAPE is undefined when any actual is zero and unstable for small counts. M17 reports undefined rather than zero-filled MAPE; sMAPE is undefined when both actual and forecast are zero and can be unstable near zero. |

MAE/RMSE are candidate metrics, not approval thresholds. The Business Owner must determine what error is operationally tolerable and what consequence (if any) attaches to over- versus under-forecasting. No numeric acceptable error, relative improvement, or coverage target is invented here.

### Zero, missing, negative, and low-volume periods

- **Observed zero:** A zero count is valid only when the source owner can affirm that the source and workflow were complete for that whole week and there were genuinely no new requests. No rows alone do not prove zero.
- **Missing/incomplete period:** Preserve it as missing with a reason. Do not impute or default to zero. For this candidate, propose `block_evaluation_if_missing_actuals`; if source coverage is not known, the period is not scoreable. The owner must approve the final missing-data rule.
- **Negative count:** Impossible under the proposed target. Treat a negative or fractional count as a source/contract violation; block the affected snapshot rather than reinterpret it.
- **Low volume:** Percentage metrics are omitted; a one-request change can create a large percentage. Report counts and absolute errors with the sample size and period list. Do not aggregate extra sources or periods merely to improve a metric.
- **Duplicates and revisions:** Deduplicate only using an owner-approved stable request key and recorded rule. Preserve corrected versions and explain exactly which version was available to each forecast origin.

## 5. Leakage, revisions, and holdout governance

### Time and correction leakage

At every origin, the forecast input must be a frozen, server-built snapshot containing only source versions and corrections available by the origin timestamp. Do not train on a later-corrected timestamp/type/value as if it had been known earlier. If the source cannot reconstruct point-in-time knowledge, correction history, or a valid as-of snapshot, abstain from retrospective backtesting.

Operations corrections are append-only, but the current Operations analysis reads current event versions; the current M17 dataset snapshot is caller supplied and is not linked to Operations revisions. Before a P2-01/P2-03 source path exists, the repository cannot prove point-in-time leakage protection for this candidate. A later correction must not rewrite an old forecast/evaluation snapshot. It should create new versioned evidence and mark affected prior evidence as superseded or limited under an owner-approved policy.

### Holdout reuse: finding and proposed control (design only)

P2-00 identified a concrete M17 limitation: one-time holdout uniqueness is scoped to a `dataset_version_id`. A caller can resubmit identical or overlapping source periods under a new dataset key/version; a new version ID is not proof of independent observations. The current implementation has no source/time/content-level exposure ledger or clone/overlap regression test. See the finding and design proposal in [`PHASE2_BASELINE_ASSESSMENT.md`](PHASE2_BASELINE_ASSESSMENT.md) and the M17 partial unique index in [`0026_forecasting_engine.py`](../../migrations/versions/0026_forecasting_engine.py).

Before any score is called independent, the Evaluation Owner, source owner, and security/privacy/retention owners should approve a design that:

1. Resolves Company and source lineage server-side and identifies holdouts by source/record versions, target/cohort, dimensions, frequency/calendar, forecast origin, exact time window, horizon, and protocol—not a client-supplied ID alone.
2. Canonicalizes the exact accepted period/cohort/value-or-missing-reason entries under an explicitly versioned rule; stores a keyed, versioned HMAC for the whole window and per-period leaves to detect exact clones and overlap without keeping another raw copy. The HMAC is a duplicate-detection token, not encryption, authorization, or proof of source authority.
3. Records exposure append-only, including evaluation/protocol/model versions, actor, time, and whether results/actuals were disclosed. Serialize concurrent attempts and fail closed on an exposed or overlapping window for independent scoring.
4. Requires a later non-overlapping chronological window or separately governed untouched source for independent evaluation. Otherwise label follow-on comparisons exploratory and never claim a fresh holdout.
5. Defines treatment of corrected source versions, cross-source duplicate content, canonicalization, key custody/rotation, access, retention, and deletion.

This is a **design recommendation only**. No ledger, fingerprint, key, migration, API change, test, or additional holdout-reuse control was implemented for this task. Until approved and implemented, repeated/overlapping M17 holdouts must not be represented as independent evidence.

## 6. Uncertainty and abstention

M17’s optional interval is an empirical nearest-rank quantile of validation absolute errors. It is labeled non-guaranteed and does not establish nominal coverage, horizon-specific coverage, or a probability that the actual lies inside. An interval must not be described as a confidence guarantee.

For this candidate, omit an interval or abstain from uncertainty claims when there are too few valid pre-holdout residuals, unstable/incomplete periods, unresolved corrections, a changed source/target definition, a marked regime shift, or no owner-approved interpretation. If an interval is later approved, disclose its empirical basis and calibration sample; keep holdout actuals out of fitting/calibration. A point forecast itself must also abstain when source authorization/readiness, minimum history, target meaning, or required horizon is not satisfied.

## 7. Acceptance and approvals

The evaluation report presented to the Business Owner must include exact source/Company/time scope, versioned target and transformations, training/validation/holdout boundaries, missing/zero/duplicate/correction counts, baseline predictions, each metric with denominators and scored windows, deviations from the current process (if comparable), uncertainty limitations, and explicit abstentions.

The Business Owner must set and approve the acceptable MAE (and any RMSE/bias or decision-level criterion), the minimum history/window requirement, the business cost of misses, and the conditions for using the result. The Evaluation Owner must approve the protocol, holdout rules, scoring population, and independent-evidence claim. Until then the acceptance status is **PENDING**, with tracking token **`PENDING_EVALUATION_OWNER_APPROVAL`**. A passing code test or synthetic result cannot close this approval.

No forecasts, model comparisons, new dependencies, production activation, or P2-01 implementation are authorized by this draft.
