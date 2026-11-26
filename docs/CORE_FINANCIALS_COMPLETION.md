# Financial & Business Core Completion — Step 1

**Status: Step 1 technical acceptance verified.** This is the backend implementation record for the first step of the company-wide intelligence expansion. It supplements, rather than rewrites, the historical project-feasibility review and the existing Core Financials contract. The software gates pass; source-governance and finance-owner decisions still block production readiness.

## Baseline and findings review

- Baseline: Phase 3 merge `44c72d32c22331f736142c6829fdeca959d29d3b` on `main`.
- GitHub issue inventory (`gh issue list --state all`): no open or closed issues were returned; there is no issue-to-finding cross-reference to inherit.
- `docs/FINAL_REVIEW_CORRECTIONS.md` is explicitly historical. It records documentation-resolved proposals separately from eight then-open owner/policy/platform/testing findings. Its former “no implementation/tests” statements are not current repository status. The exact 24 original requirements remain blocked; owner approval of accounting and source policies remains unresolved.
- `docs/IDENTITY_AND_AUTHORIZATION.md` records a bounded local login, bootstrap and M7 authorization implementation, but OIDC, MFA, invitation/recovery, broader scoped authorization and production acceptance remain open. Its security plan is not evidence of an implemented identity provider or production validation.

## Step 1 acceptance checklist

- [x] Preserve original UTF-8 CSV bytes for new imports; bind their byte length and SHA-256 to immutable, Company-scoped import evidence; make legacy imports without originals explicit and non-replayable as new calculation inputs.
- [x] Keep raw evidence behind `finance:read`, forced PostgreSQL RLS, restrictive grants, immutable-row protection and a tenant-scoped download path; add negative cross-company/member tests.
- [x] Require and persist only a hash of the run `Idempotency-Key`; bind it to the authenticated requester and canonical request; make same-key retries return the exact saved run and reject key reuse with different input.
- [x] Keep source identity/authority and declared completeness distinct from measured file/row quality; never represent caller assertions as verified. Expose a readiness/production gate with explicit blockers rather than an approval shortcut.
- [x] Preserve immutable, exact policy/mapping version and hash pins. Keep finance policy illustrative and production hard-blocked until an authorized finance owner and source-governance decisions exist.
- [x] Ensure caller-provided control arithmetic is not mislabeled as independent reconciliation; retain exact variance/tolerance evidence while independently verified control provenance is absent.
- [x] Preserve Decimal-only arithmetic, abstention on missing/unknown/incompatible evidence, the six existing measures, and the Cash Flow boundary of observed bank net movement only. Do not add or infer balances or statement components.
- [x] Verify all six measures with independent golden cases and PostgreSQL-backed import/run/evidence/replay, idempotency, authorization, tenant isolation, migration, grant, RLS, immutability, and schema-drift checks.
- [x] Record actual PostgreSQL 18.7 CI evidence. Browser E2E remains explicitly unverified unless separately run; no UI work is in scope.

## Verification evidence

- Commit `846e48ae39b24b694837091afd655ac74e6e3f0c` passed the Backend checks workflow on PR #32: [PostgreSQL 18.7 run 45888121693](https://github.com/DevMagdi/BFS/actions/runs/45888121693). The workflow asserted server version 18.7, passed Ruff formatting/lint and mypy, applied the full Alembic chain through `0023_core_financial_guardrails`, passed `alembic check`, and passed the PostgreSQL integration suite, including API/security/RLS/grants/tenant-isolation/immutability and migration downgrade coverage.
- Local database-independent verification passed **230 unit tests**, Ruff format/lint, mypy across 150 source files, `compileall`, `git diff --check`, and offline migration SQL rendering. Offline rendering is supplemental; the PostgreSQL CI run is the migration/integration evidence.
- Browser E2E is **unverified**. No UI/UX changes were made or required for this backend step.
- Readiness is intentionally blocked: passing software tests does not supply a finance owner, source authority, independently governed completeness/control, approved retention schedule, customer-data processing approval, or production authorization.

## Unresolved business-policy gates

No source system, source precedence, accountable finance owner, approved accounting definitions, control-independence procedure, legal retention schedule, customer-data authorization, or production approval has been supplied. Implementation may retain and verify bytes, bind evidence, quantify arithmetic, and report blockers; it must not turn those technical controls into a claim of source authority, completeness, accounting approval, independent reconciliation, or production readiness.
