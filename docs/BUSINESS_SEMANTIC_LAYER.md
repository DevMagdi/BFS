# Business Semantic Layer — identity and relationship foundation

This document describes the first delivered Business Semantic Layer slice: Company-owned
semantic identities, reviewed source mappings, and versioned explicit relationships. It is a
foundation for governed metric and BI work, not a general-purpose analytics engine.

## Scope and API

All routes are under `/api/v1/tenants/{tenant_id}/business-semantic`:

- `GET /catalog` — the supported entity/relationship vocabulary and its evidence boundaries.
- `GET, POST /entities` — list and create semantic identities.
- `GET, POST /mappings` — review a source-to-identity binding and inspect its current version or
  history (`include_history=true`).
- `GET, POST /relationships` — create and inspect explicit relationships and their versions;
  history is available with `include_history=true`.

Reads require the existing Company Context read permission. Writes require the existing Company
Context write permission. The authenticated tenant resolves to its one active Company; clients
cannot choose a different Company. The API also applies the existing seeded-demo write guard.

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
audited without copying source values into audit metadata.

A Universal Data amount is still a source amount, not an approved accounting fact, revenue,
expense, margin, or cash measure. Semantic identity and relationship evidence do not create
financial authority, establish completeness, imply accuracy, or prove causality. The existing
six-measure Financial Engine remains the only implementation of its supported financial
calculations; this semantic slice does not change its scope or authority.

This slice does not provide custom SQL, LLM-authored calculations, automatic identity resolution,
unsupported entity types, inferred relationships, cross-source conflict resolution, metric
readiness, saved BI analyses, or a BI workspace. Those capabilities require explicit later Phase 3
tracks and must retain the evidence and readiness boundaries above.
