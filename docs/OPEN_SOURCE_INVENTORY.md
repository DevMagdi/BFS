# BusinessFlow AI — Third-Party Repository Evaluation Inventory

- **Review date:** 2026-11-21 (UTC).
- **Disposition:** Evaluation only. No code, UI assets, package, container, license text or runtime dependency has been copied into or added to BusinessFlow AI. No component is approved for `REUSE AS-IS` at this stage.

This is an engineering source/license screen, not legal advice or a complete transitive dependency audit. Before any reuse, inspect the exact release and its package manifests/lockfiles, individual file notices, dependencies, security advisories, tests and compatibility. License grants do not grant trademarks.

**Evidence/pin note:** The historical source-evaluation SHA and the latest stable release metadata are now recorded separately. A moving `main`/`master` SHA is an immutable snapshot, **not** a release version. Where an upstream stable tag/release exists, the version and exact resolved tag commit are recorded separately; a candidate release was not necessarily the source revision tested. B.I.A.S.E.D., oc8 and EMBMIS Gateway were shallow-cloned and their local `HEAD`s matched the original recorded SHAs. Selected WrenAI, Cube and Polars files were re-fetched at the original recorded SHAs. Six additional repositories received only selective source/license checks. No complete code/security, package, SBOM or transitive-license audit was performed. The only upstream test run remains the selected EMBMIS Gateway v1 API suite documented below.

**Evidence legend:** A license or source statement here means its identified file/manifest was observed at the listed revision, not that every file or dependency was cleared. Static security observations describe inspected code paths only. “Tests run” reports actual test execution; “tests required” are proposed BusinessFlow acceptance criteria, not completed tests. Repository documentation and feature claims not directly inspected or dynamically exercised remain unverified. Release metadata resolves tags to commits; it does not establish binary reproducibility, support commitments, compatibility or production suitability. Reuse classifications are recommendations, not legal approval.

## Version, release, license scope and adoption cross-check

**Review-date metadata check: 2026-11-21 UTC.** “Evaluation SHA” is the exact source snapshot inspected previously. “Stable release pin” is upstream tag/release metadata checked during this correction and includes the resolved commit SHA. They are different pins unless explicitly noted. A stable tag is not evidence that its binaries, dependencies, or compatibility were tested. “No stable release recorded” means no non-prerelease application release was identified by the checked GitHub Releases/tags metadata; it does not imply no package distribution exists.

| Repository | Evaluation SHA and snapshot status | Stable version/tag → exact commit SHA observed | Root and inspected subdirectory/package license scope | Intended use / adoption decision |
|---|---|---|---|---|
| TheSelfSa/biased | Branch-head snapshot `75bbf77df8bf49bd3d9e332cd3202f2d6c043a60`; not a GitHub Release. Showcase tag `v1.0.0-showcase` → `8aa8746ae7592f9b2e60a45926f1754ad8b3e4cf` is a tag, not a formal release. | No formal release observed; `v1.0.0-showcase` tag only. | Root `LICENSE` Apache-2.0; package/subdirectory file-level and dependency scope not comprehensively reviewed. | UI/workflow reference only; no source/assets adopted. Product-domain/backend/auth is rejected. |
| Canner/WrenAI | Branch-head snapshot `31390f6153887e893ebebb7d5b663eb131c782d2`; not the release pin. | `wren-v0.18.0` (non-prerelease, 2026-11-10) → `f9444152150b89065481276a136ce6cea8ddfeb6`. | Mixed root path map: inspected `core/wren` Apache-2.0; docs CC BY 4.0; root contains AGPL-3.0 text for separately identified modules. Package/extension and all dependencies require path-specific review. | Optional read-only question/query candidate for a fair M5 benchmark; not selected. |
| duckdb/duckdb | Branch-head snapshot `87d96b7773a10356b11cc15c352125acdc58a9ce`; not the release pin. | `v2.0.1` (non-prerelease, 2026-11-20) → `8d78114e90a53823803660bf821f6073ac87da8c`. | Root engine `LICENSE` MIT; third-party libraries, extensions and bundled/runtime artifacts may carry separate terms. Python distribution/bindings need separate review. | Optional isolated batch analytics/import benchmark only; not transactional/canonical storage; not selected. |
| pola-rs/polars | Branch-head snapshot `90d37c54f0fb49b213a8e36944b419d92f263bbf`; not the release pin. | Python release `py-2.1.0` (non-prerelease, 2026-10-25) → `7f902bd3ffd27ff82d14278a09a295ff2f7aed92`. | Root and inspected `py-polars` MIT; runtime wheels, optional connectors and transitive dependencies need separate review. | Optional ingestion transformation benchmark only; not canonical validation/decision authority; not selected. |
| cube-js/cube | Branch-head snapshot `ece5b79e15095640e1294c1a465055269adfe26b`; not the release pin. | `v1.8.31` (non-prerelease, 2026-11-20) → `8af561bd3d511e5b403d54ccac311efc22a1df2a`. | Root default is Apache-2.0 unless a package/file differs; inspected backend packages Apache-2.0 and client core MIT. Each selected driver/client/package needs inspection. | Alternative read-only semantic metrics/query API in the same fair M5 comparison as WrenAI; not selected. |
| oc8-ai/oc8 | Recorded branch-head snapshot `c01beb749866aeabf02744f7c460d4c16487ac0a` **matches** stable `v0.17.0` release commit. | `v0.17.0` (non-prerelease, 2026-11-20) → `c01beb749866aeabf02744f7c460d4c16487ac0a`. | Root/backend repository metadata LGPL-3.0-or-later; `capas/` may carry independent provenance/licenses. Third-party notices need review. | Do not adopt as foundation/agent runtime; source can inform threat-model discussion only. |
| EMBMIS/gateway | Branch-head snapshot `0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de`; no stable tag/release observed. | No tag or release observed; snapshot only. | Root `LICENSE` Apache-2.0; `NOTICE` reserves EMBMIS trademark; bundled console fonts use SIL OFL 1.1. Package/dependency licenses not fully audited. | Reject direct reuse/security-boundary role unless corrected and independently re-audited. |
| temporalio/temporal | Branch-head snapshot `32ef011fbd1e38ecddf656fc3f5026f6641cfeab`; not the release pin. | Server `v1.32.2` (non-prerelease, 2026-11-02) → `6d085822273f4336796e6959c73ab1d14a23eb77`. | Server root `LICENSE` MIT; client SDKs are separate repositories/packages and need their own license/version audit. | Defer; use proposed PostgreSQL outbox/state machine first. |
| EMBMIS/erp | Branch-head snapshot `9043629d6c5fa1137f632102613d0a37ddced91b`; no tag/release observed. | No tag or release observed; snapshot only. | Root `LICENSE` Apache-2.0; root `NOTICE` reserves EMBMIS name/logo. Other package-level/dependency terms not comprehensively reviewed. | ERP reference/source system only; no BusinessFlow application reuse proposed. |
| frappe/erpnext | Branch-head snapshot `bc0ea5ea65408090771761da6b1411a8165c9c36`; not the release pin. | `v16.60.0` (non-prerelease, 2026-11-17) → `886ab6ae187244e7f1b852393384ab6019d56264`. | Root `license.txt` GPL-3.0. Frappe framework and bundled apps/dependencies are separately versioned and require independent license/compatibility review. | Possible external ERP/source connector target only; no code reuse or canonical-schema adoption. |
| n8n-io/n8n | Branch-head snapshot `c7cbeaeaf8815a32ac87e12cc53bc157d2a35c4b`; not the release pin. | `n8n@2.48.7` (non-prerelease, 2026-11-20) → `86dff76e10efba250a4d3e57760e4eb5d2bbea25`. | GitHub SPDX is `NOASSERTION`; root `LICENSE.md` applies Sustainable Use License to specified `master` content; `.ee` code has separate Enterprise License; branch/content exceptions apply. Not OSI-approved open source based on this license. | No MVP use. Do not use as the default workflow engine or resell/host without specific license review. |
| airbytehq/airbyte | Branch-head snapshot `f5cbf0fd54a3f79b179c8752fb3731ec713114cc`; not the release pin. | `v2.0.0` (non-prerelease, 2025-10-15) → `8b40981ae982e48cdf944414abb25058b84adc38`. | GitHub root `LICENSE` is Elastic License 2.0 (ELv2); connector/package exceptions and dependency notices need exact path-level review. This is source-available, not OSI open source. | Defer broad ingestion platform; revisit only for a demonstrated connector-scale need and after commercial/legal/ops review. |
| apache/superset | Branch-head evaluation snapshot `4e37ad09c11e3c724f0dc50c8661ddc4810c1a67`; distinct from the package release. | Stable PyPI package `apache-superset 6.1.0` (2026-05-13); matching source tag `6.1.0` → commit `c83fb2bb1dcfac41ac51bcebd82471f4a7180d18`. The release source SHA was mapped but not the code-evaluation pin. | Root `LICENSE.txt` Apache-2.0; inspected `superset-core` and `superset-extensions-cli` also Apache-2.0 at the branch snapshot. Other package/dependency licenses remain to be audited. | Optional BI/presentation product for a separate later evaluation only; not a source of canonical metrics, decisions or permissions; not selected. |
| metabase/metabase | Branch-head snapshot `b02100714f856a31555eb31675a7068fda12690d`; not the release pin. | `v0.65.6` (non-prerelease, 2026-11-18) → `518e559dcac5a32de65236c7754d4c911e644f70`. | Root `LICENSE.txt`: files outside top-level `enterprise/` are AGPL-3.0; enterprise code/features are under Metabase Commercial License; `LICENSE-EMBEDDING.txt` separately governs app-embed.js. Dependencies need review. | Defer optional BI/embedding evaluation; no copy/distribution/embedding before legal/commercial-license review. |

Additional candidate source references: [EMBMIS/erp](https://github.com/EMBMIS/erp), [Frappe/ERPNext](https://github.com/frappe/erpnext), [n8n](https://github.com/n8n-io/n8n), [Airbyte](https://github.com/airbytehq/airbyte), [Apache Superset](https://github.com/apache/superset), and [Metabase](https://github.com/metabase/metabase). Observed release references: [ERPNext v16.60.0](https://github.com/frappe/erpnext/releases/tag/v16.60.0), [n8n 2.48.7](https://github.com/n8n-io/n8n/releases/tag/n8n%402.48.7), [Airbyte v2.0.0](https://github.com/airbytehq/airbyte/releases/tag/v2.0.0), [Superset 6.1.0 source tag](https://github.com/apache/superset/tree/6.1.0), and [Metabase v0.65.6](https://github.com/metabase/metabase/releases/tag/v0.65.6). These links identify source/package metadata only, not adoption approval.

### Adoption, test and legal/security disposition for the six additional candidates

The additional candidates were checked selectively at their exact evaluation SHAs. **No tests or benchmarks were run for them, no source was copied, and no package/dependency was installed.** The following is a suitability screen, not a full security assessment or legal clearance:

- **EMBMIS ERP — `9043629…`:** intended role would be an upstream system or product/domain reference, not BusinessFlow's foundation. The observed Apache-2.0 root `LICENSE` is accompanied by a `NOTICE` reserving EMBMIS branding. Do not copy code or marks; verify every component's license, dependency tree, security controls and data model before any integration. No tests run. If reconsidered as a connector/source, test authenticated least-privilege access, tenant-safe mapping, idempotency, import reconciliation, upgrade compatibility and recovery; obtain security/legal review.
- **ERPNext — `bc0ea5…`:** treat as a possible external ERP connector target, never as BusinessFlow's canonical model. Root license is GPL-3.0; Frappe framework and each app/package must be cleared separately. Distribution, linking, SaaS and connector boundaries require counsel review before reuse. No tests run. If piloted, test the exact released ERPNext/Frappe pairing, schema/API drift, sync/checkpoint idempotency, permission boundaries, reconciliation, upgrades and backup/restore; complete legal and security review first.
- **n8n — `c7cbe…`:** workflow automation could be an external operator tool but is not needed for the first BusinessFlow outbox. The source uses Sustainable Use/Enterprise licensing rather than an OSI-approved open-source grant for all repository content; avoid embedding, hosting or commercial use until counsel reviews the exact service model and files. No tests run. If reconsidered, test secret isolation, tenant separation, approval identity, retries/idempotency, external egress, audit completeness, and outage/replay behavior; complete license and security review.
- **Airbyte — `f5cbf…`:** potential future ingestion/connectors platform, but operationally broader than file import and licensed under ELv2 at the root. Connector-specific license exceptions, deployment/security hardening, secrets, tenant isolation and all dependencies need review. No tests run. If reconsidered, test connector coverage, schema drift, checkpoints/backfills, duplicate handling, tenant/credential isolation, source freshness, resource limits, failure recovery and backup/restore; obtain ELv2 commercial/legal review.
- **Apache Superset — `4e37ad…`:** possible later read-only dashboard/presentation comparison only. Root and inspected core/extension packages are Apache-2.0; that does not grant data-model or row-security equivalence. If reconsidered, pin a stable application release and test row-level security, embedded-auth token scope, tenant-context propagation, query/cache isolation, sensitive columns, export paths, migrations/upgrades and operating footprint. Stable package `apache-superset 6.1.0` maps to source tag commit `c83fb2bb1dcfac41ac51bcebd82471f4a7180d18`; that release source was not the evaluated branch snapshot and no tests were run. Audit dependencies and notices before integration.
- **Metabase — `b021007…`:** possible later read-only BI/embedding benchmark, not BusinessFlow's metric/decision authority. Core outside `enterprise/` is AGPL-3.0; enterprise/embedding terms are separate. Obtain legal/commercial approval before distributing, embedding or offering hosted functionality. If reconsidered, pin a release and test row/column permissions, embed token scope, tenant isolation, cache/query leaks, exports, SSO/session behavior and upgrade/backup; audit dependencies and notices. No tests run.

For every candidate, any future adoption requires an exact supported release and commit, selected package paths, complete lockfile/SBOM and license notices, security-advisory review, upstream test evidence, BusinessFlow tenant/authorization/correctness tests, and written product/legal/security approval. No candidate is adopted in this assessment.

## Original eight source evaluations: snapshot and disposition

The following compact index preserves the original source-review map; use the version/release cross-check above for stable-release status and exact release-tag SHAs. These commit pins identify evaluated snapshots, not approved releases.

| Repository | Inspected default branch / commit | Observed license scope | Classification / disposition |
|---|---|---|---|
| [TheSelfSa/biased](https://github.com/TheSelfSa/biased) | `main` `75bbf77df8bf49bd3d9e332cd3202f2d6c043a60` | Root `LICENSE`: Apache-2.0 | `BUILD OURSELVES` for UI ideas; `REJECT` its backend/domain/auth/schema as product code |
| [Canner/WrenAI](https://github.com/Canner/WrenAI) | `main` `31390f6153887e893ebebb7d5b663eb131c782d2` | Root path map: `core/**`, `sdk/**`, examples and most root files Apache-2.0; `docs/**` CC BY 4.0; AGPL-3.0 text is listed for possible future modules. The inspected `core/wren` package is Apache-2.0. | `MODIFY` only as a future optional read-only semantic/query adapter candidate; not selected |
| [duckdb/duckdb](https://github.com/duckdb/duckdb) | `main` `87d96b7773a10356b11cc15c352125acdc58a9ce` | Root source `LICENSE`: MIT; extension/third-party components may carry separate terms | `MODIFY` only as a future isolated analytics/import adapter candidate; not primary DB |
| [pola-rs/polars](https://github.com/pola-rs/polars) | `main` `90d37c54f0fb49b213a8e36944b419d92f263bbf` | Root and `py-polars` license: MIT; Python project uses a separately distributed runtime package | `MODIFY` only as a future import-transformation adapter candidate |
| [cube-js/cube](https://github.com/cube-js/cube) | `master` `ece5b79e15095640e1294c1a465055269adfe26b` | Root says Apache-2.0 by default unless file/package says otherwise. Inspected backend packages declare Apache-2.0; Cube client core is MIT. | `MODIFY` only as the alternative future semantic/metrics adapter candidate; do not adopt with WrenAI |
| [oc8-ai/oc8](https://github.com/oc8-ai/oc8) | `main` `c01beb749866aeabf02744f7c460d4c16487ac0a` | Repository/backend metadata: LGPL-3.0-or-later; capas may have their own license/provenance | `REJECT` as BusinessFlow foundation or MVP agent runtime |
| [EMBMIS/gateway](https://github.com/EMBMIS/gateway) | `main` `0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de` | Apache-2.0. `NOTICE` expressly reserves the EMBMIS trademark; bundled console fonts are SIL OFL 1.1. | `REJECT` for code reuse pending major security remediation and independent review |
| [temporalio/temporal](https://github.com/temporalio/temporal) | `main` `32ef011fbd1e38ecddf656fc3f5026f6641cfeab` | Server source `LICENSE`: MIT. Client SDKs are separate projects. | `REPLACE` for MVP with a PostgreSQL outbox/state machine; revisit only if workflow evidence warrants it |

## 1. B.I.A.S.E.D.

### Components inspected

- [`apps/web/components/import-uploader.tsx`](https://github.com/TheSelfSa/biased/blob/75bbf77df8bf49bd3d9e332cd3202f2d6c043a60/apps/web/components/import-uploader.tsx), `import-ledger.tsx`, `investigation-console.tsx`, `forecast-lab.tsx`, `action-center-manager.tsx`, and `docs/feature-evidence.md`: UI/workflow patterns for imports, investigations, forecasting/scenarios and actions.
- [`apps/api/src/api/main.py`](https://github.com/TheSelfSa/biased/blob/75bbf77df8bf49bd3d9e332cd3202f2d6c043a60/apps/api/src/api/main.py), [`apps/api/src/api/services/demo_data.py`](https://github.com/TheSelfSa/biased/blob/75bbf77df8bf49bd3d9e332cd3202f2d6c043a60/apps/api/src/api/services/demo_data.py), [`apps/api/src/api/services/mcp_rpc.py`](https://github.com/TheSelfSa/biased/blob/75bbf77df8bf49bd3d9e332cd3202f2d6c043a60/apps/api/src/api/services/mcp_rpc.py): API routes, data import, demos, scenarios and MCP tools.
- [`apps/web/lib/schema.ts`](https://github.com/TheSelfSa/biased/blob/75bbf77df8bf49bd3d9e332cd3202f2d6c043a60/apps/web/lib/schema.ts), `auth.ts`, `rbac.ts`, `session.ts`: workspace schema and web authentication/RBAC helpers.

### Decision: workflow ideas `BUILD OURSELVES`; API/schema/auth core `REJECT`

- **What/relevance:** The UI workflows are close to the future BusinessFlow journey and can inform sequencing, empty states and review/confirm patterns. The API/schema implements an India-first pharmacy showcase with INR-specific fields and demo-workspace helpers.
- **How used:** Reference the interaction concepts only. No source code or assets are copied. Build a BusinessFlow-owned design system and workflow after the database/API are proven.
- **Modifications if a UI component is later reused:** Strip pharmacy/INR assumptions, use the approved BusinessFlow design/accessibility system, bind all data to backend-authorized tenant/company context, and make evidence and decision/action lifecycle states explicit.
- **Dependencies observed:** Next.js 16, React 19, Better Auth, Drizzle, `pg`; FastAPI, Pydantic settings, `psycopg`, DuckDB, Polars and openpyxl; optional LangChain/LangGraph and MCP. The repository requires Node >=24/pnpm >=10 and the API package declares Python >=3.14. The current workspace has Node 22, npm and Python 3.11.
- **License:** Root Apache-2.0. Keep copyright/license and modified-file notices if code is ever copied; product names/marks are not granted by the Apache license. Package/dependency licenses have not been fully inventoried.
- **Security finding:** Inspected FastAPI routes in `apps/api/src/api/main.py` have no authentication dependency or tenant context. `/mcp` dispatches tool calls without backend identity/authorization in the shown route. The web Better Auth/session/RBAC helpers do not establish backend enforcement for these routes. `schema.ts` uses a workspace and pharmacy/INR-oriented facts; it is not a canonical BusinessFlow model.
- **Integration boundary:** No API handler, schema, authorization helper, MCP endpoint, database table or financial logic is adopted.
- **Tests required before any future UI reuse:** Authenticated backend E2E, tenant/company isolation, role/permission checks, file security, import idempotency, accessible UI and action-state assertions. Inspect all source/dependency versions.
- **Acceptance:** A workflow is accepted only if it uses BusinessFlow APIs, verified tenant/role checks, source lineage, real deterministic calculations and explicit states; an attractive screen or passing type check is insufficient.
- **Tests run:** None. The inspected CI performs web lint/typecheck/build and Python `compileall`; it does not provide evidence of domain/authorization tests. The repo's Node/Python requirements do not match this workspace, and the component is not being reused.

## 2. WrenAI

### Components inspected

- [`core/wren-mdl/mdl.schema.json`](https://github.com/Canner/WrenAI/blob/31390f6153887e893ebebb7d5b663eb131c782d2/core/wren-mdl/mdl.schema.json), `core/wren-core-base/src/mdl/`, `core/wren-core/core/src/`: semantic manifest, relationships and model processing.
- [`core/wren/src/wren/engine.py`](https://github.com/Canner/WrenAI/blob/31390f6153887e893ebebb7d5b663eb131c782d2/core/wren/src/wren/engine.py), `policy.py`, `read_only.py`, `governed_query.py`: query planning/execution and SQL admission controls.
- [`core/wren/tests/unit/test_policy.py`](https://github.com/Canner/WrenAI/blob/31390f6153887e893ebebb7d5b663eb131c782d2/core/wren/tests/unit/test_policy.py): unit tests for table/function/read-only policy cases.
- [`core/wren/pyproject.toml`](https://github.com/Canner/WrenAI/blob/31390f6153887e893ebebb7d5b663eb131c782d2/core/wren/pyproject.toml): package metadata and direct dependencies.

### Decision: `MODIFY` as a candidate adapter only; do not adopt now

- **What/relevance:** Wren defines a structured semantic manifest, transforms queries and provides SQL AST checks. `WrenEngine.query(..., read_only=True)` invokes read-only validation and strict model/SQL policy checks; `mdl.schema.json` describes model relationships and column-level access-control metadata.
- **How used:** If it wins the M5 evaluation, run it behind a BusinessFlow-owned read-only adapter limited to approved models/views and safe result shapes. Our formulas, metric versions, approval policies and decision outcomes remain internal.
- **Required modifications/integration controls:** Do not expose an arbitrary SQL endpoint. Do not give it unrestricted production credentials. Derive tenant scope from the authenticated request and prove that the database session/query connection enforces that scope for every request and pooled connection. Use read-only DB permissions, query timeouts, row/column access tests and resource caps. If tenant context cannot be enforced at the actual connector transaction boundary, reject the integration.
- **Dependencies observed:** `wren-core-py`, DuckDB, sqlglot, PyArrow, OpenDAL, pandas, Pydantic and optional database connectors, plus additional security-related Python dependencies. A full lockfile/transitive-license/SBOM review is required before packaging. The inspected project metadata declares Apache-2.0 for `core/wren`; root licensing maps docs to CC BY 4.0 and describes possible future AGPL modules. Inspect the exact package/module path at adoption.
- **Security implications:** The source contains substantive AST policies, including read-only statement roots and a maintained list of file/remote data readers. Its own comments note that some non-source reader checks are a blocklist requiring maintenance. This does not prove correct financial joins, tenant isolation, row-policy propagation or safe connector settings. Strict mode is not proof that BusinessFlow metrics are correct.
- **License:** Relevant `core/**` and the inspected Python package are Apache-2.0. Documentation is CC BY 4.0. Root AGPL-3.0 text is described as applying to future modules; verify current path/package metadata and every dependency before reuse. Do not reuse project trademarks/assets without separate permission.
- **Integration boundary:** Optional read-only semantic-query service/adapter; never the canonical database, financial calculation engine, approval service or action executor.
- **Tests required:** SQL injection and dialect fuzzing; only approved models; wrong joins/cardinalities; Decimal/currency calculations against gold values; row/column policy; pool context leakage; tenant cross-read; timeouts/DoS; terminology in owner-approved product languages; prompt injection; concurrency and query plans.
- **Acceptance:** Every gold question in owner-approved product languages maps to intended metrics/joins and exact expected values; SQL is read-only and restricted to approved relations; unauthorized rows/columns are unreachable at the DB boundary; no untraceable number is presented as verified. Arabic/English are included only if selected as supported languages. Choose WrenAI or Cube, not both, only if the tests and benchmark justify it.
- **Tests run:** None. Relevant tests were inspected but not installed or executed.

## 3. DuckDB

### Components inspected

- [`src/`](https://github.com/duckdb/duckdb/tree/87d96b7773a10356b11cc15c352125acdc58a9ce/src), [`extension/parquet/`](https://github.com/duckdb/duckdb/tree/87d96b7773a10356b11cc15c352125acdc58a9ce/extension/parquet), root `LICENSE`, extension and benchmark directories.
- A recursive tree check at this pinned source did not find a Python binding subtree/package source; `tools/pythonpkg` is not present. The Python package distribution must therefore be reviewed independently if proposed.

### Decision: `MODIFY` behind an adapter, benchmark-gated

- **What/relevance:** The C++ analytical engine and Parquet extension can support local file scans, joins and aggregations. This could help import profiling or batch analysis, subject to representative measurements.
- **How used:** If benchmarks justify it, wrap an approved Python distribution in an isolated, read-only analytical worker that reads staged files or approved snapshots. PostgreSQL remains the transactional/canonical store.
- **Required modifications/controls:** No use as the multi-tenant primary database. Restrict extension install/autoload and arbitrary external file/URL access; use bounded input paths, temp storage, memory/time limits and per-tenant job isolation. Validate cancellation and cleanup.
- **Dependencies:** Supplied repository is the native engine; Python wheel/bindings, runtime artifacts, bundled extensions and transitive dependency licenses are not evaluated here. Audit the exact PyPI release and SBOM before use.
- **License:** Root DuckDB source license is MIT; extension/third-party terms must be checked individually. Retain license/notice for any redistribution.
- **Security implications:** Embedded analytics does not provide BusinessFlow's tenant authorization. User-provided SQL/extensions/paths and shared process state need confinement. Concurrency and memory behavior must be tested; no primary database duties are delegated to it.
- **Integration boundary:** Optional import/analytics adapter with no authority to write canonical facts, approve decisions or execute actions.
- **Tests required:** BusinessFlow-scale CSV/Parquet profiles, joins and aggregations; memory high-water, temp spill, cancellation, concurrency, tenant-separated inputs, malicious path/extension attempts and numeric semantics.
- **Acceptance:** It meets a pre-agreed latency/memory target on real representative data and beats PostgreSQL/Polars where relevant without compromising tenant isolation, Decimal semantics or reproducibility.
- **Tests/benchmarks run:** None.

## 4. Polars

### Components inspected

- [`py-polars/src/polars/`](https://github.com/pola-rs/polars/tree/90d37c54f0fb49b213a8e36944b419d92f263bbf/py-polars/src/polars), `py-polars/pyproject.toml`, `crates/polars-lazy/src/frame/mod.rs`, `crates/polars-io/src/csv/read/reader.rs` and root license.

### Decision: `MODIFY` as an optional transformation dependency, benchmark-gated

- **What/relevance:** Python/Rust dataframe APIs and lazy execution are candidates for CSV normalization, profiling and batch aggregation.
- **How used:** If selected, use it inside the ingestion/analytics adapter; domain validation, reconciliation decisions, data lineage and canonical promotion remain BusinessFlow services.
- **Required modifications/controls:** Pin a released package/runtime, explicitly set schemas where financial precision matters, validate null/type coercion, cap file/row/resource use, and stream/lazily process when appropriate. Excel support is optional and requires separate libraries; do not assume the core package parses every format.
- **Dependencies:** Inspected `py-polars/pyproject.toml` identifies version 2.1.0 in the moving source branch and depends on `polars-runtime-32 == 2.1.0`; optional Excel, Arrow, database and cloud connectors add separate dependencies. Use a reviewed release, not an unpinned branch build.
- **License:** Root and Python package metadata identify MIT. Audit the exact runtime wheel and optional dependency licenses before adoption; keep required notices.
- **Security implications:** Dataframe transforms do not enforce tenant access or semantic correctness. File parsing, decompression, memory use, Decimal/date/timezone handling and malformed data remain application concerns.
- **Integration boundary:** Ingestion transformation only; not the transactional database, canonical model, metrics authority or decision engine.
- **Tests required:** CSV/XLSX schema/type/null edge cases, Decimal/currency values, Unicode/Arabic text, date/time zones, duplicate behavior, lazy/eager parity, memory and cancellation against production-shaped files.
- **Acceptance:** Reproducible transformations with identical canonical results across test runs, documented resource bounds and measurable improvement over a simpler implementation.
- **Tests/benchmarks run:** None.

## 5. Cube

### Components inspected

- [`packages/cubejs-schema-compiler/src/compiler/CubeValidator.ts`](https://github.com/cube-js/cube/blob/ece5b79e15095640e1294c1a465055269adfe26b/packages/cubejs-schema-compiler/src/compiler/CubeValidator.ts), `JoinGraph.ts` and package manifests.
- [`packages/cubejs-api-gateway/src/query.ts`](https://github.com/cube-js/cube/blob/ece5b79e15095640e1294c1a465055269adfe26b/packages/cubejs-api-gateway/src/query.ts), `member-expression-security.ts`, `gateway.ts`.
- [`packages/cubejs-server-core/src/core/`](https://github.com/cube-js/cube/tree/ece5b79e15095640e1294c1a465055269adfe26b/packages/cubejs-server-core/src/core) and the root README security warning for development mode.

### Decision: `MODIFY` as the alternative M5 semantic/metrics candidate; not selected now

- **What/relevance:** Cube's schema compiler handles metrics/dimensions/relationships and join graphs; its API gateway exposes query surfaces and security-context integration. This may be closer to a reusable metrics API than a generic NL-to-SQL layer.
- **How used:** If it wins the M5 head-to-head test, consider one isolated read-only semantic API behind BusinessFlow authentication. BusinessFlow remains owner of authoritative metric formulas, evidence and decisions.
- **Required modifications/controls:** Supply security context only from the authenticated backend; model/validate tenant and row access explicitly; use production auth/JWT configuration; keep source schemas and secrets private; never expose development Playground/configuration. Do not set `CUBEJS_DEV_MODE=true` on a reachable environment: the inspected README explicitly warns it disables JWT verification and can expose model/configuration and data APIs.
- **Dependencies:** The inspected `cubejs-schema-compiler`, `cubejs-api-gateway` and `cubejs-server-core` packages declare Apache-2.0 and pull in a substantial Node package graph (compiler, query orchestrator, drivers, Express/JWT, native components and optional services). Cube client core is MIT. Complete package/driver/license/SBOM review is required. Do not conflate Cube Core with the separate commercial Cube product/cloud.
- **License:** Root license is mixed: Apache-2.0 by default; file/package metadata can specify another license. The three inspected backend packages declare Apache-2.0; client core is MIT. Verify every selected package and dependency; no trademark or commercial-product rights are inferred.
- **Security implications:** Member-expression guards address a specific generated-expression injection surface; they do not prove tenant isolation or BusinessFlow RBAC. Security context and production JWT behavior must be verified end to end. Model changes can alter joins/formulas and must be versioned/reviewed.
- **Integration boundary:** Optional metrics/query API only. Never the canonical business store, financial ledger, approval authority or action executor.
- **Tests required:** Tenant/row/column policy propagation, auth bypass and dev-mode prohibition, SQL/model injection, join cardinality, currency/Decimal gold cases, terminology in owner-approved product languages, query timeout/cache isolation, schema migration compatibility and concurrency. Include Arabic/English only if approved as supported languages.
- **Acceptance:** Meets the same gold suite, isolation and performance criteria as WrenAI and demonstrates a better fit/cost for the MVP. Choose at most one; if both fail, keep the controlled internal metrics/query API.
- **Tests/benchmarks run:** None.

## 6. oc8

### Components inspected

- [`ARCHITECTURE.md`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/ARCHITECTURE.md), [`docs/SCOPE_AND_LIMITATIONS.md`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/docs/SCOPE_AND_LIMITATIONS.md).
- [`backend/src/oc8/db/session.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/db/session.py), [`authz/pdp.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/authz/pdp.py), [`approvals/service.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/approvals/service.py), [`api/mcp_gateway.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/api/mcp_gateway.py), [`runtime/isolated.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/runtime/isolated.py), and [`sandbox/docker_driver.py`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/src/oc8/sandbox/docker_driver.py); migrations include RLS and approval/audit changes.
- [`backend/pyproject.toml`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/backend/pyproject.toml), root [`LICENSE`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/LICENSE), and [`THIRD_PARTY_LICENSES.md`](https://github.com/oc8-ai/oc8/blob/c01beb749866aeabf02744f7c460d4c16487ac0a/THIRD_PARTY_LICENSES.md).

### Decision: `REJECT` as product foundation/runtime

- **What/relevance:** It has agent runs, MCP gateways, human approvals, RLS-scoped sessions, a policy decision point, encrypted secrets, runtime adapters and a sandbox/provisioner. Some concepts are relevant to future threat modeling.
- **Why not reuse:** Its architecture is a broad agent platform (FastAPI/PostgreSQL/pgvector/Redis, workers/scheduler/ingestion, plugin/capa system and optional Docker provisioning) that would displace BusinessFlow's core boundaries and impose operational cost. Its docs describe a single root organization per deployment; the reference sandbox is not full multi-tenant isolation. The scope page notes dev login, HTTP-by-default, Docker socket root-equivalence and other deployment limitations. Source-level features do not equal verified production controls.
- **How used:** No code dependency or runtime adoption. Use source only as a review reference for tenant transaction binding and centralized approval funnels.
- **Dependencies:** FastAPI, SQLAlchemy async, asyncpg/psycopg, Alembic, PostgreSQL/pgvector, Redis, Docker SDK, MCP, cryptography, auth, document and model/provider packages. The full dependency graph is large and is not being audited for reuse.
- **License:** LGPL-3.0-or-later for the backend/frontend per repository metadata; individual `capas/` have their own provenance/licenses. Integrating/modifying/distributing covered code needs legal review and corresponding obligations. No code is selected.
- **Security implications:** Do not rely on its RLS, sandbox or approval claims without independent threat testing. Its own documented development and runtime limitations are disqualifying for direct adoption as a security boundary.
- **Integration boundary:** None in the MVP. BusinessFlow implements its own domain, deterministic decision/approval state and narrow integration ports.
- **Tests required if reconsidered:** Cross-tenant DB tests, pooled GUC reset, human-vs-agent authorization, approval race/stale payload, secrets handling, RCE/egress/sandbox escape, Docker-socket impact, plugin trust and backup/restore.
- **Acceptance:** Would require security/legal approval, a narrower module boundary and successful independent tests against BusinessFlow-specific identity, tenancy and action policies. No such evidence exists.
- **Tests run:** None; current workspace lacks the required Python 3.12+ target, PostgreSQL/Redis/container runtime and project dependencies.

## 7. EMBMIS Gateway

### Components inspected

- [`src/v1-api.js`](https://github.com/EMBMIS/gateway/blob/0cbc2f4cae8cb07d8dec8127e75fbfbfaf3e2de/src/v1-api.js), [`src/decisions.js`](https://github.com/EMBMIS/gateway/blob/0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de/src/decisions.js), [`src/gateway.js`](https://github.com/EMBMIS/gateway/blob/0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de/src/gateway.js), `src/financial-guard.js`, `src/policy-evaluator.js`, `src/audit-logger.js`, `src/grpc-client.js`, `src/keys.js`, [`enclave-worker.js`](https://github.com/EMBMIS/gateway/blob/0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de/enclave-worker.js).
- [`tests/v1-api.test.js`](https://github.com/EMBMIS/gateway/blob/0cbc2f4cae8cb07d8dec8127e75fb9fbfaf3e2de/tests/v1-api.test.js), `tests/security-invariants.test.js`, `tests/nonce-persistence.test.js`, `tests/crypto.test.js`, `package.json`, `LICENSE`, `NOTICE`, `SECURITY.md`.

### Decision: `REJECT` for direct reuse/security boundary

- **What/relevance:** The source implements a policy gateway, agent keys, signed execution tickets, risk/policy logic, escalation records, audit hashes and a worker boundary. Those capabilities resemble a possible later action-execution boundary.
- **Executed test:** In the temporary source checkout, `npm_config_nodedir=/usr/local npm ci --no-audit --no-fund` succeeded (171 packages), then `npm test -- v1-api` passed the selected suite (75 assertions). A direct `node tests/v1-api.test.js` first failed because the signed dev policy bundle was missing; the documented npm test runner generated development keys/bundle and the suite passed. This is one SQLite-backed suite, not the full suite or worker/PostgreSQL threat test.
- **Critical approval-control finding:** `src/v1-api.js` mounts `/decisions/:traceId/resolve` with `loadAgent` (agent API-key authentication), reads `by` from the request body and passes it to `resolveDecision`. `src/decisions.js` records that string as the person who resolved the item without independently authenticating a human. The passing test explicitly approves an escalation with `simeliKey` and `by: 'user:u_81'` (`tests/v1-api.test.js` 171–172). A compromised/authorized agent key can therefore resolve an escalation while claiming a human identity. This fails BusinessFlow's explicit-human-approval requirement.
- **Additional source findings:** `enclave-worker.js` reconstructs a signed ticket including `args` and writes the canonical ticket string to a debug log (`verifySignature`, line 191), risking disclosure of business/credential data. `src/gateway.js` returns `status: 'APPROVED'` with code `EXECUTION_DEGRADED` when the execution plane is unavailable, blurring authorization versus execution. `financial-guard.js` uses shallow `parseFloat` amount extraction and permits when an amount or configured threshold is not recognized; it is not a general financial safeguard. These are source-review findings; only the v1 route was dynamically exercised.
- **How used:** No code reuse. Build our own minimal approval/action boundary from the canonical decision/approval/action model, using exact authenticated humans, immutable approval payloads, idempotency, separate attempts and read-back verification.
- **Dependencies:** Node/Express, better-sqlite3, PostgreSQL driver, Redis, gRPC, bcrypt, crypto/PKI packages and other dependencies; `npm ci` installed 171 packages. Package lock and per-package licenses were not fully audited.
- **License:** Apache-2.0, but `NOTICE` states the EMBMIS trademark is not licensed and must not be used to identify modified products. Console fonts are OFL 1.1; package-level notices remain to be audited before any reuse.
- **Security implications:** The approval bypass finding is a release blocker for this use. Also require full worker key/nonce/replay, ticket canonicalization, secrets-in-logs, financial amount/currency bypass, idempotency, tenant binding and audit-tampering tests. Hash-chained rows in the same mutable database do not prove tamper-proof storage.
- **Integration boundary:** None. Do not put this gateway between BusinessFlow and external systems.
- **Tests required if reconsidered:** Separate human identity/session authorization from agent credentials; reject caller-supplied approver identities; prove tenant/object scope; redact logs; fail closed on missing amount/context/threshold; bind approval to exact payload and target; replay/retry and worker-failure tests; independent read-after-write verification; full PostgreSQL/worker threat suite.
- **Acceptance:** No security reuse until the source flaw is fixed upstream or independently patched, license/NOTICE and dependency audit passes, and all BusinessFlow threat scenarios pass. A passing repository suite alone is not sufficient.

## 8. Temporal

### Components inspected

- [`service/history/`](https://github.com/temporalio/temporal/tree/32ef011fbd1e38ecddf656fc3f5026f6641cfeab/service/history), `service/frontend/`, `common/persistence/`, `go.mod`, root README and `LICENSE`.

### Decision: `REPLACE` with a simpler MVP outbox; defer Temporal

- **What/relevance:** Temporal Server provides a durable workflow service with persisted history, retries/recovery and scheduling. This can be useful for complex long-running orchestrations.
- **Why defer:** The supplied repository is the Go server, not the Python client SDK. Running it adds a separate stateful service, supported persistence and operational/upgrade/monitoring responsibilities. The first project-acceptance and low-risk action flows do not yet demonstrate a need that a PostgreSQL outbox and explicit action state machine cannot meet.
- **How used:** For the MVP, build a bounded PostgreSQL-backed outbox/worker for approved actions with idempotency, attempts, retries, and verification. Revisit a Temporal client/server only if measured workflows require durable timers, complex compensation, long-running distributed orchestration or recovery guarantees beyond that design.
- **Dependencies/license:** Server source is MIT, but any selected Python SDK is a separate repo/package and needs its own version/license/dependency review. Operational persistence/visibility setup must be assessed.
- **Security implications:** Workflow durability does not itself enforce BusinessFlow tenant permissions, approval policy, secrets controls, connector idempotency or business-state verification.
- **Integration boundary:** If later adopted, the BusinessFlow action service remains the policy/approval boundary; Temporal orchestrates jobs and never becomes the decision or authorization source of truth.
- **Tests required:** Crash/restart recovery, duplicate delivery, idempotency, retries, payload versioning, tenant-scoped workflow access, approvals across long waits, cancellation, replay/determinism, history retention, backup/restore and operational failure modes.
- **Acceptance:** Adopt only when representative workflows exceed the outbox/state-machine reliability or complexity envelope and the total operating cost is accepted.
- **Tests/benchmarks run:** None; Go toolchain and Temporal service are not installed.

## 9. Reuse controls before any future adoption

Before adding a package/source component:

1. Pin a release/commit and inspect the exact source files and per-package metadata.
2. Audit the complete dependency lock/SBOM, licenses/notices, trademarks, advisories and maintenance state.
3. Establish a narrow adapter boundary and data/credential flow; do not vendor a whole repository.
4. Run upstream tests relevant to the selected code, then BusinessFlow-specific unit/integration/security tests.
5. Record changes, attribution, restrictions, threat-model notes and acceptance evidence here and in the security/dependency inventory.
6. Stop if the effective license or trademark rights are unclear; obtain legal review for copyleft or ambiguous components.
