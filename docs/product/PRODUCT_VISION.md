# BusinessFlow — Product Vision

- **Status:** Owner-directed product direction for execution. It is not customer validation, proof of market demand, or a claim that the whole platform is shipped; domain-specific data and finance-policy gates still apply.
- **Evidence boundary:** The current implementation is described in [`../BACKEND_IMPLEMENTATION.md`](../BACKEND_IMPLEMENTATION.md); the code-based assessment is in [`../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md). Future capabilities in this vision are not shipped capabilities.

## Vision

**Help each company make better-informed, human-owned decisions by bringing its selected business facts, deterministic analysis, and reviewable evidence together across the domains it chooses to use.**

BusinessFlow is intended to become an integrated company-wide Business Intelligence, Financial Intelligence, Operational Intelligence, and Decision Intelligence platform: a governed way to understand what available information supports, identify gaps or changes, analyze explicit choices, and record how an authorized person decided. “Company-wide” does not mean every domain is mandatory, every company uses the same organization chart, or the first release covers the whole enterprise.

The owner-directed strategy is not yet validated with customers. The repository does not establish adoption, willingness to pay, or the most valuable first domain question; a universal KPI or decision model is neither evidenced nor required.

BusinessFlow must be able to start from different data realities:

- **Fragmented systems:** later governed source integrations should connect selected ERP, CRM, accounting, HR, and operations evidence; no live connector is currently implemented.
- **Excel/document-led processes:** make mapping, validation, source identity, quality, and lineage explicit. Current M4 supports six workforce/project CSV/XLSX datasets only; it is not document extraction or general import.
- **No established systems:** offer a small structured capture flow for the selected use case, not a replacement ERP. That guided foundation is a target, not current functionality.

Poor or missing evidence is an onboarding and data-improvement need, not a reason to reject a business or invent defaults. Projects and project feasibility are one bounded current domain, not the product boundary.

## Product problem hypothesis

Companies may hold operational, commercial, workforce, and financial facts in systems that were designed to record transactions rather than answer a particular management question across those records. Definitions, source authority, periods, access rights, and data quality can differ. A view that hides those differences can create false certainty; an ungoverned query or generated number is not an acceptable substitute for an accountable business interpretation.

BusinessFlow should therefore focus on a narrower problem than “all data in one dashboard”: make a **selected decision question** inspectable from a defined set of authorized facts, with its definitions, source lineage, assumptions, calculation version, limitations, and human decision visible. Whether that solves a repeated, valuable problem is not yet demonstrated by the repository.

## Intended users and responsibilities

Roles are examples, not a mandatory organization model. A company may assign these responsibilities differently or combine them where its own controls allow.

| Responsibility | Need |
|---|---|
| Decision owner | Frame a business question, see supported evidence and limitations, and remain accountable for the decision. |
| Domain/data steward | Identify source authority, meaning, status, period, completeness, and correction process for the domain’s facts. |
| Finance/accounting owner, when financial information is in scope | Approve definitions and policy for financial facts and calculations; control access to sensitive information. |
| Reviewer/approver, where required | Review an exact decision and evidence version under the company’s authorization and separation-of-duties policy. |
| Administrator/operator | Configure the installation, memberships, access, and enabled domains without gaining business authority merely by operating the system. |

## Intended experience

For one chosen question, a user should be able to:

1. See which authorized sources and domain records are included, their relevant time and status, and what is missing or conflicting.
2. Understand the measure’s owner-approved meaning, unit, period, calculation version, and applicable policy—or see that no approved measure exists.
3. Inspect the evidence and assumptions behind a result; distinguish recorded facts, human assumptions, derived values, and unsupported interpretation.
4. Compare only alternatives that have comparable scope and sufficiently reliable inputs. Missing or unresolved evidence yields an explicit limitation, not a fabricated default or preferred option.
5. Record a human decision against the exact evidence and policy version, obtain a separate human review when required, and preserve later supersession without rewriting history.
6. Optionally review outcomes later, without claiming causality that the evidence cannot establish.

## Foundational product principle: Evidence Before Answers — No evidence, no confident answer

This approved principle applies to every current or future BusinessFlow AI response. Any AI-assisted answer must be designed to be:

1. **Evidence-grounded:** linked to relevant company data and source records.
2. **Traceable and verifiable:** supporting evidence and calculations can be inspected.
3. **Transparent:** assumptions, limitations, uncertainty, and missing information are explicit.
4. **Permission-aware:** retrieval and answers respect the requesting user’s authorization boundaries.
5. **Honest about insufficient data:** abstain from confident conclusions when evidence is incomplete or unreliable.
6. **Reproducible:** important assessments and decisions preserve the relevant input, calculation, policy, and evidence versions.

This is a product requirement, not an implemented AI feature. Existing M2–M8, M10–M16, and Core Financials slices provide bounded evidence and deterministic patterns in specific workflows, but no AI runtime or permission-aware AI retrieval is implemented.

## Product principles

- **Company choice, not a universal org chart.** Enable only the domains a company needs. Do not require every company to model branches, locations, business units, departments, products, or services in the same way. Add a dimension only for a demonstrated use case, with clear ownership and access semantics.
- **Books of record remain authoritative.** BusinessFlow may normalize and analyze source facts; it is not assumed to replace accounting, ERP, CRM, HR, payroll, or operational systems.
- **Facts, definitions, calculations, and decisions are distinct.** A source amount is not a recognized financial measure; a metric is not a recommendation; a recommendation is not approval or execution.
- **Deterministic and reproducible numerical truth.** Business-owned calculations run in reviewed, versioned application logic over identified inputs. AI/LLMs may not supply or modify financial calculations, accounting policy, thresholds, facts, authorization, or approval.
- **Unknown is not zero.** Missing, stale, contradictory, unauthorized, or unsupported inputs remain visible and can prevent a conclusion.
- **Human authority is explicit.** Decisions and approvals bind authenticated people to immutable versions and evidence. Automation does not confer decision authority.
- **Security is architectural.** Preserve tenant isolation, company scope, backend authorization, PostgreSQL forced RLS, least-privilege runtime/migrator roles, and append-only evidence/history as the system grows.
- **Deployable where customers control their data.** Retain the self-hosted/on-prem-first and cloud-portable direction. Do not make a managed-cloud service, external model, or third-party analytics engine a prerequisite for the core.
- **Local/on-prem AI is a target requirement.** If an AI capability is separately approved, it must be supportable with customer-controlled local/on-prem inference; it must not require sending company data to an external model. This is a target, not a current capability. No LLM, inference runtime, provider, or vector store is selected or implemented.
- **AI is an interface/reasoning layer, not the product or authority.** The product is governed data intake, deterministic analysis, and reviewable decision workflows—not a generic chatbot or AI-generated dashboard. AI may explain authorized evidence but cannot supply facts, authoritative calculations, financial policy, approval, or execution.
- **Repeatable software, not consulting-only.** Onboarding expertise can help configure a Company, but durable value should come from reusable product workflows rather than bespoke analysis as the sole offering.
- **Small vertical slices before abstractions.** Prefer a domain-owned, end-to-end slice over a giant generic schema, universal KPI catalog, or general-purpose workflow engine.

## Scope boundary: direction versus current implementation

| Area | Current repository evidence | Product direction, not yet implemented |
|---|---|---|
| Tenant and company | Tenant/company are distinct; the database enforces one company per tenant. | Preserve that working boundary until an approved use case requires a different cardinality. Company-wide reporting does not itself require multi-company consolidation. |
| Business domains | Organization/workforce and project intake/decision workflows are implemented in bounded slices. Projects are one supported domain, not the product boundary. | Add other company domains only when selected and governed; no domain is universal. |
| Data intake | Staged CSV/XLSX import supports six workforce/project dataset types; M12 registers source/evidence metadata for applied imports; Phase 1 adds a separate, manually recorded customer-delivery commitment workflow. | Continue with small selected-purpose manual capture for no-system workflows, domain file contracts for spreadsheets, and later governed connectors. M12 and Phase 1 are not general integration frameworks. |
| Analytics | M5 contains five non-financial, observational capacity/assignment/skill metrics, labelled `implementation_draft` and `observational_only_no_recommendation`. | Governed, domain-owned metrics and cross-domain decision evidence are future work. No broad BI workbench is present. |
| Decisions | M6–M7 record a project-intake capacity assessment and human-controlled, evidence-bound approval flow. | A reusable lifecycle for other typed decision subjects is a target pattern, not current functionality. |
| Finance | M8 stores project-scoped financial observations and emits an immutable assessment that remains `incomplete`; no financial policy or metric is approved in code. | Optional **company-wide Financial Intelligence** across the selected Company requires finance-owner definitions, data contracts, access controls, and policy approval; it is not limited to project feasibility. |
| User interface | Narrow static workflows exist for synthetic M10 review, M4–M7 capacity decisions (PR #26), the Company Data Foundation, Phase 1 customer-commitment intake, and default-disabled Core Financials. | Connect task-oriented data-quality, domain-analysis, financial/operational intelligence, and decision workflows; do not assume one all-purpose dashboard. |
| AI-assisted answers | No AI runtime, model provider, or permission-aware retrieval is implemented. Existing slices provide bounded evidence and authorization patterns, not AI guarantees. | Apply Evidence Before Answers to every future response; retrieve only authorized evidence, expose provenance/calculation lineage and limitations, abstain when evidence is insufficient, and support local/on-prem inference. No specific model or runtime is approved. |

Detailed code and schema evidence is in [`../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md); proposed module boundaries are in [`../architecture/TARGET_DOMAIN_ARCHITECTURE.md`](../architecture/TARGET_DOMAIN_ARCHITECTURE.md); finance-specific governance is in [`../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md`](../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md).

## Product validation and success evidence

No numeric success threshold or customer validation is claimed. The next domain slice still needs an accountable source/domain owner, security/retention decisions, and evidence that the workflow improves a real recurring decision. The existing founder discovery authorization remains limited to the earlier project-acceptance hypothesis; this direction does not expand outreach or customer-data access.

Product validation should test real recent decisions and incumbent processes, not feature enthusiasm alone. No customer interview results, pilot, product trial, or willingness-to-pay evidence is claimed by this repository review.

## What this vision does not promise

This is not a promise to implement all company domains, consolidate multiple legal entities, replace systems of record, support arbitrary SQL, provide universal accounting rules, calculate every KPI, or automate management decisions. It does not choose a target industry, data warehouse, model provider, cloud service, workflow engine, or implementation schedule. Those choices require evidence and separate approval. See [`../roadmap/PRODUCT_ROADMAP.md`](../roadmap/PRODUCT_ROADMAP.md) for the proposed staged path and gates.
