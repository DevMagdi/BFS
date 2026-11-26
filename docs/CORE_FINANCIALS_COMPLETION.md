# Financial & Business Core Completion — Step 1

**Status: in progress.** This is the backend implementation record for the first step of the company-wide intelligence expansion. It supplements, rather than rewrites, the historical project-feasibility review and the existing Core Financials contract.

## Baseline and findings review

- Baseline: Phase 3 merge `44c72d32c22331f736142c6829fdeca959d29d3b` on `main`.
- GitHub issue inventory (`gh issue list --state all`): no open or closed issues were returned; there is no issue-to-finding cross-reference to inherit.
- `docs/FINAL_REVIEW_CORRECTIONS.md` is explicitly historical. It records documentation-resolved proposals separately from eight then-open owner/policy/platform/testing findings. Its former “no implementation/tests” statements are not current repository status. The exact 24 original requirements remain blocked; owner approval of accounting and source policies remains unresolved.
- `docs/IDENTITY_AND_AUTHORIZATION.md` records a bounded local login, bootstrap and M7 authorization implementation, but OIDC, MFA, invitation/recovery, broader scoped authorization and production acceptance remain open. Its security plan is not evidence of an implemented identity provider or production validation.

## Step 1 acceptance checklist

- [ ] Preserve original UTF-8 CSV bytes for new imports; bind their byte length and SHA-256 to immutable, Company-scoped import evidence; make legacy imports without originals explicit and non-replayable as new calculation inputs.
- [ ] Keep raw evidence behind `finance:read`, forced PostgreSQL RLS, restrictive grants, immutable-row protection and a tenant-scoped download path; add negative cross-company/member tests.
- [ ] Require and persist only a hash of the run `Idempotency-Key`; bind it to the authenticated requester and canonical request; make same-key retries return the exact saved run and reject key reuse with different input.
- [ ] Keep source identity/authority and declared completeness distinct from measured file/row quality; never represent caller assertions as verified. Expose a readiness/production gate with explicit blockers rather than an approval shortcut.
- [ ] Preserve immutable, exact policy/mapping version and hash pins. Keep finance policy illustrative and production hard-blocked until an authorized finance owner and source-governance decisions exist.
- [ ] Ensure caller-provided control arithmetic is not mislabeled as independent reconciliation; retain exact variance/tolerance evidence while independently verified control provenance is absent.
- [ ] Preserve Decimal-only arithmetic, abstention on missing/unknown/incompatible evidence, the six existing measures, and the Cash Flow boundary of observed bank net movement only. Do not add or infer balances or statement components.
- [ ] Verify all six measures with independent golden cases and PostgreSQL-backed import/run/evidence/replay, idempotency, authorization, tenant isolation, migration, grant, RLS, immutability, and schema-drift checks.
- [ ] Record actual PostgreSQL 18.7 CI evidence. Browser E2E remains explicitly unverified unless separately run; no UI work is in scope.

## Unresolved business-policy gates

No source system, source precedence, accountable finance owner, approved accounting definitions, control-independence procedure, legal retention schedule, customer-data authorization, or production approval has been supplied. Implementation may retain and verify bytes, bind evidence, quantify arithmetic, and report blockers; it must not turn those technical controls into a claim of source authority, completeness, accounting approval, independent reconciliation, or production readiness.
