# Phase 2 — Universal Data Foundation

## Status and boundary

Migration `0019_universal_data_foundation` adds a bounded, Company-scoped workflow for registering source metadata, retaining CSV/XLSX evidence, explicitly mapping source columns to a versioned canonical business-record contract, validating rows, and promoting reviewed records with immutable source lineage. It is a separate Phase 2 increment beside the Phase 1 manual customer-commitment workflow; it does not turn the M12 workforce/project registry into a general importer.

Universal Data records remain source-scoped references. A source's registration, owner-selected authority classification, freshness metadata, successful validation, or reviewer acknowledgement does **not** prove source authority, business completeness, identity, accounting correctness, or customer-event truth. Cross-source matches are surfaced as review warnings only: no automatic identity resolution or merge occurs. Promoted amounts/transactions are not Core Financials facts, ledger entries, approved financial measures, or M15/M16 inputs.

This is not a general connector framework. Browser upload accepts CSV/XLSX; the only configured connector is a read-only local-directory adapter for an operator-approved root. Other source types and opaque connection/credential references are metadata only. No remote API, ERP, CRM, database, scheduler, background sync, automatic retry worker, external action, or AI/LLM capability is implemented.

## Source and contract model

Universal sources use `company_data_sources.dataset_type = 'universal_records'` and the immutable contract `businessflow.universal.business_records` version `1.0.0`. The dedicated Universal API owns their lifecycle; the legacy M12 source list/detail and M4 ingestion paths do not expose or manage them. M12 coverage remains limited to its existing workforce/project datasets.

Source profiles are appended as `company_data_source_profile_versions`. Each ingestion captures the profile-version ID used at staging. Connection and credential values must be opaque references (`config://` or `ref://` for connection configuration; `secret://`, `vault://`, or `kms://` for credentials); URL credentials and plaintext secrets are rejected. Responses expose only whether references are configured, never the reference values. `local_directory` sources use the fixed reference `config://universal/local-directory` and the operator setting `UNIVERSAL_LOCAL_CONNECTOR_ROOT`. Beneath that root, files are read only from a subdirectory named for the authorized Company UUID; the caller supplies a relative path within that Company directory, never a tenant or Company selector.

The canonical contract currently defines seven source-scoped entity types:

- **Customer** and **supplier** references with stable source codes and optional contact/registration fields.
- **Product** references with source codes, names, and optional category/unit/description.
- **Sales transaction** and **expense** references with source keys, dates, currency and exact decimal amounts.
- **Business activity** and **operational record** references with source keys, dates, type/status and source descriptions.

Required and optional fields are published through `GET /entities`. The `amount` field is a decimal string; no currency conversion, accounting classification, cash/revenue assertion, or financial calculation is performed.

## Ingestion, mapping, validation, and promotion

Direct file staging enforces the configured `IMPORT_MAX_FILE_BYTES` and `IMPORT_MAX_ROWS` limits and requires an `Idempotency-Key`. Retries with the same key return the original ingestion only when the normalized file name, format, byte fingerprint, and parser options match; changed input/configuration returns a conflict. The upload's MIME header is ignored: retained and downloaded content types are derived from the accepted `.csv`/`.xlsx` extension. Original bytes are stored as source evidence and downloaded with attachment, no-store, and `nosniff` response headers.

CSV parsing supports comma, semicolon, tab, or pipe delimiters; UTF-8 with optional BOM, UTF-16, or Windows-1252; and a user-selected header record from 1 through 25. XLSX uses the selected header row for each worksheet. Type inference is advisory only. Each non-empty row is preserved with a row hash; header-only uploads are rejected, while an empty selected worksheet produces a dataset-level validation blocker.

A mapping version must assign every displayed source column to one canonical field or explicitly ignore it. Date formats, decimal/thousands separators, text case, and trimming are explicit versioned options; ambiguous dates and decimal conventions are not silently guessed. Validation preserves row findings, counts, contract/mapping/source-profile versions, and a hash-bound snapshot. Missing required mappings, invalid rows, or an empty selected worksheet block promotion. Repeated source keys within the selected worksheet are marked duplicate and withheld. Changed existing records and possible cross-source email/tax-ID matches require explicit reviewer acknowledgement; revising an existing record also requires a non-blank correction reason.

Promotion creates a source-scoped canonical record or a new immutable revision, plus an ingestion-to-record link. Unchanged records are linked without a new revision. Duplicate rows are recorded as withheld. Replays of an already promoted ingestion return its recorded counts without writing again. No source is automatically merged with another source, and no record is turned into an M4 import or Core Financials fact.

## Persistence and security

Migration `0019_universal_data_foundation` creates:

- `company_data_source_profile_versions` for append-only source metadata history.
- `universal_source_snapshots` for original file bytes, size, format, and SHA-256 fingerprint.
- `universal_mapping_profiles` and `universal_mapping_profile_versions` for immutable mapping identity/version history.
- `universal_ingestions` and `universal_ingestion_rows` for parser configuration, inspection, staged row values, and lifecycle counts.
- `universal_ingestion_events` and `universal_row_validations` for append-only workflow and row-level evidence.
- `canonical_business_records`, `canonical_business_record_revisions`, and `universal_ingestion_record_links` for source-scoped current identity, immutable revisions, and ingestion lineage.

All new Company data tables use forced tenant/company RLS and composite scope foreign keys. `bf_runtime` receives only the required `SELECT`/`INSERT` grants on append-only evidence/version tables; `universal_ingestions` and `canonical_business_records` additionally allow guarded `UPDATE`. Runtime `DELETE`/`TRUNCATE` privileges are revoked; database triggers enforce append-only evidence and promoted-ingestion/revision invariants. The migration must run as `bf_migrator`, with the existing restricted `bf_runtime` role. Downgrade refuses to discard Universal Data source registrations, snapshots, ingestions, or canonical records.

The application stores original bytes and source rows in PostgreSQL; this increment adds no object storage, encryption-at-rest policy, malware scanner, deletion workflow, retention schedule, or evidence purge job. Configure database access, backups, retention, and the local connector root as deployment controls before using sensitive customer data. Provision only approved files under `<UNIVERSAL_LOCAL_CONNECTOR_ROOT>/<company-uuid>/`; the authorized Company UUID is selected by the server, not the caller. Do not point the local root at a home directory, secrets directory, or broad filesystem mount. The adapter rejects absolute/traversal paths, symlink components (including a symlinked Company directory), non-regular files, unsupported extensions, empty files, and files over the configured limit.

## API surface

All routes are under `/api/v1/tenants/{tenant_id}/company-data/universal`. The static `GET /entities` canonical-schema catalog requires only `company_context:read`. Reading source profiles/history, ingestion history/details, mapping profiles, raw rows, stored-file evidence, promoted records, and lineage requires `universal_data:read`; the default role matrix grants this only to owners and admins. This protects source rows that may contain customer/supplier contact details and tax identifiers. Source-profile writes, uploads/sync, mapping, validation, and promotion require both `company_context:write` and `universal_data:read`; the default owner/admin roles have both. Raw Universal Data access does not authorize customer-data analysis or processing: customer-domain, identifier-based, grouped, filtered, and linked BI analyses remain blocked until the separate processing authorization is established. The server remains authoritative for tenant/company scope and permissions.

- `GET /entities`
- `POST /sources`, `GET /sources`, `GET /sources/{source_id}`, `PATCH /sources/{source_id}/profile`, `GET /sources/{source_id}/profile-history`
- `POST /sources/{source_id}/ingestions` (multipart upload) and `POST /sources/{source_id}/sync` (configured local-directory file)
- `GET /sources/{source_id}/ingestions`, `GET /sources/{source_id}/mapping-profiles`
- `POST` or `PUT /sources/{source_id}/ingestions/{ingestion_id}/mapping`
- `GET /sources/{source_id}/ingestions/{ingestion_id}`, `/rows`, and `/evidence`
- `POST /sources/{source_id}/ingestions/{ingestion_id}/validate` and `/promote`
- `GET /records` and `GET /records/{record_id}/lineage`

The static workflow is served in `/data-foundation.html` as the Universal Data Hub beside the existing M12 and Phase 1 workflows. It shows inspection, inferred types (advisory), mappings, row findings, validation counts, source profile history, evidence downloads, canonical records, and revision lineage. UI role checks are convenience only; API authorization is mandatory.

## Verification boundary

Local verification (2026-11-21): the clean-database full repository suite passed **351 tests** on the disposable role-separated PostgreSQL **16.2** test server, with one non-blocking Starlette/httpx `TestClient` deprecation warning. Connector traversal, symlink/FIFO rejection, Company-directory isolation, parser options, migration RLS/grants, immutable revisions, promotion, downgrade guard, and the full API/security suite were included. `alembic check` reported `No new upgrade operations detected`; Ruff lint/format, mypy (**136 source files**), Python compileall, JavaScript syntax, and `git diff --check` also passed. The CI workflow remains pinned to PostgreSQL **18.7**; this local PostgreSQL 16.2 run is not a substitute for that CI gate. These software checks do not establish source authority, completeness, accounting correctness, customer-data approval, or production readiness.
