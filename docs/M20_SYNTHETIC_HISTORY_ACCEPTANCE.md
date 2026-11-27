# M20 context-history engineering evidence

**Status: technical evidence only. M20 is not accepted, production-ready, or approved for a real workflow.** PR #39 added a pure-Python synthetic contract fixture. The current audit adds PostgreSQL assertions against an existing Universal Data correction path. Neither adds an M20 application runtime, API, database schema, migration, UI, retention rule, or production field set.

## Test-only contract exercised

`tests/unit/test_m20_synthetic_history_acceptance.py` uses only inline `synthetic-*` values and a tiny in-memory fixture. It checks that:

- each immutable version has an actor identifier and a deterministic sequence in its history;
- a correction is a new version linked to the previous version; earlier versions remain readable and unmodified;
- original source-fact content and evidence references are carried through corrections without rewriting either;
- explicit `conflicts_with` and `supersedes` links are visible in history, without automatically resolving conflicts, choosing a winner, or assigning business meaning;
- an authorization callback is evaluated before the fixture store is asked for history, and a denied request causes no lookup;
- lookups are scoped by both tenant and Company, and identical history keys in different scopes remain separate; and
- identical fixture input produces the same canonical history digest.

Names and shapes in this test file are **test-harness details only**, not proposed or approved production fields, API contracts, roles, allowed-value lists, source-authority rules, approvals, retention periods, or supersession policy. The injected authorization callback is a call-order probe, not an implementation or proof of application authorization/RLS. This fixture does not replace or duplicate M7 decisions, M12 source registration, M17 evaluation evidence, or Universal Data ingestion/canonical-record history. No real or customer data is present.

## PostgreSQL verification of an existing correction path

The M20 audit adds PostgreSQL-backed assertions to `tests/test_universal_data.py::test_universal_data_isolation_mapping_history_evidence_and_governed_promotion`. Through the existing authenticated Universal Data staging, mapping, validation, and promotion flow, the test now verifies both persisted revisions, their actor IDs, source-record key, distinct ingestion/snapshot/row references, correction reason, and the original raw uploaded bytes/fingerprint after a later correction. The current migrations and related integration tests also verify forced Company-context RLS, least-privilege runtime grants, and append-only triggers for canonical revision rows.

This is evidence about the already-shipped Universal Data correction contract, not an M20 runtime implementation or acceptance. It uses synthetic test values and does not define or prove a generic context schema, a role/approval model, conflict or supersession policy, source authority, retention policy, BI-pointer retention, or customer/production-data permission. No schema, route, purge behavior, or business rule is added or changed by this test.

## BI ingestion-pointer investigation

### What the current code records

- `src/businessflow/business_intelligence/analysis_service.py::_source_last_ingestion` finds a source's latest ingestion and latest promoted ingestion. `_source_candidate_state` records their IDs and statuses in source-selection metadata. `_universal_input_snapshot` saves both the selected source and candidate-source metadata into the immutable BI analysis input snapshot.
- The selected-record path is distinct: `_load_source_records` obtains current canonical-record revisions and stores typed lineage references including each record's `ingestion_id`, `source_snapshot_id`, source-row ID, and hashes. Thus a saved analysis can contain ingestion IDs as source-state metadata as well as IDs attached to actual canonical evidence.
- `replay_metric_analysis` verifies the saved analysis and recalculates from its saved definition, configuration, and input snapshots. It does not reload those ingestions. `list_metric_evidence` builds its response from the saved `calculation_inputs.evidence`, semantic references, and external references; it does not resolve `latest_ingestion_id` or `last_promoted_ingestion_id` against live Universal Data rows. Reports likewise consume the saved BI analysis/evidence path.

### What purge checks

- `src/businessflow/universal_data/retention.py::build_purge_plan` checks canonical revisions, canonical ingestion links, semantic mapping dependencies, and the original source snapshot. `migrations/versions/0028_universal_data_retention.py::businessflow_purge_universal_ingestions` repeats canonical revision/link checks before deletion. Neither path checks BI analysis JSON or its `source_selection` / `candidate_sources` IDs.
- When eligible ingestion evidence is purged, the migration writes a `universal_ingestion_purge_tombstones` row retaining the ingestion UUID, idempotency-key hash, policy version, purging actor, and time. The ingestion, its rows/events/links, and an orphaned original source snapshot can be deleted. The tombstone is not a copy of the ingestion or source payload.
- An ingestion that still has canonical revision/link or semantic-mapping dependencies is blocked by current purge checks. This protects the ingestion lineage for canonical evidence used by BI. It does **not** establish a dependency for a BI-only source-selection pointer to an ingestion with no such dependencies.

### Finding and unresolved owner decision

In current code, `latest_ingestion_id` and `last_promoted_ingestion_id` inside a saved BI source-selection snapshot are **analysis-time metadata, not enforced retention dependencies**. When an ingestion is referenced only by those metadata pointers and has no canonical/revision/link/semantic-mapping dependency, current purge checks can remove the live ingestion and, if unshared, its original snapshot. The saved BI snapshot itself remains immutable and retains the pointer and other captured selection metadata. Its saved-analysis replay and evidence-page code do not dereference that pointer, so this code does not make replay fail solely because that ingestion was purged. A new readiness check or analysis is live-source work, not replay, and can observe the post-purge source state. The tombstone preserves the ingestion UUID and limited purge metadata, but not enough ingestion/source content to reconstruct the original ingestion details from the BI pointer alone.

That is a **gap in retention/interpretation policy**, not a code decision to change purge. The existing BI documentation (`docs/BUSINESS_INTELLIGENCE_PHASE3.md`, especially its snapshot/replay description) does not say whether these metadata pointers pin ingestion retention. The Universal Data documentation (`docs/UNIVERSAL_DATA_FOUNDATION.md`, especially its purge dependency list) describes canonical/revision/link/semantic dependencies and source snapshots, but does not list saved BI analysis pointers as purge dependencies. Neither document establishes an owner decision for this case.

The required decision is for the accountable BI/source-evidence and Universal Data retention owner—**no such decision-maker is identified by this repository**—to decide separately whether `latest_ingestion_id` and `last_promoted_ingestion_id` in saved BI snapshots (including unselected candidate-source metadata) are retention dependencies when no canonical evidence depends on them:

1. treat either or both pointer categories as retention dependencies, keeping the referenced ingestions and any needed source snapshots; or
2. treat the pointers as descriptive, permit the current purge path, and accept that they may no longer resolve to live ingestion details.

The owner must also decide what post-purge audit/interpretation evidence is required and whether the existing tombstone is sufficient; no role, authority, retention period, or answer is inferred here. Existing owner/admin permissions to configure or approve a Company retention policy do not by themselves answer this separate BI pointer contract.

**If unresolved:** current purge behavior remains unchanged; a metadata-only BI pointer may outlive the live ingestion and original source payload. Saved snapshot replay still uses its own saved inputs, and canonical evidence dependencies remain protected by existing checks, but a later operator or consumer cannot rely on resolving every metadata pointer to the original ingestion details. No new audit guarantee, retention exception, or production interpretation is claimed.

## Verification boundary

The synthetic tests exercise only the test fixture contract. Database downgrade refusal, API authorization, RLS, append-only persistence, quality gates, and schema drift remain separate PostgreSQL/CI checks. Passing this artifact cannot establish an owner-approved use case, production field set, production authorization model, retention policy, M20 acceptance, or production readiness. M21 and optimization work are outside this slice.
