# BusinessFlow AI — MVP Project-Acceptance Decision Contract

- **Status:** Proposed product/domain contract for owner and finance review; not an approved accounting policy or implementation.
- **Decision:** “Should we accept this new project?” for the initial Software & IT Services hypothesis.
- **Scope precedence:** [`BRD.md`](BRD.md) and [`MVP_SCOPE.md`](MVP_SCOPE.md) govern current Must/Should/Out priorities. The scenario and action concepts in this broader decision model are candidates, not a release commitment.
- **Invariant:** BusinessFlow owns the canonical calculations, scenario versions, evidence, recommendation, approval and outcome. A model may help explain inputs/results but may not supply authoritative values.

## 1. Decision output states

A run returns exactly one of:

- **`recommendation_ready`** — all required inputs and approved rules exist for the stated comparison; returns alternatives, formulas, evidence and limitations. A preferred option appears only when an approved policy explicitly supports it.
- **`insufficient_reliable_data`** — a decision-critical fact, approved formula, policy, source-quality condition, or currency conversion required for the selected analysis is missing or unreliable. Lists blocking items and evidence needed; has no preferred option.
- **`not_authorized`** — the user may not access the required tenant/company/source data or perform the requested decision operation. Do not reveal which protected records exist; do not report protected data as a data-quality blocker.

A partial metric may be returned only when clearly labeled as a partial observation; it must not be promoted into a recommendation.

## 2. Minimum required inputs

The evaluator must validate these for each decision run. A value must include source/provenance, observation time or effective period, validation state, and applicable tenant/company.

| Input | Minimum content | Blocks recommendation when |
|---|---|---|
| Decision scope | Tenant and its one MVP Company; question, proposed project identity, decision date, requester and authorized scope | Tenant/company mismatch, absent membership, or unsupported decision type |
| Proposed scope and effort | Work packages/tasks, required skills/roles, estimated hours, estimate method/version, delivery dates and deadline | Scope is undefined, total effort cannot be estimated, or skill/time-phased requirements are missing for the capacity comparison |
| Proposed commercial terms | Contract/proposal amount and currency, payment milestones/terms, status (draft/proposed/signed), included scope and effective dates | Price or currency is absent for contribution comparison; unsigned or contradictory terms are presented as committed revenue; or dates do not align |
| Current delivery portfolio | Active/approved project allocations, commitments and time-phased capacity for the same company and horizon | Existing commitments are incomplete or stale enough to make available capacity unreliable |
| Resource availability | Employee/resource schedule, work calendar/period, approved absence/leave, skill match and current allocation | Required skills/capacity are unknown or unavailable for the proposed delivery window |
| Internal cost basis | Effective-dated, finance-approved loaded cost rate for forecast hours, or explicitly approved alternative costing basis | A cost-based comparison is requested but cost rates or their approved definition are missing/stale; never substitute zero silently |
| Direct non-labour costs | Known project-specific costs, materials, travel, licences or other cost items, with currency and source; explicit zero only when attested | Material direct costs are unknown or a required estimate is absent |
| Approved calculation and policy versions | Revenue/cost definitions, margin/contribution definitions, rounding/currency rules, and decision policy effective on the decision date | A needed formula or policy has not been approved, or option selection depends on a threshold that has not been set |
| Data quality and lineage | Relevant imports reconciled; no unresolved contradiction/duplicate; source IDs, mapping versions and freshness available | Any critical input is untrusted, unreconciled, expired, contradictory, source-unauthorized, or lacks traceable evidence. A user lacking permission receives `not_authorized`. |

For recommendations that compare cross-currency amounts, an approved dated FX source/rate and conversion policy are also required. Without them, report values separately by currency and abstain from a combined financial comparison.

## 3. Optional inputs and assumptions

Optional context may improve a comparison but must not be invented:

- Historical project performance, approved timesheets and actual costs.
- Invoice aging, receipts, customer payment history, and cash snapshots.
- Current customer relationship/risk evidence and approved risk assessments.
- A valid subcontractor quote and availability window.
- Hiring cost, onboarding lead time, skills ramp, and time-to-productivity.
- Indirect-cost allocation, tax, overhead, or risk contingency **only after** the applicable policy is approved.
- Business goals, portfolio priorities, or strategic constraints with an owner and effective period.

An assumption is explicitly labeled, attributed to its author, timestamped, versioned, optionally range-valued, confirmed before calculation, and included in sensitivity analysis. No hidden defaults, LLM-generated amounts, or guessed collection probabilities are allowed.

## 4. Inputs that must block a recommendation

Return `insufficient_reliable_data` (not a best guess) when any input required for the selected analysis is absent, contradictory, stale beyond an approved freshness rule, source-unauthorized/untrusted, untraceable, or unreconciled; when a required formula/policy has no owner-approved version; when project scope/effort or capacity cannot be established; when a requested cost comparison depends on missing rates/quotes; when a requested cross-currency comparison has no approved conversion; or when a required policy threshold has not been set. If the caller lacks permission, return `not_authorized` without disclosing the protected gap.

The response must name each blocker, affected option/metric, source or missing source, last known observation time, and what would resolve it. An analyst may explicitly narrow the question (for example, “capacity only”), but the narrower result is not an acceptance recommendation.

## 5. Proposed deterministic calculation framework

All calculations require exact-decimal semantics, preserve original amounts/currencies, and store formula/input snapshot versions. Python `Decimal` and PostgreSQL `NUMERIC` are candidate implementations only, pending technical approval. The following are candidate formulas, **not approved accounting rules**.

### Resource capacity by skill and period

For each resource `r` and time bucket `t`:

`available_hours(r,t) = scheduled_hours(r,t) - approved_absence_hours(r,t) - existing_committed_hours(r,t)`

Only resources with a validated skill match contribute to the required skill bucket. Existing allocations must be non-overlapping, status-filtered, and time-bounded. Do not count the same hours from both an allocation and an approved timesheet. Sum by required skill and period, not just across the company. Net available capacity is not a utilization recommendation; any reserve/utilization target is an owner-approved policy.

### Project effort

`required_hours(skill,t) = sum(versioned work-package estimates for skill and bucket t)`

Store the estimate source, estimator, version, range/uncertainty if used, and scope assumptions. A total without skill and timing detail cannot prove a delivery schedule.

### Internal employee cost

Forecast cost candidate:

`forecast_labor_cost = sum(forecast_hours(employee,t) × approved_effective_loaded_cost_rate(employee,t))`

Actual labor cost candidate:

`actual_labor_cost = sum(approved_time_entry_hours × approved_effective_cost_rate_for_work_date)`

The cost rate must be effective-dated, access-restricted, and defined by a finance-approved policy. Missing rates remain unknown; they are not zero and cannot be replaced with a billing rate.

### Outsourcing cost

`outsourcing_cost = valid_quote_fixed_amount + sum(quoted_units × quote_rate) + explicitly supported direct quote costs`

A quote needs supplier, scope, currency, issue/expiry dates, status and provenance. Unaccepted or expired quotes are scenario assumptions, not commitments. Cross-currency comparison follows the same FX gate.

### Project costs

Candidate direct delivery cost:

`direct_delivery_cost = internal_labor_cost + outsourcing_cost + approved_project_direct_expenses`

Do not silently allocate general overhead, tax, shared software, or contingency. If a policy allocates them, store the formula version and inputs and show the amount separately.

### Project revenue and contribution

Keep these facts distinct: contract/proposal value, forecast milestone billing, recognized revenue, invoice amount, receivable balance and cash received. Candidate commercial measures are `contract_value(currency) = sum(valid versioned contract/proposal line amounts in that currency)` and `scheduled_billing(period,currency) = sum(valid payment milestones contractually due in that period/currency)`. These represent price and expected billing, not earned/recognized revenue. Recognized revenue is a validated source `revenue_fact` for its approved accounting period, or a separately approved finance-owner recognition method with its own formula/version; do not infer percentage-of-completion, accrual, contract changes or invoice value as revenue. If a decision requires recognized revenue and neither a reliable source fact nor an approved recognition method exists, abstain. Never sum different currencies without the approved FX gate.

Candidate scenario metric for a fixed-price proposal:

`estimated_direct_contribution = comparable_contract_price - direct_delivery_cost`

`estimated_direct_contribution_margin = estimated_direct_contribution / comparable_contract_price`

These labels are intentionally “estimated direct” rather than accounting profit/margin. The numerator, denominator, treatment of taxes/credits/changes, timing, zero/negative-price cases, rounding and reporting label all require finance-owner approval. Do not compute the ratio when the denominator is zero or incomparable.

### Cash collection exposure

Where reliable evidence exists, keep a currency- and due-period schedule. Candidate observations are `open_receivable(i, as_of) = max(0, invoiced_amount(i) - validated_receipts_allocated(i, as_of) - approved_credit_adjustments(i, as_of))` and `past_due_receivable(currency, as_of) = sum(open_receivable(i, as_of) for validated invoices i where currency(i) = currency and due_date(i) < as_of)`. Unapplied receipts/overpayments stay separately visible; unresolved allocations block the metric. Report each `receipt_timing_delta_days = actual_receipt_date - contractual_due_date` for settled receipt/invoice pairs (positive is late, negative is early); do not aggregate into an unapproved average or probability. Invoice/credit/receipt allocation rules require finance-owner approval. Compare the schedule with signed payment milestones and dated cash positions only in the same currency; report known timing gaps and historical observations. A probability or forecast requires an approved method and enough comparable, reconciled history; absent that, state “not assessed.” No inferred credit score, payment probability, FX rate, or cash buffer is permitted. “Not assessed” is not “low risk”: cash exposure may be omitted from an acceptance recommendation only if an owner-approved decision policy treats it as non-gating for that case. If policy requires reliable cash-exposure evidence, missing inputs or an unapproved method block the recommendation.

## 6. Finance/product owner approval required for formulas and policies

Before M5/M6 recommendation behavior is accepted, an accountable finance/product owner must review and version:

1. Working calendar, periodization, absence, utilization, resource skill matching and treatment of reserved/overlapping time.
2. Work-estimation methods, contingency/range handling and how estimated effort maps to periods and skills.
3. Loaded employee cost components, effective dating, rate changes, cost currency and treatment of unapproved timesheets.
4. What counts as direct labour, supplier/subcontractor, and other direct cost; allocation/exclusion of overhead, tax, benefits and shared costs.
5. Contract/proposal value versus recognized revenue, milestone timing, cancellations/credits/change orders, and whether comparisons use contract value or a recognized-revenue forecast.
6. The approved contribution/margin definition, denominator, sign/zero cases, rounding and displayed terminology.
7. FX source, timestamp/date, conversion rate, rounding and comparisons across currencies.
8. Cash-collection exposure method, minimum sample/freshness, invoice aging treatment and any future probability model.
9. Data freshness, reconciliation tolerances, minimum evidence quality and recommendation abstention policy.
10. Business acceptance thresholds, exception policy and any separation-of-duties/approval thresholds. No universal threshold is assumed here.

These approvals are open; the table does not establish accounting policy.

## 7. Scenario set and versioning

This is the broader candidate set. Under the current provisional scope, Must Have options are current resources, user-authored reallocation, phase/defer and decline. Hire and outsource are conditional/Should Have; they are shown as incomplete when required evidence is absent. There is no automatic optimizer or mutation of current allocations/contracts. Each `ScenarioVersion` stores the decision-case ID, option, input snapshot ID, formula versions, user-confirmed assumptions, output values, currency, limitations, and created-by principal. Editing an input produces a new scenario/recommendation version; it never mutates an already-approved version.

The recommendation may prefer an option only when all relevant required inputs, quality gates and approved policies pass. Otherwise it returns blockers and can offer a human follow-up task rather than pretending a scenario is complete.

## 8. Optional controlled-action concept (not core MVP)

**Candidate action:** create an internal, reversible project-assessment follow-up task in BusinessFlow—for example, “obtain a current subcontractor quote” or “confirm the effort estimate.” This may resolve an abstention without modifying a customer system or making a commitment, but is Should Have in [`MVP_SCOPE.md`](MVP_SCOPE.md) and can be deferred without blocking the core decision workflow.

- **Affected system:** BusinessFlow’s own internal task/action boundary only. If PostgreSQL is selected after technical review, the task may use PostgreSQL tables; no external connector, contract, project system, payment, hiring, or customer record is changed.
- **Permissions:** requester needs `decision:read` and `action:request`; an authenticated human with approved `action:execute` or an explicitly designated approval permission confirms the exact text, assignee, due date and decision-case link. Role separation remains policy-controlled and must be approved.
- **Approval:** required for this first version; binds to an immutable task payload hash and recommendation/decision version. A model or service principal cannot approve.
- **Idempotency:** backend-generated action ID plus a unique key scoped by `(tenant_id, decision_case_id, recommendation_version, action_type, caller_idempotency_key)`. Retries reuse the same action ID; edited content creates a new version/key.
- **Failure/retry:** write task, action, audit and outbox event in one DB transaction. Retry transient DB/outbox failures with bounded backoff; uniqueness prevents duplicates. Mark permanent validation/policy failures failed with a reason; do not silently retry authorization failures.
- **Reversal and verification:** cancel/close the task with an audited reason rather than deleting it. A separate fresh read transaction/API read-back verifies tenant, payload hash, state and assignee; only then mark verified. This is an independent application read-back, not a second external system.
- **Audit evidence:** requester/approver actor IDs, tenant/company, permission/policy version, decision and scenario versions, exact payload hash, idempotency key, timestamps, attempts, cancellation reason and verification result. No credentials or unnecessary personal data in logs.

If an internal follow-up task has no product value after owner review, defer action execution and release a recommendation-and-approval-only MVP. In either case, keep high-impact or irreversible operations disabled.
