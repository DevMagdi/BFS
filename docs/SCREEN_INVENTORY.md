# BusinessFlow AI — MVP Screen Inventory and Navigation

- **Status:** Historical UI requirements for the former project-feasibility hypothesis, superseded as current product direction by the M9 assessment in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md). They do not describe a shipped frontend or authorize one.
- **Cross-references:** Requirements and permissions in [`BRD.md`](BRD.md); end-to-end use in [`USER_WORKFLOWS.md`](USER_WORKFLOWS.md); pass/fail behavior in [`MVP_ACCEPTANCE_CRITERIA.md`](MVP_ACCEPTANCE_CRITERIA.md).
- **UX rule:** Owner/manager-first, decision-centered navigation. Use tables, clear status labels, and comparison views. No AI-chat-first home screen; chat is not required for MVP.

## Navigation map

```text
Company Setup
├── Users & Roles
├── Data Hub
│   ├── Import Mapping & Preview
│   └── Data Quality & Reconciliation
├── Business Context
├── Finance & Decision Policies (restricted)
├── Decision Center
│   ├── New Project Decision
│   ├── Decision Workspace (quality / capacity / finance / scenarios / evidence)
│   └── Decision Brief & Approval
└── Outcomes
```

Only show navigation/actions the authenticated user is authorized to use. Financial-rate fields and cost details are separately permission-checked; hiding a menu is not authorization.

## Shared screen-state contract

- **Loading:** For asynchronous fetch, preview, calculation, promotion, or export, show the operation in progress and disable duplicate submissions where needed; never present placeholder data as current.
- **Empty:** Use each screen's specific empty state and a clear next action; do not seed customer-looking demo data.
- **Error:** Show a recoverable, non-sensitive message and correlation/reference ID where available; preserve safe unsaved input and never imply a failed/partial write succeeded.
- **Denied:** Show `not_authorized` or an equivalent generic denial without confirming protected record existence. The server enforces access even when navigation is hidden.
- **Stale/abstention:** Label stale snapshots and show `Insufficient reliable data to support a recommendation.` when the decision contract requires it; do not show a preferred option.

## S-01 — Company Setup

- **Purpose:** Create the Tenant's single MVP Company and set operating context.
- **Primary user:** Tenant Administrator.
- **Main content:** Company identity, time zone, base currency, working calendar, locale/language, one-Company constraint, setup checklist.
- **Actions:** Create/edit permitted profile fields; select calendar; save; see import/setup requirements.
- **Required fields:** Company name, time zone, ISO currency, workweek/calendar reference.
- **Validation:** Required values; valid timezone/currency; one Company per Tenant; edits produce audit/version metadata.
- **Empty state:** “Set up your Company to start importing business data.”
- **Error state:** Invalid/missing field, duplicate Company, or unauthorized edit; preserve unsaved data where safe and do not expose another Tenant.
- **Related:** BR-001–002; WF-01; AC-001.

## S-02 — Users & Roles

- **Purpose:** Configure Tenant membership and minimum access roles.
- **Primary user:** Tenant Administrator.
- **Main content:** Active/invited/revoked users, assigned Data Steward/Decision Manager/Resource Manager/Finance Steward/Approver roles and scopes, employee link when needed, audit of role changes.
- **Actions:** Invite, activate/revoke membership, assign/remove a role, inspect effective permissions.
- **Required fields:** Authenticated user identity; Tenant membership; role; scope when applicable.
- **Validation:** No unverified identity; valid Tenant/Company scope; no role assignment from job title/email; cannot grant cost-rate access without explicit permission.
- **Empty state:** “Invite the people who prepare, validate, and approve project decisions.”
- **Error state:** Invalid invitation, inactive member, duplicate assignment, forbidden permission, or identity-provider failure; no access is granted on partial failure.
- **Related:** BR-003; NFR-001–002; WF-02; AC-002, AC-031.

## S-03 — Data Hub

- **Purpose:** Start imports and review source/batch history.
- **Primary user:** Tenant Administrator or authorized data steward.
- **Main content:** Downloadable template versions; file-only source labels; supported sheets; recent batches, as-of dates, status/counts; no live connector catalog.
- **Actions:** Download template; upload CSV/XLSX; select Company/source/as-of date; reopen a batch; view lineage/status; export authorized source records or issue summaries.
- **Required fields:** Company scope, accepted file, source label/type, as-of/effective date where applicable.
- **Validation:** Only approved `.csv` or `.xlsx`, provisionally ≤25 MiB per file; block unsupported/macro-enabled formats and oversize files before parsing; do not run formulas/macros; check user scope. The size limit is pending owner/technical confirmation.
- **Empty state:** Show template and setup checklist; no fabricated sample customer data.
- **Error state:** Invalid type/size, corrupt workbook, unauthorized Company, duplicate batch, storage failure; canonical data stays unchanged.
- **Related:** BR-004, BR-009; WF-03; AC-003, AC-007.

## S-04 — Import Mapping & Preview

- **Purpose:** Confirm how uploaded columns map to canonical facts before validation/promotion.
- **Primary user:** Data steward; Finance Steward for financial sheets.
- **Main content:** Source columns, canonical fields, sample rows, inferred data types, required-field indicator, unit/currency/date selection, mapping version.
- **Actions:** Map/unmap fields; select date/currency conventions; preview; save new mapping version; return to batch.
- **Required fields:** All required canonical fields for the chosen sheet; explicit Company and source context.
- **Validation:** No unresolved required columns; data types compatible; no formulas/code execution; mapping suggestions are untrusted until confirmed; original mapping remains immutable.
- **Empty state:** Explain required template columns and provide a sample-free template.
- **Error state:** Ambiguous mapping, missing required field, mixed currency/date format, or unsupported cell value; prevent confirmation and point to row/column.
- **Related:** BR-005–006, BR-036; WF-04; AC-004, AC-034.

## S-05 — Data Quality & Reconciliation

- **Purpose:** Resolve import errors and decide which source facts can be promoted.
- **Primary user:** Data steward; Finance Steward/Resource Manager for owned records.
- **Main content:** Valid/warning/blocking counts; row-level issues; exact duplicates; same-ID conflicts; existing canonical value/source; resolution history; batch promotion status.
- **Actions:** Filter issues; view authorized source row; resolve/keep/reject/defer; attest a true zero or assumption; promote accepted records; export an issue list if authorized.
- **Required fields:** A validated mapping and authorized resolution; reason for any manual resolution/attestation.
- **Validation:** Critical conflicts block dependent metrics; exact duplicates are idempotent; no silent merge or overwrite; promotion is atomic and audited.
- **Empty state:** “No blocking findings”; show what was checked and batch freshness.
- **Error state:** Reconciliation conflict, stale source, invalid reference, or promotion failure; original records remain intact and no partial promotion occurs.
- **Related:** BR-006–010; WF-05, WF-08; AC-005–008.

## S-06 — Business Context

- **Purpose:** Inspect the Company facts that inform capacity and project feasibility.
- **Primary user:** Resource Manager; Decision Manager sees permitted aggregates; Finance Steward sees financial facts separately.
- **Main content:** Tabs for Resources & Skills, Availability/Absence, Current Projects & Allocations, Commitments, Approved Time, Direct Expenses. Each row shows source, effective period, as-of, status, and issue badge.
- **Actions:** Review/import facts; resolve missing skills/periods; inspect project allocations; request refresh from data owner; open the related import batch.
- **Required fields:** Resource/project IDs; skill and effective dates; capacity hours/calendar; allocation/commitment status; approved time state; currency/date for expense.
- **Validation:** Same-Company links; nonnegative hours; no double count; approved hours distinct from forecast; permission checks on compensation fields.
- **Empty state:** Guide to applicable import templates; clearly state that capacity cannot be assessed until required data exists.
- **Error state:** Missing/stale/conflicting resource or project fact; show affected weeks/skills and prevent a false total.
- **Related:** BR-010–014, BR-019, BR-025; WF-06; AC-008–011, AC-018, AC-021.

## S-07 — Finance & Decision Policies

- **Purpose:** Review sensitive finance inputs and activate only explicitly approved formula/policy versions.
- **Primary user:** Finance Steward; Tenant Administrator manages access only.
- **Main content:** Effective loaded-cost basis, currencies, contribution definition, expense boundary, freshness policy, FX method (if any), approval rules, definition version, status (draft/reviewed/approved/superseded), approver and effective date.
- **Actions:** Finance Steward reviews/approves a predefined finance definition; an authorized Tenant Administrator/Approver maintains company freshness/decision settings within granted policy. Inspect cost-rate imports. No executable formula editor.
- **Required fields:** Finance owner; version/effective date; approved scope; source and rounding/currency semantics; signed decision/rationale.
- **Validation:** Only an authenticated Finance-authorized person may approve; no activation without required definitions and example review; changes create a new version; no universal margin/risk threshold is preloaded.
- **Empty state:** “Financial recommendation policy is not approved; project analysis may show observations but cannot provide a financial preference.”
- **Error state:** Missing policy field, invalid rate interval, unauthorized reader, overlapping effective rates, or unapproved FX; dependent recommendation abstains.
- **Related:** BR-015, BR-024–025, BR-031; WF-06, WF-09, WF-12; AC-012, AC-019–021, AC-028, AC-031.

## S-08 — New Project Decision

- **Purpose:** Capture the proposed project and the question the manager wants answered.
- **Primary user:** Decision Manager.
- **Main content:** Case summary, customer/reference label, decision owner/date, dates, work packages, required skill/week/hours, price/currency/terms/milestones, direct expense rows, assumptions and evidence links.
- **Actions:** Save draft; add/edit a work package; attach a source reference; validate case; open Decision Workspace.
- **Required fields:** Case title/question, requester/owner, decision date, scope summary, proposed start/delivery window, at least one work package with skill/time/effort for capacity analysis; price/currency for contribution analysis; source/assumption status.
- **Validation:** Date order; Company taxonomy; explicit source vs assumption; same-currency comparisons; unknown fields remain unknown. Draft may be saved incomplete but cannot yield a preferred option.
- **Empty state:** “No project decisions yet”; offer a new case and explain required evidence.
- **Error state:** Invalid scope/date/skill/currency/permission; show field-level message and retain safe draft state.
- **Related:** BR-016–019; WF-07; AC-013–014, AC-016, AC-018.

## S-09 — Decision Workspace

- **Purpose:** Investigate evidence, calculate observations, compare scenarios, and resolve blockers.
- **Primary user:** Decision Manager; Resource Manager/Finance Steward resolve assigned blockers.
- **Main content:** Tabs for Overview, Data Quality, Capacity, Financials, Scenarios, Evidence. Show output state, input snapshot, completeness/freshness, weekly capacity by skill, actual vs forecast, allowed costs/pricing, assumptions, deltas and source lineage.
- **Actions:** Refresh/re-import through Data Hub; request missing information; confirm a case assumption; create a new scenario version; calculate; inspect evidence; open brief.
- **Required fields:** Valid case; source snapshot; policy state; scenario inputs; user permission.
- **Validation:** Every figure carries unit/currency/period/source; scenario is immutable; calculation only uses allowed rows/formula versions; no comparison across incompatible periods/currencies/policies.
- **Empty state:** Explain what data is missing and provide the exact import/intake path; never show sample customer data as live.
- **Error state:** `insufficient_reliable_data` with blockers and no preferred option; `not_authorized` without protected-record details; calculation failure leaves previous version intact and labels it stale.
- **Related:** BR-020–029; WF-08–WF-10; AC-008, AC-011–026.

## S-10 — Decision Brief & Approval

- **Purpose:** Present the report and record the human decision/approval.
- **Primary user:** Decision Manager and authorized Approver.
- **Main content:** The 12 BRD sections: executive summary; proposed project overview; data completeness; capacity; financial analysis; risks/limitations; assumptions; scenario comparison; evidence; missing information; recommended next steps; decision options; plus approval history.
- **Actions:** Print/export PDF; export permitted CSV; record accept/conditional accept/phase-defer/decline with rationale; submit/review approval; request revision. Optional approved task is exposed only if included and permitted.
- **Required fields:** Exact case/scenario/recommendation version; chosen option; human rationale; conditions; approver/policy if required.
- **Validation:** No preferred option unless policies pass; a human decision under abstention is clearly labeled and leaves the system state unchanged; approvals bind immutable version/payload; stale case invalidates approval; output exactly distinguishes recommendation, human choice, approval, action, and verification.
- **Empty state:** No brief until a case is analyzed; guide to finish data intake.
- **Error state:** Insufficient-data brief with exact phrase; denied export/approval; stale approval; failed export without changing decision state.
- **Related:** BR-027–033, BR-035; WF-11–WF-13; AC-024–030, AC-033.

## S-11 — Outcomes

- **Purpose:** Record later actuals and compare them with the original decision snapshot.
- **Primary user:** Decision Manager; Finance/Resource Stewards validate actual inputs.
- **Main content:** Original option/version, expected facts, actual dates/hours/cost/revenue/receipts when available, source and observed variance, unresolved items.
- **Actions:** Add actual observation; import or link a validated source fact; mark unavailable; export authorized report.
- **Required fields:** Decision case, original scenario/formula version, actual measure/period/source, observer and timestamp.
- **Validation:** Actual and expected are distinct; changed formula gets a new version; no causal ROI text without a method.
- **Empty state:** “No outcomes recorded”; show follow-up timing chosen by user/company.
- **Error state:** Missing/invalid source, inaccessible financial value, or incomparable currency/period; do not overwrite original recommendation.
- **Related:** BR-034–035; WF-14; AC-032–033.
