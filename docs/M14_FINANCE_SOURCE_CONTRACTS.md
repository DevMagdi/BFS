# M14 — Finance Source Contract, Profiling, and Reconciliation

## Status and gate boundary

M14 implements one narrow, manual finance-source intake for a single Company: a versioned finance-specific CSV contract, explicit mapping and ignored-column rationale, source profiling, row-quality evidence, freshness assessment, and comparison against caller-supplied control totals. It is not M12's workforce/project registry, a connector, a ledger, or a canonical finance-fact model.

This engineering slice does **not** satisfy or imply the still-unresolved business gates: no finance/accounting data owner or authoritative source/access path was selected; no correction or legal retention policy was approved; and no organizational definition establishes that a caller-provided control is independent. Until those approvals exist, the implementation is not authorization to process production/customer accounting data or to present its output as accounting truth. The current policy behavior is deliberately explicit: source identity, source-as-of time, freshness age, and control references are caller assertions; a missing freshness rule is reported as `policy_missing` rather than invented.

M14 keeps the current optional Company model. It requires no universal department, product, service, branch, project, or other operating-structure hierarchy. Its source and reconciliation keys are opaque labels only; no account chart, classification, fiscal calendar, recognition, or financial interpretation is introduced.

## Contract and mapping

The migration seeds immutable global contract `businessflow.finance.source-observations`, version `1.0.0`, with SHA-256 `b16e6e87f4b1a25d0313b895cf5304de284caff4d82f1069158d4a60d09b53b3`. The contract is intentionally semantic-light and supports one UTF-8 CSV with six required, caller-mapped fields:

| Field | Contract meaning |
| --- | --- |
| `source_record_id` | Exact opaque source record identity, used only to find repeated IDs within one intake. It is not an account or canonical entity ID. |
| `reconciliation_key` | Exact opaque grouping key, used only to match a supplied control. |
| `period_start`, `period_end` | Source-reported date boundaries; no fiscal, effective-date, or recognition semantics are inferred. |
| `amount` | Plain base-10 decimal text. No exponent, whitespace, float conversion, rounding, or default is accepted. |
| `currency` | A supported ISO 4217 code. Different currencies are never combined. |

Each contract field must map to a distinct exact CSV header. Every file header must be mapped or explicitly ignored with a non-empty rationale. The mapping hash binds the Company-local caller-supplied `source_key`, contract key/version/hash, field mapping, ignored headers, and rationales. Mapping versions are append-only and advance when that canonical mapping changes. Reusing an identical mapping reuses its version; changing it creates a new version. A control does not automatically establish that a source is authoritative or organizationally independent.

Only one CSV file is accepted per request. The implementation reuses the repository's bounded file-reading and CSV parser; it does not use the M4 workforce/project contracts or M12 ingestion path. Parsing and validation occur in memory. Raw uploaded files and raw data-row values are not written to the finance-source tables.

## Intake and evidence lifecycle

1. **Permission and scope.** Every operation first requires active tenant membership and the finance-specific permission. Reads require `finance:read`; an intake write requires `finance:write`. These permissions are currently granted to owner/admin roles only. The service resolves the active Company from verified tenant context, locks and revalidates authorization on writes, then sets transaction-local tenant/company RLS context.
2. **Contract and mapping.** The service verifies the migration-seeded contract definition and hash, parses the single CSV, validates exact header coverage, profiles each column, and obtains an immutable Company/source mapping version.
3. **Row quality and identity.** Required dates, opaque identifiers, currency, and plain Decimal amounts are validated without silent defaults. Repeated exact `source_record_id` values in one intake are marked duplicate; they are retained as evidence and not auto-deduplicated. This does not compare identities across different uploads or apply a correction/supersession policy. Row evidence stores source-row hashes, HMAC-protected identity hashes, group hashes, status, and safe issue codes—not source values.
4. **Freshness.** The caller may supply a timezone-aware source-as-of instant and positive maximum age. The shared freshness evaluator reports `fresh`, `stale`, `policy_missing`, `source_as_of_missing`, or `future_as_of`. A caller-supplied maximum age is recorded as unapproved; it is not an accepted finance policy.
5. **Reconciliation.** Each control names one exact `(reconciliation_key, period_start, period_end, currency)` group and carries expected amount, absolute and relative tolerances, and a caller-asserted control-source reference. The service reuses M11's Decimal reconciliation primitive; its effective tolerance is `max(absolute_tolerance, abs(expected_amount) * relative_tolerance)`. This is an explicit arithmetic rule, not finance-policy approval. No rounding, FX, classification, or causal analysis occurs. It rejects an obvious same-file control and retains a reason when supplied evidence overlaps the uploaded source. Missing, invalid, duplicate, unmatched, stale, or unsupported input is not turned into zero. Unmatched groups and invalid inputs produce `not_verifiable`; a numeric result outside the caller-supplied tolerance is `failed`. Freshness remains a separate recorded dimension: a numeric `passed` state is only a comparison of the supplied Decimal values and is not an overall data-eligibility or accounting-correctness verdict.
6. **Immutable evidence.** An intake stores versioned input/result snapshots and hashes, counts, mapping/contract bindings, profile summaries, control references/values, quality findings, freshness state, reconciliation results, limitations, and an audit event. Row evidence is separately append-only. The response exposes no raw uploaded row values.
7. **Idempotency.** A Company-scoped idempotency-key hash and request fingerprint prevent accidental duplicate creation. An identical retry returns the original immutable response with `replayed: true`; it does not rerun calculations. Because raw rows are not retained, M14 does not claim deterministic calculation replay.

The stored controls and result snapshots contain finance-related values and references. Hashes provide integrity comparison, not encryption or independent source authentication. Runtime roles cannot update/delete evidence; this is not a legal retention schedule and does not protect against privileged database operators. An approved data-handling/retention policy remains a prerequisite for production use.

## Persisted domain and security

Migration `0011_finance_source_intake` adds:

- `finance_source_contracts`: global migration-seeded immutable contract catalog; `bf_runtime` has read-only access.
- `finance_source_mappings`: immutable tenant/company-scoped mapping versions bound by composite foreign keys to the exact contract definition hash.
- `finance_source_intakes`: immutable tenant/company-scoped intake, whose composite foreign keys bind the exact mapping version/hash and contract key/version/hash.
- `finance_source_row_evidence`: immutable row-level hash and safe validation-status evidence bound to one same-tenant/company intake.

All three tenant/company tables use forced company-context RLS and `SELECT`/`INSERT`-only runtime grants. The runtime role has no update, delete, or truncate privileges. Database triggers reject updates/deletes on the contract, mapping, intake, and row-evidence tables. Same-scope composite foreign keys and transaction-local tenant/company context provide database isolation in addition to service authorization. The established `bf_migrator`/`bf_runtime` separation remains in force. This extends but does not change M11 verification, M12 evidence, or M13 metric traceability.

## API

Routes use `/api/v1/tenants/{tenant_id}/finance-sources`:

| Method and path | Access | Purpose |
| --- | --- | --- |
| `GET /contracts` | `finance:read` | Verify and return the published finance-source contract. |
| `POST /intakes` | `finance:write` | Multipart request with one CSV `file`, strict JSON `metadata_json`, and `Idempotency-Key`; profile, validate, and compare one intake. |
| `GET /intakes` | `finance:read` | Bounded page of Company-scoped intake summaries and evidence snapshots. |
| `GET /intakes/{intake_id}` | `finance:read` | Read one hash-verified intake. |
| `GET /intakes/{intake_id}/rows` | `finance:read` | Bounded page of row hashes, group hashes, status, and safe issue codes. |

There is no update, delete, source connector, mapping-approval, canonical-fact, ledger, or M15 calculation endpoint. Non-finance members cannot read finance-source contracts/intakes merely by having generic company-context access.

## Validation and limits

Unit coverage pins the contract/hash to the migration seed, validates strict input and mapping rules, duplicate semantics, Decimal arithmetic, missing/invalid-control abstention, freshness inputs, snapshot integrity, and parser/profile behavior. PostgreSQL integration coverage exercises migration upgrade/downgrade/re-upgrade, schema drift, API authorization and tenant isolation, forced RLS with missing/wrong context, grants, composite scope, and append-only triggers. Offline Alembic SQL generation is not a substitute for database migration execution or RLS validation.

Software checks establish only that this versioned, bounded implementation behaves as specified. They do not establish accounting correctness, data-source authority, independent-control governance, policy approval, or production readiness. M15 was not started.
