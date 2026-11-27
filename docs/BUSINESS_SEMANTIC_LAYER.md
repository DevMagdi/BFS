# Business Semantic Layer — identity and relationship foundation

This document describes the identity-and-relationship foundation of the Business Semantic Layer:
Company-owned semantic identities, reviewed source mappings, and versioned explicit relationships.
The separate Phase 3 Business Intelligence API builds on this foundation for versioned metric
definitions, Company configuration, readiness, deterministic saved analyses, replay, and evidence
references. See [`BUSINESS_INTELLIGENCE_PHASE3.md`](BUSINESS_INTELLIGENCE_PHASE3.md) for that
increment's API, calculation, authorization, and verification boundaries. Neither slice is a
general-purpose analytics engine or BI workspace.

## Scope and API

All routes are under `/api/v1/tenants/{tenant_id}/business-semantic`:

- `GET /catalog` — the supported entity/relationship vocabulary and its evidence boundaries.
- `GET, POST /entities` — list and create semantic identities.
- `GET, POST /mappings` — review a source-to-identity binding and inspect its current version or
  history (`include_history=true`).
- `GET, POST /relationships` — create and inspect explicit relationships and their versions;
  history is available with `include_history=true`.

The static catalog and ordinary Company Context identities use `company_context:read`; writes require `company_context:write`. Employee identities and employee-linked mappings/relationships additionally require `workforce:read`. Universal Data-backed identities, mappings, and relationships require `universal_data:read`; without it, broad lists omit those records and explicit Universal Data filters fail closed. The default role matrix grants both `workforce:read` and `universal_data:read` only to owners and admins. This separates raw source-data access from customer-data processing approval: BI processing can remain blocked even for an authorized source reader. The authenticated tenant resolves to its one active Company; clients cannot choose a different Company. The API also applies the existing seeded-demo write guard.

The built-in vocabulary covers Company Context business units, departments, employees, and
projects, plus Universal Data customer, supplier, product, sales-transaction, expense,
business-activity, and operational-record references. Semantic identities, mappings, and
relationships are append-only. The current type vocabulary is versioned in code; controlled
releases can extend it without putting user-supplied code or query logic into the runtime. Corrections
create a new mapping or relationship version rather than overwriting reviewed history.
Mapping and relationship writes use `expected_version` so stale concurrent edits fail explicitly.

## Identity mapping and provenance

A semantic identity is Company-scoped and has a caller-supplied stable key and display label. A
source record is never joined to an identity based on a name, email, tax identifier, fuzzy match,
or guessed mapping. A mapping request must cite an existing same-Company record and include a
human review note. Changing an existing target identity additionally requires a correction reason.

Universal Data mappings retain the exact Company Data source, canonical record, immutable record
revision, and Universal Data mapping-profile version reviewed. The mapping response reports both
whether its mapping version is current and whether its pinned source revision is still current. If
Universal Data advances the record revision, the semantic mapping becomes stale until an authorized
reviewer appends a refreshed mapping version. Phase 2 remains the source of record and lineage;
this layer does not duplicate ingestion, source snapshots, validation, or promotion.

Company Context references are scoped to existing business-unit, department, employee, and project
rows. Those existing models remain authoritative for their operational facts; the semantic mapping
only records a reviewed identity link.

## Relationships and evidence

The initial verifiable relationships are deliberately bounded:

- Customer → sales transaction and supplier → expense require the matching non-empty source code
  in both cited records, the same Company Data source, and current reviewed source revisions.
- Employee → department and department → business unit require the current Company Context foreign
  key to match the reviewed references.
- Project → employee requires a committed or completed Company Context resource assignment; a
  proposed assignment is not sufficient.
- Project → manager employee requires the current `Project.project_manager_employee_id` reference to
  match the mapped employee. The relationship type is versioned in the semantic catalog; the
  project-manager reference alone is not exposed to callers without workforce access.

A `source_verified` relationship must cite the exact reviewed mapping versions. On reads, the
service rechecks that the relationship version and mappings are current and that the stored source
fact still supports the edge. If source records or operational references change, the old edge is
retained for lineage but `analytically_usable` becomes false until it is reviewed again. A
`manual_assertion` remains visibly distinct and is never analytically usable. Retractions are new
relationship versions.

## Security and interpretation boundaries

The tables use forced PostgreSQL row-level security on both tenant and Company context, composite
Company/tenant foreign keys, same-scope references, and database append-only triggers. Runtime
permissions are limited to the operations needed by the API. Mapping and relationship changes are
audited without copying source values into audit metadata. Migration `0024_company_wide_intelligence`
extends the allowed relationship vocabulary with the verified project-manager edge and adds a
Company-scoped workforce-sensitivity marker used to protect saved BI analysis snapshots. The marker
is backfilled from People/M5 definitions, pinned dependencies, and employee-linked relationships;
legacy BI input snapshots remain immutable.

A Universal Data amount is still a source amount, not an approved accounting fact, revenue,
expense, margin, or cash measure. Semantic identity and relationship evidence do not create
financial authority, establish completeness, imply accuracy, or prove causality. The existing
six-measure Financial Engine remains the only implementation of its supported financial
calculations; this semantic slice does not change its scope or authority.

The identity-and-relationship slice itself does not provide custom SQL, LLM-authored calculations,
automatic identity resolution, unsupported entity types, inferred relationships, or cross-source
conflict resolution. The separate BI increment now provides the bounded metric/readiness/analysis
capabilities described above, but not a BI workspace or those unsupported identity, query, and
calculation behaviors. It must retain the evidence, authorization, and readiness boundaries in this
foundation.
