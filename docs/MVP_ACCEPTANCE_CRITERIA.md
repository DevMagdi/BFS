# BusinessFlow AI — Provisional MVP Acceptance Criteria

- **Status:** Historical testable requirements for the former project-feasibility MVP, superseded as current product scope by the M9 assessment in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md). These criteria do not describe the whole M2–M8 implementation and do not authorize future work.
- **Source:** [`BRD.md`](BRD.md). Workflows are in [`USER_WORKFLOWS.md`](USER_WORKFLOWS.md); screen behavior is in [`SCREEN_INVENTORY.md`](SCREEN_INVENTORY.md); priorities are in [`MVP_SCOPE.md`](MVP_SCOPE.md).
- **Test data:** Use synthetic/de-identified fixtures. Production formulas remain blocked until the Finance owner approves definitions. Test-only policy fixtures validate arithmetic but do not set a company-wide threshold.

## Acceptance rules

- A feature is not accepted because a screen exists; its business rules, permissions, source/version behavior, failures, and acceptance cases must pass.
- No universal accuracy, margin, risk, utilization, or acceptance threshold is introduced.
- Numeric examples below test arithmetic only. If a formula is not Finance-approved, the product must not use it to issue a financial recommendation.
- Tenant isolation, migrations, and recovery must be tested against the owner-approved target database; SQLite-only testing is insufficient for a real production database. If PostgreSQL/RLS is selected after technical approval, exercise the RLS and role/pooling controls described in AC-031.

## Functional acceptance criteria

### Company, roles, and source data

**AC-001 — Tenant/Company setup**
- Given an authorized Tenant Administrator, when they create a Company with a name, ISO currency, time zone, and calendar reference, then the Company is linked to that Tenant and setup status is visible.
- A second Company for the same Tenant is rejected. Mismatched Tenant/Company identifiers are rejected without exposing another Tenant's data.

**AC-002 — Membership and least-privilege roles**
- An inactive/unverified user or user without active Tenant membership cannot read or write Company data.
- A Decision Manager can create/review cases but cannot see individual loaded cost rates without explicit finance permission.
- A Resource Manager can manage authorized capacity facts without receiving compensation access by default.
- Actor/approver identity is derived from the authenticated principal; a request-supplied `by` value cannot change it.

**AC-003 — Template and file acceptance**
- The user can download the current versioned XLSX/CSV templates and upload supported `.xlsx` and `.csv` files with a Company/source/as-of label.
- Unsupported file types, macro-enabled files, corrupt workbooks, and files larger than the provisional 25 MiB per-file limit are rejected before canonical data changes; the limit remains subject to owner/technical confirmation.
- No workbook formula, macro, or embedded code is executed.

**AC-004 — Mapping preview**
- Before promotion, the user sees source columns, canonical target fields, data types, sample rows, and required-field status, and confirms a mapping version.
- Missing required fields or ambiguous mapping prevents confirmation. A suggested mapping is never silently activated.

**AC-005 — Validation and atomic promotion**
- Invalid type/date/currency/reference/status rows show row/field-specific errors. Invalid or unresolved decision-critical rows cannot be promoted.
- If promotion fails partway, no partial batch becomes canonical; the import remains failed/retryable with preserved raw input.
- Blank values remain unknown; the system never silently turns a blank cost, effort, price, or expense into zero.

**AC-006 — Duplicate detection and conflict handling**
- Re-importing the same source ID/value or exact file is idempotent and does not duplicate canonical facts.
- Reusing a source ID with conflicting values creates a reconciliation finding; neither value is silently overwritten.
- Fuzzy matches do not merge employee, project, customer, or supplier records automatically.

**AC-007 — Reconciliation and lineage**
- A user resolution records actor, reason, timestamp, source row, and resulting canonical version.
- For every metric shown in a decision brief, a reviewer can navigate to the input source/import row or see that it was a user-entered assumption, plus mapping/formula/policy versions.
- Original source content/hash and prior mapping/reconciliation versions remain traceable after correction.

**AC-008 — Freshness and completeness**
- The workspace displays source/as-of/effective times and per-input freshness/completeness state; it does not hide issues inside an unexplained aggregate score.
- If a freshness rule is absent for a decision-critical input, the system requires an explicit case-level confirmation or abstains; it does not invent a default age threshold.

### Business context, intake, and calculations

**AC-009 — Resources, skills, availability, and absences**
- The system displays resource, Company skill, capacity, approved absence, and effective period with source status.
- Unknown skill/availability is not inferred from a job title or filled with standard hours. The same resource hours cannot be counted for two simultaneous skill requirements in one scenario.

**AC-010 — Current projects and commitments**
- Active/approved allocations and commitments reduce availability in the correct skill/week.
- Opportunity-only or unapproved placeholders are visible with status and do not become committed work unless the authorized user confirms the scenario assumption.
- The same work is not double-counted as both an allocation and approved timesheet actual.

**AC-011 — Approved timesheet actuals**
- Given approved entries of 6.5h and 2.0h for the same project/resource/period, actual effort is 8.5h.
- A separate unapproved 4.0h entry is shown separately and excluded from approved actuals.
- Actuals are not substituted for forecasts in future periods.

**AC-012 — Loaded cost rate protection**
- A cost rate is applied only when effective on the forecast/work date, currency and Finance-approved cost-basis version match, and the requester has permission.
- Missing or unauthorized cost rates cannot be replaced with a billing rate, zero, or an AI estimate. Denied users see no protected rate value.

**AC-013 — Proposed project case**
- The Decision Manager can create a draft case with owner, decision date, scope, project window, and proposed status, without making it an accepted/active project.
- A missing scope or permission error is shown; a partial draft may be saved but cannot yield a preferred option.

**AC-014 — Work packages and estimates**
- Each capacity-analysis work package requires an explicit skill/role, estimated hours, weekly timing, estimate source/version, and status.
- Incomplete estimates remain visibly incomplete. The MVP does not infer hours from narrative or historical averages.

**AC-015 — Capacity arithmetic and over-commitment**
- Test fixture: 40 scheduled hours − 8 approved absence hours − 20 committed hours = 12 net available hours in the specified resource/week.
- For 16 required hours in a matching skill/week, the system shows 4h gap, 0h headroom. For 8 required hours, it shows 0h gap and 4h headroom.
- If committed hours exceed scheduled less absence (for example, 45h committed against 40h schedule and no absence), show 0 available and 5h over-committed—not negative “free” capacity.
- Results are by skill/week; no universal utilization or feasibility threshold is applied.

**AC-016 — Proposal value and terms**
- Proposal/contract line values are summed only within the same currency and matching status/scope. Proposal value is labeled as proposed contract value, not recognized revenue, invoice, or cash.
- Payment milestones are dated schedule facts, with status/assumption shown.

**AC-017 — Currency isolation**
- USD and EUR values are not added, compared as a single contribution, or ranked when no approved dated FX source/policy exists.
- The brief shows the original amounts/currencies separately and the blocker. No default base-currency conversion occurs.

**AC-018 — Direct expense facts**
- Given USD 80 travel and USD 20 project license explicitly marked direct, the direct-expense observation is USD 100.
- A blank expense sheet does not mean zero expense. Zero requires a sourced or explicitly attested statement; overhead/tax is excluded unless Finance policy says otherwise.

**AC-019 — Labor, outsourcing, and direct-cost arithmetic**
- Under a test-only approved cost fixture, 16 forecast hours × USD 50/hour = USD 800 labor cost.
- With a valid USD 1,100 supplier quote plus USD 100 explicitly supported direct quote cost, outsourcing cost = USD 1,200.
- Outsourced work is not simultaneously counted as internal labor for the same package. Invalid/expired quote or missing hire ramp/rate leaves that conditional scenario incomplete.

**AC-020 — Contribution and margin policy gate**
- Under a test-only Finance-approved fixture: proposed price USD 1,500 − USD 800 direct labor − USD 100 direct expense = USD 600 candidate direct contribution; USD 600 / USD 1,500 = 40% candidate margin.
- If no production Finance approval exists for loaded rate, cost boundary, denominator, currency, or formula, the product does not present this as a financial recommendation or accounting margin. The test fixture does not become a default threshold.

**AC-021 — Optional cash observations**
- Under a test-only approved invoice-allocation fixture: USD 1,000 invoice − USD 400 allocated receipts − USD 100 approved credit = USD 500 open receivable.
- If due 1 May and as-of 8 May, the USD 500 is shown as past due. A receipt on 12 May against a 8 May due date is `+4 days` late.
- Scheduled billing, recognized revenue, invoice, receivable, receipt, and cash position remain separate. No cash-risk score or payment probability is generated.

### Scenarios, report, and decision control

**AC-022 — Core scenario comparison**
- Given a validated baseline, the user can create current-resource, manual reallocation, phase/defer, and decline options.
- Every option has an immutable version and points to its input/formula/policy snapshot. Reallocation shows affected existing projects/weeks; phase/defer changes only explicit case assumptions; decline invents no lost opportunity value.
- Incomparable scenario inputs/currency/policy versions are marked not comparable rather than ranked.

**AC-023 — Conditional hire/outsource scenarios**
- If implemented as Should Have, outsourcing requires a valid, scope-comparable supplier quote; hiring requires explicit start/ramp/capacity, effective rate, and any approved one-time cost.
- Missing evidence produces an incomplete option, not a zero-cost scenario. No purchasing, recruiting, or external-system action is available.
- Omission of these Should Have options does not fail the core MVP acceptance test.

**AC-024 — Abstention behavior**
- If a capacity, cost, price, approved formula/policy, freshness condition, reconciliation, or FX rule required for the selected analysis is missing/invalid, the brief states exactly **“Insufficient reliable data to support a recommendation.”** A metric that is not needed for the selected question does not by itself block an explicitly narrower analysis.
- It lists blockers, affected metric/option, source/as-of if known, and what would resolve each; it displays no preferred option.
- Authorized partial metrics may appear only as labeled observations. A user lacking access receives `not_authorized` without disclosure of protected record existence.

**AC-025 — Decision brief content**
- The brief contains all 12 sections in `BRD.md` §8: executive summary; proposed project; data completeness; capacity; financial analysis; risks/limitations; assumptions; scenario comparison; evidence; missing information; recommended next steps; decision options.
- The report distinguishes system recommendation state, human choice, approval, action, verification, and outcome. A successful export or calculation is not labeled as approval.

**AC-026 — Evidence and assumptions**
- Every material number/claim has source/evidence type, locator or assumption author, as-of/effective date, unit/currency, formula/policy version, and limitation where relevant.
- Changing an assumption or input creates a new scenario/recommendation version and marks any old approval stale.

**AC-027 — Human decision record**
- An authorized manager can record accept, accept with conditions, phase/defer, or decline against an exact option/version, with actor, timestamp, rationale, and conditions.
- When the system abstains, the manager may record a clearly labeled human decision under uncertainty/override with rationale; the system remains `insufficient_reliable_data` and shows no preferred option.
- The stored decision is distinct from calculated recommendation and can be revised only by a new audited record/version.

**AC-028 — Approval controls**
- When an approval policy applies, approval binds to the exact unexpired case/option/version or payload hash, records authenticated approver/time/reason/policy version, and rejects a stale/changed payload.
- Separation-of-duties is enforced where configured. A model/service principal cannot approve itself or invent the human actor.

**AC-029 — Audit history**
- Import/mapping/reconciliation, sensitive data changes, calculations, scenario revisions, decisions, approvals, exports, and actions produce actor/scope/time/result/version audit records.
- Logs contain no passwords, bearer/session tokens, recovery artifacts, or unnecessary compensation/PII. Revisions do not overwrite earlier decision evidence.

**AC-030 — Optional approved internal action**
- If the Should Have task feature is released, it creates only an internal follow-up task after separate human approval of exact text/assignee/due date/decision link; retries do not create duplicates; a fresh read-back verifies the saved state.
- If the feature is deferred, a manager can still use the brief's next-step text; the core MVP remains acceptable.

**AC-031 — Tenant/company/security boundary**
- Backend authorization and Company/object scope checks are tested against the owner-approved target database, including same-scope foreign keys, identity/tenant context, pooled connection reuse, and background jobs where supported.
- Cross-tenant/company reads/writes/deletes fail closed. A real integration test against the selected production database is required; SQLite-only tests are not accepted.
- If the owner-approved technical baseline selects PostgreSQL with RLS, also test tenant-aware foreign keys, a non-owner/non-BYPASSRLS runtime role, transaction-local tenant context, RLS policies, connection reuse, and background jobs. Those controls are conditional on technical approval, not already approved by this BRD.

**AC-032 — Outcome tracking**
- If the Should Have outcome screen is released, it links actual inputs and variances to the original scenario/formula version; missing values remain missing; formula changes create a new record; no causal ROI is asserted.

**AC-033 — Export and permissions**
- An authorized user can export a printable/PDF brief and permitted CSV case/source data with currency, source, and version metadata.
- Export respects field/object permissions and does not include credentials, hidden rates, or unauthorized Company facts. Export does not mutate case state.

**AC-034 — AI independence and boundaries**
- With every model/provider feature disabled, users can import data, calculate, compare scenarios, make/approve a decision, and export the brief.
- If AI assistance is added later, it cannot write canonical facts, change formulas/permissions, supply authoritative numbers, select/approve/execute a decision, or bypass evaluation/consent gates.

**AC-035 — Privacy and sensitive data**
- Cost-rate fields are inaccessible to unauthorized roles at API/service and database layers; logs and exports redact restricted fields; uploaded source files are not sent to a model/provider.
- Retention/deletion behavior and operator responsibilities are documented and approved before production release.

### Non-functional and end-to-end acceptance

**AC-036 — Provisional performance target**
- On an owner-approved reference host, measure import validation for 10,000 rows and a scenario with 100 resources, 12 weekly buckets, 1,000 allocations, and 20 work packages.
- Proposed targets are ≤60s for validation and ≤5s p95 for a scenario calculation. These are provisional, pending hardware/owner approval; no model/calculation accuracy percentage is invented.

**AC-037 — Self-hosting, backup, and restore**
- Core functions without a public cloud or external LLM. A single owner-approved reference deployment installs from documented artifacts; the selected database and file metadata/blob backups restore consistently in an isolated environment.
- RPO/RTO and support/operations owner must be approved before release; no uptime/restore claim is made before a successful drill.

**AC-038 — Maintainability and versioning**
- Every promoted import references a mapping version; every calculation references an immutable formula/policy version; every option references a scenario version. Financial calculation tests use exact-decimal golden examples. No user/model-supplied executable formula or SQL is run.

**AC-039 — Owner/manager usability**
- A manager can find cases, identify missing data, inspect capacity/financial/scenario evidence, record a human decision, and export without using a chat interface.
- Each screen has explicit empty/loading/error/denied states; hidden menu items are not a substitute for server authorization.

**AC-040 — End-to-end core workflow**
- Using synthetic CSV/XLSX fixtures and a test-only approved formula policy: create Tenant/Company and roles; import resource/skill/availability/commitment/time/cost/proposal data; review mapping/validation/reconciliation; create a proposed project; calculate capacity and direct economics; compare the four core options; produce a traceable brief; record a human decision and, where policy requires, approval; and export. Internal follow-up and later outcome entry are optional extensions, not core acceptance gates.
- With one required cost rate removed or a critical source conflict added, repeat the analysis and verify the exact abstention phrase, named blockers, and absence of a preferred option; also verify that any human override remains visibly separate. No real customer dataset is required for the acceptance fixture.

## Provisional implementation gate

These criteria define acceptance for a future build; they do not authorize one. Before build starts, the owner must separately approve product scope and technical baseline; Finance must approve production formula/policy versions; security/deployment decisions must be resolved; and the exact original Part 2 requirements must still be supplied for the separate blocked traceability exercise. Do not start Milestone 0 under this document.
