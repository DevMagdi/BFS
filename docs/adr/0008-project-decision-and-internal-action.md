# ADR 0008: Historical project-acceptance proposal — abstention and action boundary

- **Status:** Historical proposal for the earlier project-acceptance hypothesis. It is not the current product direction; see [`../product/PRODUCT_VISION.md`](../product/PRODUCT_VISION.md) and [`../roadmap/PRODUCT_ROADMAP.md`](../roadmap/PRODUCT_ROADMAP.md). The proposal remains subject to finance and owner approval. M6–M8 implement bounded project-specific slices only; they do not implement or approve this broader recommendation/action workflow. Hire/outsource options and the internal task were conditional candidates, not current release requirements.
- **Date:** 2026-11-21
- **Detailed contract:** `../MVP_DECISION_MODEL.md` and `../MVP_ENTITY_CATALOG.md`

## Context

The first decision hypothesis is “Should we accept this new project?” for Software & IT Services. The initial model lists many potentially relevant facts but does not freeze minimum inputs, calculation definitions, formula ownership, abstention behavior, or the first action. Project finance and operational policies vary and must not be invented.

## Proposed decision

- Return `recommendation_ready`, `insufficient_reliable_data`, or `not_authorized`; do not return a preferred option when a required fact, calculation, policy, or currency conversion is missing/unreliable. If the caller lacks permission, return `not_authorized` without disclosing protected facts.
- Require time-phased scope/effort and skill needs, validated capacity and commitments, comparable proposed commercial terms, an approved cost basis, direct cost evidence, data quality/lineage, and approved formula/policy versions.
- Keep contract price, recognized revenue, invoice, receivable and cash receipt distinct. Proposed contribution/margin and cash-exposure calculations are candidate metrics pending finance-owner approval. No universal thresholds are set here.
- The provisional core compares current resources, manual reallocation, phase/defer and decline. Hire/outsource are conditional/Should Have alternatives only when their required inputs exist; scenario edits create immutable versions.
- Treat a human-approved, reversible BusinessFlow-internal follow-up task as an optional candidate, not a required first-release action. It may resolve missing evidence without changing a customer system or making a contractual commitment. If separately included, use idempotency, attempts, cancellation, separate read-back verification and audit.
- If the internal task has no product value, defer action execution and ship recommendation-and-approval-only. High-impact/irreversible actions remain excluded.

## Reasoning

An abstention-first decision protects the product from fluent but unsupported financial claims. A local follow-up task is reversible and avoids external side effects while testing the approval/action lifecycle. It remains optional/Should Have under the reviewed provisional scope, not a confirmed product requirement.

## Consequences and approval gates

- Finance owner must approve capacity, cost, revenue recognition, contribution/margin, FX, collection exposure, data quality and recommendation thresholds before any broader project-acceptance recommendation behavior or product acceptance. This gate does not describe the bounded M5 observational metrics or M6 human capacity-only comparison; neither produces a project recommendation or commercial decision.
- Product owner must confirm the MVP input set and whether a follow-up task is useful.
- Human approval must bind an authenticated person to the exact payload, decision/scenario versions and policy.
- The internal task is not implemented, and this ADR does not authorize any code or Milestone 0 work.
