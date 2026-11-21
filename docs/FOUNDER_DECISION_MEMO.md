# Founder Decision Memo — Phase 1 Product Validation

- **Decision state:** **AUTHORIZED — bounded discovery only.** Founder selected this option on 2026-11-21 (UTC); no implementation was authorized.
- **Prepared:** 2026-11-21 (UTC).
- **Related evidence:** [`PHASE_1_COMPETITIVE_VALIDATION.md`](PHASE_1_COMPETITIVE_VALIDATION.md).
- **Purpose:** Record the founder’s decision to authorize bounded discovery only for the proposed BusinessFlow project-acceptance workflow and define its limits. This memo is not a market verdict, Product Baseline, or technical approval.

## Decision question

Should BusinessFlow pursue a product for project-based service firms that helps a human decide whether to accept, reshape, delay, outsource, hire for, or reject a proposed project by combining capacity, direct-margin, scenario, and cash-timing evidence?

The Software & IT Services segment and this product scope remain hypotheses. The current `MVP_DECISION_MODEL.md` describes a proposed calculation contract, not a customer-validated feature set or approved finance policy.

## Evidence-tagged assessment

1. **The broad workflow has substantial direct product overlap.** Kantata markets pre-contract project scoping, capacity/readiness, margin analysis, what-if scenarios and staffing comparisons. Projectworks connects pipeline/proposals with resource planning, invoicing, forecasts and margin, and a vendor-published CT Management story describes using unsigned opportunities and shadow resourcing to assess tender feasibility. Microsoft Project Operations documents quote estimates; its quote what-if feature is explicitly a preview and compares staffing/pricing scenarios using revenue, cost, gross-margin and budget-variance metrics.
2. **Cash features exist in adjacent parts of the same workflow.** Kantata markets cash-flow, billing, AR and DSO capabilities; Projectworks markets resource-based predictive invoicing and documents accounting payment sync; Microsoft’s integrated project-accounting documentation describes forecast/actual project cash flows and expected payment dates. The scope and quality of these capabilities differ, and no product trial or customer reference has verified them.
3. **No dedicated automatic accept/reject recommendation has been established in the reviewed public sources.** That is a bounded source finding, not proof of absence or an unmet need. The exact cross-system workflow has not been tested.
4. **Market-pressure evidence is not demand evidence.** SPI/Deltek benchmark data and vendor-curated customer stories indicate that capacity, margin, forecasting and manual workflow issues are discussed in the market. They do not establish a BusinessFlow buyer, frequency/severity of a distinct pain, integration appetite, willingness to pay, or a sustainable differentiation.
5. **Published price signals are not like-for-like TCO.** Projectworks lists USD $17/$24/$41 per user/month with annual billing (10-user minimum, then bands of five); Microsoft’s U.S. page lists $135 per user/month paid yearly, with Copilot Credits/Azure relevant to agents. A public Kantata list price was not established in this sweep. Verify local price, beta/GA scope, implementation and deployment before using these as comparisons. See the cited matrix in the validation report.
6. **Independent validation is absent.** No customer interviews, reference calls, hands-on product tests, or independent outcome measurements were conducted.

## Research recommendation (not a product verdict)

**Do not approve a generic “business decision layer” position, freeze a Product Baseline, or start implementation on the strength of this desk research.** The founder has authorized bounded discovery only. Use that discovery to test whether a narrow, vendor-neutral decision brief across existing CRM/PSA/ERP systems solves a real problem that buyers cannot already configure in their incumbent tools—especially whether cash timing changes a decision beyond scheduled billing, project forecasts, and ordinary receivables views.

This is a recommendation to gather evidence, not a recommendation that the product should be built. A positive interview response, a vendor case study, or an impressive demo is not sufficient validation by itself.

## Authorized discovery-only scope and study design

- Interview project-services owners/COOs, finance leaders, resource managers, and sales/BD leaders about a **recent real decision**, not an imagined feature request.
- Establish the actual decision cadence, alternatives, authority, current process, source systems, data freshness, failure cost, and consequences of a late/wrong call.
- Ask what the customer’s current PSA/CRM/ERP can already do for quote estimates, capacity, margin, scenario comparisons, and cash timing, and why its current configuration is inadequate.
- Keep incumbent comparisons to participant descriptions and public documentation for no more than Kantata, Projectworks, and Microsoft Project Operations. Do not create vendor accounts, book demos, or run trials under the current approval; any hands-on comparison requires separate explicit approval for account, contact, data, and spend as applicable.
- Separate project invoice forecasts, expected payment dates, actual collection history, and available cash. Do not describe one as another.
- **Study design selected:** 9–12 exploratory interviews across the listed roles; founder recruits and conducts; anonymized notes only; no numeric pass/fail threshold. Full limits are in [`PHASE_1_DISCOVERY_PROTOCOL.md`](PHASE_1_DISCOVERY_PROTOCOL.md). No implementation, integration build, customer-system/data access, vendor-demo booking, trial account, or spend is part of this authorization.

Qualitative questions for the exploratory synthesis (no numeric pass/fail threshold was selected):

- Can participants recount recent, repeated decisions with specific consequences?
- Does a decision-critical failure persist after accounting for the incumbent product’s available features and configuration?
- Can participants identify likely authoritative inputs and their data owners, and explain governance/security constraints? No customer data access is authorized in this phase.
- Is any proposed gap specific enough to define a future comparative test? No BusinessFlow outcome can be tested without later product/prototype approval.
- Can participants identify the potential buyer, evaluation process, budget authority, and required proof? Do not seek a paid-pilot commitment at this stage.

These questions guide an exploratory synthesis; they are not pass/fail tests. Any product or implementation decision requires a later founder review and separate approval.

## Founder decision recorded

The founder selected **Authorize bounded discovery only** on 2026-11-21 (UTC).

- [x] **Authorize bounded discovery only** within the scope below.
- [ ] Revise the hypothesis first.
- [ ] Pause or stop this product direction.

- **Authorized scope:** 9–12 exploratory interviews across the roles in [`PHASE_1_DISCOVERY_PROTOCOL.md`](PHASE_1_DISCOVERY_PROTOCOL.md); founder recruits and conducts; anonymized notes only, with no numeric pass/fail threshold.
- **Not authorized:** Implementation or product changes; customer-system access or confidential customer datasets; audio/video recording, identifying quotes, or raw customer records; agent-led participant/vendor outreach or demo booking; trial-account creation, paid trials, subscriptions, or other spend. Each excluded activity requires separate explicit authorization.
- **Owner and date:** Founder (name not supplied), 2026-11-21 (UTC).
- **Current status:** Interview parameters are recorded; interviews have not started. Founder will recruit and conduct.

## Independent gates and unchanged status

- This decision is separate from review/approval of the earlier technical assessment and technical-baseline proposal; both remain unapproved and unchanged.
- Exact Part 2 traceability is still **BLOCKED — awaiting the original 24 requirements verbatim**. Do not reconstruct or substitute a list.
- Finance-owner review is still required for the proposed decision formulas and policies in `MVP_DECISION_MODEL.md` before any recommendation behavior is accepted.
- No milestone, implementation, code, dependency, migration, connector, deployment, customer-system/dataset processing, or purchase is authorized by this memo.

**Next gate:** The founder may recruit and conduct the 9–12 interviews under [`PHASE_1_DISCOVERY_PROTOCOL.md`](PHASE_1_DISCOVERY_PROTOCOL.md) after setting the private note-storage and retention/deletion details required by that protocol. Keep notes anonymized and outside this repository; do not record, quote by identity, collect confidential/raw data, or request system access. No product/implementation work, agent-led outreach, vendor/demo booking, account creation, subscription, paid trial, or other spend is authorized; obtain separate explicit approval before any excluded activity.
