# BusinessFlow AI — Phase 1 Competitive Validation

- **Status:** Desk-based competitive validation; evidence package for founder review. No product verdict, ICP, positioning, or Product Baseline has been approved.
- **Research date:** 2026-11-21 (UTC).
- **Decision hypothesis examined:** “Should we accept this proposed project?” for the still-hypothetical Software & IT Services segment, with capacity, direct margin, scenario, cash-timing, and management-action inputs.
- **Scope:** Three direct workflow alternatives only—Kantata, Projectworks, and Microsoft Dynamics 365 Project Operations. Oracle, SAP, Palantir, and Cayra are treated as adjacent/category checks, not ranked as direct alternatives.
- **Important limit:** This is public-source desk research. No product trials or demos, customer interviews/references, independent capability tests, or BusinessFlow product tests were performed.

## Executive read

**The broad workflow is already substantially served by professional-services automation (PSA) and project-operations products.** Kantata markets pre-contract scoping, delivery-readiness, resource/capacity analysis, margin forecasts, and scenario comparisons. Projectworks connects opportunities/proposals to capacity, billing, forecasts, and margin; one of its vendor-published customer stories describes using unsigned opportunity projects and “shadow resourcing” to decide which tenders to pursue. Microsoft Project Operations documents project quote estimates and a new, explicitly **preview** what-if workflow that compares quote revenue, cost, gross margin, and budget variance across staffing/pricing scenarios.

Cash timing is not an obvious empty category either: Kantata markets DSO, accounts receivable and cash-flow improvement; Projectworks describes resource-based predictive invoicing and syncs actual payment information from accounting systems; Microsoft’s integrated project-accounting documentation includes forecast and actual project cash flows and expected payment dates. These are not all equivalent to a reliable, customer-specific cash-collection-risk assessment before commitment, but they materially weaken a claim that “cash-aware project decisions” are wholly unserved.

**No reviewed public source establishes a general-purpose, reliable automatic accept/reject recommendation spanning all of these inputs.** That narrower distinction is not yet a demonstrated gap or moat. Public product pages describe features, not independently tested performance; customer outcomes are vendor-reported. The possible wedge—an evidence-backed, human-controlled decision across a customer's existing CRM/PSA/ERP, including cash timing—remains a hypothesis requiring customer discovery and product trials.

**Interim conclusion:** Do not position BusinessFlow as a generic “business decision layer,” or treat the existing project-acceptance MVP proposal as a validated product requirement. The evidence supports a discovery decision, not a go-to-market verdict and not implementation.

## Evidence labels and comparison rule

- **[D] Official product documentation/help:** establishes what the vendor documents; it does not establish runtime performance or customer outcomes.
- **[M] Vendor product/marketing material:** establishes the vendor’s current public claim; verify release, SKU, configuration, and behavior in a trial/demo.
- **[C] Vendor-published customer story:** attributed customer quote or account of reported use/outcome; not an independent audit or causal evaluation.
- **[B] Benchmark/research summary:** market context with the source’s sample, sponsor, and methodology limitations.
- **[NR] Not found in the public pages reviewed:** a scoped research gap, not proof that the product lacks the capability.

“Overlap” below means a documented or marketed workflow component exists. It does **not** mean BusinessFlow has been tested against it or that feature parity, availability, reliability, or customer adoption is independently verified.

## At-a-glance comparison

This is a capability-evidence map, not a feature score or quality ranking. “Not established” is limited to the public pages reviewed and is not evidence of product absence. Availability, deployment/SKU, configuration, and behavior need direct verification.

| Decision dimension | Kantata | Projectworks | Microsoft Dynamics 365 Project Operations |
|---|---|---|---|
| **New-work acceptance / intake** | SX markets pre-work scoping, delivery readiness, capacity/profitability inputs and new-work scenarios; no general automatic go/no-go recommendation verified. [M][NR] [K1][K2] | CRM/proposals connect pipeline to accepted-proposal project/budget; CT Management’s vendor case describes unsigned-opportunity “shadow resourcing” to judge tender feasibility. This is a human workflow, not an evidenced automatic recommendation. CRM/proposals are still marked beta on pricing despite “available” messaging. [M][C] [PW1][PW2][PW3] | Quote estimates and a quote what-if workflow support pre-award analysis; the scenario feature is preview, and no general automatic acceptance recommendation was established. [D][NR] [MS1][MS2] |
| **Capacity / staffing** | Markets role/skill forecasting, soft-booking on unsold work, AI staffing optimization, and internal/external candidate comparisons by availability, skills and margin. [M] [K1][K3] | Markets live capacity, skills, forecasts and Kea matching/rebalancing; Kea is marked beta on pricing. [M] [PW2][PW5] | Docs cover resource profiles/skills, requests, bookings, availability and matching/substitution workflows. [D] [MS4] |
| **Margin / project economics** | Markets project cost, revenue, margin and financial what-if analysis. [M] [K4][K5] | Markets actual/projected burn, revenue and margin; glossary distinguishes invoice forecasts from recognized revenue and related margin bases. [M][D] [PW6][PW9] | Quote estimates include sales/cost amounts; preview what-if exposes revenue, cost, gross-margin and budget-variance deltas; project financial budgets/forecasts are also documented. [D] [MS1][MS2][MS3] |
| **Scenario comparison** | Publicly markets financial what-if and staffing scenarios; not product-tested here. [M] [K1][K3][K4] | Reporting markets showing how resourcing/billing changes affect outcomes. A versioned, side-by-side, multi-option intake simulator was not established in reviewed pages. [M][NR] [PW5][PW6] | Docs describe saved quote scenarios, side-by-side KPI deltas and applying a chosen case to a draft quote; feature is prerelease/preview, environment-flagged and limited to draft quotes with estimates. [D] [MS1] |
| **Cash pressure / timing** | Markets cash-flow optimization, billing, receivables and DSO; a pre-award payment-risk/probability model was not established. [M][NR] [K4][K5] | Cash/invoice forecasts are based on scheduled work; accounting integrations sync actual payment information. The reviewed evidence does not establish calibrated collection-probability forecasting. [M][D][NR] [PW7][PW8][PW9][PW10] | Finance/project-accounting docs describe project cash forecasts/actuals and expected payment dates based on invoice terms and configured buffers; confirm Finance integration, SKU and deployment applicability. [D] [MS3] |
| **Management action / approval** | Markets risk/staffing insights and scenario recommendations; exact generally available action, write-permission and approval behavior was not tested. [M] [K1][K2] | Accepted proposals can seed a project/budget; CRM/proposals and Kea have beta status on pricing. A governed automatic go/no-go recommendation and follow-up action was not established. [M][NR] [PW1][PW2][PW5] | A selected quote case can be applied to a draft. The Approvals Agent is a preview for submitted time/expense/material policy review—not prospective project acceptance. [D] [MS1][MS5] |
| **Public price signal** | No public list price was established in the reviewed sources; no like-for-like TCO available. [NR] | USD view: **$17 / $24 / $41 per user/month** for Build / Scale / Unleash with annual billing; minimum 10 users, then billable in bands of five. The Unleash plan lists CRM/proposals as beta; Kea is also beta. Not a like-for-like TCO. [M] [PW2] | U.S. public list page: **$135 per user/month, paid yearly**. The pricing page states agent use requires Copilot Credits and an Azure subscription; regional availability/terms and implementation cost vary. Not comparable without deployment and scope alignment. [M] [MS6] |

## Direct-alternative workflow comparison

### 1. New-work acceptance / intake

- **Kantata — strong workflow overlap [M].** Kantata SX describes estimating new work before it begins; surfacing delivery-readiness, resource needs, capacity risks, and profitability drivers; comparing revenue/profit/risk scenarios; and modeling staffing, pricing, and discount strategies. Its public material documents analysis and recommendations, but the pages reviewed do not establish a general, authoritative “accept/reject this opportunity” score or an independent test of one. [K1][K2]
- **Projectworks — direct intake and feasibility workflow; partly customer-configured [M][C].** Its CRM/proposal material describes a pipeline, proposals, and creating a project from a won/accepted proposal with the proposal budget carried into delivery. The CT Management story says the customer entered potential tenders as unsigned opportunities, used shadow resourcing to assess feasibility, and made a management decision about whether to proceed. That is close to the proposed decision moment, but it is a customer-reported workflow using the product—not evidence of an automated recommendation engine. The current pricing page labels Consulting CRM and Proposals “in Beta,” while the feature page says they are available and carries a beta marker; current release status is inconsistent and needs a demo check. [PW1][PW2][PW3]
- **Microsoft Project Operations — detailed quote decision support [D].** Project quote and estimate documentation covers work, resources, dates, customer budget, costs, revenue, and margin. A quote can be modeled before being finalized. The reviewed pages document a human quote workflow, not an automatic business go/no-go recommendation. [MS1][MS2][MS3]

**Read-through:** The decision point and required input families are familiar PSA/project-operations workflows. A dedicated, cross-system recommendation is not verified as common—but neither is unmet demand for it.

### 2. Capacity and staffing feasibility

- **Kantata — strong [M].** Public pages describe forecasting roles and skills, soft-booking resources on unsold work, AI staffing optimization across projects, and comparing internal versus external candidates by skills, availability, and margin impact. These are especially close to the proposed current-team/outsource/hire alternatives. [K1][K3]
- **Projectworks — strong [M][C].** The product describes capacity views, skill mapping, leave/HRIS inputs, placeholders, utilization and demand forecasting, and an in-app Kea agent that matches people to work and rebalances a plan. Its pricing page still labels Kea an AI Agent “in Beta.” LifeSci Consulting’s vendor-published story reports using future availability to staff proposals; the story says margin analysis was a future step for that customer at that time. [PW2][PW4][PW5]
- **Microsoft Project Operations — strong [D].** Its resource-management documentation describes skills matching, qualified bookings, substitutions, schedule/assignment reconciliation, out-of-office changes, utilization history, and skills/proficiency profiles. [MS4]

**Read-through:** Capacity alone is not an evident differentiator. Skill/time-phased capacity combined with a decision outcome might still be valuable, but the frequency, severity, and unmet part of that workflow are unvalidated.

### 3. Margin and project profitability

- **Kantata — strong [M].** It markets project/portfolio revenue, cost and margin visibility, forecast-to-completion analysis, and scenario modeling. Hakkoda and BTM Global stories report customer outcomes; those figures remain vendor-presented and are not independently measured here. [K4][K5][K6][K7]
- **Projectworks — strong [M].** It describes projected burn, revenue and margin by project/person/company; project cost rates; and profitability forecasts based on resourcing and billing. Its published glossary distinguishes invoice forecasts from recognized revenue and describes separate invoicing- and recognition-basis margin calculations. [PW6][PW7][PW9]
- **Microsoft Project Operations — strong [D].** Project quote lines contain estimated sales and cost amounts; the quote what-if preview recalculates estimated revenue, cost, gross margin and budget variance. Project financial workflows also support project cost/revenue budgets and forecasts. [MS1][MS2][MS3]

**Read-through:** A generic “see margin before and during delivery” claim is not a defensible moat. BusinessFlow’s proposed formulas are also still candidate formulas requiring finance-owner approval; see `MVP_DECISION_MODEL.md`.

### 4. Scenario and alternative comparison

- **Kantata — strong public claim [M].** Kantata markets “What-If Modeling,” portfolio staffing scenarios, and comparison of internal/external candidates by margin impact. Product availability, scenario data model, and actual behavior were not trialed. [K1][K3][K4]
- **Projectworks — partial public evidence [M][NR].** Its reporting page says users can see how resourcing or billing changes affect the bottom line. The pages reviewed did not document a versioned, side-by-side, multi-option project-intake simulator equivalent to the proposed `ScenarioVersion` contract. This is a “not established in reviewed pages” finding, not a claim that no such capability exists. [PW5][PW6]
- **Microsoft Project Operations — explicit but preview [D].** The “Model what-if scenarios for a project quote” documentation describes multiple saved quote scenarios, isolated from the original draft quote, with side-by-side revenue, cost, gross-margin and budget-variance deltas; a chosen scenario can be applied to the quote. The page is prerelease documentation; the feature is behind an environment flag and is limited to draft quotes with estimates. [MS1]

**Read-through:** Scenario modeling is already a stated capability at Kantata and Microsoft. A BusinessFlow advantage would need to be demonstrated in the cross-system decision, quality of inputs, treatment of uncertainty/cash, governance, or measurable workflow outcome—not the word “scenario.”

### 5. Cash pressure, billing, and collection timing

- **Kantata — relevant finance capabilities [M].** Kantata markets project cash-flow optimization, billing, accounts receivable, collections/DSO reduction, and financial forecasting. The pages reviewed do not establish a customer-specific, pre-award cash-at-risk calculation or payment-probability model. [K4][K5]
- **Projectworks — cash forecasting plus accounting-linked actual payments [M][D].** Its “predictive invoicing” article describes using projected resource hours and rates to forecast invoice/revenue timing and future cash position. The described mechanism is based on planned work/revenue, not demonstrated customer-specific collection probability; no independent accuracy validation was found. Projectworks also documents payment reconciliation from Xero/QuickBooks, invoice/debtor visibility, and a distinction between invoice forecasts and recognized revenue. [PW7][PW8][PW9][PW10]
- **Microsoft Project Operations — strong in integrated project-accounting context [D].** Finance/project-accounting documentation describes forecasting and monitoring actual project cash flows, with expected customer-payment dates based on invoice date, payment terms, and configured buffer days. This is a broader/integrated Finance workflow; availability should not be assumed for every Project Operations deployment or SKU. [MS3]

**Read-through:** A cash-pressure lens could be worth testing, but “cash flow” may mean scheduled billing or expected payment dates rather than validated liquidity risk or probability of collection. Define that distinction before comparing products or claiming whitespace. Existing systems already cover parts of the data and workflow.

### 6. Management action and approval

- **Kantata — agentic action claims [M].** Kantata describes recommendations and workflows for staffing, risk, and margin, with business processes and approval paths. The Expertise Engine page includes examples of projects flagging risk and staffing before a request. Exact generally available product scope, permissions, and write/action controls were not independently tested. [K1][K2]
- **Projectworks — connected workflow; AI maturity needs checking [M].** Accepted proposals can seed projects and budgets. Kea and the MCP connector are labelled Beta on the pricing page; the resource page markets matching and plan rebalancing. The pages reviewed do not establish a generally available, audited go/no-go recommendation followed by a governed internal follow-up action. [PW1][PW2][PW5]
- **Microsoft Project Operations — quote and transaction approvals, not the same decision [D].** The quote what-if flow can apply a chosen scenario to a draft quote. The Approvals Agent is a production-ready **preview** that classifies submitted time, expense, and material entries against policy, with optional auto-approval requiring testing. That is post-submission transaction review, not prospective project-acceptance advice. [MS1][MS5]

**Read-through:** A human decision record, source evidence, abstention, and a reversible internal follow-up action may be useful product requirements. Their existence in BusinessFlow does not by itself establish customer value or differentiation; the proposed action is still an owner-review item.

## Customer and market evidence — what it does and does not establish

### Customer stories

- **Projectworks / CT Management Group [C].** The vendor story reports use of unsigned opportunity projects and shadow resourcing to evaluate tender feasibility and decide whether to proceed; it also reports a change in tender volume and win rate. This is the clearest publicly reported example in this sweep of a customer operationalizing a pre-commitment feasibility decision inside a PSA. The source is vendor-curated; the account and outcomes were not independently corroborated.
- **Projectworks / LifeSci Consulting [C].** The vendor story reports that proposal preparation required seeing who would be available; the customer uses future resourcing/forecasting to staff new work. The same story says project-margin monitoring was still a next step for the team at that time. This supports a real capacity/proposal workflow, but it does not show that a complete cross-functional accept/reject recommendation is necessary or purchased.
- **Kantata / Hakkoda and BTM Global [C].** Vendor case studies report resource planning, longer forecast horizons, and margin/utilization results. These are evidence of reported PSA adoption and operational use; outcome magnitude, causality, transferability, and current product behavior are not independently verified here.
- **Microsoft customer evidence:** Product and case material shows Project Operations adoption in broader project-based organizations, but this sweep did not validate a customer’s use of an automated project go/no-go recommendation. Product adoption is not evidence of a standalone BusinessFlow demand.

### Market context

SPI’s 2026 PS Maturity Benchmark page describes a sample of 509 firms, 245,000+ consultants, and nearly $63B in professional-services revenue. Deltek’s summary of that benchmark reports 2025 billable utilization of 66.4% and project margin of 37.7%. This provides context for capacity and margin pressure; SPI/Deltek materials are not independent evidence that buyers want BusinessFlow or will pay for it. [B1][B2]

### Evidence gaps that remain decisive

No interviews, reference calls, product trials, paid pilots, or independent capability tests have been run. Still unvalidated: the target buyer and firm size; how often a specific project-acceptance decision occurs; its cost and delay; whether the failure is data quality, process ownership, or missing software; what current PSA/CRM/ERP configurations already do; integration appetite and access to cost/receivables data; trust and approval expectations; willingness to pay; and a differentiating outcome. The Software & IT Services ICP remains a hypothesis.

## Adjacent/category checks (not part of the three-direct-alternative comparison)

- **Oracle Fusion Project Management:** Oracle’s current product material describes AI-assisted project proposal generation, project planning, cost/revenue, staffing, billing, and program actions. Its 26D project-execution manual describes an editable AI-generated proposal covering objectives, scope, timeline, budget estimates, and staffing, based on project details including a high-level plan and budget. This is an adjacent ERP-native workflow, not proof of a general cross-vendor go/no-go recommender. [OR1][OR2]
- **SAP:** “Plan Customer Projects” documents planning, contract-preparation, and execution stages with work packages, resources, cost/revenue calculation, and financial KPIs. Joule can read planned/actual cost, commitments, and revenue for specified project objects in SAP S/4HANA Cloud Private Edition. Availability depends on SAP release/configuration; Joule for this private edition requires RISE with SAP and an SAP-managed data center. [SA1][SA2][SA3]
- **Palantir Foundry:** Official docs describe governed operational apps/actions and scenario comparisons; Ontology Scenarios are explicitly Beta and may not be available to every enrollment. This is a broad platform benchmark, not a PSA product or proof that a small-services buyer wants another decision layer. [PA1]
- **Cayra:** Its product page describes natural-language questions against configured schema and database permissions, with query evidence available for human review. This is adjacent on analytics/briefings; the source does not establish a full project-acceptance recommendation workflow. [CA1]

These adjacent capabilities further weaken a broad “decision layer” positioning. They do not establish that any one incumbent solves BusinessFlow’s exact hypothesized cross-system workflow end to end.

## Bounded discovery hypothesis — not positioning or an MVP decision

A testable but unvalidated hypothesis is: **some project-based services firms may need a vendor-neutral, evidence-linked human decision brief for a new engagement that combines time-phased capacity, finance-approved direct-margin calculations, scenario alternatives, and billing/collection timing from systems they already use—without replacing their PSA/CRM/ERP.**

This is deliberately not presented as a differentiated market need. The closest competing systems already cover many inputs, scenarios, workflows, or cash-related operations. Discovery should determine whether customers actually cannot configure/use those tools for the decision, whether the missing integration is valuable enough to buy, and whether BusinessFlow can obtain sufficiently reliable/authorized inputs.

## Recommended validation work before any Product Baseline

If the founder authorizes discovery, keep it research-only and compare the same realistic, de-identified decision case across no more than the three direct alternatives above. Suggested questions for buyers and operators:

1. Walk through the last real tender/project you accepted, declined, delayed, or reshaped. Who made the call, on what date, and what happened next?
2. What evidence was used for demand, role/skill capacity, delivery dates, price, cost, margin, billing milestones, receivables, and cash timing? Which systems and spreadsheets supplied it?
3. What was uncertain or stale, who resolved it, how long did the decision take, and what was the measurable consequence of being wrong or late?
4. How do you represent unsold opportunities, compare internal staff with subcontracting/hiring/phase changes, and record the decision? Which parts are recommendations versus human judgment?
5. What can your current PSA/CRM/ERP already do for quote estimates, staffing, margin, what-if scenarios, and invoice/cash forecasts? What did you configure, trial, reject, or work around?
6. Do you need invoice timing, expected payment dates, historic collection behavior, or actual available cash? Which of these can you legally and operationally share with a separate tool?
7. What would need to be true for a separate tool to be trusted and budgeted? What concrete behavior, evidence, or outcome—not a feature list—would justify replacing the current process?

Set interview sample, success criteria, data access, trial accounts, and any spend with the founder before starting. Do not treat positive reactions, vendor case studies, or benchmark results as purchase validation.

## Source register

Sources were accessed on 2026-11-21 unless the page states a publication/update date. Official product pages are vendor claims; help/documentation is evidence of documented feature scope. No source below substitutes for a demo or independent validation.

### Kantata

- **[K1]** [Kantata SX](https://www.kantata.com/psa/kantata-sx) — scoping, pre-work demand/readiness, capacity, pricing, delivery scenarios, staffing, margin.
- **[K2]** [Kantata AI Expertise Engine](https://www.kantata.com/ai/expertise-engine) — agentic BI, risk/staffing examples, business-data claims.
- **[K3]** [Resource forecasting and staffing](https://www.kantata.com/solutions/resource-forecasting-and-staffing) — unsold-work skills profiling, staffing optimizer, internal/external candidate comparison.
- **[K4]** [Project accounting](https://www.kantata.com/psa/financial-management-software/project-accounting) — What-If Modeling, margins, billing, DSO, AR and integrations.
- **[K5]** [Financial Management & Visibility](https://www.kantata.com/psa/financial-management-software) — forecasting, cash-flow, invoicing, collections and DSO marketing claims.
- **[K6]** [Hakkoda success story](https://www.kantata.com/success-story/hakkoda) — vendor-reported case and customer quotation.
- **[K7]** [BTM Global success story](https://www.kantata.com/success-story/btm) — vendor-reported case and customer quotation.
- **[K8]** [Enterprise Times on Hakkoda and Kantata](https://www.enterprisetimes.co.uk/2023/08/10/hakkoda-moves-to-kantata-and-drives-profitability-and-improves-forecasts/) — external trade-press account; not an independent outcome audit.

### Projectworks

- **[PW1]** [Consulting CRM and Proposals](https://www.projectworks.com/features/crm-and-proposals) — pipeline, proposal-to-project handoff, budgets; page has conflicting availability/beta signals.
- **[PW2]** [Pricing](https://www.projectworks.com/pricing) — current plan feature flags and published beta status; check live terms and currency before relying on price.
- **[PW3]** [CT Management Group case study](https://www.projectworks.com/case-studies/ct-management-group) — unsigned opportunities, shadow resourcing, tender feasibility and reported customer use.
- **[PW4]** [LifeSci Consulting case study](https://www.projectworks.com/case-studies/lifesci-consulting) — proposal staffing, future capacity and customer-reported margin-analysis plans.
- **[PW5]** [Resource management](https://www.projectworks.com/features/resourcing) — live capacity, skills/leave, Kea and forecasts (vendor marketing).
- **[PW6]** [Project reporting](https://www.projectworks.com/features/reporting) — forecast revenue/burn/margin and reporting claims.
- **[PW7]** [Invoicing](https://www.projectworks.com/features/invoicing) — billing, WIP, accounting integration and profitability claims.
- **[PW8]** [Predictive invoicing and cash-flow article](https://www.projectworks.com/blog/accurately-forecast-next-months-cash-flow-with-predictive-invoicing) — dated 2026-04-01; headline accuracy claim lacks a published validation method.
- **[PW9]** [Glossary of Projectworks terms](https://help.projectworks.com/knowledge/glossary-and-definitions) — contractual status, invoice forecasts, recognized revenue and margin distinctions; dated 2026-09-20.
- **[PW10]** [Xero integration](https://www.projectworks.com/integrations/xero) — payment reconciliation and finance data flow.

### Microsoft Dynamics 365 Project Operations

- **[MS1]** [Model what-if scenarios for a project quote (Preview)](https://learn.microsoft.com/en-us/dynamics365/project-operations/sales/quote-what-if-analysis) — prerelease, feature-flagged quote scenarios, KPI deltas and apply workflow.
- **[MS2]** [Estimate a project quote line](https://learn.microsoft.com/en-us/dynamics365/project-operations/pro/sales/create-estimate-quote-line-sales) — estimate details and sales/cost prices.
- **[MS3]** [Project management and accounting overview](https://learn.microsoft.com/en-us/dynamics365/project-operations/prod-pma/overview-project-management-accounting) — project cash-flow forecast/actuals and payment-date logic in the Finance/project-accounting context.
- **[MS4]** [Resource management key concepts](https://learn.microsoft.com/en-us/dynamics365/project-operations/resource-management/resource-management-key-concepts) — skills, booking, substitutions, utilization and resource management.
- **[MS5]** [Approvals Agent overview (Preview)](https://learn.microsoft.com/en-us/dynamics365/project-operations/approvals/approvals-agent-intro) — time/expense/material policy classification; does not document prospective acceptance advice.
- **[MS6]** [Project Operations pricing](https://www.microsoft.com/en-us/dynamics-365/products/project-operations/pricing) — U.S. public list page shows $135/user/month paid yearly; agent usage requires Copilot Credits and an Azure subscription. Regional availability, deployment and implementation/TCO can vary.

### Adjacent/category checks

- **[OR1]** [Oracle Fusion Cloud Project Management 26D — Using Project Execution Management (PDF)](https://docs.oracle.com/en/cloud/saas/project-management/26d/oapem/using-project-execution-management.pdf) — AI-generated, editable project proposal in the documented release.
- **[OR2]** [Oracle Project Management](https://www.oracle.com/erp/project-management/) — AI-assisted project proposal, planning, cost/revenue, staffing and billing claims.
- **[SA1]** [SAP Plan Customer Projects](https://help.sap.com/docs/SAP_S4HANA_CLOUD/29cf986299714386847f4d4c9bb86994/21218854c46ee75ee10000000a44176d.html?locale=en-US&version=2608.500) — project phases, resources, cost/revenue calculation and financial KPI documentation.
- **[SA2]** [SAP Joule project cost/revenue capability](https://help.sap.com/docs/joule/capabilities-guide/read-cost-and-revenue-details-of-project-wbs-element-network-network-activity) — reads planned/actual costs, commitments and revenue.
- **[SA3]** [Joule constraints for SAP S/4HANA Cloud Private Edition](https://help.sap.com/docs/joule/integrating-joule-with-sap/constraints) — release/deployment conditions.
- **[PA1]** [Palantir Ontology Scenarios (Beta)](https://www.palantir.com/docs/foundry/ontology/overview-ontology-scenario) — what-if scenarios, governed actions and explicit beta limitation.
- **[CA1]** [Cayra “How it works”](https://cayraai.com/product/how-it-works) — configured-source natural-language analysis; adjacent, not a verified acceptance workflow.

### Market context

- **[B1]** [SPI 2026 PS Maturity Benchmark](https://spiresearch.com/reports/2026-ps-maturity-benchmark/) — vendor/research-provider benchmark page and sample description.
- **[B2]** [Deltek’s 2026 PSO benchmark summary](https://www.deltek.com/resources/articles/professional-services-benchmarks/) — summary of 2025 results; Deltek sponsors the benchmark.

## Status preserved

- No product verdict, ICP, final positioning, MVP scope, or Product Baseline is selected.
- `MVP_DECISION_MODEL.md` remains an unapproved domain/decision proposal; this research does not approve its formulas or policies.
- The prior technical assessment and technical baseline proposal remain unchanged and unapproved.
- `REQUIREMENTS_TRACEABILITY.md` remains **BLOCKED — awaiting the exact original Part 2 requirements**. This work does not reconstruct or replace them.
- No code, package, test, connector, migration, deployment, or product implementation was added or authorized.
- Stop here for founder review before any discovery spend, product decision, or implementation.
