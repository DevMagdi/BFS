# BusinessFlow — Historical Project-Feasibility MVP Scope

- **Status:** Historical, superseded as current product direction by the M9 assessment in [`product/PRODUCT_VISION.md`](product/PRODUCT_VISION.md). This page records priorities for the former project-feasibility hypothesis; it is not a current MVP commitment, validated Product Baseline, or implementation authorization.
- **Historical source set:** [`BRD.md`](BRD.md), [`USER_WORKFLOWS.md`](USER_WORKFLOWS.md), [`SCREEN_INVENTORY.md`](SCREEN_INVENTORY.md), and [`MVP_ACCEPTANCE_CRITERIA.md`](MVP_ACCEPTANCE_CRITERIA.md) describe that earlier candidate workflow. Their Must/Should/Out labels are not current product priorities. The current roadmap proposal is [`roadmap/PRODUCT_ROADMAP.md`](roadmap/PRODUCT_ROADMAP.md).
- **Historical use case:** New-Project Feasibility Decision Support for project-based services; initial segment hypothesis was Software & IT Services.

## MVP product boundary

One Tenant contains one Company. The authorized user imports structured company/resource/project/finance data from versioned CSV/XLSX templates, enters one proposed engagement per decision case, investigates quality and freshness, and compares capacity and finance observations under a limited set of explicit scenarios. Multiple independent cases may exist for the same Company. BusinessFlow produces a source-linked brief; an authorized person records the decision. It does not replace CRM, PSA, ERP, accounting, HR, or project-delivery systems.

The core remains deterministic and useful with AI disabled. No automated acceptance threshold or preferred option is enabled until the relevant business and finance policy is approved. User interviews proceed separately and can revise these provisional priorities; they do not block the specification.

## Must Have — required for the core decision

1. **Single-Company onboarding and access:** one Company per Tenant; company timezone, base currency, working calendar; authenticated membership and the minimum scoped roles.
2. **File-only ingestion:** versioned XLSX workbook and matching CSV templates; mapping preview; row-level validation; exact-ID duplicate detection; explicit conflict resolution; idempotent promotion; import/mapping history and source lineage. Provisional maximum is 25 MiB per file, pending owner/technical confirmation.
3. **Decision-critical business context:** employees/resources, job roles/skills, capacity and approved absences, active project allocations/commitments, approved timesheet actuals, effective-dated finance-restricted loaded cost rates, direct expenses, proposal value/terms, and effort/schedule estimates.
4. **New-project case entry:** proposed scope, work packages, required skills, estimated hours by week, delivery window, price/currency, payment terms/milestones, owner, and confirmed assumptions.
5. **Deterministic analysis:** weekly skill capacity/gap, approved actual effort, forecast labor cost, explicit direct costs, same-currency proposal value, and candidate direct contribution only after finance approval. Display partial observations without presenting them as a recommendation.
6. **Core scenario set:** accept with current resources; accept with a user-authored reallocation of flexible/uncommitted work; phase/defer by changing schedule/scope; decline. Scenarios are immutable and comparable only when scope, units, currency, and policy version match.
7. **Evidence-first decision brief:** executive summary, completeness/freshness, capacity, financial observations, scenario comparison, assumptions, risks/limitations, source evidence, missing information, and next steps.
8. **Abstention and human decision:** exact `Insufficient reliable data to support a recommendation.` state; no preferred option on blockers; human records accept, conditional accept, phase/defer, or decline against an exact version.
9. **Review and accountability:** policy-governed human approval where configured; versioned audit history; exportable printable/PDF brief and permission-filtered CSV case/data export.
10. **Meaningful nonfunctional controls:** backend authorization and verifiable Tenant/Company isolation; secure identity lifecycle; sensitive-cost restriction; safe import handling; self-hosted-first operation; durable state; and tested backup/restore before release. Database choice and controls such as PostgreSQL/RLS remain candidate design decisions pending technical approval.

## Should Have — follow-on or conditional within the MVP boundary

- **Outsource scenario:** only with a current, scope-comparable supplier quote; no procurement or supplier discovery.
- **Hire scenario:** manual estimate only from explicit start/ramp/capacity and finance-approved loaded-rate assumptions; no recruiting or hiring action.
- **Cash observations:** scheduled billing, invoice/open/past-due receivables, allocated receipts, and timing observations as separate facts. No cash-pressure score or predicted payment probability.
- **Internal follow-up task:** optional reversible BusinessFlow-only task, bound to a human-approved immutable payload. It may be deferred without breaking the core decision workflow.
- **Outcome tracking:** lightweight, manual expected-vs-actual values linked to the original case and formula version; no causal ROI claim.
- **AI-assisted text explanation or mapping suggestion:** only after a separate privacy/provider/security/evaluation decision, human confirmation, and a test showing deterministic behavior remains authoritative; no customer data leaves its environment without explicit approval. Core MVP does not depend on it.

## Explicitly Out of Scope — do not build now

- Live connector catalog, automatic sync, arbitrary ERP schema mirroring, arbitrary SQL, or a general-purpose data warehouse/BI workbench.
- CRM pipeline, proposal authoring/signature, project scheduling/execution, task/time-entry management, invoicing, revenue recognition ledger, payroll, HRIS, recruitment, or full accounting.
- Autonomous go/no-go, generated acceptance/risk/utilization thresholds, unexplained “AI score,” or auto-resolution of conflicted/stale facts.
- Payment-probability, cash forecasting, bank access, payment execution, credit scoring, or combining invoice forecasts with recognized revenue/cash receipts.
- Automatic skills inference, staffing optimizer, hiring action, job posting, supplier search/procurement, or external project/ERP write.
- Multi-company selection, consolidation, intercompany accounting, group reporting, or cross-tenant analytics.
- General risk register, goals/KPI suite, department hierarchy configuration beyond the minimum scope, plugin marketplace, custom formula/code editor, or model/provider management UI.
- Unbounded AI chat, model-generated financial numbers, model access to credentials/SQL, or any agent approval/execution authority.
- Kubernetes/microservices, managed-cloud dependency, optional analytics engines, vector store, or a large background-workflow platform.

## Scenario scope decision

| Alternative | Priority | Include in initial scope? | Required evidence / limit |
|---|---|---|---|
| Accept with current resources | Must Have | Yes | Valid skills, capacity, current commitments, dates, proposal scope/price. |
| Accept with resource reallocation | Must Have | Yes, manually authored | User selects flexible/uncommitted allocations; system shows displaced work and recalculates. No automatic optimizer or silent changes. |
| Phase or defer | Must Have | Yes, basic | User edits start/period/scope; no schedule optimization and no automatic contract-term rewrite. |
| Decline | Must Have | Yes | Record human choice; do not invent opportunity cost or lost revenue. |
| Accept with outsourcing | Should Have | Conditional | Valid current supplier quote; no procurement action. Incomplete quote => scenario visibly incomplete, not zero-cost. |
| Accept with hiring | Should Have | Estimate only | User-supplied start/ramp/capacity plus finance-approved rate; no hiring/recruiting feature or action. |

This sequence keeps the first demonstrable decision focused on data already represented in the capacity and proposal model. Outsourcing and hiring stay visible as possible alternatives, but they do not block a basic feasibility brief when the source evidence does not exist.

## Future demonstrable slices — planning only, not authorization

After the separate product, technical, security, and data-policy gates are approved, deliver small end-to-end slices against synthetic fixtures. Each slice should be demoable on its own and leave prior evidence/version history intact:

1. **Company and source intake:** one Company, least-privilege membership, versioned CSV/XLSX template, mapping preview, row validation, quarantine, and source lineage.
2. **Capacity-only case:** proposed work packages plus weekly skills/availability/commitments; show required/available/gap and an abstention when an essential input is missing.
3. **Finance observations:** effective loaded-rate/direct-expense/proposal inputs and exact-decimal calculations under a Finance-approved test policy; production preference remains disabled until owner approval.
4. **Scenario brief:** immutable current-resource, manual-reallocation, phase/defer, and decline options with evidence, assumptions, limitations, and no unsupported ranking.
5. **Human decision and export:** record the human choice/override separately from recommendation, enforce permissions/versioning, and export the authorized brief/data.
6. **Optional extensions:** only by separate prioritization, add quote/hire estimates, cash observations, internal follow-up, or outcomes; none blocks the core slice.

These slices do not authorize Milestone 0, dependencies, code, migrations, customer data, or deployment. The exact original requirements and technical/finance/security approvals remain separate gates.

## Release gates and non-negotiable exclusions

A requirements document does not make a calculation policy or technology approved. Financial recommendation behavior stays disabled until Finance approves the relevant definitions and golden examples. No cross-currency ranking without an approved FX source/policy. No threshold-based preference until a company owner approves the objective and threshold. Existing domain/entity/decision docs remain proposals; where they label optional cash, hiring/outsourcing, internal actions, or outcomes as MVP candidates, this scope's Must/Should/Out boundary governs the provisional release priority. The exact original 24 Part 2 requirements remain blocked and are not replaced by BRD IDs.

**Implementation remains unauthorized.** Do not start Milestone 0, add dependencies, create code/migrations, connect customer data, create trial accounts, or deploy anything as a consequence of this scope document. Technical baseline, identity/deployment details, data retention/residency, and security acceptance gates remain subject to the existing roadmap and technical documents.
