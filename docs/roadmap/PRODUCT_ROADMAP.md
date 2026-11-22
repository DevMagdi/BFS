# BusinessFlow — Product Roadmap

- **Status:** M9 planning proposal for owner review. No future implementation, release date, scope, or feature is approved by this document.
- **Product direction:** A company-wide BI & Decision Platform; see [`../product/PRODUCT_VISION.md`](../product/PRODUCT_VISION.md).
- **Assessment basis:** [`../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md) and [`../architecture/TARGET_DOMAIN_ARCHITECTURE.md`](../architecture/TARGET_DOMAIN_ARCHITECTURE.md).
- **Sequencing rule:** Use capability names, not a new numbered milestone sequence. M9 is documentation-only; this roadmap does not create an M10 or authorize implementation.

## 1. Current implementation baseline

The migrations show a concrete bounded backend foundation through M8, not a completed BI platform:

| Existing slice | Present capability | Product-level gap that remains |
|---|---|---|
| M2 — backend foundation | Authentication/session baseline, tenant/company, membership authorization, audit, PostgreSQL RLS and separate runtime/migrator roles. | No production identity lifecycle such as MFA/account recovery, configurable domain roles, or enterprise IAM is claimed. |
| M3 — company context | Business units, departments, employees, tenant skills, capacity, projects, and employee-project assignments. | No broad organizational model or general company-domain coverage. |
| M4 — staged imports | Explicit CSV/XLSX staging, mapping, validation, confirmation, and provenance for six workforce/project datasets. | No general source registry, live connectors, scheduled sync, or financial dataset intake. |
| M5 — metrics | Five deterministic, immutable, non-financial capacity/assignment/skill observations, explicitly draft and observational. | No general KPI framework, company dashboard, or cross-domain analytics layer. |
| M6 — capacity assessment | Versioned proposed-project intake and capacity-only assessment over M5 evidence. | No broad commercial or company-level decision analysis; business feasibility stays incomplete. |
| M7 — human decision/approval | Immutable project-stage decision versions and evidence-bound human approval. | No general typed decision-subject model, external action, or general workflow engine. |
| M8 — financial observation/assessment | Project-scoped sourced financial inputs and a policy-gated immutable assessment that always returns `incomplete`. | No company-wide financial facts, approved formulas/policies, or financial verdict. |

M1 is not represented by a distinct migration or runtime feature in the inspected M1–M8 implementation chain; the code-backed migration sequence begins with the documented M2 foundation. Projects and project-intake decisions are one supported business domain in the current implementation, not the product boundary. See [`../BACKEND_IMPLEMENTATION.md`](../BACKEND_IMPLEMENTATION.md) for bounded implementation details and evidence.

## 2. Decisions required before product implementation

The broader direction remains a hypothesis. Before selecting a feature or writing business code, the product owner should decide:

1. **Product question and user:** Which repeated company-level decision or analytic question should be solved first, for whom, and what would make the current process inadequate?
2. **Domain and source owner:** Which single optional business domain is the first vertical slice? Who owns its facts, meanings, source authority, correction, freshness, and access?
3. **Organization scope:** Does the use case fit the existing single-company-per-tenant model? Do not infer multi-company, consolidation, or any fixed branch/location/department hierarchy.
4. **Finance policy:** If the chosen slice uses financial values or derived measures, an accountable finance/accounting owner must approve the data contract and every required interpretation/calculation rule before those calculations are implemented or presented as truth. See [`../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md`](../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md).
5. **Security and operations:** Confirm sensitive-data permissions, retention, residency, backup/restore, deployment/operator responsibilities, and testing expectations for the selected domain.
6. **Product validation:** Decide whether and how to validate the broader company-wide hypothesis. The existing founder discovery authorization in [`../FOUNDER_DECISION_MEMO.md`](../FOUNDER_DECISION_MEMO.md) is limited to the earlier project-acceptance hypothesis; this roadmap does not extend that authorization or approve new outreach, customer-system access, recording, or spending.

If these decisions are not made, the appropriate next step is owner-led domain/product discovery—not schema, API, formula, UI, or infrastructure work.

## 3. Recommended first implementation candidate (conditional)

### Company-Level Governed Data Vertical Slice

This is the recommended **next candidate implementation**, only after the product owner approves a specific domain, user question, and source contract. It is deliberately unnumbered; M9 authorizes no implementation.

**Bounded outcome:** Ingest or record one owner-approved domain data contract for one Company; preserve source identity, effective/as-of context, status, quality, lineage, and authorized access; present one read-only company-level observation or answer for one defined question. Use the existing PostgreSQL modular monolith, M4 staging/validation patterns, M5 snapshot/hash/replay patterns where applicable, and the established tenant/company isolation controls. Do not build a universal registry or schema before this slice demonstrates the need.

If the selected domain is finance, the first slice may preserve and inspect exact, sourced observations only. No financial calculation, cross-currency arithmetic, financial verdict, forecast, or ranking is permitted unless the relevant owner has separately approved its exact data definition, policy, and golden tests. Reuse M8's intentionally incomplete behavior until that gate passes.

The first slice does not require AI. If AI is separately approved later, every response must follow **Evidence Before Answers — No evidence, no confident answer**. Any such capability must support local/on-prem inference; no LLM, provider, inference runtime, or vector store is selected or implemented by M9.

**Acceptance evidence, to be agreed before implementation:**

- One named user question, owner, domain, Company scope, and authoritative source/data contract.
- **Evidence Before Answers — No evidence, no confident answer:** any future AI answer must satisfy all six requirements:
  - **Evidence-grounded:** link material claims to authorized company data and source records.
  - **Traceable and verifiable:** expose supporting evidence and calculation inputs, versions, and results.
  - **Transparent:** make assumptions, limitations, uncertainty, and missing information explicit.
  - **Permission-aware:** check authorization before retrieval and before returning an answer.
  - **Honest about insufficient data:** abstain from confident conclusions when evidence is incomplete or unreliable.
  - **Reproducible:** preserve relevant input, calculation, policy, and evidence versions for important assessments and decisions.
- Explicit mapping and validation with visible missing/conflicting/estimated states; no silent promotion or zero-filling.
- Backend membership/permission checks, forced RLS, same-company foreign keys, least-privilege grants, and PostgreSQL isolation tests.
- Reproducible read-only output with source lineage, scope, period, definition/code version, quality state, and limitations.
- No unapproved formula or business verdict; decisions remain with authorized people.
- Clean migration/drift, regression/security tests, documented retention and runbook impacts, and reviewed product acceptance.

**Outside this candidate:** all-company domain coverage, a general-purpose BI workbench, multi-company consolidation, a universal KPI or subject registry, new datastore/microservice, broad live connectors, external actions, autonomous AI, and a full finance/accounting engine. A minimal user experience needed to test the selected question requires its own product and scope decision.

## 4. Conditional later capabilities

These are sequencing options, not commitments or implementation authorization. Each requires evidence from the preceding slice and a separately approved scope.

| Capability | Entry condition | Intended limit |
|---|---|---|
| Second optional domain and cross-domain read model | The first domain has a stable owner-approved contract, security tests, and demonstrated question/value. | Join only typed, authorized, compatible evidence for a defined question. Keep domain-owned facts and calculations separate. No generic data lake or arbitrary SQL. |
| Reusable decision lifecycle for a new subject | A second real workflow demonstrates repeated need for the M7 evidence/approval pattern beyond project intake. | Add a typed domain adapter and only necessary shared lifecycle concepts. Preserve M7 tables/routes and exact evidence binding; no generic workflow engine by default. |
| Product UI for the selected workflow | A named user’s workflow and interaction requirements are approved. | Start with the selected data/decision flow and clear quality/permission states; no assumption of an all-purpose dashboard suite. |
| Additional file/source connectors | A specific source is repeatedly required and its access, rate, retention, schema, retry, and operations are approved. | Add one connector boundary at a time; no automatic sync by default. |
| Financial calculations | Finance owner approves exact definitions, source policy, currency/precision/rounding/period behavior, and golden examples. | Implement only the reviewed calculation/version and scope. Missing/invalid inputs remain incomplete. No LLM arithmetic. |
| Outcomes, actions, or AI assistance | A product use case, data-flow/security design, permission model, and evaluation plan are approved separately. | Keep decisions, approvals, actions, verification, and outcomes distinct. AI responses must satisfy Evidence Before Answers, use permission-aware evidence retrieval, abstain when evidence is insufficient, and be reproducible where used in important assessments/decisions. Any AI feature must support local/on-prem inference; no model/runtime is preselected. AI may not be calculation or approval authority. |

## 5. MVP exclusion boundary

The first MVP should demonstrate one valuable, governed company-level slice—not the entire company-wide vision. Unless the owner approves otherwise, exclude:

- all-domain coverage, universal org structures, industry packs, and configurations for fields a company does not use;
- multiple companies per tenant, group consolidation, intercompany accounting, and cross-company analytics;
- full accounting/ERP/CRM/HR/payroll replacement, a general ledger, transaction posting, billing, or payment processing;
- universal KPI catalog, arbitrary formulas or SQL, a general-purpose semantic layer, and an all-purpose BI workspace;
- live connector catalog, real-time sync, arbitrary ERP mirroring, broad warehouse/lakehouse adoption, or external writeback;
- any financial formula, accounting policy, threshold, currency conversion, rounding, cash forecast, margin/profitability verdict, or collection probability not approved by the responsible owner;
- AI-generated financial values, autonomous recommendation/approval/execution, high-impact actions, causal ROI claims, and ungoverned outcome scoring;
- microservices, extra databases, workflow engines, brokers, or a new analytics engine without a demonstrated requirement.

These are exclusions from the **first candidate slice**, not a permanent claim that the product could never support such capabilities. Any addition requires explicit value, governance, architecture, security, and owner approval.

## 6. Governance of this roadmap

The roadmap has no delivery dates or effort estimates because user, domain, data, finance, deployment, and product gates remain unresolved. Updating this roadmap does not approve M0, a technical baseline, customer-data processing, a pilot, or a code change. M9 remains documentation-only. The previous project-feasibility roadmap is retained only as historical context in [`../ROADMAP.md`](../ROADMAP.md); this file is the current strategic roadmap proposal.
