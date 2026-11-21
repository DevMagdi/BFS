# BusinessFlow AI

**Project documentation baseline** — New-Project Feasibility Decision Support for Project-Based Services Companies.

## Current status

The provisional MVP is documented and reviewed for this documentation delivery, initially hypothesizing Software & IT Services. It supports a human decision on whether to accept a proposed project and under what conditions. It is not a customer-validated Product Baseline or implementation approval.

- Core scope, workflows, screens, and acceptance criteria are linked below.
- The technical stack and proposed database/security controls remain unapproved. Finance policies and production calculations remain pending Finance-owner approval.
- Tenant and Company remain distinct with a 1:1 MVP direction; self-hosted/on-prem-first and hybrid identity are requirements, not approval of particular technologies.
- The exact original 24 Part 2 requirements were not supplied. [`REQUIREMENTS_TRACEABILITY.md`](docs/REQUIREMENTS_TRACEABILITY.md) remains **BLOCKED**; no substitute list is used.
- Founder-led 9–12 exploratory interviews continue separately under the bounded protocol; discovery does not block this provisional definition.
- No Milestone 0 or implementation is authorized by these documents.

## Product requirements — start here

1. [Business Requirements Document](docs/BRD.md)
2. [MVP Scope](docs/MVP_SCOPE.md)
3. [User Workflows](docs/USER_WORKFLOWS.md)
4. [Screen Inventory](docs/SCREEN_INVENTORY.md)
5. [MVP Acceptance Criteria](docs/MVP_ACCEPTANCE_CRITERIA.md)
6. [Roadmap and Approval Gates](docs/ROADMAP.md)

The BRD's traceability map covers only its own BR/NFR IDs. It does not resolve the blocked original-24 exercise.

## Domain and technical proposals

These documents describe proposed designs; they do not freeze the technology or authorize implementation:

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

This repository currently contains documentation proposals and requirements only. A merge of the documentation baseline does not approve the technical stack, accounting/finance policies, or implementation. Do not add product code, dependencies, migrations, or deployment artifacts until the separately documented owner and technical approval gates are met.
