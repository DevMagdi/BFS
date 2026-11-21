# ADR 0004: Keep BusinessFlow calculations and action governance deterministic

- **Status:** Product constraint for this assessment; it does not mean the architecture, technical baseline, ADR set, or implementation has been accepted. Implementation details remain deferred to the relevant milestones.
- **Date:** 2026-11-21

## Context

BusinessFlow AI must explain business decisions and may use language models for interpretation, contextual assistance and explanation. Project profitability, capacity, cash, permissions, validation, approvals and action execution affect real businesses and must be traceable.

## Decision

The BusinessFlow-owned deterministic domain/metrics/decision code is the source of truth for calculations, constraints, data-quality gates, approval requirements and execution state. A model may call narrow authorized tools and explain validated results. It may not invent facts, produce authoritative financial values, alter permissions, approve its own recommendation or execute privileged operations directly.

## Consequences

- Each result records its calculation/rule version, inputs, source lineage, assumptions, limitations and quality state.
- Insufficient/conflicting data may block a recommendation.
- Model prompts and responses are not canonical business records unless explicitly reviewed and typed as such.
- Action approval, execution attempt, verification and measured outcome are stored as distinct records.
- Any future semantic SQL layer is a read-only adapter and cannot replace BusinessFlow formulas or decision policies.
