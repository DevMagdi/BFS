#!/bin/sh
set -eu

: "${BF_MIGRATOR_PASSWORD:?BF_MIGRATOR_PASSWORD must be set}"
: "${BF_RUNTIME_PASSWORD:?BF_RUNTIME_PASSWORD must be set}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=migrator_password="$BF_MIGRATOR_PASSWORD" \
  --set=runtime_password="$BF_RUNTIME_PASSWORD" <<'SQL'
CREATE ROLE bf_migrator
  LOGIN PASSWORD :'migrator_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
CREATE ROLE bf_runtime
  LOGIN PASSWORD :'runtime_password'
  NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS NOREPLICATION;
-- Keep bf_runtime standalone: migrations 0014/0015 fail closed on role memberships.

CREATE DATABASE businessflow OWNER bf_migrator;
CREATE DATABASE businessflow_test OWNER bf_migrator;
REVOKE ALL ON DATABASE businessflow FROM PUBLIC;
REVOKE ALL ON DATABASE businessflow_test FROM PUBLIC;
GRANT CONNECT ON DATABASE businessflow TO bf_runtime, bf_migrator;
GRANT CONNECT ON DATABASE businessflow_test TO bf_runtime, bf_migrator;
SQL

for database in businessflow businessflow_test; do
  psql --set=ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$database" <<'SQL'
REVOKE ALL ON SCHEMA public FROM PUBLIC;
ALTER SCHEMA public OWNER TO bf_migrator;
GRANT USAGE ON SCHEMA public TO bf_runtime;
REVOKE CREATE ON SCHEMA public FROM bf_runtime;
SQL
done
