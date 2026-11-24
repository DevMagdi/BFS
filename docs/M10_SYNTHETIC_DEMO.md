# M10 Monthly Position Review — synthetic local walkthrough

**Status:** internal implementation slice; synthetic data only; not production-ready. This guide does not authorize customer data, customer outreach, commercial use, or deployment.

The browser workflow is served by the FastAPI process at `/`. It uses the existing auth, tenant/company context, M4 import pipeline, and M5 metric snapshots. M12 can optionally bind an eligible existing M4 import to a versioned Company source registration and immutable freshness/quality evidence; this does not relax any M10 synthetic-template or mapping restrictions. See [`M12_COMPANY_DATA_INTELLIGENCE.md`](M12_COMPANY_DATA_INTELLIGENCE.md). No frontend build step, LLM, or external API is used.

## Start a fresh local workspace

Prerequisites: Docker Compose, Python 3.11 or newer, and a modern browser.

1. From the repository root, copy `.env.example` to `.env`. Replace all four placeholder secrets (`BF_BOOTSTRAP_PASSWORD`, `BF_MIGRATOR_PASSWORD`, `BF_RUNTIME_PASSWORD`, `APP_SECRET_KEY`) with independent random values. For each, generate one with:

   ```sh
   python -c 'import secrets; print(secrets.token_urlsafe(48))'
   ```

   `BF_BOOTSTRAP_PASSWORD` is only the PostgreSQL initialization-superuser password; it is not a BusinessFlow user password. Keep `.env` local and do not reuse production or customer credentials.

2. Start PostgreSQL and apply migrations using the dedicated migration role:

   ```sh
   docker compose up -d db
   docker compose --profile tools run --build --rm migrate
   ```

3. Before starting the API, provision the initial owner and tenant/company using the non-public command. The CLI image is already built by the migration service; it reads the migration URL from the service environment and prompts for the owner password without echoing it. Use tenant/company names that satisfy the M10 synthetic-seed guard:

   ```sh
   docker compose --profile tools run --rm -it --entrypoint sh migrate -c \
     'export BOOTSTRAP_DATABASE_URL="$MIGRATION_DATABASE_URL"; exec businessflow-admin bootstrap-owner --email demo-owner@example.com --display-name "Demo Owner" --tenant-name "M10 Synthetic Demo" --company-name "Synthetic Demo Company" --base-currency USD --timezone UTC --locale en'
   ```

   The command is for an authorized operator on a pristine install only. It does not verify email ownership or send email. See [`../scripts/README.md`](../scripts/README.md) for the security boundary and remaining onboarding requirements.

4. Start the API and open `http://localhost:8000/`:

   ```sh
   docker compose up -d api
   ```

   Sign in using the provisioned email and the password entered at the CLI prompt. Public signup is disabled; the CLI-created tenant and Company are already available. No invitation or email-delivery workflow exists.

5. In **Create Review**, explicitly select **Seed synthetic demo data**. Seeding is idempotent and only proceeds for a labeled Company with no existing workforce, project, staged/applied import, or metric data. It applies the bundled prior-period M4 examples and records an immutable four-week M5 baseline. It does not silently seed or overwrite an existing Company.

6. Download each of the four synthetic operational CSVs, select it in its matching card, inspect the suggested mapping, keep every column mapped to its same-named field, validate, review the preview, then confirm and apply. The seeded employee and project rows are intentionally duplicates; acknowledge those warnings. Capacity and assignment imports must have no warnings or skipped rows. M4 preserves source-row hashes and canonical links and does not overwrite existing facts.

   **This demo accepts only the exact built-in synthetic CSV template bytes.** The browser checks before upload, the API rejects non-template operational files before parsing or persisting their source rows, and the API requires the exact same-name field mapping. Direct create/update/delete operations on canonical workforce, project, capacity, and assignment records are also disabled in a seeded M10 workspace; use only the guarded import flow. Do not upload customer, payroll, accounting, personal, or other confidential data. The finance endpoint also rejects any file other than its exact built-in synthetic export before persisting parsed rows.

7. Optionally validate and explicitly confirm the built-in finance CSV. The review shows source-native text, currency, period, and row provenance only; it performs no aggregation, FX conversion, variance, financial classification, or financial conclusion. Leaving finance unselected records an explicit abstention.

8. Review the attestations. The four-week planned-assignment comparison is withheld unless the M5 definition, active cohort, and assignment-completeness attestations are all checked and the bounded inputs are compatible. Capacity currentness attestations are recorded separately. These are human attestations, not proof of completeness or source accuracy.

9. Select **Create Position Brief**. Inspect the saved version, M5 run hashes, M4 file/validation hashes, row evidence, exact units and period semantics, limitations, and abstentions in **Position Decision Brief**. Then use **Record Review** to save a human disposition and rationale against the exact result hash. A reviewer can record one immutable disposition per brief; no external action is executed.

Every screen remains labeled synthetic. M5’s recorded capacity and planned assignment hours are not actual work, timesheets, utilization, or financial performance. The comparison is observational and never explains why a plan changed.

## Checks

DB-free M10 checks (safe without a database):

```sh
PYTHONPATH=src .venv/bin/pytest --noconftest -q tests/test_position_reviews_calculation.py tests/test_position_review_web.py
.venv/bin/ruff format --check src migrations tests
.venv/bin/ruff check src migrations tests
.venv/bin/mypy src/businessflow
node --check src/businessflow/web/assets/app.js
.venv/bin/alembic upgrade head --sql > /tmp/businessflow-m10-migration.sql
```

`tests/test_position_reviews.py` is an end-to-end PostgreSQL test: it creates and migrates tenant data. Offline SQL generation checks the migration graph and renders SQL; it does **not** verify PostgreSQL DDL, trigger behavior, RLS, privileges, or the browser/API round trip.

The full integration suite is destructive to its configured `businessflow_test` database: its fixture downgrades to Alembic `base` before upgrading. The preferred verification route is the active GitHub Actions workflow on a PR to `main`; it provisions a fresh per-job PostgreSQL 18 service, creates the test-only roles/database, verifies the server identity, then runs the migration checks and full suite.

Do **not** use the Compose test profile for this destructive suite by default: `docker-compose.yml` connects it to the same PostgreSQL service backed by the persistent `postgres-data` volume. A separate database name on that volume does not make the instance disposable. Run PostgreSQL integration tests locally only against a newly provisioned, dedicated disposable instance after verifying its exact server, database, roles, and lack of persistent/shared data. Never target `businessflow`, an existing developer database, or unknown credentials. To stop the local demo without deleting its data, use `docker compose down`; only use `docker compose down -v` when intentionally deleting its local development volume.
