# ADR 0003: Enforce tenant isolation in both backend authorization and PostgreSQL RLS

- **Status:** Proposed; PostgreSQL/RLS implementation details and validation remain under review.
- **Date:** 2026-11-21

## Context

Cross-tenant data exposure would be a critical security failure. Frontend filtering, opaque UUIDs, ORM query filters or RLS alone do not provide a complete authorization design.

## Problem

Choose the tenant-isolation enforcement model for the modular monolith and future background workers.

## Options considered

1. Backend filters only.
2. PostgreSQL RLS only.
3. Backend permission checks plus fail-closed RLS and tenant-aware relational constraints.
4. Separate database/schema per customer from the start.

## Proposed decision

**Recommend option 3.** Derive tenant scope from a verified membership, authorize every backend operation, bind a tenant transaction-locally, and enable/force RLS on tenant-owned tables using a non-owner application role without `BYPASSRLS`. Use tenant-aware composite foreign keys and explicit cross-tenant tests. Keep system administration on a separate narrowly authorized and audited path.

## Reasoning

This combines request-level business authorization with a database backstop against omitted predicates. Transaction-local scope is compatible with pooled connections when correctly bound/reset. It is less operationally complex than database-per-tenant while retaining a future migration path if a customer requires stronger physical isolation.

## Trade-offs and consequences

- RLS policy coverage and migration tests become mandatory; SQLite cannot validate them.
- Background jobs must bind tenant context from a trusted persisted job record.
- Table owners and privileged roles can bypass ordinary RLS semantics; runtime credentials must not be privileged.
- RLS does not check business permissions or prevent an authorized user from accessing a disallowed company/field; service authorization still applies.

## Approval gate

The owner confirmed that Tenant and Company remain separate with strict 1:1 MVP cardinality. PostgreSQL and this RLS approach remain recommendations, not owner-approved technical decisions. The security design is not considered complete until the 1:1 constraints and cross-tenant, pool-reuse, background-job, missing-context, privilege, write-path and upsert tests pass against real PostgreSQL. No implementation is approved by this ADR.
