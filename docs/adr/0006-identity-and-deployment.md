# ADR 0006: Self-hosted-first deployment and hybrid identity direction

- **Status:** Owner-confirmed direction for the assessment; implementation approval is pending.
- **Deployment:** Self-hosted/on-prem first; keep the design portable for future cloud deployment.
- **Identity:** First-party authentication plus OIDC/enterprise SSO, with proper MFA, account linking, recovery, session management and authorization policies.
- **Date:** 2026-11-21

## Context

BusinessFlow AI needs secure user authentication, authorization, tenant isolation, secrets protection, backup/restore and potentially data-residency controls. The owner confirmed self-hosted/on-prem as the initial deployment target while requiring deployment portability so cloud hosting can be added later without major architectural changes. The owner also confirmed hybrid identity and explicitly requires MFA, safe account linking and recovery, session management and authorization policies. The assessment remains under review; these directions do not authorize implementation.

## Decision direction

- Prioritize a customer-operated deployment for the first release. Recommend one Linux-host OCI/Compose reference profile, but the OS, exact operator responsibilities, storage encryption and packaging support policy remain open and owner approval is pending.
- Support first-party authentication and OIDC/enterprise SSO behind a provider-neutral identity boundary. Recommend Keycloak 26.8.2 local accounts/upstream OIDC brokering and Authlib 1.9.10 integration as one concrete option; this adds a separately operated identity service and is not owner-approved. Define the account and session model to support both, with explicit MFA, linking, recovery, revocation and audit policies.
- Keep data and operations inside the customer-controlled environment by default. Do not claim air-gap support, a particular residency jurisdiction, a specific identity provider, secret manager or backup service until those are selected and validated.
- Preserve a cloud path through standard PostgreSQL, explicit storage/secret/identity adapters and externalized configuration; avoid making cloud-specific services part of domain logic.

## Consequences and unresolved implementation details

The self-hosted choice affects installation/upgrade mechanics, supported OS/runtime, network boundaries, customer-operated TLS, secret provisioning, logging/monitoring, backup/restore, incident response, data export/deletion, support burden and security patch cadence. The hybrid identity choice adds account-linking and recovery risks in addition to OIDC configuration.

Before identity or deployment code is written, document and approve at least:

- The supported reference deployment and packaging, whether an installation hosts one or multiple tenants, and which party operates each component.
- First-party credential technology and storage; MFA methods and which users/actions require step-up MFA; account registration/invitation and tenant-admin bootstrap.
- OIDC issuer/provider support, redirect/callback controls, claims mapping, JIT provisioning policy and behavior when an IdP is unavailable.
- Safe account linking/merging, recovery, session issuance/expiry/revocation, CSRF and reauthentication rules.
- Secret-management, TLS, backup/restore, upgrade/rollback, logging/telemetry, data-residency, retention and deletion responsibilities.

No container/orchestrator, identity library, secret service, deployment infrastructure or auth package is approved by this ADR.

## Approval gate

The owner confirmed the direction above for the assessment. The owner has not approved Milestone 0 or implementation. Keep implementation paused until the complete revised assessment and detailed security decisions are explicitly approved.
