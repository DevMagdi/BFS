# Operational scripts

`db-init/010-create-roles.sh` runs only during first initialization of the local PostgreSQL data volume. It creates separate `bf_migrator` and `bf_runtime` roles with explicit `NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`, and `NOINHERIT` attributes, plus the `businessflow` / `businessflow_test` databases. `bf_runtime` must not be granted membership in any other role. Migrations and bootstrap re-read these properties from `pg_roles`; the script is not a substitute for checking an already-existing role. The bootstrap superuser is used only by the PostgreSQL image entrypoint and is not placed in the API or migration connection URL.

## Initial owner provisioning

Public self-service registration is disabled. After database migrations have completed and before opening the application to users, an authorized deployment operator provisions the first owner and first tenant with the installed `businessflow-admin bootstrap-owner` command. Run it only against a pristine installation; it refuses any database that already contains a user account, predates migration 0014, gives `bf_runtime` effective `INSERT` or `UPDATE` on `users`, or has unsafe runtime-role attributes or memberships. The validator checks the actual role properties (`NOSUPERUSER`, `NOBYPASSRLS`, `NOCREATEDB`, `NOCREATEROLE`, `NOREPLICATION`, `NOINHERIT`, and no memberships) and inspects `PUBLIC` and effective user-table grants. Those checks are repeated inside the provisioning transaction. The command uses a PostgreSQL URL supplied only through `BOOTSTRAP_DATABASE_URL` and requires a direct `bf_migrator` login that is neither superuser nor `BYPASSRLS`. The authorized operator is responsible for controlling access to that migration credential.

Run the command from an interactive terminal so it can request and confirm the initial password without echoing it or placing it in shell history/process arguments. Supply the owner's email/display name and the initial tenant/company details as command options; do not pass a password option. The command rechecks emptiness inside a serialized transaction, creates the user/tenant/company/owner membership atomically, and does not print the password, token, or database URL. It does not issue a session token or send an email. A second or concurrent initialization is rejected. Stop any older application workers that still expose account registration before provisioning; the current application has no public registration route and the runtime database role cannot insert or update user rows.

Example (with the privileged URL already delivered to the operator's environment by the deployment secret mechanism; do not echo it):

```sh
businessflow-admin bootstrap-owner \
  --email owner@example.invalid \
  --display-name 'Initial Owner' \
  --tenant-name 'Example Tenant' \
  --company-name 'Example Company' \
  --base-currency USD \
  --timezone UTC \
  --locale en
```

The example address is illustrative only. The operator must provision the intended owner; the application does not verify email ownership or deliver credentials. Email delivery/provider selection and invitation/verification token lifetime/security policy are undecided. Until those are approved and implemented, new tenant membership creation is unavailable; the product does not implement end-to-end invitations or verification. Existing accounts and memberships are retained without automatic revocation or a claim that their email addresses were verified.

## User-table privilege downgrades

Migration `0014_disable_public_signup` keeps `INSERT` revoked from `PUBLIC` and `bf_runtime` when downgraded; `0015_limit_runtime_users_update` likewise keeps all table- and column-level `UPDATE` revoked. This is a deliberate monotonic security boundary: `alembic downgrade` does not restore the earlier runtime account-write contract. An older application binary that expects public registration, runtime user-row updates, or automatic login-time password rehash may therefore fail closed after a schema downgrade. The current login path continues to verify existing Argon2id hashes without rewriting them. Any future need to restore a user-table write capability requires a separately reviewed migration and deployment plan; do not infer it from a schema downgrade.

If the PostgreSQL volume already exists, changing `.env` passwords does not change database roles. Use `docker compose down -v` only when you intentionally want to destroy local development/test data and recreate the roles. Never use that destructive reset as an onboarding or recovery procedure for a database containing existing users or memberships.
