# ADR 0002: Keep Tenant and Company separate with strict 1:1 MVP cardinality

- **Status:** Owner-confirmed direction for the assessment; implementation approval is pending.
- **Decision:** `Tenant` and `Company` are separate entities. Enforce exactly one company per tenant in the MVP.
- **Date:** 2026-11-21

## Context

BusinessFlow AI needs an authorization/data-isolation boundary and a model of the real business whose operational and financial context is analyzed. The owner confirmed that `Tenant` represents the customer/security boundary and `Company` represents the actual business entity. The MVP must have one company per tenant, while future multi-company support and group-level consolidation should be possible without redesigning the entire domain model. Consolidation is not in the MVP.

## Decision

Keep `Tenant` as the root customer/security boundary and `Company` as a distinct child business entity. For the MVP, enforce a strict 1:1 relationship: each active tenant has exactly one company. Keep `tenant_id` on all tenant-owned rows and `company_id` on company-scoped facts. Use tenant-aware composite foreign keys so a company-scoped row cannot reference a company from another tenant.

The 1:1 rule is an MVP cardinality constraint, not a reason to collapse the entities or bake one-company assumptions into financial facts, domain services, or APIs. In a future multi-company version, the cardinality may expand to one tenant / many companies; group-level reporting would require explicit, versioned consolidation rules and is not enabled now.

## Reasoning

The separation gives the product an independent customer/security boundary and a clear business entity for currency, timezone, legal/operational profile and company-scoped facts. The explicit `tenant_id` + `company_id` model allows future company-scope authorization and expansion to multiple companies while preserving canonical facts at their source-company level.

## Trade-offs and consequences

- A unique constraint on `company.tenant_id` enforces at most one company in the MVP. Tenant provisioning must create tenant and company atomically and activate the tenant only after both exist.
- Removing that unique constraint is a future schema change; the domain must continue to scope company facts explicitly so this is not a wholesale redesign.
- Tenant-level aggregation is not automatically valid accounting consolidation. Future group reporting must address currency/FX, ownership/effective dates, fiscal periods, intercompany transactions/eliminations and metric semantics.
- Company-specific permissions are retained in the model even though only one company exists per tenant in the MVP, to avoid coupling domain authorization to the MVP cardinality.
- Multi-company access, selection, consolidated reporting and intercompany accounting are explicitly out of MVP.

## Approval gate

The owner confirmed this boundary/cardinality for the assessment. The overall assessment remains in review; no schema implementation or Milestone 0 work is approved yet.
