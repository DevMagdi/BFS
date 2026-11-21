# BusinessFlow AI — Final Independent Review Corrections

- **Status:** Historical documentation-review record. The provisional MVP requirements have since been reviewed for documentation delivery; technical baseline, accounting policies and implementation remain unapproved, unimplemented and unverified.
- **Review date:** 2026-11-21 UTC.
- **Traceability:** The 24-item section remains **BLOCKED — awaiting the exact original Part 2 requirements**; see `REQUIREMENTS_TRACEABILITY.md`. No substitute list is provided.

## 1. What is resolved by documentation

- Specific, date-stamped version recommendations and alternatives are in `TECHNICAL_BASELINE_PROPOSAL.md`; all remain unapproved and no package was installed.
- A logical entity/ownership/key/tenant-isolation catalog is in `MVP_ENTITY_CATALOG.md`; it is not DDL. The current BRD/MVP_SCOPE Must/Should/Out boundary supersedes broader candidate entity labels for release priority.
- Required/optional inputs, formula candidates and finance-owner approvals, explicit abstention states, scenario versioning, and a reversible internal follow-up action are in `MVP_DECISION_MODEL.md`.
- `OPEN_SOURCE_INVENTORY.md` now distinguishes default-branch SHA snapshots from verified release pins, records observed root and package license scope, adds six candidates, and keeps WrenAI/Cube benchmark-gated.
- Relevant architecture, identity, deployment, roadmap and ADR documents link to the proposals and preserve confirmed product direction.

## 2. Findings still open

1. Exact original 24 requirements and verbatim traceability — blocked pending owner input.
2. Owner acceptance/revision of any proposed technical components and versions — open.
3. Installation tenancy topology, supported Linux target, packaging, storage encryption/key management, backups, retention, residency and operator responsibilities — open.
4. Keycloak/Authlib recommendation, specific OIDC providers, MFA factors/enforcement, recovery, first-party registration, SMTP and account-linking policy — open.
5. Finance-owner approval of accounting/capacity/cost/revenue/margin/FX/collection formulas and decision thresholds — open.
6. The internal follow-up task is optional/Should Have and may be deferred without blocking the core MVP; final release prioritization remains open.
7. OSS release mapping, full file/package license and dependency/SBOM/security review, exact selected versions, tests and legal clearance — open.
8. All RLS, identity, calculation, action, deployment and restore acceptance tests — not yet run.

## 3. Status distinctions

| Status | Current result |
|---|---|
| Documentation baseline | The provisional BRD/scope/workflow/screen/acceptance set has been reviewed for delivery; exact 24-item traceability remains blocked. |
| Approved by owner | No. The owner has not approved the technical baseline or overall architecture. Existing product directions remain owner-confirmed. |
| Implemented | No. No application code, migrations, dependencies, tests, deployment configuration, or infrastructure were added. |
| Tested | No BusinessFlow tests were run. Documentation-only static checks are not product tests. The previously recorded Gateway upstream test remains limited evidence about that candidate only. |
| Independently verified | No BusinessFlow control or end-to-end workflow is independently verified. Selected upstream source/license metadata was inspected, not fully audited. |

## 4. Change log

| File | Section changed | Previous decision/state | Clarification/recommendation and reason |
|---|---|---|---|
| `docs/REQUIREMENTS_TRACEABILITY.md` | Entire new document | Prior review contained a reconstructed 24-theme matrix | Replaced with an explicit BLOCKED marker. No guessed or paraphrased requirements; exact original Part 2 text is needed for faithful mapping. |
| `docs/TECHNICAL_BASELINE_PROPOSAL.md` | New technical proposal plus support-range section | FastAPI/PostgreSQL/Next.js/SQLAlchemy/Alembic were broad proposals; versions, auth provider, storage, packaging and support windows were unresolved | Adds date-stamped stable version/release evidence, Python/Node/package compatibility ranges, license observations, operational implications, alternatives, and explicit upstream support limits. Combined compatibility is untested; all remain recommendations, not owner-approved choices. |
| `docs/MVP_ENTITY_CATALOG.md` | Entire new document; identity entities include link/recovery lifecycle | Logical ERD and high-level entity lists only | Adds MVP/deferred status, ownership boundary, PK/FKs, tenant isolation and key integrity constraints for requested entities, including external identities, link/recovery events and sessions. No schema or migration is generated. |
| `docs/MVP_DECISION_MODEL.md` | Entire new document | Project-acceptance formulas, minimum inputs and first action were not defined | Defines required/optional/assumed/blocking inputs, candidate formulas, finance approvals, abstention contract, scenario versioning and a reversible internal follow-up task. No thresholds/accounting rules are invented. |
| `docs/FINAL_REVIEW_CORRECTIONS.md` | Entire new document | Findings and approval status were spread across earlier assessment docs | Centralizes remaining gaps, status distinctions, change log and review recommendation without implying implementation or approval. |
| `docs/ARCHITECTURE.md` | Sections 5–6 and project-decision references | Versions and runtime decisions were broad/unselected | Links the versioned baseline, clarifies REST/module boundaries, and links the abstention-first decision and internal-action proposal. Core modular-monolith recommendation is preserved; none of it is owner-approved. |
| `docs/DOMAIN_MODEL.md` | Project-acceptance slice, MVP/deferred entity references | Formulas/inputs were intentionally unspecified | Links to the explicit MVP decision contract and entity catalog while preserving Tenant/Company, determinism, provenance and no-consolidation directions. |
| `docs/DATABASE_DESIGN.md` | ERD/entity ownership and 1:1, identity, RLS references | Logical ERD did not fully show external identity/session/recovery and entity-level key rules | Links the catalog and clarifies global identity versus Tenant/Company-owned rows and tests. Deferred-FK 1:1 remains a proposal requiring PostgreSQL validation. |
| `docs/IDENTITY_AND_AUTHORIZATION.md` | Provider/session/recovery recommendation and decisions | Provider and session storage open | Adds a Keycloak/Authlib technical recommendation and app-session lifecycle; keeps all provider/MFA/recovery/authorization policy choices unapproved. |
| `docs/DEPLOYMENT_ARCHITECTURE.md` | Packaging, storage, identity services and gates | Platform, storage and packaging unselected | Proposes one-host OCI/Compose and private local storage adapter for an initial on-prem reference; leaves OS, tenant-per-installation, encryption, backup and operations responsibilities open. |
| `docs/AI_GOVERNANCE.md` | Abstention and project decision cross-reference | Generic abstention requirement | Links explicit decision states and blocks model-authored calculations/thresholds; preserves no-provider/no-LLM requirement. |
| `docs/OPEN_SOURCE_INVENTORY.md` | Pin/license matrix and new candidates | Eight candidates documented; branch heads could be confused with releases | Makes release-vs-SHA status explicit, adds six repositories, records observed license scopes and keeps all adoption subject to audit/legal/testing. No reuse approved. |
| `docs/ROADMAP.md` | Pre-M0, MVP definition, milestone gates and delivery status | Existing sequence; formula acceptance and candidate comparison were under-specified | Marks exact traceability blocked and no-M0 status, adds finance-owner gates, separates candidate engine evaluation, and records the provisional MVP as a documentation direction without implementation approval. |
| `README.md`, `BRD.md`, `MVP_SCOPE.md`, `USER_WORKFLOWS.md`, `SCREEN_INVENTORY.md`, `MVP_ACCEPTANCE_CRITERIA.md` | Reviewed documentation baseline | Provisional product decision and requirements were not yet in a connected, testable package | Adds the provisional decision-support BRD, narrow Must/Should/Out scope, workflows, screen inventory and acceptance suite, with cross-document IDs and explicit finance/technical/traceability gates. These documents do not approve implementation. |
| `docs/INITIAL_TECHNICAL_ASSESSMENT.md` | Status, executive recommendation, source-review conclusions, owner gates and delivery state | Prior recommendation was not sufficiently versioned and could blur proposal versus approval | Reframes the stack as owner-review-only, records the specific baseline/entity/decision links, fair WrenAI/Cube scope and six extra candidates, exact traceability block, open gates and no-implementation status. |
| `docs/adr/0001-modular-monolith.md` | Baseline reference and approval gate | Proposed modular monolith/PostgreSQL | Preserves proposal and links versioned baseline; explicitly no owner approval. |
| `docs/adr/0003-layered-tenant-isolation.md` | Approval gate | Wording could imply PostgreSQL had already been accepted | Clarifies PostgreSQL is recommended but not owner-confirmed; keeps layered isolation proposal and tests. |
| `docs/adr/0004-deterministic-decision-core.md` | Status/approval distinction | “Accepted as a product constraint” could be read as architecture approval | Preserves deterministic product constraint while stating it is not approval of architecture, baseline, or implementation. |
| `docs/adr/0005-defer-analytics-and-semantic-engines.md` | Candidate distinction and benchmark gate | Fair benchmark planned; candidates described adjacently | Preserves WrenAI/Cube benchmark and BusinessFlow ownership; distinguishes NL query from semantic API; DuckDB/Polars remain optional batch tools. |
| `docs/adr/0006-identity-and-deployment.md` | Proposed provider/packaging recommendation | Direction owner-confirmed; exact technologies open | Adds Keycloak/Authlib and OCI/Compose as review recommendations only; owner approval remains required. |
| `docs/adr/0007-proposed-technical-baseline.md` | Entire new ADR | No versioned baseline decision | Records specific component recommendations as proposed, with no dependency installation or implementation authorization. |
| `docs/adr/0008-project-decision-and-internal-action.md` | Entire new ADR | No minimum input/formula or first action decision | Records abstention-first calculation framework and reversible internal task recommendation; formulas and action release remain owner/finance approval gates. |

## 5. Updated recommendation and next owner decisions

**Recommendation:** keep the modular-monolith/PostgreSQL direction and proposed first project-acceptance slice as proposals; do not lock the stack or start Milestone 0. The technical baseline, identity provider/session design, filesystem storage, OCI/Compose profile and formula candidates remain unapproved. The internal follow-up task is optional/Should Have under the subsequently reviewed MVP scope. Preserve the owner-confirmed Tenant/Company 1:1 MVP, self-hosted/on-prem-first/cloud-portable direction and hybrid identity requirement.

The provisional MVP documentation has since been reviewed for delivery. The **verbatim original 24 Part 2 requirements** are still missing, so separate traceability remains blocked. Finance calculation policies, technical baseline, identity/deployment/security decisions and any implementation require their own approvals. No universal finance threshold is proposed.

## 6. No implementation authorization

This review did not start Milestone 0. No application/source code, migrations, dependencies, product tests, deployment configuration or infrastructure are included. A subsequent documentation-only Git commit/PR/merge does not count as implementation, technical approval or milestone completion. The exact Part 2 input is still required for the separate traceability exercise; no implementation is authorized here.
