# Company Data Foundation — Phase 2: Universal Data

Phase 2 adds governed intake and source-scoped canonical records to the Company Data Foundation. This is separate from the preceding manual customer-commitment workflow (see [`COMPANY_DATA_FOUNDATION_PHASE1.md`](COMPANY_DATA_FOUNDATION_PHASE1.md)).

## Scope

- Tenant/Company-scoped Universal Data sources with append-only profile history, source metadata, and explicit opaque connection/credential references.
- Safe, bounded CSV/XLSX upload plus an optional read-only local-directory connector rooted at an operator-configured directory with Company-UUID subdirectories.
- Explicit parser options, versioned mappings, row-level validation, staged evidence, and reviewer-controlled promotion.
- Source-scoped canonical business records, immutable revisions, ingestion links, and record lineage.
- A Universal Data Hub surface within `/data-foundation.html`.

This is not a general-purpose connector framework, live synchronization service, source-authority verification, entity-resolution/merge engine, financial fact model, or AI workflow. Cross-source matches are only review warnings. Universal records do not become Core Financials facts or M4 imports.

## Product and API guide

See [`UNIVERSAL_DATA_FOUNDATION.md`](UNIVERSAL_DATA_FOUNDATION.md) for the detailed entity types, parser/mapping semantics, security controls, migration, endpoints, deployment constraints, and verification boundary. The source, service, and route modules live under `src/businessflow/universal_data/` and `src/businessflow/api/universal_data_routes.py`.
