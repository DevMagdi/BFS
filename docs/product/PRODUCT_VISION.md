# BusinessFlow — Product Vision

- **Status:** M9 strategic direction for owner review. This is an evidence-based product hypothesis, not customer validation, an approved Product Baseline, a committed MVP, or implementation authorization.
- **Evidence boundary:** The current implementation is described in [`../BACKEND_IMPLEMENTATION.md`](../BACKEND_IMPLEMENTATION.md); the code-based assessment is in [`../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md). Future capabilities in this vision are not shipped capabilities.

## Vision

**Help each company make better-informed, human-owned decisions by bringing its selected business facts, deterministic analysis, and reviewable evidence together across the domains it chooses to use.**

BusinessFlow is intended to become a company-wide Business Intelligence & Decision Platform: a governed way to understand what the company’s available information supports, identify important gaps or changes, compare explicitly defined choices, and record how an authorized person decided. “Company-wide” describes the potential reach across relevant company domains; it does **not** mean every domain is mandatory, every company uses the same organization chart, or the first release must cover the whole enterprise.

The product direction is a hypothesis about a useful category and workflow. The repository and prior project-feasibility documents do not establish that customers need this broader product, which domain should come first, or that a single universal KPI or decision model is appropriate.

Projects and project feasibility are one bounded domain in the current implementation, not the product boundary. The company-wide direction is to add selected business domains incrementally and compose only relevant, governed evidence for a specific company question.

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

This is a product requirement, not an implemented AI feature. The implemented M2–M8 slices provide bounded provenance, deterministic calculations, authorization, and immutable snapshots in specific workflows, but no AI runtime or permission-aware AI retrieval is implemented.

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
- **Small vertical slices before abstractions.** Prefer a domain-owned, end-to-end slice over a giant generic schema, universal KPI catalog, or general-purpose workflow engine.

## Scope boundary: direction versus current implementation

| Area | Current repository evidence | Product direction, not yet implemented |
|---|---|---|
| Tenant and company | Tenant/company are distinct; the database enforces one company per tenant. | Preserve that working boundary until an approved use case requires a different cardinality. Company-wide reporting does not itself require multi-company consolidation. |
| Business domains | Organization/workforce and project intake/decision workflows are implemented in bounded slices. Projects are one supported domain, not the product boundary. | Add other company domains only when selected and governed; no domain is universal. |
| Data intake | Staged CSV/XLSX import supports six workforce/project dataset types with explicit mapping, validation, confirmation, and source-row links. | Expand source coverage through domain-specific contracts; a general connector/source registry is not present. |
| Analytics | M5 contains five non-financial, observational capacity/assignment/skill metrics, labelled `implementation_draft` and `observational_only_no_recommendation`. | Governed, domain-owned metrics and cross-domain decision evidence are future work. No broad BI workbench is present. |
| Decisions | M6–M7 record a project-intake capacity assessment and human-controlled, evidence-bound approval flow. | A reusable lifecycle for other typed decision subjects is a target pattern, not current functionality. |
| Finance | M8 stores project-scoped financial observations and emits an immutable assessment that remains `incomplete`; no financial policy or metric is approved in code. | Optional **company-wide Financial Intelligence** across the selected Company requires finance-owner definitions, data contracts, access controls, and policy approval; it is not limited to project feasibility. |
| User interface | No product frontend or dashboard is present in the reviewed backend. | A usable read-only decision experience is a later product choice; this assessment does not authorize a UI implementation. |
| AI-assisted answers | No AI runtime, model provider, or permission-aware retrieval is implemented. The M2–M8 slices provide only bounded evidence, authorization, and reproducibility patterns. | Apply Evidence Before Answers to every future response; retrieve only authorized evidence, expose provenance/calculation lineage and limitations, abstain when evidence is insufficient, and support local/on-prem inference. No specific model or runtime is approved. |

Detailed code and schema evidence is in [`../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md`](../architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md); proposed module boundaries are in [`../architecture/TARGET_DOMAIN_ARCHITECTURE.md`](../architecture/TARGET_DOMAIN_ARCHITECTURE.md); finance-specific governance is in [`../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md`](../architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md).

## Product validation and success evidence

No numeric product, customer, financial, or operational success threshold is set here. Before treating this direction as a Product Baseline, the owner should decide which customer segment and repeated decision to investigate, who owns the domain definitions and source systems, and what observable evidence would show that the workflow is more useful and trustworthy than the customer’s existing tools and processes. The current founder-authorized discovery memo is specific to the earlier project-acceptance hypothesis; this M9 assessment does not expand its outreach or data-handling authorization.

Product validation should test real recent decisions and incumbent processes, not feature enthusiasm alone. No customer interview results, pilot, product trial, or willingness-to-pay evidence is claimed by this repository review.

## What this vision does not promise

This is not a promise to implement all company domains, consolidate multiple legal entities, replace systems of record, support arbitrary SQL, provide universal accounting rules, calculate every KPI, or automate management decisions. It does not choose a target industry, data warehouse, model provider, cloud service, workflow engine, or implementation schedule. Those choices require evidence and separate approval. See [`../roadmap/PRODUCT_ROADMAP.md`](../roadmap/PRODUCT_ROADMAP.md) for the proposed staged path and gates.
