# BusinessFlow

**M9 product direction under assessment:** a company-wide Business Intelligence & Decision Platform.

BusinessFlow is intended to help a company use selected, governed business facts and deterministic analyses to support reviewable human decisions across the domains it chooses to enable. Projects are one currently supported business domain, not the product boundary. This is a proposed product direction—not a validated market position, approved Product Baseline, or description of capabilities already shipped.

The approved foundational trust principle is **Evidence Before Answers — No evidence, no confident answer**. Any future AI response must be evidence-grounded, traceable, transparent, permission-aware, honest about insufficient evidence, and reproducible for important assessments/decisions. Local/on-prem inference is a target requirement for any separately approved AI capability; no AI runtime or model is implemented or selected. See [`docs/AI_GOVERNANCE.md`](docs/AI_GOVERNANCE.md).

## Current implementation status

The repository contains bounded **M2–M8 backend slices** plus a separately scoped M10 slice in a Python/FastAPI modular monolith backed by PostgreSQL. The migration sequence is `0001_backend_foundation` through `0008_monthly_position_review`; M9 was documentation-only, and no separate M1 runtime/schema feature is evidenced in that sequence. The implementation includes tenant/company security foundations, a narrow workforce/project model, staged imports for six workforce/project dataset types, five draft observational metrics, a project capacity assessment, a project-stage human decision/approval flow, and project-scoped financial observations whose assessment remains incomplete without approved policy.

M10 adds an **internal, synthetic-only monthly position-review walkthrough**: an explicitly seeded synthetic baseline, four exact-hash operational CSV templates, optional source-native synthetic finance observations, a hash-bound evidence brief with limitations/abstentions, and an immutable human disposition. This bounded slice is not production-ready and does not accept customer data. See [`docs/M10_SYNTHETIC_DEMO.md`](docs/M10_SYNTHETIC_DEMO.md) for scope and local steps.

BusinessFlow is **not yet** a company-wide BI platform: broad company-domain coverage, a general KPI/analytics layer, approved financial calculations or policies, reusable non-project decision subjects, a general-purpose frontend, live connectors, and external actions are not implemented. M8 is not a company-wide finance engine; M10 does not change that boundary. The exact current behavior, schema paths, and tests are in [`docs/BACKEND_IMPLEMENTATION.md`](docs/BACKEND_IMPLEMENTATION.md).

## M9 product and architecture assessment

Start with these documentation-only assessment deliverables:

1. [Product vision](docs/product/PRODUCT_VISION.md)
2. [Target domain architecture](docs/architecture/TARGET_DOMAIN_ARCHITECTURE.md)
3. [Current-versus-target gap analysis and seven final answers](docs/architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md)
4. [Financial intelligence architecture](docs/architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md)
5. [Product roadmap proposal](docs/roadmap/PRODUCT_ROADMAP.md)

M9 itself changed documentation only and did not authorize a general implementation milestone. M10 is a later, separately requested synthetic-only implementation slice with the strict limits documented above; it does not approve customer-data handling, production deployment, a broader product baseline, or finance policy. See the M10 guide and the M9 gap analysis for their distinct scopes.

## Earlier project-feasibility hypothesis (historical)

The [`BRD.md`](docs/BRD.md), [`MVP_SCOPE.md`](docs/MVP_SCOPE.md), workflows, screens, acceptance criteria, competitive review, and project-decision proposal record a previous, project-feasibility-focused hypothesis. They are retained as historical input and must not be treated as the current product direction or as proof that those features are shipped. The prior founder discovery authorization is specific to that earlier hypothesis; M9 does not expand it.

The original 24 Part 2 requirements were not supplied. [`REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) remains **BLOCKED**; no substitute list is used.

## Developer and implementation references

- [Backend implementation and local developer guide](docs/BACKEND_IMPLEMENTATION.md)
- [PostgreSQL role initialization](scripts/README.md)
- Safe placeholder configuration: [`.env.example`](.env.example)
- [Current architecture overview](docs/ARCHITECTURE.md)
- [Domain model proposal](docs/DOMAIN_MODEL.md)
- [Database design and implemented schema subset](docs/DATABASE_DESIGN.md)
- [AI governance boundaries](docs/AI_GOVERNANCE.md)
- [Deployment architecture](docs/DEPLOYMENT_ARCHITECTURE.md)

The integration tests use PostgreSQL roles and forced RLS; SQLite is not a substitute for those security tests. See the backend guide and CI workflow for exact commands and current coverage.

## Delivery boundary

The M9 change was documentation-only. The later M10 slice adds APIs, models, migration `0008`, deterministic calculations, and a narrow internal browser workflow, all restricted to built-in synthetic data. It is **not production-ready** and does not authorize customer-data processing, general finance calculations, external actions, or deployment. Product, finance-policy, data-handling, and production-approval gates remain separate.
