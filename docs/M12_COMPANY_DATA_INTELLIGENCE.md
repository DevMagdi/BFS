# M12 — Company Data Intelligence

## Status and scope

M12 adds a bounded, Company-scoped data-source registry and immutable ingestion evidence for the existing M4 file-import path. It does not add a second parser, copy raw data into a new store, or implement a connector, scheduler, sync worker, general data warehouse, company-wide fact model, or production data integration. Its six M4 contracts are frozen projections of the M4 dataset catalog; existing M4 imports, M5 snapshots, and M4 source-row lineage remain the source of truth. The later Phase 1 manual customer-commitment increment adds a separate `manual_entry` contract to the shared catalog; it is not an M4 dataset and does not change M12 coverage or ingestion semantics. See [`COMPANY_DATA_FOUNDATION_PHASE1.md`](COMPANY_DATA_FOUNDATION_PHASE1.md) for that separate workflow.

The registry is usable with the existing M4 workforce/project datasets. The separately scoped M10 workflow remains synthetic-only and continues to accept only its exact built-in templates. M12 does not relax M10's upload, mapping, canonical-write, finance, or disposition safeguards.

## Data and evidence lifecycle

1. **Contract catalog.** Migration `0009_company_data_intelligence` seeds immutable `1.0.0` contracts for `employees`, `skills`, `employee_skills`, `weekly_capacity`, `projects`, and `resource_assignments`. Each contract records the M4 field names, required fields, aliases, choice/exclusion rules, and defaults. A canonical SHA-256 hash is tested against the live M4 catalog. Contract changes require a new version and migration seed; an existing contract version must not be edited in place.
2. **Source registration.** An owner/admin with `company_context:write` and `workforce:read` registers a unique Company-local `source_key`, display name, dataset type, contract version, active tenant-member owner, and optional positive maximum age in hours. A registration identifies an expected source; it is not a live external connection. Registration rows are append-only.
3. **M4 import and ingestion evidence.** The user stages, maps, validates, confirms, and applies an import through M4. M12 then binds that exact applied import to one registered source with an optional timezone-aware `source_as_of_at` assertion. Dataset and Company must match. An M4 import can be recorded once; an identical retry returns the existing immutable evidence, while a different source or as-of assertion conflicts.
4. **Quality assessment.** M12 checks that the saved mapping matches the frozen contract, required and exclusive fields are respected, mapped headers exist in the saved worksheet inspection, the M4 validation hash and mapping version exist, and canonical source-row links are retained. M4 row findings and import counts are recorded without replacing or hiding the M4 report. Errors fail closed; warnings remain visible in evidence.
5. **Freshness assessment.** Freshness uses the registered source's explicit maximum age and the caller-reported source as-of timestamp. The recorded status is evaluated at ingestion time; read responses also report current freshness against the same immutable policy and timestamp. Missing policy, missing as-of time, future timestamps, and expired data are distinct states. A reported timestamp is an assertion, not independently authenticated evidence.
6. **Immutable snapshot and lineage.** Each ingestion stores a versioned snapshot with source/import IDs, contract definition and hash, M4 file/validation hashes, mapping version, counts, quality findings/status, freshness inputs/status, and a content hash. Row-level lineage is read from the existing M4 `data_import_record_links` and `data_import_rows`; M12 does not duplicate canonical facts or M4 row payloads.

A passed import can have `succeeded` / `passed` quality. A warning import records `succeeded_with_warnings` / `warning`; failed validation evidence, mapping mismatches, missing lineage, or invalid rows records `blocked` / `failed`. Freshness is orthogonal to ingestion/quality state, so a stale ingestion remains retained as evidence but is not eligible as fresh M10 evidence.

## API surface

All routes are under `/api/v1/tenants/{tenant_id}/company-data` and require authenticated Company access. `GET /contracts` requires `company_context:read` only because it exposes the global, data-free schema catalog. Coverage, source, ingestion, and lineage reads require both `company_context:read` and `workforce:read`, because the six M4 contracts and their lineage can expose employee records. Source registration and M4-ingestion evidence writes require `company_context:write` plus `workforce:read` and the existing write-access lock/revalidation path; the current role matrix grants these to owners/admins, not members. The separately scoped manual customer-commitment routes keep their existing `customer_commitment:*` permissions and are not governed by these generic M12 workforce guards.

| Method and path | Purpose |
| --- | --- |
| `GET /contracts` | Read the published contract catalog: six versioned M4 contracts plus the separate Phase 1 manual contract. |
| `POST /sources` | Register an immutable Company source and bind its dataset/contract/freshness policy. |
| `GET /sources` | Page through Company sources with latest and last-successful ingestion summaries. |
| `GET /sources/{source_id}` | Read one Company-scoped source summary. |
| `POST /sources/{source_id}/ingestions` | Record or replay immutable evidence for one applied M4 import. |
| `GET /sources/{source_id}/ingestions` | Page through that source's immutable ingestion history. |
| `GET /sources/{source_id}/ingestions/{ingestion_id}` | Read and hash-verify one ingestion snapshot. |
| `GET /sources/{source_id}/ingestions/{ingestion_id}/lineage` | Page through the selected M4 version's existing canonical row links. |

Source keys and ingestions are not updatable/deletable through this API. API pagination is bounded. Cross-tenant and cross-Company lookups return not found, consistent with existing access conventions.

## M10 evidence binding

M10 may proceed without an M12 binding for compatibility with its original synthetic workflow. When a selected M4 import has a registered M12 ingestion, M10 verifies that the source, import, dataset, file hash, validation hash, mapping version, contract snapshot, quality findings, and recorded freshness evidence all match. Stale or otherwise ineligible evidence is rejected. The exact ingestion ID is also persisted on the append-only M10 operational-import link, replay is checked against that link and immutable snapshot, and a PostgreSQL trigger independently guards the bridge. For the M10 synthetic workflow, only the already-allowed roster duplicate warnings may accompany a bound ingestion; capacity and assignment imports must still satisfy M10's existing stricter checks.

The bridge does not broaden M10 input eligibility. M10 still requires exact built-in synthetic templates and exact mappings in seeded demo Companies, still records abstentions/limitations, performs no financial calculations or conversions, and has no external-action path.

## Database and security boundary

- `company_data_contracts` is a global, migration-seeded catalog: runtime has read-only access; database triggers reject updates/deletes. The definitions contain no tenant data.
- `company_data_sources` and `company_data_ingestions` carry `tenant_id` and `company_id`, same-scope composite foreign keys, forced company-context RLS, and runtime `SELECT`/`INSERT` only. Append-only triggers reject updates/deletes; runtime cannot truncate them.
- The M10 operational-import bridge uses a nullable composite foreign key to an ingestion in the same tenant and Company. A database trigger validates the selected import and fresh/acceptable evidence when an M10 source link is inserted.
- Source-registration and ingestion events are written to the existing audit log in the same transaction.
- M4 remains responsible for import lifecycle, row validation, source-row hashes, canonical links, and staged-payload retention/purge. M12 stores hashes/counts/mapping/evidence snapshots, not a second copy of the uploaded file.

These are database and application safeguards, not a claim of production readiness or tamper-proof protection from privileged database administrators. Existing tenant/company authorization and transaction-local RLS context remain required.

## Validation

Database-independent checks:

```sh
.venv/bin/ruff format --check src migrations tests
.venv/bin/ruff check src migrations tests
.venv/bin/mypy src/businessflow
.venv/bin/python -m pytest --confcutdir=tests/unit tests/unit -q
.venv/bin/alembic heads
.venv/bin/alembic upgrade head --sql > /tmp/businessflow-m12-migration.sql
```

The unit tests pin all six contract hashes and compare migration seeds to the live M4 catalog. The PostgreSQL integration suite in `tests/test_company_data.py` covers the API lifecycle, authorization, replay, M4 lineage, Company isolation, forced RLS, grants, and append-only behavior. The M10 workflow test additionally exercises stale-source rejection followed by a fresh synthetic M12 binding. Migration upgrade/downgrade/re-upgrade and Alembic metadata-drift checks require real PostgreSQL with the repository's separate runtime, migrator, and admin roles; offline SQL rendering is not a substitute for those checks. The full integration fixture rebuilds its dedicated test database and must never target shared or valuable data. See [`BACKEND_IMPLEMENTATION.md`](BACKEND_IMPLEMENTATION.md) for the isolated PostgreSQL test setup.
