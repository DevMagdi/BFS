# AUVRA Phase 1 — Technical Acceptance and Verification Record

- **As of:** 2026-11-21 (UTC; final local verification and document review).
- **Scope status:** The bounded Phase 1 engineering scope and Operations-first sequencing are accepted in [`PHASE1_SCOPE_ACCEPTANCE.md`](PHASE1_SCOPE_ACCEPTANCE.md).
- **Technical status:** **Final local technical verification passed; formal closure remains pending current-branch CI.** The final-tree PostgreSQL-backed suite passed 475 tests; repository Ruff format/lint, mypy, Alembic parity, and final `git diff --check` passed. No current-branch GitHub CI result is recorded.
- **Branch:** `arena/f5138e90-bfs`, based on `b928ee7758b882c00a40fdda4941a75f609b407c`.
- **Authoritative requirements:** [`../product/AUVRA_INTELLIGENCE_CORE_PHASE1.md`](../product/AUVRA_INTELLIGENCE_CORE_PHASE1.md).
- **Evidence matrix:** [`../REQUIREMENTS_TRACEABILITY.md`](../REQUIREMENTS_TRACEABILITY.md).
- **Assessment:** [`AUVRA_PHASE1_REPOSITORY_ASSESSMENT.md`](AUVRA_PHASE1_REPOSITORY_ASSESSMENT.md).

## Scope and evidence boundary

The accepted deliverable is an API-only, source-scoped Operations observation lifecycle proving a bounded slice of the shared Intelligence Core. The repository baseline at the branch point used migration head `0028_universal_data_retention`; the active branch adds migration `0029_operations_intelligence`, Operations models/schemas/engine/service/router, authorization wiring, and tests. The four company-wide pillars remain first-class; Operations is the first implementation proof, not the product boundary.

This report separates **scope acceptance** from **technical verification** and **external policy decisions**. The evidence below establishes only software properties exercised by local checks. It does not establish source authority, customer value, business correctness, customer-data authorization, legal/security approval, production readiness, or release readiness. Accountable owner decisions and use restrictions are listed in the scope record.

**Run-history note:** One preceding full-suite attempt had a single failure in the unrelated existing test `tests/test_financial_calculations.py::test_financial_calculation_api_runs_exact_m14_subtotal_and_replays`, whose assertion searches a timestamp-bearing JSON snapshot for raw amount substrings. That test passed in an isolated rerun, and the subsequent final full-suite run above passed. The cause of the one-off failure was not established; it is not reported as a confirmed Operations defect.

## Environment and safety

- Local validation used temporary dependencies under `/tmp/bfs-pydeps` and PostgreSQL settings loaded from `/tmp/bfs-pg-test-env.sh`; secrets are intentionally not recorded.
- The final full-suite run used the uniquely named disposable database `businessflow_test_phase1_a178e5f1e2154505801128aa7c98bceb`; the latest focused run used `businessflow_test_phase1_78cbae55a00b4633964dd7395c61ba9e`. Each wrapper verified its name was unused, ran the checks, terminated connections, and dropped the database.
- Tests/conftest migration URLs are pinned to the configured disposable target during fixture upgrades and restored afterward. A separate mismatch-isolation check verified that the Operations test still passed with a deliberately nonexistent migration URL and that no such database was created.
- Automated Operations records are synthetic. No customer or production data was used.

## Results actually run on the final tree

| Check | Result | Scope/limitation |
|---|---|---|
| Full PostgreSQL-backed repository suite | **Passed: 475 tests** — `python -m pytest -q --tb=short`; 1 Starlette/httpx deprecation warning; 158.41 seconds. | Ran on the final code/test tree against the disposable PostgreSQL database above. Includes Operations API/integrity/concurrency/retention and event-history locking tests, plus unit coverage. |
| Focused Operations PostgreSQL/API and unit tests | **Passed: 17 tests** — `tests/test_operations_intelligence.py` and `tests/unit/test_operations_observations.py`; 1 Starlette/httpx deprecation warning, 7.49 seconds. | Latest separate focused run used the unique disposable PostgreSQL database recorded above. The full-suite result also includes these tests. Coverage includes correction-chain ancestors and current-pointer timestamp integrity, concurrent corrections, event-history pointer locking, bounded version SELECTs, input limits, saved-analysis tampering, retention, authorization/RLS/grants, and downgrade protection. |
| Alembic migration/model parity | **Passed:** `python -m alembic check` → `No new upgrade operations detected.` | Executed after the final-tree full suite while the isolated test database remained at migration head `0029_operations_intelligence`. |
| Migration upgrade/downgrade/re-upgrade | **Passed in suite.** | Clean upgrade to `0029`; safe empty downgrade/re-upgrade; retained event evidence and analysis-only evidence refuse downgrade and preserve tables/RLS/data. |
| Ruff format check | **Passed:** 268 files already formatted. | `python -m ruff format --check src migrations tests`. |
| Ruff lint | **Passed:** all checks passed. | `python -m ruff check src migrations tests`. |
| mypy | **Passed:** no issues in 172 source files. | `python -m mypy src/businessflow`. |
| `git diff --check` | **Passed** after the implementation/document edits reviewed for this report. | Whitespace/conflict-marker check only; not semantic review. |
| Focused implementation/security review | **Completed for this bounded repository diff.** | Reviewed Operations API/service/schema/engine/models/migration, role wiring, tenant/company RLS, runtime grants, trigger guards, source advisory-lock reuse, retention dependencies, fixture isolation, tests, and Phase 1 documents. The review corrected a read-side correction-chain race by locking the current pointer before paginated history reads; a PostgreSQL regression test asserts lock-before-read ordering. This is not a complete threat model, penetration test, dependency/SBOM audit, or production security certification. |
| Current-branch GitHub CI / PR / release | **Not run or claimed.** | Local validation is not CI. No push, PR, merge, release, or deployment is claimed. |
| Customer, source-authority, production, legal/privacy acceptance | **Not performed.** | Separate accountable owner/domain/security/privacy/legal decisions are required for the relevant use. |

## Evidence coverage and limits

- `tests/test_operations_intelligence.py` covers API workflows, event idempotency and replay, correction history, full-chain integrity and ancestor tamper detection, current-pointer timestamp tamper detection, concurrent correction serialization, read-side history pointer locking, bounded analysis queries, input limits, saved input/result integrity, source/revision linkage, owner/admin/member access, cross-Tenant isolation, retention dependencies, forced RLS/grants, and downgrade safety.
- `tests/unit/test_operations_observations.py` covers deterministic observation behavior, exact labels/counts, explicit empty/unassessed states, input validation, timezone handling, and the analysis input bound.
- Migration `0029` implements Company-scoped constraints, forced RLS, runtime grants, append-only evidence/history, guarded downgrade, and a database-maintained current pointer. The service verifies full correction chains, current version and timestamp pointers, saved analysis hashes, and deterministic replay.
- Exact linked canonical revisions use `ON DELETE RESTRICT`; the existing Universal Data purge plan/procedure blocks referenced revisions. Tests verify that linked Operations evidence remains accessible and that no Operations purge policy is added. This technical behavior does not select a retention duration or authorize an Operations purge.
- A performance regression test covers 12 events with two versions each and asserts no more than two Operations version-table SELECTs. Correction-chain length is not technically capped; the test does not exercise long histories or high-volume load. A prior local timing was 29.743 ms; it is not a load benchmark, production latency measurement, or SLO.
- These are focused repository checks, not a complete threat model, penetration test, high-volume concurrency benchmark, production deployment test, customer validation, or browser E2E run. This API-only scope has no UI.

## Remaining closure and use gates

1. **Current-branch CI:** run and review CI when this branch is pushed/eligible. Until then, technical closure remains pending under AC-10; local checks do not substitute for CI.
2. **Policy decisions:** Operations retention/purge and legal-hold treatment; sensitive-data classification; source authority/completeness/freshness and first business question; whether the observation count may become a KPI; and authorization for customer or production data remain open in [`PHASE1_SCOPE_ACCEPTANCE.md`](PHASE1_SCOPE_ACCEPTANCE.md). They block the corresponding business/customer/production use, not the accepted four-pillar direction or bounded engineering scope.
3. **Future deployment/security:** customer/production use requires its separate data-use, security, retention, operational, legal/privacy, and release approvals. This repository review does not supply them.

Passing technical checks establishes only tested software behavior. It does not establish market fit, source authority, customer validation, production readiness, accounting correctness, complete security, legal/privacy approval, release readiness, or owner approval of any KPI or external use.
