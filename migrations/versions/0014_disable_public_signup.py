"""Disable runtime-role account creation after registration is removed from the API.

Revision ID: 0014_disable_public_signup
Revises: 0013_direct_cash_forecast
Create Date: 2026-11-21
"""

from __future__ import annotations

from alembic import op

revision = "0014_disable_public_signup"
down_revision = "0013_direct_cash_forecast"
branch_labels = None
depends_on = None


def _require_runtime_role() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          runtime_oid oid;
          users_oid regclass;
        BEGIN
          IF current_user <> 'bf_migrator' THEN
            RAISE EXCEPTION 'migrations must run as the dedicated bf_migrator role';
          END IF;

          SELECT oid INTO runtime_oid FROM pg_roles WHERE rolname = 'bf_runtime';
          IF runtime_oid IS NULL THEN
            RAISE EXCEPTION 'required non-privileged role bf_runtime does not exist';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles
            WHERE oid = runtime_oid
              AND (
                rolsuper OR rolbypassrls OR rolcreatedb OR rolcreaterole OR rolreplication
              )
          ) THEN
            RAISE EXCEPTION '%',
              'bf_runtime must be NOSUPERUSER, NOBYPASSRLS, NOCREATEDB, '
              || 'NOCREATEROLE, and NOREPLICATION';
          END IF;
          IF EXISTS (SELECT 1 FROM pg_roles WHERE oid = runtime_oid AND rolinherit) THEN
            RAISE EXCEPTION 'bf_runtime must be configured NOINHERIT';
          END IF;
          IF pg_has_role(runtime_oid, 'bf_migrator'::regrole, 'MEMBER') THEN
            RAISE EXCEPTION 'bf_runtime must not inherit or assume the migration role';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_roles AS candidate
            WHERE candidate.oid <> runtime_oid
              AND pg_has_role(runtime_oid, candidate.oid, 'MEMBER')
          ) THEN
            RAISE EXCEPTION '%',
              'bf_runtime must not be a member of other PostgreSQL roles; '
              || 'remove role memberships before changing public.users grants';
          END IF;

          users_oid := to_regclass('public.users');
          IF users_oid IS NULL THEN
            RAISE EXCEPTION 'required table public.users does not exist';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_class
            WHERE oid = users_oid AND relowner = runtime_oid
          ) THEN
            RAISE EXCEPTION 'bf_runtime must not own public.users';
          END IF;
          IF EXISTS (
            SELECT 1 FROM pg_database
            WHERE datname = current_database() AND datdba = runtime_oid
          ) OR has_database_privilege(runtime_oid, current_database(), 'CREATE') THEN
            RAISE EXCEPTION '%',
              'bf_runtime must not own the application database or '
              || 'create objects in it';
          END IF;
          IF has_schema_privilege(runtime_oid, 'public', 'CREATE') THEN
            RAISE EXCEPTION 'bf_runtime must not create objects in the public schema';
          END IF;
        END;
        $$;
        """
    )


def _require_no_delegated_insert() -> None:
    op.execute(
        """
        DO $$
        DECLARE
          runtime_oid oid := 'bf_runtime'::regrole;
        BEGIN
          IF EXISTS (
            SELECT 1
            FROM pg_class AS relation
            CROSS JOIN LATERAL aclexplode(
              COALESCE(relation.relacl, acldefault('r', relation.relowner))
            ) AS privilege(grantor, grantee, privilege_type, is_grantable)
            WHERE relation.oid = 'public.users'::regclass
              AND privilege.grantor = runtime_oid
              AND privilege.grantee <> runtime_oid
              AND privilege.privilege_type = 'INSERT'
          ) OR EXISTS (
            SELECT 1
            FROM pg_attribute AS column_privilege
            CROSS JOIN LATERAL aclexplode(column_privilege.attacl)
              AS privilege(grantor, grantee, privilege_type, is_grantable)
            WHERE column_privilege.attrelid = 'public.users'::regclass
              AND column_privilege.attnum > 0
              AND NOT column_privilege.attisdropped
              AND privilege.grantor = runtime_oid
              AND privilege.grantee <> runtime_oid
              AND privilege.privilege_type = 'INSERT'
          ) THEN
            RAISE EXCEPTION '%',
              'bf_runtime has delegated INSERT grants on public.users; '
              || 'revoke those scoped downstream grants before upgrading';
          END IF;
        END;
        $$;
        """
    )


def _revoke_users_insert() -> None:
    op.execute("REVOKE INSERT ON TABLE public.users FROM PUBLIC, bf_runtime")
    op.execute(
        """
        DO $$
        DECLARE
          column_name text;
        BEGIN
          FOR column_name IN
            SELECT attname
            FROM pg_attribute
            WHERE attrelid = 'public.users'::regclass
              AND attnum > 0
              AND NOT attisdropped
          LOOP
            EXECUTE format(
              'REVOKE INSERT (%I) ON TABLE public.users FROM PUBLIC, bf_runtime',
              column_name
            );
          END LOOP;
        END;
        $$;
        """
    )


def _assert_runtime_insert_disabled() -> None:
    op.execute(
        """
        DO $$
        BEGIN
          IF has_table_privilege('bf_runtime', 'public.users', 'INSERT') THEN
            RAISE EXCEPTION
              'bf_runtime retains effective table-level INSERT on public.users after 0014';
          END IF;
          IF EXISTS (
            SELECT 1
            FROM pg_attribute
            WHERE attrelid = 'public.users'::regclass
              AND attnum > 0
              AND NOT attisdropped
              AND has_column_privilege(
                'bf_runtime', 'public.users', attname::text, 'INSERT'
              )
          ) THEN
            RAISE EXCEPTION
              'bf_runtime retains effective column-level INSERT on public.users after 0014';
          END IF;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _require_runtime_role()
    _require_no_delegated_insert()
    # Account creation is restricted to the operator-only bootstrap command. These
    # revokes are scoped to public.users and remove table- and column-level grants only.
    _revoke_users_insert()
    _assert_runtime_insert_disabled()


def downgrade() -> None:
    _require_runtime_role()
    _require_no_delegated_insert()
    # Security revocations are monotonic. A schema downgrade must not re-enable runtime
    # account creation or restore PUBLIC writes; older application versions may fail closed.
    _revoke_users_insert()
    _assert_runtime_insert_disabled()
