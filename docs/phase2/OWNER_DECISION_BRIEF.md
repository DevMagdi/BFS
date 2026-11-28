# Phase 2 Owner Decision Brief

**Execution authorization:** Phase 2 engineering is authorized to proceed sequentially. P2-01 may begin with bounded synthetic fixtures and focused tests. This engineering authorization is not a human Business Owner decision, does not close any Entry Gate, and authorizes no customer/production-data access, connector activation, or production forecasting. Production remains disabled.

## 1. Adopted engineering target — synthetic/non-production scope only

For engineering and synthetic testing, use the **count of distinct new work requests first received in each week**, at weekly grain, with a **one-week horizon**. The candidate domain is Operations. A workflow must be identified explicitly and selected only when the source schema and evidence support that workflow and its first-received semantics. Each observation must explicitly identify the workflow, period, calendar/timezone, and target definition.

Use an authoritative first-received timestamp and stable request identity. Corrections and replayed transmissions must not create additional requests; preserve correction/version lineage and make duplicate handling explicit. Do not infer first receipt from a generic Operations `occurred_at`, event label, status transition, or upload timestamp. If reliable first receipt, workflow identity, or period completeness cannot be established, report the limitation and block/abstain rather than fabricate a target or convert missing evidence to zero. A zero is valid only with affirmative evidence that the full period was observed and complete.

Synthetic fixtures may exercise the target contract and edge cases, but synthetic values are never real business evidence, source authorization, or model-acceptance evidence. Use only a synthetic owner persona/role when tests need one; do not invent or register a real person.

## 2. Adopted engineering controls and architecture

- **Reuse M17** as the sole forecasting/evaluation engine; do not build a competing engine, UI/UX, or general ingestion framework. Keep dependencies unchanged absent a demonstrated technical gap. Add a migration only if required by the selected architecture.
- Preserve server-resolved Tenant/Company authorization and isolation; immutable snapshot identity, source/record/version lineage, explicit readiness and blockers, and versioned target semantics; explicit calendar/timezone and period boundaries; idempotency and correction/replay handling; reproducibility and auditability.
- Do not silently impute, drop, deduplicate conflicting records, or transform outliers. Preserve missing/incomplete periods and reasons. Do not leak future corrections into historical forecast origins.
- Use chronological rolling-origin evaluation. The initial synthetic acceptance protocol requires **at least 12 valid weekly rolling-origin evaluation targets plus a separate untouched holdout**. Record an evidence-based training-history floor from the selected M17 baseline requirements and contiguous complete-period requirements. A dataset-version ID alone does not establish holdout independence; enforce or clearly block source/time/content overlap and reuse.
- Reuse M17 `naive_last_value`; use `seasonal_naive` only when an explicit cycle is supported by the data and its required history is available. Report MAE/RMSE and signed bias; use percentage metrics only when their denominators are valid. Handle zero, negative, fractional, sparse, missing, and irregular inputs explicitly. Insufficient evidence means `not_evaluable`/abstention, not a relaxed floor or automatic promotion. No universal accuracy threshold or automatic promotion is adopted.

## 3. Human/business approvals remain pending

The four mandatory Entry Gate names remain unchanged and each remains **PENDING** for real-source/business use:

1. **Use-case Selection** — an accountable Operations Business/Decision Owner has not accepted the business use, target semantics, workflow, decision, error consequences, or acceptance criteria. The engineering target above is adopted only for synthetic/non-production engineering.
2. **Source Authorization** — no specific source, Company, fields, rights, source owner, or predictive-purpose authorization has been supplied.
3. **Governance** — applicable Data Governance, Security/Privacy/Legal, retention, and operational approvals have not been supplied.
4. **Evaluation Protocol Approval** — no authorized Evaluation Owner or Business Owner has approved business acceptance thresholds, independent-evidence claims, or production use.

Business Owner designation/approval remains a separate cross-cutting prerequisite. The technical owner authorization recorded here does not identify a real person or establish their business authority. See [`ENTRY_GATE_APPROVAL_REGISTER.md`](ENTRY_GATE_APPROVAL_REGISTER.md) for the decision/approval separation and evidence requirements.

## 4. Source evidence and P2-01 boundary

The current Operations foundation records generic source-scoped observations and append-only corrections. It does **not** establish a typed work-request entity, authoritative first receipt, workflow semantics, complete-week coverage, or a point-in-time history suitable for this target. No real operational source or time series is available in the reviewed repository.

P2-01 is therefore authorized to implement a bounded, reusable typed foundation and synthetic tests, while explicitly blocking or abstaining when first-received semantics or required lineage/completeness cannot be established. It must not adapt generic Operations events into a request target by assumption, connect to a real source, or claim business readiness. If source-specific capability remains blocked, document the exact dependency and continue to independent Phase 2 milestones within this authorization.

## 5. Production restriction

Forecasting remains disabled by default and hard-blocked in production. Do not access customer or production data or activate a real connector. Production forecasting requires separate real-source authorization, business acceptance, governance and retention approvals, operational monitoring, deployment/release requirements, and explicit production authorization. Synthetic acceptance is not production acceptance.