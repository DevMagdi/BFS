# BusinessFlow

**M9 product direction under assessment:** a company-wide Business Intelligence & Decision Platform.

BusinessFlow is intended to help a company use selected, governed business facts and deterministic analyses to support reviewable human decisions across the domains it chooses to enable. Projects are one currently supported business domain, not the product boundary. This is a proposed product direction—not a validated market position, approved Product Baseline, or description of capabilities already shipped.

The approved foundational trust principle is **Evidence Before Answers — No evidence, no confident answer**. Any future AI response must be evidence-grounded, traceable, transparent, permission-aware, honest about insufficient evidence, and reproducible for important assessments/decisions. Local/on-prem inference is a target requirement for any separately approved AI capability; no AI runtime or model is implemented or selected. See [`docs/AI_GOVERNANCE.md`](docs/AI_GOVERNANCE.md).

## Current implementation status

The repository contains bounded **M2–M8 backend slices**, a separately scoped M10 synthetic-only slice, M12 Company-data registration/evidence, and M13 bounded metric comparisons in a Python/FastAPI modular monolith backed by PostgreSQL. The migration sequence is `0001_backend_foundation` through `0010_metric_insight_runs`; M9 was documentation-only, M11 is a database-free calculation/verification package, and no separate M1 runtime/schema feature is evidenced in that sequence. The implementation includes tenant/company security foundations, a narrow workforce/project model, staged imports for six workforce/project dataset types, five draft observational metrics, a project capacity assessment, a project-stage human decision/approval flow, project-scoped financial observations whose assessment remains incomplete without approved policy, a Company-scoped registry for existing M4 import evidence, and versioned M5 comparisons that retain neutral direction semantics, completion boundaries, evidence/quality/freshness gates, percentage-change abstentions, hashes and replay.

M10 adds an **internal, synthetic-only monthly position-review walkthrough**: an explicitly seeded synthetic baseline, four exact-hash operational CSV templates, optional source-native synthetic finance observations, a hash-bound evidence brief with limitations/abstentions, and an immutable human disposition. It may bind optional M12 evidence but continues to accept only exact built-in templates in seeded demo Companies. This bounded slice is not production-ready and does not accept customer data. See [`docs/M10_SYNTHETIC_DEMO.md`](docs/M10_SYNTHETIC_DEMO.md) for scope and local steps.

M12 versions the six M4 mapping contracts and records Company-scoped source registrations, quality/freshness status, immutable snapshots and existing M4 lineage; it does not add a second importer, live connector, sync worker, or scheduler. See [`docs/M12_COMPANY_DATA_INTELLIGENCE.md`](docs/M12_COMPANY_DATA_INTELLIGENCE.md).

M13 compares three existing M5 metrics over equal-length, adjacent, completed Monday–Sunday periods, using matching Company scope and M5/M12 evidence. Direction labels are neutral; percentage changes use a signed-baseline Decimal formula, with explicit zero/missing/incompatible abstentions. The catalog intentionally keeps any owner-approved business interpretation unavailable. See [`docs/M13_METRIC_INSIGHTS.md`](docs/M13_METRIC_INSIGHTS.md).

BusinessFlow is **not yet** a company-wide BI platform: broad company-domain coverage, a general KPI/analytics layer, approved financial calculations or policies, reusable non-project decision subjects, a general-purpose frontend, live connectors, and external actions are not implemented. M8 is not a company-wide finance engine; M10 does not change that boundary. The exact current behavior, schema paths, and tests are in [`docs/BACKEND_IMPLEMENTATION.md`](docs/BACKEND_IMPLEMENTATION.md).

## M9 product and architecture assessment

Start with these documentation-only assessment deliverables:

1. [Product vision](docs/product/PRODUCT_VISION.md)
2. [Target domain architecture](docs/architecture/TARGET_DOMAIN_ARCHITECTURE.md)
3. [Current-versus-target gap analysis and seven final answers](docs/architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md)
4. [Financial intelligence architecture](docs/architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md)
5. [Product roadmap proposal](docs/roadmap/PRODUCT_ROADMAP.md)

M9 itself changed documentation only and did not authorize a general implementation milestone. M10, M12 and M13 are later, separately scoped implementation slices: M10 remains synthetic-only; M12 registers existing M4 imports with versioned contracts and evidence but does not add connectors; M13 adds only bounded descriptive M5 comparisons. None approves production deployment, a broader product baseline, finance policy, or a business interpretation. See the M10/M12/M13 guides and the M9 gap analysis for their distinct scopes.

## Earlier project-feasibility hypothesis (historical)

The [`BRD.md`](docs/BRD.md), [`MVP_SCOPE.md`](docs/MVP_SCOPE.md), workflows, screens, acceptance criteria, competitive review, and project-decision proposal record a previous, project-feasibility-focused hypothesis. They are retained as historical input and must not be treated as the current product direction or as proof that those features are shipped. The prior founder discovery authorization is specific to that earlier hypothesis; M9 does not expand it.

The original 24 Part 2 requirements were not supplied. [`REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) remains **BLOCKED**; no substitute list is used.

## Developer and implementation references

- [Backend implementation and local developer guide](docs/BACKEND_IMPLEMENTATION.md)
- [M12 company data intelligence](docs/M12_COMPANY_DATA_INTELLIGENCE.md)
- [M13 bounded metric comparisons](docs/M13_METRIC_INSIGHTS.md)
- [PostgreSQL role initialization](scripts/README.md)
- Safe placeholder configuration: [`.env.example`](.env.example)
- [Current architecture overview](docs/ARCHITECTURE.md)
- [Domain model proposal](docs/DOMAIN_MODEL.md)
- [Database design and implemented schema subset](docs/DATABASE_DESIGN.md)
- [AI governance boundaries](docs/AI_GOVERNANCE.md)
- [Deployment architecture](docs/DEPLOYMENT_ARCHITECTURE.md)

The integration tests use PostgreSQL roles and forced RLS; SQLite is not a substitute for those security tests. See the backend guide and CI workflow for exact commands and current coverage.

## Delivery boundary

The M9 change was documentation-only. Later M10 adds APIs, models, migration `0008`, deterministic comparisons, and a narrow internal browser workflow restricted to built-in synthetic data. M12 adds APIs, models, migration `0009`, versioned M4 contracts, Company-scoped source registration and immutable ingestion evidence for existing M4 imports. M13 adds migration `0010` and APIs for immutable, hash/replay-verified comparisons of three existing M5 metrics; it adds no business target, interpretation, causation, finance policy, or recommendation. M10 is **not production-ready**; M12 reuses the existing M4 file intake and does not add a second upload workflow or live connectors. Product, finance-policy, data-handling, production-approval, and deployment gates remain separate.
