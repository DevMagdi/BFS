# BusinessFlow — Historical Project-Feasibility MVP Requirements

- **Status:** Historical provisional requirements for a prior project-feasibility hypothesis. Superseded as the current product direction by the M9 assessment in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md). This is not the current BRD, a customer-validated Product Baseline, or an implementation authorization.
- **Important scope note:** The Must/Should/Conditional requirements and project-based segment below record the earlier hypothesis only. They must not be read as the current platform scope or as evidence that those features are implemented. Current actual behavior is in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md); the current strategic roadmap proposal is [`roadmap/PRODUCT_ROADMAP.md`](roadmap/PRODUCT_ROADMAP.md).
- **Historical decision basis:** Founder direction to define a narrow project-acceptance MVP. The related discovery authorization remains specific to that hypothesis; M9 does not extend it.
- **Historical positioning hypothesis:** **New-Project Feasibility Decision Support for Project-Based Services Companies.**
- **Historical segment hypothesis:** Software & IT Services companies.
- **Historical core question:** **“Should we accept this new project, and under what conditions?”**
- **Relationship to prior work:** This document was drafted from `DOMAIN_MODEL.md`, `MVP_ENTITY_CATALOG.md`, `MVP_DECISION_MODEL.md`, `INITIAL_TECHNICAL_ASSESSMENT.md`, `TECHNICAL_BASELINE_PROPOSAL.md`, `AI_GOVERNANCE.md`, `PHASE_1_COMPETITIVE_VALIDATION.md`, and the former project-specific `ROADMAP.md`. Existing technical recommendations remain unapproved.

## 1. Product definition

### 1.1 What BusinessFlow is

BusinessFlow is a self-hosted-first, company-scoped decision-support product for project-based services firms. It brings structured project, resource, capacity, time, cost, and proposal facts from customer-provided CSV/XLSX files into a traceable model. A manager can describe a proposed engagement, compare a limited set of delivery scenarios, inspect the supporting facts and gaps, and record a human decision.

The product's core output is a **decision brief**, not a project plan or autonomous accept/reject command. It shows what the data supports, what it does not support, which calculations and assumptions were used, and what should be confirmed next. It must be able to say: **“Insufficient reliable data to support a recommendation.”**

### 1.2 Problem to solve

**Hypothesis, not yet customer-validated:** A services firm may have to decide whether to take on a bid/project using information split across proposals, spreadsheets, resource schedules, timesheets, cost-rate files, and finance records. Reconciliation is manual; capacity conflicts and financial assumptions may be hidden; alternative delivery plans are hard to compare consistently. The consequence could be accepting work that cannot be staffed or priced responsibly, or declining work that could have been reshaped.

The MVP addresses the **decision-preparation and evidence-coordination** part of this problem. It does not guarantee better decisions or financial results.

### 1.3 Users and buyer hypotheses

| Role | MVP use | Buyer / approval hypothesis |
|---|---|---|
| Owner, COO, managing director, or delivery executive | Opens a decision case, reviews capacity/economics, compares scenarios, records the decision | **Likely economic buyer**, not validated. |
| Resource/staffing manager | Supplies capacity, skills, availability, allocations, and staffing assumptions | Operational data steward; may be the primary daily user. |
| Finance lead/controller | Validates financial inputs and approves formula/policy versions; restricts cost-rate data | Finance authority for cost and contribution definitions. |
| Decision approver | Reviews and approves an exact case/option/version when the company requires approval | May be the owner or a separate authorized person; approval policy is company-configured. |
| Tenant administrator | Configures the Company, memberships, roles, imports, and security settings | Administrative operator, not automatically a finance approver. |

The **primary user** is the owner/operations or resource leader preparing a bid decision. The **buyer** is provisionally the owner/COO or finance/operations executive. Buyer, budget, frequency, and purchase authority require discovery.

### 1.4 The business decision

The decision is whether to accept a specified new engagement, and whether acceptance depends on conditions such as changing dates/scope, reallocating available resources, or obtaining a validated cost/quote. The system reports evidence for the options; an authorized human chooses and records the outcome.

MVP decision states from the existing decision contract are preserved:

- `recommendation_ready` — required inputs and approved rules exist for the stated comparison; a preferred option is allowed only if an approved policy supports it.
- `insufficient_reliable_data` — a decision-critical input, reliable source, approved formula/policy, freshness condition, or currency rule required for the selected analysis is missing or unreliable; show blockers and **no preferred option**. A user who lacks access receives `not_authorized`, not a data-quality explanation that leaks protected facts.
- `not_authorized` — the user lacks permission; do not disclose protected records.

A partial metric can be displayed as a labeled observation but cannot be presented as a recommendation. No universal margin, utilization, risk, or acceptance threshold is assumed.

### 1.5 Honest differentiation and value

The competitive report shows substantial overlap, not an empty category. Kantata markets pre-work scoping, staffing/capacity, financial what-if analysis, and margin visibility ([Kantata SX](https://www.kantata.com/psa/kantata-sx), [project accounting](https://www.kantata.com/psa/financial-management-software/project-accounting)). Projectworks connects proposals to delivery and reports a customer workflow using unsigned opportunities and shadow resourcing to assess tender feasibility ([CRM/proposals](https://www.projectworks.com/features/crm-and-proposals), [CT Management case—vendor-published](https://www.projectworks.com/case-studies/ct-management-group)). Microsoft documents quote estimates and a **preview** quote what-if workflow ([quote what-if](https://learn.microsoft.com/en-us/dynamics365/project-operations/sales/quote-what-if-analysis)). The full desk-source comparison and limitations are in `PHASE_1_COMPETITIVE_VALIDATION.md`.

The provisional distinction to test is a **vendor-neutral, evidence-linked, human-controlled decision brief** that can work from structured exports, shows cross-source freshness/conflicts, and abstains when facts or approved policies are insufficient. This is a **positioning hypothesis, not a proven moat**. Do not claim feature superiority, automatic acceptance advice, causal ROI, or validated customer demand.

Expected customer value, if validated:

1. Less manual assembly of a project feasibility view.
2. Earlier visibility of skill- and time-specific capacity gaps.
3. Comparable, explicitly labeled direct-cost and proposal-price scenarios.
4. Visible assumptions, stale/conflicting facts, and missing evidence instead of hidden defaults.
5. A reviewable record of which scenario a human chose and why.

No quantified time savings, margin lift, or ROI is promised.

## 2. Business objectives and product principles

| ID | Objective |
|---|---|
| **O-01** | Make the pre-commitment project-feasibility question reviewable from one company-scoped case. |
| **O-02** | Make capacity and finance calculations reproducible, source-linked, and explicit about units/currency/time. |
| **O-03** | Keep decision authority with an authenticated human; support abstention and record outcomes. |
| **O-04** | Protect customer, employee, and financial information in a self-hosted-first product. |
| **O-05** | Keep ingestion practical: structured files and explicit mapping/reconciliation before any connector program. |

Principles: `Tenant` and `Company` remain separate with strict 1:1 MVP cardinality; Company facts retain both IDs. Source facts, calculations, recommendations, approvals, actions, verifications, and outcomes are different records. No external ERP schema is canonical. Money uses exact decimal values and explicit currency. Missing is not zero. Imported data and model outputs never override authorized facts or policies.

## 3. Provisional assumptions and decisions still open

| ID | Provisional assumption / open decision | MVP handling |
|---|---|---|
| **A-01** | Software & IT Services is the initial segment hypothesis; firm size and geography are not selected. | Specify a project-based services workflow; do not hard-code size, geography, or industry thresholds. |
| **A-02** | One Tenant has exactly one Company in this MVP. | Enforce one Company per Tenant conceptually; no multi-company selection or consolidation. |
| **A-03** | Ingestion starts with versioned CSV/XLSX templates, not live connectors. | Accept `.csv` and `.xlsx` only; do not execute workbook formulas/macros. Direct integrations are out of scope. |
| **A-04** | Capacity is planned in weekly buckets for the first version. | Store dates/time zone; normalize to company-calendar weeks. This periodization needs owner review before build. |
| **A-05** | A finance-approved effective loaded-cost basis and contribution definition are required. | Candidate formulas below are **pending finance-owner approval**. Without it, no financial recommendation. |
| **A-06** | The company sets working calendar, data-freshness expectations, and any decision/approval policies. | No universal thresholds/default risk score. If a required policy is absent, abstain. |
| **A-07** | Proposal price, payment milestones, and scope are user-provided or imported source facts. | Do not generate prices, rates, estimates, or contract language. |
| **A-08** | Cross-currency calculations need an approved FX source and policy. | Preserve original currency; show separate currencies; no combined ranking without approved FX. |
| **A-09** | Core MVP does not need an LLM. | Deterministic calculations and file workflow work without AI. No model/provider is selected. |
| **A-10** | The first optional action, if enabled, is an internal BusinessFlow follow-up task. | No external project, HR, supplier, contract, invoice, or bank write. Action release remains optional. |
| **A-11** | English is the provisional first UI language only. | Localization and Arabic/other-language acceptance are not defined; do not infer requirements from the owner's location. |
| **A-12** | Customer interviews continue separately. | They may revise the hypothesis, but are not a blocker to drafting these provisional requirements. |
| **A-13** | The first upload limit is provisionally 25 MiB per file. | Reject larger uploads before parsing; confirm the limit against the approved deployment and support model before build. |

## 4. Business requirements

Priority labels: **Must** = required for the core decision; **Should** = useful but may follow without invalidating the core workflow; **Conditional** = only when supplied evidence/policy exists. IDs are this BRD's traceability IDs; they do **not** reconstruct the separate original Part 2 list.

### Company, access, and data foundation

- **BR-001 — Company onboarding [Must].** An authorized tenant administrator shall create/configure exactly one Company for a Tenant, with company name, time zone, base currency, and working calendar reference. A second Company under the same Tenant shall be rejected in MVP.
- **BR-002 — Company decision context [Must].** The Company shall identify its organizational/job-role and skill vocabulary, weekly capacity convention, and approved effective dates for context. A change shall be versioned or time-stamped; no multi-company consolidation is supported.
- **BR-003 — Membership and minimum roles [Must].** The application shall scope user access to an active Tenant membership and the minimum roles in §6. Job roles shall not grant application permissions. Cost-rate visibility shall be separately restricted from capacity visibility.
- **BR-004 — Import templates and uploads [Must].** A user shall download a versioned workbook template and upload CSV/XLSX sheets for resources, skills, availability/absence, project allocations/commitments, approved timesheets, effective cost rates, direct expenses, contracts/payment milestones, and invoices/receipts where used. The proposed project is entered in the app. Optional sheets may be omitted; required sheets depend on the requested analysis. The provisional per-file upload limit is 25 MiB (A-13); file size/type limits must be confirmed before implementation.

### Initial import template contract (provisional)

The template workbook and equivalent CSVs are versioned; the exact column dictionary is a release artifact, not an inferred mapping. The selected Company, source, import actor, file hash, template/mapping version and batch as-of time are import metadata. Each dataset carries a stable source record ID (or a documented composite key), effective/work date or period, explicit status, and Company-scope validation. Use ISO dates/currency codes, decimal hours/amounts, explicit units, and stable resource/project/skill references. Include only the minimum employee display data needed to resolve records; do not require compensation values in capacity files.

| Dataset | Minimum row-level fields | Priority and behavior |
|---|---|---|
| Resources and skills | Stable resource ID; active status; skill code; skill effective dates/source status | Must for skill/capacity analysis; unresolved skill match remains unknown. A display label is optional and should be minimized. |
| Capacity and absence | Resource ID; company-calendar period/week start; scheduled hours; approved absence hours or separately keyed absence rows; source/status | Must for capacity analysis; hours are nonnegative decimals; no standard-week default. |
| Existing commitments | Stable commitment ID; project ID; resource ID; skill; period/week; committed hours; commitment status | Must for capacity analysis; only approved/active commitments reduce availability; opportunity-only rows remain visibly uncommitted. |
| Approved actual time | Stable entry ID; project/resource IDs; work date; hours; approval status | Must when actual effort is shown; unapproved/rejected records are separate, not part of approved actuals. |
| Loaded cost rates | Resource ID; effective-from/to; exact decimal rate; ISO currency; finance-approved cost-basis version/status | Finance-restricted and required only for cost/contribution analysis; no billing-rate substitution or zero fill. |
| Proposal/contract terms | Case/contract and line IDs; status; scope reference; price amount/currency; effective dates; payment milestone dates/amounts where supplied | Entered in the case UI or imported; proposal value stays distinct from recognized revenue, invoice, and receipt. |
| Direct expenses | Stable expense ID; project/case reference; effective date; exact amount/currency; direct/indirect classification and source/status | Required when material direct expenses are in scope; blank does not mean zero; indirect costs require approved policy. |
| Optional invoice/receipt evidence | Invoice/receipt source IDs; project/invoice references; issue/due/receipt dates; exact amount/currency; status and allocation | Should Have only; unresolved allocation is not a cash fact and never creates payment probability. |

Every CSV uses the same field meanings and validation rules as its workbook sheet. A file may omit optional sheets; missing decision-critical data is shown as missing, not synthesized.

- **BR-005 — Mapping and preview [Must].** Before promotion, the user shall map source columns to the canonical template, select source/as-of context, preview rows/types, and confirm the mapping version. Unrecognized columns shall be ignored only with visible notice; no implicit semantic mapping is permitted.
- **BR-006 — Validation and quarantine [Must].** The system shall report required-field, type, date, currency, referential-integrity, negative/invalid-value, and status errors at row/field level. Invalid or unresolved decision-critical rows shall not be promoted into calculations. No silent repair or zero fill.
- **BR-007 — Duplicate detection [Must].** Exact file hashes and stable source IDs shall be used for idempotency/deduplication. Same-ID/same-value rows may be skipped as duplicates; same-ID/conflicting-value rows shall be flagged for resolution. Fuzzy matching shall not merge people, projects, or parties automatically.
- **BR-008 — Reconciliation [Must].** Users shall review duplicate/conflicting facts and explicitly resolve, reject, or defer them. Unresolved critical conflicts shall block affected calculations. A correction creates a new mapping/import/reconciliation version; original input remains traceable.
- **BR-009 — Import history and lineage [Must].** Each import shall retain source type/file hash, actor, timestamps, mapping/normalization version, row counts, validation/reconciliation state, and promoted canonical records. A decision metric must link back to the fact/source row or explicitly state that its source is a user-entered assumption.
- **BR-010 — Data freshness [Must].** Every source-backed value used in a case shall show source and as-of/effective time. Freshness is judged against a company-approved policy; no universal age limit is invented. If a required freshness policy is missing, the user must confirm the data for this case or the system abstains where freshness is decision-critical.

### Business context and project intake

- **BR-011 — Skills and resource profile [Must].** The Company shall maintain/import resource IDs, job roles, skills, effective dates, proficiency/source status, and active status. Skill membership shall be explicit and sourced/approved; it shall not be inferred by AI from a title.
- **BR-012 — Availability and absences [Must].** The system shall import/record scheduled capacity, company calendar, approved leave/absence, and time bucket for each resource. Unknown capacity remains unknown, not zero or full-time by default.
- **BR-013 — Current project commitments [Must].** The system shall represent active/approved project allocations and other resource commitments by resource/skill/week/status. It shall distinguish committed work from unapproved opportunity placeholders and prevent allocations and timesheet actuals from being counted twice.
- **BR-014 — Timesheets and actual effort [Must].** The system shall ingest/import time entries with employee, project, work date, hours, and approval state. Only approved entries shall contribute to actual effort/cost unless a separately approved policy says otherwise. Unapproved hours remain visible as unapproved facts.
- **BR-015 — Cost rates [Must].** The Finance Steward shall import/review effective-dated loaded labor cost rates with currency and cost-basis version. These fields shall not be visible to users without `workforce:compensation:read` or equivalent. Missing/stale rates are unknown and block cost-based comparison; billing rates shall not substitute for labor cost.
- **BR-016 — New-project intake [Must].** A Decision Manager shall create a proposed project case with customer/reference label, decision date, scope summary, proposed start/delivery dates, requester/owner, and status. The case is a decision subject, not an accepted/active project or CRM opportunity.
- **BR-017 — Scope and effort estimates [Must].** The user shall add work packages with required skill/job-role, forecast hours, weekly timing, estimate source/version, and explicit assumptions. MVP uses user-supplied point estimates; it does not estimate effort from prose. Missing scope/skills/timing blocks the affected capacity recommendation.
- **BR-018 — Price and terms [Must].** The user shall enter/import proposed price lines, currency, status, effective dates, included scope, and available payment milestones/terms. Proposal/contract value, scheduled billing, recognized revenue, invoice, receivable, and cash receipt shall remain distinct. No price or revenue is generated by AI.
- **BR-019 — Direct expenses and supplier quotes [Must/Conditional].** Users shall enter/import project-specific direct expenses, currency, date, and source; zero must be attested, not inferred from a blank. A valid supplier quote may be attached for the optional outsourcing scenario. Overhead, tax, shared costs, and hiring overhead are excluded unless an approved policy explicitly defines them.

### Calculations and scenarios

- **BR-020 — Capacity calculations [Must].** Calculate planned availability and required hours by skill and week, with capacity gap/headroom and over-commitment shown. Do not net different weeks or skills into a single pass/fail score.
- **BR-021 — Actual effort [Must].** Show approved actual hours by project/resource/period separately from forecasts and unapproved time. Preserve source and approval state.
- **BR-022 — Internal labor cost [Must].** Calculate forecast direct labor cost only using finance-approved effective loaded rates and the scenario's assigned forecast hours. No missing rate may silently become zero.
- **BR-023 — Direct expense/cost [Must].** Calculate explicitly sourced direct costs and scenario-specific supplier costs without double-counting the same work package. Label actuals versus estimates/assumptions.
- **BR-024 — Revenue, contribution, and margin [Must; formula approval pending].** Show proposed price and candidate estimated direct contribution/margin only under an approved finance definition. Until approved, expose source inputs as separate observations and abstain from financial recommendation. Never label candidate direct contribution as accounting profit or recognized margin.
- **BR-025 — Cash facts [Should/Conditional].** If imported, show scheduled billing, invoice/open/past-due receivable, receipts, payment terms, and receipt timing as distinct dated facts. Do not calculate payment probability, liquidity risk score, or cash-pressure recommendation in MVP. A cash-policy-dependent recommendation remains blocked until an approved method exists.
- **BR-026 — Scenario comparison [Must for core set].** Provide immutable, side-by-side versions for: accept with current resources; accept with a user-authored reallocation plan; phase/defer by editing schedule/scope; and decline. Optional hire/outsource estimates are conditional/Should Have under §7. The system shall not auto-optimize or mutate current allocations/contracts.
- **BR-027 — Evidence and assumptions [Must].** Each calculation and material statement shall label source-backed facts, user-entered assumptions, formula/policy version, as-of time, currency/unit, and limitations. Any assumption shall have author, date, explicit confirmation, and a new scenario version if edited.
- **BR-028 — Decision brief [Must].** Generate the manager-facing sections in §8 and `SCREEN_INVENTORY.md`, including executive summary, data completeness, capacity, financial observations, risks/limitations, scenarios, evidence, missing information, next steps, and decision options.
- **BR-029 — Abstention and authorization [Must].** When an input, source-quality condition, policy, freshness rule, or currency conversion required for the selected analysis is missing or unreliable, show the exact message **“Insufficient reliable data to support a recommendation.”** List blockers; do not show a preferred option. If the user lacks permission, return `not_authorized` without exposing protected record existence.

### Human decision, governance, and learning

- **BR-030 — Human decision record [Must].** A Decision Manager shall record accept, accept with conditions, phase/defer, or decline against an exact case/scenario/recommendation version, with actor, time, reason, and conditions. This record is the manager's decision, not an AI approval. If the system abstained, recording a choice is an explicitly labeled human decision under uncertainty/override with rationale; it never changes the system's `insufficient_reliable_data` state or creates a preferred option.
- **BR-031 — Human approval [Must].** If company policy requires approval, an authorized human approver shall approve/reject/request revision against an immutable decision option/version or exact action payload hash. Approval requirements and separation-of-duties policy must be configured; no universal approval threshold is assumed. Editing a case invalidates stale approval.
- **BR-032 — Audit trail [Must].** Record imports, mapping/reconciliation, sensitive reads/writes, calculations, scenario versions, decisions, approvals, exports, and optional actions with actor, tenant/company, timestamp, target/version, result, and correlation ID. Audit logs shall redact credentials and unnecessary personal/compensation data. Do not claim database audit records are tamper-proof.
- **BR-033 — Optional approved action [Should].** If enabled, create only a reversible BusinessFlow-internal follow-up task after authenticated human approval of an exact payload. No external connector or customer-system write is in MVP. If action execution is deferred, the recommendation-and-decision workflow remains complete.
- **BR-034 — Outcome tracking [Should].** Allow a user to record actual start/delivery, approved hours, direct costs, agreed revenue facts, billing/receipts where available, and variance against the original scenario. Keep expected and actual versions; do not assert causal ROI.
- **BR-035 — Data/report export [Must].** An authorized user shall export a decision brief to a printable/PDF form and export authorized case/source data in CSV with source/version metadata. Export follows the same permissions as the UI; no raw credentials or hidden compensation fields.
- **BR-036 — AI governance [Must].** Core calculations, import facts, authorization, approval, and actions shall work without an LLM. AI, if added later, may summarize approved evidence or suggest mappings for human review only; it may not invent values, formulas, rates, thresholds, permissions, decisions, approvals, or actions.

## 5. Calculation and decision contract

All amounts use exact decimals with ISO currency codes. All calculations use a timestamped snapshot and versioned formula/policy. The expressions below are **candidate formulas** from `MVP_DECISION_MODEL.md`, not approved accounting policy. A production recommendation requiring an unapproved formula must abstain. Source records retain provenance; a spreadsheet's value is not proof of accounting correctness.

| ID / calculation | Formula and inputs | Source / units | Assumptions and limitations | Missing/invalid behavior | Validation example |
|---|---|---|---|---|---|
| **CAL-01 Scheduled capacity** | `net_available(r,w) = scheduled_hours(r,w) - approved_absence_hours(r,w) - existing_committed_hours(r,w)`. `available=max(0,net_available)`; `overcommitted=max(0,-net_available)`. | Resource calendar/capacity, approved leave, active/approved allocations and commitments. Hours/resource/week. | A proposed weekly time-bucket convention. Prevent double-counting approved time entries and allocations. Skill supply counts only where an explicit valid skill match exists; one person's hours cannot be assigned to two simultaneous requirements in a scenario. | Missing schedule/leave/commitment state or stale source => unknown capacity for that skill/week; do not substitute 40 hours or zero. | `40 scheduled − 8 absence − 20 committed = 12 available`; if 16 hours are required, gap is `4 hours`. If committed is 45, available is `0` and over-commitment is `5 hours`. |
| **CAL-02 Required effort and capacity gap** | `required_hours(s,w)=sum(confirmed work-package estimate hours for skill s and week w)`. `gap=max(0,required-available)`; `headroom=max(0,available-required)`. | User-authored work packages; resource/skill schedule. Hours/skill/week. | Point estimates only in Must Have. Role/skill labels must map to an approved Company taxonomy. Gap is a fact, not a risk score or accept/reject threshold. | Undefined scope, missing skill/time allocation, or unknown availability blocks a capacity recommendation for affected bucket. | Required 16h vs available 12h gives gap 4h and headroom 0; required 8h vs available 12h gives gap 0 and headroom 4h. |
| **CAL-03 Approved actual effort** | `actual_hours(p,r,w)=sum(approved time_entry.hours)` for project `p`, resource `r`, work week `w`. | Approved timesheets/time entries. Hours/week. | Unapproved, rejected, or duplicated entries are displayed separately. Historical actuals do not automatically replace future estimates. | Missing approval state or source conflict => exclude from approved actual total and show issue. | Approved 6.5h + 2h = 8.5h. A separate unapproved 4h entry is not included. |
| **CAL-04 Forecast internal labor cost** | `forecast_labor_cost = sum(forecast_hours(r,w) × approved_effective_loaded_cost_rate(r,w))`. | Scenario allocation/estimate + Finance-approved effective-dated employee rate. Currency per hour × hours = currency. | Candidate direct labor cost only; rate basis, benefits/loading, effective dating, FX, and rounding need finance sign-off. Never substitute billing rate or zero. | Missing/stale/unapproved rate or currency mismatch blocks cost-based recommendation; capacity-only observations may still be shown. | `16h × USD 50/h = USD 800`, only under an approved rate definition. |
| **CAL-05 Direct expense cost** | `direct_expense_total(currency)=sum(approved project-specific direct expense amounts in that currency)`. | Expense source rows, user-attested estimate, contract/quote. Currency. | Excludes overhead, shared cost, tax, and contingency unless separately approved. A blank does not mean zero. | Unknown material direct expense => financial recommendation blocked; explicit user-attested zero is recorded as an assumption. | USD 80 travel + USD 20 license = USD 100 direct expense; it is not included in labor. |
| **CAL-06 Outsourcing estimate (Should Have)** | `quote_cost = fixed_quote_amount + sum(quoted_quantity × quoted_rate) + explicit direct quote costs`. | Supplier quote with scope, supplier, currency, issued/expiry dates and status. Currency. | Only a current/comparable quote is a sourced estimate. No supplier search, purchase, or procurement action. Unaccepted quote is an assumption, not a commitment. | Missing, expired, incomparable, or currency-mismatched quote => scenario shown incomplete, not ranked. | USD 1,100 fixed quote + USD 100 supported direct cost = USD 1,200. |
| **CAL-07 Hiring estimate (Should Have)** | `hire_cost = sum(assumed post-start forecast_hours × approved loaded rate) + explicitly approved one-time hiring/onboarding costs`. | Human-entered start/ramp date, role capacity, loaded rate, and optional approved cost. Currency/time. | Estimate only; no candidate discovery, job posting, HR workflow, or hiring action. Ramp and onboarding assumptions must be explicit and user-confirmed. | Missing start date, capacity ramp, rate, or approved one-time cost policy => incomplete hiring scenario. | Example: 80 post-start hours × USD 50/h = USD 4,000 before any separately approved onboarding amount. |
| **CAL-08 Proposed contract price** | `proposal_value(currency)=sum(valid proposal line amounts in the same currency)`. | User-entered/imported proposal/contract lines, status, effective dates. Currency. | This is proposed contract value—not earned revenue, recognized revenue, invoice, or cash. No mixed-currency sum. | Missing price or mismatched currency blocks direct contribution calculation. | USD 1,500 + USD 500 = USD 2,000 proposed value. |
| **CAL-09 Scheduled billing** | `scheduled_billing(currency,period)=sum(contractual payment milestones due in that period/currency)`. | Proposal/contract payment milestones and terms. Currency/period. | Billing schedule is not recognized revenue or a receipt. A changed date/amount is a new user-confirmed scenario assumption. | Missing/unsigned terms are labeled proposed/uncertain; no committed cash is implied. | A confirmed/proposed USD 500 milestone due in May is shown in May's schedule, not as cash received. |
| **CAL-10 Estimated direct cost, contribution, and margin** | Candidate: `direct_cost = labor + direct_expenses + applicable outsource_cost` (do not double-count work replaced by outsourcing). `estimated_direct_contribution = comparable_proposal_value - direct_cost`. `candidate_contribution_margin = contribution / proposal_value` only where price > 0. | CAL-04–08, same currency/scope/time horizon. Currency; ratio (%). | **Finance approval pending** for loaded cost, direct-cost boundary, denominator, taxes/credits, treatment of changes, sign/zero/rounding, and display label. Not GAAP/IFRS profit, recognized margin, or universal acceptability. | If formula/policy not approved, or required values incomplete, show inputs separately and `insufficient_reliable_data`; do not calculate/compare a preferred option. | Under a test-only approved fixture: price USD 1,500 − labor USD 800 − expense USD 100 = USD 600 contribution; USD 600/USD 1,500 = 40%. This is arithmetic validation, not an approved business threshold. |
| **CAL-11 Open and past-due receivable (Should Have)** | `open_invoice=max(0,invoice_amount−allocated_receipts−approved_credits)`. `past_due(currency,as_of)=sum(open_invoice where due_date<as_of)`. | Invoice, receipt allocation, approved credit, due date and as-of timestamp. Currency. | Finance approval pending for allocation/credit rules. Overpayments/unapplied receipts are separate. Not a collection-probability model or available-cash balance. | Unresolved payment allocation or stale source => mark amount uncertain and block any policy-dependent cash conclusion. | USD 1,000 invoice − USD 400 allocated receipt − USD 100 approved credit = USD 500 open. If due 1 May and as-of 8 May, USD 500 is past due. |
| **CAL-12 Receipt timing observation (Should Have)** | `receipt_timing_delta_days = actual_receipt_date − contractual_due_date`. | Matched invoice/receipt pair. Days; invoice currency preserved. | Positive = late, negative = early. No aggregate average, probability, credit score, or future prediction until a separately approved method and sufficient comparable data exist. | Unmatched/partial/unallocated receipt => show as unresolved, not a timing fact. | Due 8 May, paid 12 May => +4 days. |
| **CAL-13 Scenario delta** | `delta(metric)=scenario_metric−baseline_metric`, only for same metric version, currency, period, and scope. | Immutable baseline and scenario snapshots. Same units/currency. | A delta is not a score or preference. Negative/positive sign is shown with metric definition. | Incomparable units/currency/policy versions => display separately; no ranking. | Baseline cost USD 900 vs scenario cost USD 1,000 => delta +USD 100. |

### Decision rules

1. Validate permission, source, effective date, row reconciliation, currency, formula/policy version, and case scope before calculation.
2. Treat zero only as an explicit sourced value or attested assumption. Unknown, blank, stale, contradictory, and unauthorized are distinct states.
3. Show capacity by skill and week; do not sum away a bottleneck. Preserve original currency. Do not rank cross-currency alternatives without approved FX.
4. A preferred option can appear only where a versioned, authorized business policy defines the objective/threshold and all required evidence passes. No universal threshold is bundled.
5. Otherwise return the prescribed insufficient-data state, with blockers and no preferred option. A human can still review clearly labeled partial metrics and request more evidence.
6. The human decision is stored separately from recommendation/approval/action. Editing inputs creates a new scenario/recommendation version and invalidates any stale approval.

## 6. Roles and authorization boundaries

The role names below are MVP product roles, not a final permission catalog. One person may hold more than one role only through explicit assignment; approval identity comes from the authenticated principal, never a free-text field.

| MVP role | May do | Must not do by role alone |
|---|---|---|
| **Tenant Administrator** | Configure one Company, invite/revoke memberships, assign permitted roles, manage source/import settings, view import/audit status. | Automatically read compensation, change finance policy, approve own sensitive action, or bypass server-side permissions. |
| **Decision Manager** | Create cases; view allowed resource/financial aggregates; investigate, model scenarios, generate brief, record a human decision. | Read individual cost rates without finance permission; approve/execute a restricted action by virtue of title alone. |
| **Resource Manager** | Maintain/import resource skills, calendars, leave, allocations and approved capacity facts. | Read compensation or finance-sensitive rates unless separately granted. |
| **Finance Steward** | Validate cost/revenue/expense/receivable imports; review and approve versioned calculation definitions and finance policies; view sensitive rates. | Change historical source facts silently or approve an action outside granted scope. |
| **Data Steward** | Upload files, map/preview fields, resolve non-financial data-quality findings, and promote permitted data categories within assigned Company scope. | Read compensation data, approve finance policy, or resolve restricted financial findings without separate permission. |
| **Approver** | Approve/reject/request revision for an exact case/option/version or payload under configured policy. | Approve a different/stale version; impersonate another user; let an AI/model approve. Separation-of-duties is policy-controlled and pending owner approval. |

All API and data access must be Tenant- and Company-scoped and authorized by the backend. Database-level isolation controls are required as an outcome; PostgreSQL RLS/constraints are candidates from the technical proposal and remain subject to technical approval. Compensation data is separately restricted. No superuser, UI-only permission, or external IdP group claim grants BusinessFlow access.

## 7. Scenario set and scope decision

| Scenario | Scope | MVP behavior and reason |
|---|---|---|
| **Accept with current resources** | **Must Have** | Baseline; uses validated availability and the proposal's required skill/time plan. |
| **Accept with resource reallocation** | **Must Have** | User authors explicit changes to flexible/uncommitted allocations; system recalculates affected projects and capacity. No optimizer or silent reassignment. |
| **Phase or defer** | **Must Have** | User changes start/period/scope in a new scenario. The system recalculates weekly capacity; it does not automatically change contract terms or milestones. |
| **Decline** | **Must Have** | Records the decision option and shows the proposed work not undertaken. No invented lost-revenue/opportunity-cost value. |
| **Accept with outsourcing** | **Should Have / Conditional** | Compare only with a valid, in-scope supplier quote. No procurement, vendor search, or payment. |
| **Accept with hiring** | **Should Have / Estimate only** | Requires user-supplied start/ramp/capacity and finance-approved loaded rate. No job posting, candidate tracking, or hiring action. |

The first release can produce a useful decision without hiring or outsourcing integrations. These options remain conditional so a missing quote/ramp does not masquerade as zero cost or prevent the baseline capacity view.

## 8. Decision brief: exact manager view

The brief shall show, in this order:

1. **Executive summary:** case, decision date/owner, output state, and a preferred option only when approved rules permit it.
2. **Proposed project overview:** scope, customer/reference label, dates, work packages, required skills, proposed price/terms, and status.
3. **Data completeness:** required inputs present/missing, conflicts, authorization limits, source as-of times, and policy readiness. No opaque single quality score.
4. **Capacity analysis:** weekly required/available hours and gaps/headroom by skill; affected resources only where permitted; current-project commitments and any modeled reallocation.
5. **Financial analysis:** proposed price, estimated direct labor, direct expenses, conditional quote/hire estimate, contribution/margin only under approved formula, currency, and timing labels. Distinguish proposal value, recognized revenue, invoiced amounts, receivables, and receipts.
6. **Risks and limitations:** operational constraints, stale/conflicting inputs, missing evidence, and finance-policy limitations. No unvalidated AI risk score.
7. **Assumptions:** explicit case assumptions with author, date, source/status, and effect on the comparison.
8. **Scenario comparison:** selected alternatives, per-option results/deltas with identical scope/units/currency, and incomplete options explicitly marked.
9. **Supporting evidence:** source and row/document locator, import/mapping version, effective/as-of time, formula/policy version, and evidence type.
10. **Missing information:** blocking vs non-blocking items and who can resolve each.
11. **Recommended next steps:** specific human follow-up (e.g., confirm hours or obtain a quote), with no external action implied.
12. **Decision options:** accept, accept with conditions, phase/defer, decline; user records choice and rationale. Approval status is distinct.

## 9. Non-functional requirements

Targets explicitly labeled **proposed** need owner approval; the initial technical assessment and baseline remain unapproved.

| ID | Requirement | Provisional target / approval state |
|---|---|---|
| **NFR-001 Security/isolation** | Enforce authentication, membership, tenant/company/object scope, least privilege and sensitive-field controls. Database-enforced isolation (for example, PostgreSQL RLS and tenant-aware constraints) is a candidate defense-in-depth control, not an owner-approved technology decision. | Isolation and least-privilege outcomes are required; the database/control selection remains a technical approval gate. Test against the owner-approved target database; a SQLite-only test is insufficient. |
| **NFR-002 Identity** | Support first-party identity and OIDC/enterprise SSO, MFA, safe linking/recovery, revocable sessions, and server-side authorization. | Owner-confirmed requirement; Keycloak/Authlib and detailed policy are unapproved. |
| **NFR-003 Privacy** | Minimize personal/compensation data; expose cost rates only to Finance-authorized users; redact secrets and sensitive values from logs; no customer data or source files go to an external AI provider in the core MVP. | Any optional AI assistance requires separate privacy/provider/security approval before data leaves the customer environment; retention, deletion, residency, and support access remain owner decisions before production. |
| **NFR-004 Performance** | Validate data/calculations on a defined small-business dataset and report P50/P95. | **Proposed target pending reference-host approval:** import validation of 10,000 rows ≤60 seconds; one decision calculation for 100 resources, 12 weekly buckets, 1,000 allocations and 20 work packages ≤5 seconds p95 on the agreed host. These are planning targets, not an accuracy claim; recalibrate after host selection. |
| **NFR-005 Reliability** | Import promotion, decision snapshots, approval records, and audit writes must be transactional/durable; a failed run must not partially promote a batch or report a successful action. | No uptime/SLO target selected. Failure/retry behavior must be tested. |
| **NFR-006 Backup/recovery** | Customer must be able to back up and restore database plus imported-file metadata/blobs consistently. | RPO/RTO, backup schedule, encryption keys, and operator responsibility need owner approval; successful restore test required before release. |
| **NFR-007 Auditability** | Record actor, scope, timestamps, source/mapping/formula/policy/scenario versions, decision, approval, and action state; preserve revision history. | Append-only application behavior is required; do not claim database-level tamper-proof audit. |
| **NFR-008 Maintainability** | Keep domain calculations deterministic, versioned, tested independently, and separated from UI/LLM. Use reviewed migrations and stable source mappings. | Modular-monolith/PostgreSQL are proposals; no dependency lock is approved. |
| **NFR-009 Self-hosted/on-prem** | Core workflow shall run in a customer-controlled environment without requiring a public cloud, external BI, or hosted LLM. | On-prem-first is owner-confirmed; Linux/container/reference topology and offline/air-gap support remain open. |
| **NFR-010 Export/portability** | Users can export authorized briefs and structured case/source data without losing currency, provenance, or version metadata. | PDF/CSV output is MVP requirement; full backup/export formats and retention are open. |
| **NFR-011 AI governance** | Numeric outputs, thresholds, permissions, decisions, approvals, and actions are system-controlled and deterministic. AI cannot create facts or silently fix/import/approve/execute. | No provider/SDK/model is selected; core must work with AI disabled. |
| **NFR-012 Usability** | Optimize for an owner/manager's decision workflow, visible data quality, and comparison—not an AI chat-first UI. | Keyboard and accessibility target, localization, and usability benchmark require later owner/design-partner decisions. |

## 10. Requirement traceability map

This map traces **this provisional BRD** to business objectives, user needs, workflows, screens/features, and acceptance IDs in the companion documents. It does **not** complete `REQUIREMENTS_TRACEABILITY.md`. The exact original 24 Part 2 requirements remain blocked and are not reconstructed here.

| Requirement | Business objective | User need | Workflow | MVP screen/feature | Acceptance |
|---|---|---|---|---|---|
| BR-001 | O-04 | Admin needs the right customer/company boundary. | WF-01 | S-01 Company Setup | AC-001 |
| BR-002 | O-01 | Manager needs company calendar/currency context. | WF-01, WF-06 | S-01, S-07 | AC-001, AC-008 |
| BR-003 | O-04 | Users need scoped access without job-title privilege. | WF-02 | S-02 Users & Roles | AC-002, AC-031 |
| BR-004 | O-05 | Admin needs a practical standard file path. | WF-03 | S-03 Data Hub | AC-003 |
| BR-005 | O-05 | User needs to map and preview before import. | WF-04 | S-04 Import Mapping | AC-004 |
| BR-006 | O-05 | User needs errors surfaced before facts affect decisions. | WF-05 | S-05 QA & Reconciliation | AC-005 |
| BR-007 | O-05 | User needs duplicate/conflicting records handled safely. | WF-05 | S-05 QA & Reconciliation | AC-006 |
| BR-008 | O-05 | User needs a human resolution trail for conflicts. | WF-05 | S-05 QA & Reconciliation | AC-006, AC-007 |
| BR-009 | O-02 | Reviewer needs every metric traceable to a source. | WF-05, WF-09 | S-03, S-05, S-09 | AC-007, AC-026 |
| BR-010 | O-02 | User needs to know whether inputs are current. | WF-06, WF-08 | S-06, S-09 | AC-008 |
| BR-011 | O-01 | Resource lead needs valid skills and role data. | WF-06 | S-06 Business Context | AC-009 |
| BR-012 | O-01 | Resource lead needs time-bounded capacity/leave. | WF-06 | S-06 Business Context | AC-009, AC-015 |
| BR-013 | O-01 | Manager needs existing workload included. | WF-06, WF-09 | S-06, S-09 | AC-010, AC-015 |
| BR-014 | O-02 | Finance/ops needs approved actual effort distinguished. | WF-06, WF-09 | S-06, S-09 | AC-011 |
| BR-015 | O-02/O-04 | Finance needs secure, effective cost rates. | WF-06, WF-09 | S-07, S-09 | AC-012, AC-031 |
| BR-016 | O-01 | Manager needs a proposed work case, not an active project. | WF-07 | S-08 New Project Decision | AC-013 |
| BR-017 | O-01/O-02 | Manager needs role/skill/period effort assumptions explicit. | WF-07, WF-08 | S-08, S-09 | AC-014, AC-015 |
| BR-018 | O-02 | Finance/manager needs proposal value and terms classified correctly. | WF-07, WF-08 | S-08, S-09 | AC-013, AC-016, AC-017 |
| BR-019 | O-02 | Manager needs direct costs/quote evidence without false zero. | WF-07, WF-09 | S-08, S-09 | AC-018, AC-019 |
| BR-020 | O-01/O-02 | Manager needs weekly skill gap/headroom. | WF-09 | S-09 Decision Workspace | AC-015 |
| BR-021 | O-02 | Manager needs actual hours distinct from forecast. | WF-06, WF-09 | S-06, S-09 | AC-011 |
| BR-022 | O-02 | Finance needs loaded cost applied to correct hours/date. | WF-09 | S-09 Decision Workspace | AC-012, AC-019 |
| BR-023 | O-02 | Manager needs direct costs separated from overhead. | WF-09 | S-09 Decision Workspace | AC-018, AC-019 |
| BR-024 | O-02 | Finance/owner needs policy-governed contribution metrics. | WF-09 | S-07, S-09 | AC-019, AC-020 |
| BR-025 | O-01/O-02 | Manager needs cash/invoice facts distinguished if available. | WF-06, WF-09 | S-06, S-09 | AC-021 |
| BR-026 | O-01 | Manager needs a limited, comparable option set. | WF-10 | S-09 Decision Workspace | AC-022, AC-023 |
| BR-027 | O-02/O-03 | Reviewer needs assumptions and source/formula versions. | WF-08, WF-09, WF-11 | S-09, S-10 | AC-026 |
| BR-028 | O-01/O-03 | Manager needs a readable decision-ready brief. | WF-11 | S-10 Decision Brief | AC-024, AC-025 |
| BR-029 | O-02/O-03 | User needs explicit blockers and no false recommendation. | WF-08, WF-09, WF-11 | S-09, S-10 | AC-024 |
| BR-030 | O-03 | Human owner needs to record their choice and reasoning. | WF-12 | S-10 Decision & Approval | AC-027 |
| BR-031 | O-03 | Company needs optional policy-based approval of exact version. | WF-12 | S-07, S-10 Decision & Approval | AC-028 |
| BR-032 | O-03/O-04 | Reviewer needs an actor/version/result history. | WF-03–WF-14 | All sensitive screens | AC-029 |
| BR-033 | O-03 | Manager may need an approved internal follow-up. | WF-13 | S-10 / internal task | AC-030 |
| BR-034 | O-02/O-03 | Owner needs to compare expected vs actual later. | WF-14 | S-11 Outcomes | AC-032 |
| BR-035 | O-04/O-05 | Customer needs portable authorized records. | WF-03, WF-11, WF-14 | S-03, S-10, S-11 | AC-033 |
| BR-036 | O-02/O-03/O-04 | User needs safe assistance without model authority. | WF-04, WF-08–WF-13 | S-04, S-09, S-10 | AC-034 |
| NFR-001 | O-04 | Every user needs tenant/company isolation. | All | API/database policy | AC-031 |
| NFR-002 | O-04 | Users need secure authentication and revocable membership. | WF-01, WF-02 | S-01, S-02 | AC-002, AC-031 |
| NFR-003 | O-04 | Employee/financial data needs minimization and restricted access. | WF-03–WF-14 | S-02, S-06–S-11 | AC-012, AC-031, AC-035 |
| NFR-004 | O-01/O-05 | Owner needs usable response times on an agreed host. | WF-05, WF-09 | S-05, S-09 | AC-036 |
| NFR-005 | O-02/O-03 | User needs durable, non-partial imports/decisions. | WF-05, WF-12, WF-13 | S-05, S-10 | AC-005, AC-027, AC-030 |
| NFR-006 | O-04 | Customer needs recoverable self-hosted data. | WF-01–WF-14 | Deployment/operations | AC-037 |
| NFR-007 | O-02/O-03 | Reviewers need versioned logic and durable audit. | WF-09–WF-14 | S-09–S-11 | AC-026, AC-029 |
| NFR-008 | O-02/O-05 | Product team needs maintainable, testable changes. | WF-03–WF-14 | Service/version contracts | AC-038 |
| NFR-009 | O-04 | Customer needs operation in its own environment. | All | Deployment profile | AC-037 |
| NFR-010 | O-04/O-05 | Customer needs to export its data and briefs. | WF-11, WF-14 | S-10, S-11 | AC-033 |
| NFR-011 | O-02/O-03 | Customers need deterministic decisions with safe AI boundaries. | WF-04, WF-08–WF-13 | S-04, S-09, S-10 | AC-034 |
| NFR-012 | O-01/O-03 | Owner/manager needs a usable, non-chat-first workflow. | WF-01–WF-14 | S-01–S-11 | AC-039 |

## 11. Approval and traceability limits

This BRD is actionable as a provisional requirements baseline for owner review. It is **not** approval to implement the technical baseline, start Milestone 0, install dependencies, create migrations, connect customer data, or conduct vendor trials. The exact original 24 Part 2 requirements remain **BLOCKED** in `REQUIREMENTS_TRACEABILITY.md`; the BR/NFR map above is separate and must not be substituted for them.

Before a recommendation behavior is accepted, a finance owner must approve formula definitions, loaded cost basis, direct-cost boundaries, revenue/margin semantics, FX policy, data freshness, cash rules, and any decision/approval thresholds. The owner must separately review the technical baseline, identity, deployment, security, and traceability gates in the existing documentation.
