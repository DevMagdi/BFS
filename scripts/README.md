# Operational scripts

`db-init/010-create-roles.sh` runs only during first initialization of the local PostgreSQL data volume. It creates the separate `bf_migrator` and `bf_runtime` roles and the `businessflow` / `businessflow_test` databases. The bootstrap superuser is used only by the PostgreSQL image entrypoint and is not placed in the API or migration connection URL.

If the PostgreSQL volume already exists, changing `.env` passwords does not change database roles. Use `docker compose down -v` only when you intentionally want to destroy local development/test data and recreate the roles.
