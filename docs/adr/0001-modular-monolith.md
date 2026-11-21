# ADR 0001: Start with a modular monolith and PostgreSQL

- **Status:** Proposed; owner review required before Milestone 0 baseline is frozen.
- **Date:** 2026-11-21

## Context

BusinessFlow AI needs a canonical multi-tenant business model, transactional approvals/audit, integrations and deterministic business calculations. The product is greenfield and has no demonstrated load or team boundary requiring independent services. The target stack suggests FastAPI/Python and PostgreSQL; no infrastructure is present in the checkout.

## Problem

Choose a system structure and primary transactional store that supports database-first delivery, tenant isolation, data lineage and a controlled action lifecycle without premature operational complexity.

## Options considered

1. Modular monolith with PostgreSQL and explicit module/service boundaries.
2. Microservices and distributed event infrastructure from the beginning.
3. A BI/agent/workflow platform as the product foundation.

## Decision

**Propose option 1.** Use a Python/FastAPI modular monolith and PostgreSQL as the transactional and canonical source. Keep modules separable by interfaces and tests. Add an internal PostgreSQL outbox worker only when asynchronous action work is introduced. Frontend, analytics engines, model providers and connectors enter only at their approved milestones. The specific component/version recommendation is recorded separately in [ADR 0007](0007-proposed-technical-baseline.md) and `../TECHNICAL_BASELINE_PROPOSAL.md`; it remains owner-review-only.

## Reasoning

This meets the multi-tenant relational, constraints, transaction and RLS needs while keeping deployment/debugging simple. It preserves ownership of the business model and decision engine. Options 2 and 3 add complexity or delegate product-specific logic before evidence demonstrates a need.

## Trade-offs and consequences

- A shared database requires strict service/repository discipline and tenant-aware migrations.
- A module boundary is architectural, not a network/security boundary; application authorization and RLS remain mandatory.
- A single database may eventually need read replicas or dedicated analytics infrastructure; benchmark before adding them.
- PostgreSQL is a deliberate core dependency; migration tests must use real PostgreSQL.

## Approval gate

Owner confirms or changes the proposed baseline before Milestone 0 locks dependency/tool choices. No database or runtime dependency is installed by this ADR.
