# BusinessFlow AI — MVP User and System Workflows

- **Status:** Provisional workflow specification; no implementation authorization.
- **Requirements:** See [`BRD.md`](BRD.md) for BR/NFR IDs and policies. This document describes the user's and system's end-to-end behavior; it does not duplicate the full requirement text.
- **Navigation:** `Company Setup → Users & Roles → Data Hub → Import Review → Business Context / Finance Policy → New Decision → Decision Workspace → Decision Brief → Decision & Approval → (optional) Internal Follow-up → Outcomes`.
- **Data boundary:** Initial ingestion is user-uploaded CSV/XLSX only. There are no live connectors, external writes, or AI-generated facts in this workflow.

## WF-01 — Company onboarding and configuration

- **User:** Tenant Administrator.
- **User does:** Creates the Company and sets name, time zone, base currency, working calendar reference, and business/project terminology needed for imports.
- **System does:** Creates a Tenant-scoped Company; enforces strict 1:1 cardinality; initializes default empty source, decision, and role spaces; records who created/changed settings.
- **Inputs:** Company name; time zone; ISO base currency; calendar/workweek settings; locale/language if enabled.
- **Outputs:** Company profile and setup checklist; empty-state links to invite users and import templates.
- **Failure/exception:** Missing time zone/currency or invalid calendar blocks company activation. Existing Company causes a safe conflict; the system does not create another Company or show another Tenant's data.
- **Trace:** BR-001–002; S-01; AC-001.

## WF-02 — User access and minimum roles

- **User:** Tenant Administrator; invited Data Steward, Decision Manager, Resource Manager, Finance Steward, and Approver.
- **User does:** Invites/activates members and assigns explicit scopes/roles. A business job role is separately assigned to an employee/resource.
- **System does:** Resolves authentication to active Tenant membership, checks permission per request, and hides/rejects sensitive cost-rate access without Finance permission. No role is inferred from a title or email domain.
- **Inputs:** Authenticated identity, membership status, role/scope assignments; existing first-party/OIDC policy (provider remains unapproved).
- **Outputs:** Active access with role-specific navigation; audit event for role changes.
- **Failure/exception:** Unverified/inactive user, missing membership, invalid scope, or revoked role returns `not_authorized`; it does not disclose whether protected records exist. A user may hold multiple roles only through explicit assignment.
- **Trace:** BR-003; NFR-001–002; S-02; AC-002, AC-031.

## WF-03 — Select a data source and upload a template

- **User:** Admin or authorized data steward.
- **User does:** Downloads the versioned workbook/CSV template, selects applicable sheets, enters source/as-of information, and uploads CSV/XLSX files. An authorized data steward may also export permitted source rows/data with provenance metadata.
- **System does:** Checks accepted file type/size (provisionally ≤25 MiB per file), checksum, template version, workbook sheet names, and upload authorization; creates an import batch and stores immutable source metadata. It does not execute workbook formulas or macros. The size bound remains pending owner/technical confirmation.
- **Inputs:** Versioned template; CSV or XLSX; source name/type; observed/as-of date; Company scope.
- **Outputs:** Import-batch ID, file/hash metadata, sheet/row inventory, next-step mapping link; authorized source-data export when requested.
- **Failure/exception:** Unsupported file, missing required template sheet, malformed workbook, invalid size, or upload permission error leaves canonical facts unchanged and gives a safe error. Failed attempts are recorded without logging file secrets.
- **Trace:** BR-004, BR-009, BR-035; S-03; AC-003, AC-007, AC-033.

## WF-04 — Map columns and preview rows

- **User:** Data steward.
- **User does:** Confirms or edits a mapping from uploaded columns to allowed canonical fields; reviews sample rows, types, dates, units, currencies, and scope.
- **System does:** Suggests only deterministic header matches or previously approved mappings; shows any suggestion as untrusted until confirmed; validates types and prevents arbitrary formulas/SQL/code. Saves a new immutable mapping version on confirmation.
- **Inputs:** File columns, canonical schema, mapping version, Company, timezone/currency assumptions.
- **Outputs:** Preview, mapping version, preliminary row/field warnings and errors.
- **Failure/exception:** Missing required fields, ambiguous map, unsupported type, or multiple possible matches prevents confirmation. No silent column inference or overwrite of an existing mapping version.
- **Trace:** BR-005–006, BR-036; S-04; AC-004, AC-034.

## WF-05 — Validate, detect duplicates, reconcile, and promote

- **User:** Data steward; Finance Steward for financial rows; Resource Manager for capacity rows.
- **User does:** Reviews valid/warning/blocking counts, row errors, duplicate keys, source conflicts, and canonical matches; resolves, rejects, or defers conflicts; explicitly confirms valid records for promotion.
- **System does:** Applies schema/range/reference checks; exact source-ID/hash deduplication; creates reconciliation findings; preserves raw rows; atomically promotes only accepted validated rows; versions corrections. Critical unresolved findings remain blockers.
- **Inputs:** Confirmed mapping, source IDs, canonical entities, approved timesheet status, currency and effective dates.
- **Outputs:** Import state/counts; reconciled canonical facts; data lineage and issue list; batch history.
- **Failure/exception:** An invalid or critical-conflicted row remains quarantined. A re-import with identical IDs is idempotent. A same-ID/different-value record is not overwritten; it becomes a finding. A failure during promotion leaves no partial canonical batch.
- **Trace:** BR-006–009; NFR-005; S-05; AC-005–007.

## WF-06 — Build and refresh business context

- **User:** Resource Manager, Finance Steward, or data steward according to sensitivity.
- **User does:** Reviews employees/resources, skills, scheduled capacity, approved absences, active projects, allocations/commitments, approved timesheets, rates, direct expenses, contract terms, and timestamps. An authorized Company owner/admin confirms freshness rules; the Finance Steward separately reviews/approves finance definitions.
- **System does:** Displays data by Company, effective period, status, source, and permissions; distinguishes current allocations from timesheets and opportunity placeholders; flags missing/conflicting sources; does not infer a skill, rate, or zero cost.
- **Inputs:** Promoted import rows; company calendar; finance-approved loaded rate/cost definitions; data freshness policy; user resolution/attestation.
- **Outputs:** A usable company context, data-as-of labels, policy readiness status, missing-input checklist.
- **Failure/exception:** Stale/conflicting/unauthorized source facts are visibly excluded or marked; no recommendation uses them. If the freshness or costing policy is unapproved, the case remains partial/blocked.
- **Trace:** BR-010–015, BR-025; NFR-003; S-06–S-07; AC-008–012, AC-021.

## WF-07 — Submit a new project decision case

- **User:** Decision Manager.
- **User does:** Creates a proposed case; enters scope/work packages, customer/reference label, dates, required skills/effort by week, price/currency/terms, direct expenses, payment milestones, and known assumptions/source references.
- **System does:** Creates a proposed (not accepted/active) project subject; validates required fields, dates, currency, skill taxonomy, and user scope; captures author/version and source state.
- **Inputs:** Proposal/contract fact or user input, work packages, dates, price/terms, customer reference, cost/effort assumptions.
- **Outputs:** Draft `decision_case`, versioned input snapshot, required-evidence checklist.
- **Failure/exception:** Missing scope, required skill/week, price/currency for financial analysis, or permission prevents the affected calculation. User may save a draft, but the system does not recommend acceptance.
- **Trace:** BR-016–019; S-08; AC-013–014, AC-016, AC-018.

## WF-08 — Investigate evidence and resolve gaps

- **User:** Decision Manager, Resource Manager, Finance Steward.
- **User does:** Opens each missing/conflicted item, checks source/import timestamp and policy status, requests a current file/value from its authorized owner, or records a clearly labeled assumption with author/date/reason. Re-imports or updates a new version as needed.
- **System does:** Links the issue to source batch/row/canonical fact; identifies the affected metric/scenario; distinguishes blockers from informational limitations; records changes as new versions and marks prior results stale.
- **Inputs:** Source evidence, mapping/reconciliation result, company policy, user-confirmed assumption.
- **Outputs:** Resolved/remaining blockers; new immutable input snapshot; updated case completeness.
- **Failure/exception:** Unauthorized user cannot see restricted values. Contradictory or missing decision-critical fact remains blocked; user cannot convert unknown to zero without explicit attestation and provenance.
- **Trace:** BR-008–010, BR-027, BR-029; S-05, S-09; AC-006–008, AC-024, AC-026.

## WF-09 — Calculate capacity and financial observations

- **User:** Decision Manager; Finance/Resource stewards resolve inputs.
- **User does:** Requests analysis for a specific case/version.
- **System does:** Runs deterministic capacity, actual effort, labor cost, direct cost, proposal-value, and (if included) cash-observation calculations on a frozen snapshot. It attaches units, currency, period, source, formula version, assumptions, and quality state. It does not choose a policy or invent an input.
- **Inputs:** Validated case scope, resource calendar/skills, allocations/commitments, approved time, finance-approved rates/formulas, proposal price and direct cost evidence.
- **Outputs:** Per-skill/per-week capacity; actual vs forecast effort; cost and contribution observations where approved; data/policy readiness; versioned calculation result.
- **Failure/exception:** Missing rate, FX rule, skill evidence, fresh capacity, approved formula, or reconciled source yields the exact insufficient-data state for the affected recommendation, with no preferred option. Authorized partial metrics may still display as partial observations.
- **Trace:** BR-020–025, BR-027–029; S-09; AC-011–021, AC-024, AC-026.

## WF-10 — Compare delivery scenarios

- **User:** Decision Manager; optionally Resource Manager/Finance Steward.
- **User does:** Starts with the current-resource baseline; may create a new reallocation or phase/defer version. Optionally attaches a supplier quote or hiring assumptions where Should Have functionality is enabled.
- **System does:** Recalculates only affected capacity/cost values; shows differences against a versioned baseline; prevents double assignment/double cost; does not mutate current project allocations, contract terms, or HR/supplier systems.
- **Inputs:** Frozen base case, approved formulas, explicit scenario changes, supported quote/hire evidence.
- **Outputs:** Immutable version per option; side-by-side metric changes, incomplete-option flags, assumptions and consequences for current work.
- **Failure/exception:** Scenario with invalid dates, overallocated resource, missing quote/ramp/rate, incompatible currency, or unapproved policy is marked incomplete/not comparable, not assigned a zero-cost value or rank.
- **Trace:** BR-026–027; S-09; AC-022–023, AC-026.

## WF-11 — Generate and export the decision brief

- **User:** Decision Manager and authorized reviewers.
- **User does:** Reviews the executive summary, capacity, finance, completeness, source evidence, assumptions, missing items, and options; exports/prints when permitted.
- **System does:** Produces the exact report structure in `BRD.md`; shows a preferred option only when policy gates pass; otherwise displays **“Insufficient reliable data to support a recommendation.”** It records report/case version and export actor.
- **Inputs:** Calculation/scenario snapshot; source/lineage; permissions; approved policies.
- **Outputs:** In-app brief and permission-filtered PDF/CSV case/decision export with timestamps/version IDs. Authorized source-data export is initiated from the Data Hub (S-03).
- **Failure/exception:** Incomplete/stale inputs produce an abstention brief rather than a failed page. A protected export is denied without revealing protected records. Export failure does not change the decision state.
- **Trace:** BR-027–029, BR-035; S-10; AC-024–026, AC-033.

## WF-12 — Record human decision and approval

- **User:** Decision Manager; Approver if policy requires.
- **User does:** Selects accept, accept with conditions, phase/defer, or decline; records rationale and conditions; submits for approval where configured. If the system abstained, the user explicitly marks the choice as a human decision under uncertainty/override. Approver accepts, rejects, or requests revision against the exact version.
- **System does:** Stores actor, timestamp, reason, option/version and policy; checks authorization and stale state; keeps recommendation separate from the human decision and approval. A human override never changes the abstention state or creates a preferred option. Changes create new versions and invalidate stale approval.
- **Inputs:** Reviewed brief, selected option, rationale/conditions, approval policy.
- **Outputs:** Human decision record and optional approval record; complete audit history.
- **Failure/exception:** Unauthorized, self-review where configured, expired/stale version, or changed payload prevents approval. No AI/model may act as approver.
- **Trace:** BR-030–032; S-10; AC-027–029.

## WF-13 — Optional approved internal follow-up

- **User:** Decision Manager requests; authorized Approver authorizes; assigned employee completes.
- **User does:** Optionally requests one internal task such as “confirm effort estimate” or “obtain a current supplier quote.”
- **System does:** If this Should Have feature is included, binds an immutable task payload to the decision and human approval, writes it idempotently to BusinessFlow only, and verifies a fresh read-back. Otherwise it leaves the next step as a manual recommendation in the brief.
- **Inputs:** Approved decision/version, exact task, assignee, due date, action permission.
- **Outputs:** Internal task state and audit/verification record; or no action when deferred.
- **Failure/exception:** Missing approval, stale payload, duplicate request, invalid assignee, or storage failure blocks execution; no external system is contacted and no duplicate task is created on retry.
- **Trace:** BR-031–033; S-10; AC-028–030.

## WF-14 — Track outcome

- **User:** Decision Manager, Finance Steward, Resource Manager.
- **User does:** Later enters/validates available actuals and notes deviations from the chosen scenario.
- **System does:** Links actual facts to original decision, scenario, and formula version; compares expected vs actual without claiming the recommendation caused the outcome.
- **Inputs:** Approved actual hours/costs, realized schedule, validated revenue/receipt facts where available, source evidence.
- **Outputs:** Outcome record, variances, assumptions to revisit, and source lineage.
- **Failure/exception:** Missing actuals remain missing; a changed metric definition creates a new version. The system does not silently rewrite the original decision or infer causal ROI.
- **Trace:** BR-034; S-11; AC-032.

## State and exception summary

- **Import:** `uploaded → mapped → validated → reconciled → confirmed/promoted`; invalid or unresolved critical rows stay quarantined.
- **Decision:** `draft → analysis → recommendation_ready | insufficient_reliable_data | not_authorized → human decision → optional approval`.
- **Scenario:** immutable versions; changing an input creates a new version and makes prior approval stale.
- **Action:** optional and internal only: `requested → approved → queued → attempted → completed → verified`, with failed/cancelled states audited. It may be deferred.
- **Unknown, stale, contradictory, or unauthorized data:** never silently repaired, treated as zero, or presented as a preferred-option recommendation.
