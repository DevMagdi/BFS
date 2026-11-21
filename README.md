# BusinessFlow AI

**Project:** New-Project Feasibility Decision Support for Project-Based Services Companies.

## Current status

The product and MVP documents remain provisional; they are not customer-validated product approval. This repository now also contains a small Milestone 2 backend foundation, implemented under explicit task authorization. That work does not retroactively approve the proposed stack, settle finance policies, or complete the product definition.

- Product scope, workflows, screens, and acceptance criteria remain linked below.
- The technical choices in the code are a minimal working baseline, not a permanent architecture or product decision.
- Finance policies, accounting assumptions, and production calculations remain outside this implementation and require their own owners and approval.
- The exact original 24 Part 2 requirements were not supplied. [`REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) remains **BLOCKED**; no substitute list is used.
- Discovery and product validation continue separately. This implementation does not claim that discovery or business acceptance gates are complete.

## Backend implementation

The current backend provides a versioned FastAPI API, PostgreSQL schema and Alembic migration, registration/login and refresh-session lifecycle, tenant-scoped owner/admin/member authorization, atomic tenant/company/owner onboarding, audit events, and PostgreSQL row-level security. It is intentionally a backend baseline, not a full MVP or a production-hardening approval.

- [Backend implementation and local developer guide](docs/BACKEND_IMPLEMENTATION.md)
- [PostgreSQL role initialization](scripts/README.md)
- Safe placeholder configuration: [`.env.example`](.env.example)

The integration tests require real PostgreSQL and separate runtime, migration, and test-admin connections. SQLite is not a supported test substitute for RLS. See the backend guide for setup and commands.

## Product requirements — start here

1. [Business Requirements Document](docs/BRD.md)
2. [MVP Scope](docs/MVP_SCOPE.md)
3. [User Workflows](docs/USER_WORKFLOWS.md)
4. [Screen Inventory](docs/SCREEN_INVENTORY.md)
5. [MVP Acceptance Criteria](docs/MVP_ACCEPTANCE_CRITERIA.md)
6. [Roadmap and Approval Gates](docs/ROADMAP.md)

The BRD traceability map covers only its own BR/NFR IDs. It does not resolve the blocked original-24 exercise.

## Domain and technical proposals

These documents describe proposed designs. They do not freeze the technology or authorize additional implementation beyond the current task:

- [Initial Technical Assessment](docs/INITIAL_TECHNICAL_ASSESSMENT.md)
- [System Architecture](docs/ARCHITECTURE.md)
- [Domain Model](docs/DOMAIN_MODEL.md)
- [Database Design](docs/DATABASE_DESIGN.md)
- [MVP Entity Catalog](docs/MVP_ENTITY_CATALOG.md)
- [Decision Model and Formula Candidates](docs/MVP_DECISION_MODEL.md)
- [Technical Baseline Proposal](docs/TECHNICAL_BASELINE_PROPOSAL.md)
- [Architecture Decision Records](docs/adr/)

## Security, deployment, AI, and dependencies

- [Identity and Authorization](docs/IDENTITY_AND_AUTHORIZATION.md)
- [Deployment Architecture](docs/DEPLOYMENT_ARCHITECTURE.md)
- [AI Governance](docs/AI_GOVERNANCE.md)
- [Open-Source Inventory](docs/OPEN_SOURCE_INVENTORY.md)

## Product evidence and governance

- [Competitive Validation](docs/PHASE_1_COMPETITIVE_VALIDATION.md)
- [Discovery Protocol](docs/PHASE_1_DISCOVERY_PROTOCOL.md)
- [Founder Decision Memo](docs/FOUNDER_DECISION_MEMO.md)
- [Final Review Corrections and Open Gates](docs/FINAL_REVIEW_CORRECTIONS.md)
- [Requirements Traceability — blocked pending exact input](docs/REQUIREMENTS_TRACEABILITY.md)

## Delivery boundary

The backend does not implement product/business calculations, resource or finance engines, AI/LLM/RAG or semantic engines, workflow automation, external ERP integrations, a large frontend, microservices, Kubernetes, or unrelated product features. It does not reconstruct the original 24 requirements or decide finance policy. Treat it as a limited implementation baseline; evaluate production deployment and future technical choices independently.
