# BusinessFlow

**M9 product direction under assessment:** a company-wide Business Intelligence & Decision Platform.

BusinessFlow is intended to help a company use selected, governed business facts and deterministic analyses to support reviewable human decisions across the domains it chooses to enable. Projects are one currently supported business domain, not the product boundary. This is a proposed product direction—not a validated market position, approved Product Baseline, or description of capabilities already shipped.

The approved foundational trust principle is **Evidence Before Answers — No evidence, no confident answer**. Any future AI response must be evidence-grounded, traceable, transparent, permission-aware, honest about insufficient evidence, and reproducible for important assessments/decisions. Local/on-prem inference is a target requirement for any separately approved AI capability; no AI runtime or model is implemented or selected. See [`docs/AI_GOVERNANCE.md`](docs/AI_GOVERNANCE.md).

## Current implementation status

The repository contains bounded **M2–M8 backend slices**, a separately scoped M10 synthetic-only slice, M12 Company-data registration/evidence, M13 bounded metric comparisons, M14 finance-source intake, experimental M15/M16 finance prototypes, and a default-disabled Core Financials six-measure workflow in a Python/FastAPI modular monolith backed by PostgreSQL. The migration sequence runs through `0017_core_financial_cash_transfers`; 0014 revokes runtime user insertion and 0015 revokes runtime user updates, with both restrictions persisting through downgrades. Core Financials selects one measure per immutable run: five P&L measures use general-ledger imports and Cash Flow uses bank activity only as net observed bank movement—not a cash-flow statement or inferred opening/closing balance. Its policies and formulas are illustrative, `CORE_FINANCIALS_ENABLED` defaults off, and production remains blocked. M9 was documentation-only, M11 is a database-free calculation/verification package, and no separate M1 runtime/schema feature is evidenced in that sequence. M15/M16 remain separate mechanical prototypes and do not establish accounting policy, forecast completeness/accuracy, or production/customer-data authorization. The broader implementation includes tenant/company security foundations, a narrow workforce/project model, staged imports for six workforce/project dataset types, five draft observational metrics, project assessments and human decisions, incomplete project-scoped financial observations, a Company-scoped registry of existing M4 import evidence, versioned M5 comparisons, and explicit finance-source contracts with immutable reconciliation evidence.

M10 adds an **internal, synthetic-only monthly position-review walkthrough**: an explicitly seeded synthetic baseline, four exact-hash operational CSV templates, optional source-native synthetic finance observations, a hash-bound evidence brief with limitations/abstentions, and an immutable human disposition. It may bind optional M12 evidence but continues to accept only exact built-in templates in seeded demo Companies. This bounded slice is not production-ready and does not accept customer data. See [`docs/M10_SYNTHETIC_DEMO.md`](docs/M10_SYNTHETIC_DEMO.md) for scope and local steps.

M12 versions the six M4 mapping contracts and records Company-scoped source registrations, quality/freshness status, immutable snapshots and existing M4 lineage; it does not add a second importer, live connector, sync worker, or scheduler. See [`docs/M12_COMPANY_DATA_INTELLIGENCE.md`](docs/M12_COMPANY_DATA_INTELLIGENCE.md).

M13 compares three existing M5 metrics over equal-length, adjacent, completed Monday–Sunday periods, using matching Company scope and M5/M12 evidence. Direction labels are neutral; percentage changes use a signed-baseline Decimal formula, with explicit zero/missing/incompatible abstentions. The catalog intentionally keeps any owner-approved business interpretation unavailable. See [`docs/M13_METRIC_INSIGHTS.md`](docs/M13_METRIC_INSIGHTS.md).

M14 adds one manual, finance-specific CSV intake with a versioned semantic-light contract, strict exact-header mappings, profiling, row-quality evidence, and caller-supplied Decimal control comparisons. Its entire finance-source API is disabled by default; only a deployment operator can explicitly enable it with `FINANCE_SOURCE_INTAKE_ENABLED=true` for a controlled test deployment. A passing result is caller-supplied arithmetic only, not independent control verification, finance approval, or accounting correctness. It creates no canonical finance facts, ledger, FX conversion, forecast, or accounting conclusion. Finance-owner/source-access/correction/retention/independent-control gates remain unapproved, so customer/production accounting data is not authorized. M15 adds row-bound keyed amount commitments without storing raw row amounts. See [`docs/M14_FINANCE_SOURCE_CONTRACTS.md`](docs/M14_FINANCE_SOURCE_CONTRACTS.md).

M15 is a conditional engineering prototype, default-disabled by `FINANCIAL_CALCULATIONS_ENABLED=false`, hard-blocked in production, and dependent on M14 being enabled. It exposes only the exact mechanical subtotal of one complete M14 reconciliation group using M11 Decimal arithmetic and replay. M16 adds a separate fixed 13-week, default-disabled, production-blocked known-item cash roll-forward with explicit opening balance and caller-asserted flow types, dates, actual/forecast basis, and supersession. M14 does not authenticate those cash semantics or timing; absent categories remain unknown, never zero-filled. The M16 output is not a complete/accurate or source-authoritative forecast, and the feature flags do not authorize customer or production accounting data. See [`docs/M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md`](docs/M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md) and [`docs/M16_DIRECT_CASH_FORECAST.md`](docs/M16_DIRECT_CASH_FORECAST.md).

BusinessFlow is **not yet** a company-wide BI platform: broad company-domain coverage, a general KPI/analytics layer, approved financial calculations or policies, reusable non-project decision subjects, a general-purpose frontend, live connectors, and external actions are not implemented. M8 is not a company-wide finance engine; M10 does not change that boundary, and M14 does not satisfy the unresolved finance governance gates. Neither M15 nor M16 approves financial policy or closes those gates. The exact current behavior, schema paths, and tests are in [`docs/BACKEND_IMPLEMENTATION.md`](docs/BACKEND_IMPLEMENTATION.md).

## M9 product and architecture assessment

Start with these documentation-only assessment deliverables:

1. [Product vision](docs/product/PRODUCT_VISION.md)
2. [Target domain architecture](docs/architecture/TARGET_DOMAIN_ARCHITECTURE.md)
3. [Current-versus-target gap analysis and seven final answers](docs/architecture/CURRENT_VS_TARGET_GAP_ANALYSIS.md)
4. [Financial intelligence architecture](docs/architecture/FINANCIAL_INTELLIGENCE_ARCHITECTURE.md)
5. [Product roadmap proposal](docs/roadmap/PRODUCT_ROADMAP.md)

M9 itself changed documentation only and did not authorize a general implementation milestone. M10, M12, M13 and M14 are later, separately scoped implementation slices: M10 remains synthetic-only; M12 registers existing M4 imports with versioned contracts and evidence but does not add connectors; M13 adds bounded descriptive M5 comparisons; M14 adds only manual, source-asserted finance intake and reconciliation evidence. The M15 subtotal and M16 direct-cash schedule are separate conditional engineering prototypes, not milestone approval. M14–M16 do not approve production deployment, customer-data processing, a finance source, accounting policy, correction/retention rules, or any business interpretation. See the M10/M12/M13/M14/M15/M16 guides and the M9 gap analysis for their distinct scopes.

## Earlier project-feasibility hypothesis (historical)

The [`BRD.md`](docs/BRD.md), [`MVP_SCOPE.md`](docs/MVP_SCOPE.md), workflows, screens, acceptance criteria, competitive review, and project-decision proposal record a previous, project-feasibility-focused hypothesis. They are retained as historical input and must not be treated as the current product direction or as proof that those features are shipped. The prior founder discovery authorization is specific to that earlier hypothesis; M9 does not expand it.

The original 24 Part 2 requirements were not supplied. [`REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) remains **BLOCKED**; no substitute list is used.

## Developer and implementation references

- [Backend implementation and local developer guide](docs/BACKEND_IMPLEMENTATION.md)
- [M12 company data intelligence](docs/M12_COMPANY_DATA_INTELLIGENCE.md)
- [M13 bounded metric comparisons](docs/M13_METRIC_INSIGHTS.md)
- [M14 finance source contracts and reconciliation](docs/M14_FINANCE_SOURCE_CONTRACTS.md)
- [M15 deterministic financial calculation prototype](docs/M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md)
- [M16 direct cash schedule prototype](docs/M16_DIRECT_CASH_FORECAST.md)
- [PostgreSQL role initialization](scripts/README.md)
- Safe placeholder configuration: [`.env.example`](.env.example)
- [Current architecture overview](docs/ARCHITECTURE.md)
- [Domain model proposal](docs/DOMAIN_MODEL.md)
- [Database design and implemented schema subset](docs/DATABASE_DESIGN.md)
- [AI governance boundaries](docs/AI_GOVERNANCE.md)
- [Deployment architecture](docs/DEPLOYMENT_ARCHITECTURE.md)

The integration tests use PostgreSQL roles and forced RLS; SQLite is not a substitute for those security tests. See the backend guide and CI workflow for exact commands and current coverage.

## Delivery boundary

The M9 change was documentation-only. Later M10 adds migration `0008` and a narrow internal browser/API workflow restricted to built-in synthetic data. M12 adds migration `0009`, versioned M4 contracts, Company-scoped source registration and immutable ingestion evidence for existing M4 imports. M13 adds migration `0010` and immutable, hash-checked comparisons of three existing M5 metrics. M14 adds migration `0011`, one manual finance-source contract, strict mapping/profiling, row evidence, and caller-supplied Decimal control comparisons. The M15 prototype adds migration `0012`, keyed M14 amount commitments and an append-only, hash-bound M15 run record; only one exact mechanical subtotal is executable, and no raw source amount is retained in the M15 run table. M16 adds migration `0013`, a default-disabled, append-only M14-bound 13-week cash roll-forward with caller-asserted semantics, dates, basis and supersession; its input snapshot does not retain raw submitted amount text. Migration `0014` removes runtime-role account insertion; migration `0015` revokes all runtime user updates. Downgrading either revision does not restore those privileges. Public signup is disabled and the initial owner is provisioned through the operator-only command documented in [`scripts/README.md`](scripts/README.md). M10 remains **synthetic-only and not production-ready**; M12 reuses the existing M4 file intake; M14's finance governance gates remain unresolved and M15/M16 do not approve accounting policy, production use, milestone acceptance, or a complete/accurate forecast. See the [M15 prototype boundary](docs/M15_DETERMINISTIC_FINANCIAL_CALCULATIONS.md) and [M16 prototype boundary](docs/M16_DIRECT_CASH_FORECAST.md).
