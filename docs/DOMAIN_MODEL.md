# BusinessFlow AI — Proposed Universal Domain Model

- **Status:** This document remains the broad logical domain proposal; it is not a complete implemented schema.
- **MVP hypothesis:** Software & IT Services, beginning with “Should we accept a new project?”
- **Scope precedence:** This conceptual model is broader than the bounded Milestone 3 company-context implementation and the current provisional release boundary. [`BRD.md`](BRD.md) and [`MVP_SCOPE.md`](MVP_SCOPE.md) govern Must/Should/Out priority; candidate cash, hire/outsource, action and outcome concepts below are not automatically core-MVP commitments.
- **Implemented subset:** The backend implements Business Unit, Department, Employee, Skill, Employee Skill, versioned Weekly Capacity, Project and Resource Assignment records; M4 staged imports and source-row links; and the bounded M5 versioned metric catalog and immutable metric-run snapshots. See [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md) for actual fields, routes, constraints and limitations. M5 covers only non-financial capacity, recorded assignment and skill-inventory observations; it does not implement finance, general-purpose analytics, project decisions or recommendations. The remaining entities below are proposals, not implemented M3 features.

## 1. Domain rules

1. **Tenant and Company are separate entities with strict 1:1 MVP cardinality.** Tenant is the customer/security/data-isolation boundary; Company is the actual business entity and its operational/financial context. Enforce one Company per Tenant initially. Keep company-scoped facts explicit so future multi-company support can expand the relationship without replacing the domain model. Multi-company selection and group-level consolidation are not in the MVP.
2. **External systems are sources, not the canonical model.** The domain model represents business concepts and relationships. Adapters map external schemas into canonical entities without making an external ERP's table structure our product model.
3. **Facts, derivations and decisions are distinct.** Imported source facts retain provenance. Derived metrics record formula and input versions. Recommendations record evidence and assumptions. Approval, execution, verification and outcome each have their own records and state.
4. **No unsupported business fact.** Conflicting, stale, incomplete or untrusted data is surfaced as a quality limitation. “Insufficient reliable data” is a valid decision result.
5. **Money is exact and contextual.** Store decimal amounts with an explicit ISO currency code and original source amount. Do not silently convert currencies or use binary floating-point for financial decisions.
6. **Identity and employment are distinct.** A user account can be a company employee, but employees need not be product users; an approver is identified from an authenticated principal, never from a free-text `by` field.

## 2. Canonical entity definitions

| Concept | Definition and key relationships |
|---|---|
| Tenant | Customer account and security/data-isolation boundary. Has exactly one `Company` in the MVP, plus memberships, sources, policies and tenant-scoped records. The tenant remains the parent if the company cardinality later expands. |
| Company | The actual operating/legal business entity and its operational/financial context; belongs to one tenant and has base currency, timezone, locale and profile. Exactly one per tenant in the MVP; a future tenant may own multiple companies. |
| Business Unit | Implemented company grouping with a name, optional description and active/inactive status; may contain departments. |
| Department | Implemented company subdivision with a name, optional description and active/inactive status; it may reference one optional business unit. Nested departments and managers are not implemented. |
| User / Membership | Authenticated person or service principal; a membership grants access to a tenant and optionally scoped roles. This is separate from an employee record. |
| Access Role / Permission | Platform authorization role and permission catalog. Grants view/edit/approve/execute rights within a tenant/company/department scope. |
| Job Role | Business-position catalog (e.g. developer, delivery lead, project manager), distinct from access control. The current Employee model stores a job-title string; a Job Role entity is not implemented. |
| Employee | Implemented tenant/company-scoped person record with employee code, full name, job title, optional department, active/on-leave/inactive/terminated status, employment dates and standard weekly hours. It is separate from `UserAccount`; employee logins, manager links, compensation and payroll are not implemented. |
| Skill / Employee Skill | Implemented tenant-owned skill catalog and employee association. Proficiency is an explicit integer from 1 to 5 with labels from beginner through expert; both catalog and association have active/inactive status. |
| Weekly Capacity | Implemented employee-specific Monday-start week record with confirmed/estimated/unknown availability, scheduled/non-project/unavailable hours, source and optional reason. Each correction appends a version; unknown/missing capacity is not treated as available. |
| Business Resource | A broader future abstraction for employees and possible contractors/subcontractors. The current implementation supports employees only; a separate generic resource model is not implemented. |
| Customer / Supplier | Roles played by a canonical external `Business Party`. One party may be both a customer and a supplier; parties and roles are not implemented. |
| Project | Implemented company project with code, name, description, lifecycle status, start/planned/actual end dates and optional employee manager. It does not store customer, contract, financial facts or feasibility results. |
| Resource Assignment | Implemented employee-to-project record with start/end dates, planned hours per week, proposed/committed/completed/cancelled status and audit-backed change history. It records intent, not a capacity-feasibility calculation. |
| Contract | Commercial agreement with parties, value/currency, dates, payment terms and commitments. Terms are versioned or source-linked; the system does not replace the signed contract. |
| Revenue Fact | Imported/verified recognized or planned revenue amount, date, currency and project/customer/source link. It is not a general ledger. Invoice value is not automatically treated as recognized revenue. |
| Expense | Imported expense/cost fact, optional project/contract/supplier link, amount/currency/date/category and source evidence. |
| Invoice / Receivable | Imported billing and amount-due facts with issue/due/settlement state. A payment receipt is separate; invoice issuance is not cash collection. |
| Cash Position | Point-in-time cash balance or cash-flow fact from an approved source, with company, date, currency and provenance. No bank account or transfer operation is implied. |
| Budget | Planned allocation of financial resources for a company/unit/department/project and period; separate from actual spend. |
| Commitment | Future financial or resource obligation, often associated with a contract, project, supplier or due date. |
| Goal | Strategic or operational objective with owner, period and measurable target(s). |
| KPI Definition / Observation | Broad domain concept for a reviewed, versioned name, unit, dimensions, deterministic formula and source policy; observations contain period/value, calculation version and lineage. KPI definitions are reviewed product knowledge, not model-generated SQL. The backend's narrow M5 catalog is not a general-purpose KPI framework. |
| Metric Catalog / Definition | Implemented M5 catalog `businessflow.metrics.v1` defines five versioned non-financial observations for weekly capacity, recorded committed assignments, capacity-versus-commitment comparison, recorded overload and skill inventory. Its status is `implementation_draft`, use is `observational_only_no_recommendation`, and formula version is `businessflow.capacity-weekly.v1`; none represents owner approval of a financial or product decision policy. |
| Metric Run | Implemented immutable, tenant/company-scoped calculation record containing a bounded input snapshot, result snapshot, SHA-256 hashes, period, catalog/calculation versions and reliability state. A saved run can be replayed from its input snapshot. It is not a decision case, recommendation or approval. |
| Risk | Business risk tied to one or more entities, with evidence, likelihood/impact rationale, owner, status and review date. Risk statements are not verified facts without supporting evidence. |
| Data Source / Import | Connector or file origin, source account/configuration reference, sync/import batches, raw and normalized rows, mapping version and reconciliation state. Credentials are not stored in plain text. |
| Business Event | A typed operational fact such as timesheet approved or invoice received; distinct from a security/audit event. |
| Evidence / Lineage | A reference to source records, metric calculations, documents or business events, including locators, hashes, timestamps and transformations. Evidence supports a claim; it does not prove the source system is correct. |
| Policy | Versioned declarative business/approval rule with scope and effective dates. The rule engine validates a constrained schema; it does not execute arbitrary code supplied by users or models. |
| Decision Case | A traceable management question such as accepting a proposed project, tied to a company and relevant entities/data snapshot. |
| Recommendation | A versioned explanation of what is happening, why it matters, evidence, assumptions, limitations, risks, alternatives and a preferred option. It is neither an approval nor an action. |
| Approval | A separate human decision against an immutable recommendation/option or exact action payload hash, recorded with authenticated approver, time, reason and policy. Revisions invalidate stale approval. |
| Action | A proposed or approved work item to be carried out internally or through an external connector. Includes target system, exact payload hash, idempotency key and state. |
| Action Attempt / Verification | Each submission/retry is recorded separately. Verification is a subsequent read-back or independent check, not a successful HTTP request. |
| Outcome | Expected-versus-actual business impact measured later, with period, source, metric and lineage. It may reveal that a recommendation or assumption was wrong. |
| Industry Pack | Versioned declarative domain knowledge: terms, KPI formulas, validation rules, scenario templates, integration mappings and workflows. Not an unrestricted plugin runtime. |

## 3. Core relationship examples

- A tenant has exactly one company in the MVP; people may have tenant memberships and separately be employees in that company. Tenant and company remain distinct 1:1 entities by owner decision. There is no multi-company selection or consolidation in the MVP.
- The implemented company-context subset supports business units, departments, employees, tenant-owned skills, weekly capacity, projects and employee resource assignments. M4 adds staged imports and source-row links; M5 adds only the narrow, non-financial catalog and immutable metric runs described above. Broader company concepts—parties, contracts, budgets, goals, financial facts, general KPI policy, data sources/connectors and decision entities—remain proposals and are not implemented by these slices.
- Future project relationships may include customers, contracts, time entries, expenses, commitments, revenue and invoices; none of those relationships or facts are part of the current M3 schema. An invoice's due date and later payment receipt influence cash collection, not the accounting recognition rule.
- A future project-acceptance case may use current project, contract, capacity, timesheet, cost, invoice/receivable and commitment facts. It must disclose which are absent or contradictory. The current M5 calculation covers only recorded capacity, assignments and skill inventory; it creates no acceptance case, financial calculation, recommendation or approval.
- A decision case has one or more recommendation versions; each recommendation has alternatives and evidence. A human approval points to an exact option/version. Any resulting action has attempts and separate verification. Outcomes are attached to the original decision and may be recorded even if no external action was necessary.

## 4. Initial project-acceptance slice

The following is a broad future domain-design candidate, not an implemented acceptance-decision capability or the current release feature list. The bounded M5 backend calculation is limited to capacity, recorded assignment and skill-inventory observations; it is not the project-acceptance workflow. The provisional core is narrowed in [`MVP_SCOPE.md`](MVP_SCOPE.md): four options (current resources, manual reallocation, phase/defer, decline), with cash evidence, hiring/outsourcing estimates, internal actions and outcomes remaining conditional/Should Have. The first decision is not a canned AI answer. It is intended to be a deterministic calculation over validated, tenant-scoped facts:

1. Import project, employee/resource, timesheet, project cost/expense, invoice/receivable, contract and commitment data.
2. Validate data types, required fields, uniqueness, currency, dates, relationships and reconciliation. Preserve raw input and source identifiers.
3. Compute current-project profitability/capacity measures using explicitly approved definitions and versioned formulas.
4. Represent a proposed project with scope, timeline, price/payment terms, skill/effort requirements and known commitments. If hiring or outsourcing is compared, require explicit cost/capacity assumptions or source quotes.
5. Compare accept with current team, hire, outsource, phase, postpone and reject where required inputs exist. Each option records financial/operational impact, risks, assumptions, sensitivity and limitations.
6. Recommend only when data quality and policy gates pass. Otherwise return a structured insufficient-data result and name the missing/conflicting inputs.
7. Route a recommendation for human approval. On approval, create an internal task or a later connector action. Verify any external change independently and measure actual results later.

The proposed minimum inputs, candidate formulas, explicit abstention conditions, finance-owner approvals, scenario versioning and reversible first action are detailed in [`MVP_DECISION_MODEL.md`](MVP_DECISION_MODEL.md). Entity-by-entity MVP/deferred status, ownership, keys, tenant/company isolation and integrity constraints are in [`MVP_ENTITY_CATALOG.md`](MVP_ENTITY_CATALOG.md). Those proposals do not freeze accounting policy, numerical thresholds, or an owner decision.

## 5. Future multi-company extension (not MVP)

The domain is structured so the future change is primarily cardinality and policy, not replacement of business facts:

- Keep `Tenant` as the customer/security boundary and `Company` as the child operating/financial entity. Company-scoped projects, resources, financial facts, KPIs and evidence retain explicit `company_id` plus `tenant_id`.
- The MVP database enforces one company per tenant. A future approved migration may remove that uniqueness restriction, expand company-scoped authorization and add company selection; it must not make company facts tenant-ambiguous.
- If group-level views are introduced, add explicit group/consolidation configuration and deterministic aggregation rules. A tenant-level total is not automatically accounting consolidation: currency/FX source and date, ownership/effective periods, fiscal calendars, intercompany balances/eliminations and metric definitions must be resolved.
- Multi-company access, group-level consolidation and intercompany accounting remain out of scope until a separate product/security decision and acceptance suite.

## 6. Explicitly separate concepts

- `User` versus `Employee`; `Access Role` versus `Job Role`.
- `Customer`/`Supplier` roles versus the underlying `Business Party`.
- Contract value versus recognized revenue versus invoiced amount versus cash received.
- Budget versus commitment versus expense versus actual payment.
- Recommendation versus approval versus action request versus attempted submission versus completed action versus verified result.
- Expected outcome versus measured outcome.
- `Business Event` versus `Audit Event`.
- A semantic/query result versus a canonical fact or validated deterministic metric.

## 7. MVP model and decision contract references

`MVP_ENTITY_CATALOG.md` remains the logical entity catalog: it describes candidate status, ownership boundaries, keys, tenant isolation and integrity constraints and identifies deferred entities. The implemented database subset is the four Alembic revisions described in [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md): M2 foundation, M3 company context, M4 staged imports/lineage, and M5 immutable metric runs.

`MVP_DECISION_MODEL.md` is still a proposed acceptance-decision contract. The listed formulas and action are recommendations for owner/finance review, not approved accounting policies or a release commitment. The M5 observations do not produce a recommendation; a future recommendation must abstain when required evidence, an approved formula/policy, currency conversion, or authorization is missing.
