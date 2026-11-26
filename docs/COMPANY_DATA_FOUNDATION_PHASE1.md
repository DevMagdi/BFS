# Company Data Foundation — Phase 1: Manual customer commitments

## Status and boundary

Phase 1 adds one guided, manually recorded customer-delivery commitment workflow to the existing Company Data Foundation. It captures a human assertion and its provenance; it does not establish that a customer order exists or that the entered information is authoritative. The workflow is deliberately separate from M4 file imports and Core Financials.

This is a small operational intake, not a customer master, quote/order-line system, signed-contract repository, legal record, revenue/cash-flow fact, financial measure, or approved business policy. A record is never automatically copied or converted into a Core Financials record, M4 import, canonical financial fact, or accounting conclusion.

## Record contract and lifecycle

The migration-seeded immutable contract is `businessflow.manual.customer_commitments` version `1.0.0`, dataset type `customer_commitments`, source kind `manual_entry`. Its canonical SHA-256 is pinned by the code and migration. The Company-local source key is `manual.customer-commitments`; the first draft automatically registers that source using the same contract. Generic source registration rejects this dataset, and both the M4 ingestion service and a database trigger reject the manual source.

A record contains:

- required-to-submit customer name, order date, and commitment summary;
- optional customer reference, requested delivery date, and notes; and
- zero or more user-supplied supporting files.

Drafts may be incomplete. Before submission, required values must be present and a requested delivery date cannot precede the order date. Missing optional information and missing evidence are warnings, not inferred facts; submitters explicitly acknowledge outstanding warnings. A reviewer must also acknowledge warnings before acceptance.

The state transitions are `draft → submitted → accepted | rejected | changes_requested`, with `changes_requested → submitted` after correction. Active members may create drafts and edit their own drafts or returned records; owner/admin members may edit any returned draft and review submissions. The submitter cannot review their own submission. Accepted and rejected rows are terminal and immutable. Rejection and correction requests require a reviewer comment; acceptance requires an explicit confirmation that intake criteria were checked.

Every record revision has one append-only event with the actor, status transition, bounded comment, canonical snapshot, snapshot SHA-256, prior-event hash, and event SHA-256. The API recomputes the hashes and checks chain continuity when returning history. Submission and review history say explicitly that they record process and user assertions, not independent verification of the underlying event.

## Evidence handling

Evidence accepts a PDF, PNG, JPEG, or UTF-8 plain-text file only after extension, signature/encoding, and declared-media checks. Each file is limited to 10 MiB; at most ten files may be attached to one record. The database stores file bytes and their SHA-256 with Company scope; downloads recheck file length and hash. Evidence is append-only in this workflow: there is no delete, replacement, malware scanning, retention schedule, or external object-store integration. Use only documents approved for this environment. A hash detects byte changes; it does not authenticate the uploader, source, document, or customer assertion.

## Company scope and API

The record, event, and evidence tables use forced tenant-and-company RLS, same-scope foreign keys, runtime least-privilege grants, and database lifecycle/append-only guards. Service authorization resolves the Company's canonical primary company from an active membership, then sets verified transaction-local tenant/company/user context. Clients cannot choose a tenant/company inside a commitment payload. Audit events are written in the same transaction as the lifecycle action.

Routes are under `/api/v1/tenants/{tenant_id}/company-data/customer-commitments`:

| Method and path | Purpose |
| --- | --- |
| `GET /` | Bounded list with optional status filter. |
| `POST /` | Create a draft and register the manual source if needed. |
| `GET /{commitment_id}` | Read the current record, validation findings, and evidence metadata. |
| `PUT /{commitment_id}` | Save a draft or returned correction using `expected_revision`. |
| `POST /{commitment_id}/submit` | Validate and submit with explicit warning acknowledgement. |
| `POST /{commitment_id}/review` | Accept, request correction, or reject using independent owner/admin review. |
| `GET /{commitment_id}/history` | Page through hash-linked immutable revision events. |
| `POST /{commitment_id}/evidence` | Add one bounded multipart file to an editable record. |
| `GET /{commitment_id}/evidence/{evidence_id}` | Download and verify an attached file within Company scope. |

The guided UI is served at `/data-foundation.html` and linked from the main workspace. Its lists label records as manual and the boundary copy distinguishes this workflow from M4 imports and Core Financials.

## Migration, verification, and known limits

Migration `0018_customer_commitments` adds the manual contract, three tables, indexes, RLS policies, database guards, and runtime grants. Downgrade refuses to discard a registered manual source or any commitment history. It must be applied by `bf_migrator`; `bf_runtime` must be non-superuser, non-`BYPASSRLS`, and cannot be a member of the migration role.

Database-independent checks:

```sh
.venv/bin/ruff format --check src migrations tests
.venv/bin/ruff check src migrations tests
.venv/bin/mypy src/businessflow
.venv/bin/python -m pytest --confcutdir=tests/unit tests/unit -q
.venv/bin/python -m compileall -q src tests
node --check src/businessflow/web/assets/data-foundation.js
```

The API lifecycle/RLS integration tests are in `tests/test_customer_commitments.py`. The full suite, migration upgrade/downgrade/re-upgrade, and Alembic model-drift check require the repository's disposable PostgreSQL test database with separate runtime, migrator, and admin roles. They must never target a valuable/shared database. Software checks do not constitute customer/source-owner approval, document retention approval, legal review, financial policy, or production authorization.
