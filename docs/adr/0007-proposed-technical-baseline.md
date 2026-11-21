# ADR 0007: Recommend a versioned Python/PostgreSQL self-hosted baseline

- **Status:** Proposed technical recommendation; not owner-approved. The exact stack/version recommendations below remain proposals.
- **Date:** 2026-11-21
- **Detailed component matrix:** `../TECHNICAL_BASELINE_PROPOSAL.md`

A later, separate delivery task authorized the limited backend described in [`../BACKEND_IMPLEMENTATION.md`](../BACKEND_IMPLEMENTATION.md). That task-scoped baseline uses some of the same component categories but does not accept this ADR, select the proposed versions wholesale, or approve product/finance decisions.

## Context

The architecture already proposes a FastAPI/Python modular monolith, PostgreSQL, Next.js, SQLAlchemy/Alembic, hybrid identity, and self-hosted-first operations. The prior review found the versions, identity integration, storage, API pattern, queue and container approach insufficiently specific. The owner has not approved those technology choices; the owner-confirmed direction is limited to Tenant/Company cardinality, on-prem first/cloud portability, and hybrid authentication requirements.

## Proposed decision

Recommend, for owner review only:

- CPython 3.13.18; FastAPI 0.165.0; Pydantic 2.14.4; SQLAlchemy 2.1.9; Alembic 1.20.1; psycopg 3.3.9.
- PostgreSQL 18.7 as canonical transaction/fact store.
- Next.js 16.4.6, React/React DOM 19.3.1, TypeScript 7.0.2, and Node 24.22.3 for the later UI.
- REST `/api/v1` with OpenAPI, synchronous request-scoped unit-of-work, and no arbitrary SQL surface.
- PostgreSQL transactional outbox plus a supervised worker from the same Python package; no Redis/Celery/Temporal initially.
- Bounded private local filesystem storage behind a `BlobStore` interface for imported originals; operator-managed encrypted volume, checksum, quotas and tested backups. No storage vendor selected.
- Keycloak 26.8.2 for local first-party accounts and upstream OIDC brokering; Authlib 1.9.10 for the application OIDC client; BusinessFlow remains authoritative for membership and authorization.
- pytest 9.2.0 and Playwright 1.65.1, with real PostgreSQL integration/security tests in CI.
- OCI images and Docker Compose 5.8.0 with Moby/Docker Engine 29.10.0 for a single-host Linux reference environment, pending owner selection of OS and operations model.

Exact license observations, compatibility metadata, alternatives and operational implications are maintained in the linked baseline proposal. The versions are review-date stable release/registry observations, not an approved lockfile or tested combination.

## Reasoning

This stack preserves the existing modular-monolith proposal, provides PostgreSQL semantics needed for RLS and constraints, keeps business rules within Python services, and supports a customer-operated deployment without immediately adding a workflow service or analytics engine. Keycloak is preferred over product-built password/MFA/OIDC primitives but adds a separate stateful service and operational responsibilities. Local file storage avoids an unnecessary object-store service in the first self-hosted slice while leaving an adapter boundary for later portability.

## Alternatives and trade-offs

Django/DRF offers more integrated administration/auth but changes the proposed backend shape. Async SQLAlchemy may be revisited if measurements justify it. Redis/Celery/Temporal add services before a need is demonstrated. MinIO/cloud object storage adds licensing/operations and remains deferred. Kubernetes is not justified before one reference deployment. The exact supported OS, installation tenant topology, IdP provider set, object-store migration path and storage encryption/key responsibility remain open.

## Compatibility and licensing gate

Metadata constraints were checked against PyPI/npm/GitHub on the review date, but the packages were not installed or tested together. psycopg is LGPL-3.0; exact wheel/binary packaging and distribution implications need legal review. All transitive licenses/advisories, SBOM, image provenance, supported version policy and security configuration must be reviewed before adopting any version.

## Consequences and approval gate

- **Historical at proposal review:** No package, image, database, identity service or deployment configuration had been installed or added. The later task added only the bounded code/configuration documented in `BACKEND_IMPLEMENTATION.md`.
- The proposal does not approve PostgreSQL, Keycloak, storage, Compose, or any exact versions; the implementation does not change that status.
- The full Milestone 0 gates and owner review of this proposed baseline remain incomplete; the separate backend task is not blanket authorization for other implementation decisions.
