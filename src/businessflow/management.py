from __future__ import annotations

import argparse
import getpass
import os
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn
from uuid import uuid4

from alembic.config import Config
from alembic.script import ScriptDirectory
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from businessflow.database import set_user_context
from businessflow.models import UserAccount
from businessflow.schemas import RegisterRequest, TenantCreateRequest
from businessflow.security.passwords import hash_password
from businessflow.services.tenancy import create_tenant

_BOOTSTRAP_LOCK_CLASS_ID = 4_344_403
_BOOTSTRAP_LOCK_ID = 1
_BOOTSTRAP_POLICY_REVISION = "0014_disable_public_signup"
_SOURCE_ROOT = Path(__file__).resolve().parents[2]


class BootstrapError(Exception):
    """A safe, operator-facing bootstrap failure with no credential details."""


class BootstrapConfigurationError(BootstrapError):
    pass


class BootstrapAuthorizationError(BootstrapError):
    pass


class BootstrapSchemaError(BootstrapError):
    pass


class BootstrapAlreadyInitialized(BootstrapError):
    pass


class BootstrapInputError(BootstrapError):
    pass


class _SafeArgumentParser(argparse.ArgumentParser):
    """Keep argparse diagnostics from echoing unknown values that may be secrets."""

    def error(self, _message: str) -> NoReturn:
        self.exit(2, f"{self.prog}: error: invalid command arguments; details were withheld.\n")


def _engine_for_bootstrap(database_url: str):
    try:
        parsed_url = make_url(database_url)
    except Exception:
        raise BootstrapConfigurationError("BOOTSTRAP_DATABASE_URL is invalid.") from None
    if parsed_url.drivername != "postgresql+psycopg":
        raise BootstrapConfigurationError(
            "Bootstrap requires a PostgreSQL URL using the psycopg driver."
        )
    try:
        return create_engine(
            parsed_url,
            hide_parameters=True,
            poolclass=NullPool,
        )
    except Exception:
        raise BootstrapConfigurationError(
            "The bootstrap database could not be configured."
        ) from None


def _require_authorized_operator(connection) -> None:
    role = connection.execute(
        text(
            """
            SELECT session_user::text AS session_user,
                   current_user::text AS current_user,
                   rolsuper,
                   rolbypassrls
            FROM pg_roles
            WHERE rolname = current_user
            """
        )
    ).one_or_none()
    if (
        role is None
        or role.session_user != "bf_migrator"
        or role.current_user != "bf_migrator"
        or role.rolsuper
        or role.rolbypassrls
    ):
        raise BootstrapAuthorizationError(
            "Bootstrap requires a direct, non-superuser bf_migrator database login."
        )


def _require_bootstrap_schema(connection) -> None:
    schema = connection.execute(
        text(
            """
            SELECT to_regclass('public.users') IS NOT NULL AS users,
                   to_regclass('public.tenants') IS NOT NULL AS tenants,
                   to_regclass('public.companies') IS NOT NULL AS companies,
                   to_regclass('public.tenant_memberships') IS NOT NULL AS memberships,
                   to_regclass('public.tenant_onboarding_requests') IS NOT NULL AS onboarding,
                   to_regclass('public.alembic_version') IS NOT NULL AS alembic_version
            """
        )
    ).one()
    if not all(schema):
        raise BootstrapSchemaError(
            "The database is not initialized with the required BusinessFlow schema."
        )


def _revision_descends_from(
    script_directory: ScriptDirectory,
    revision_id: str,
    required_revision: str,
) -> bool:
    pending = [revision_id]
    visited: set[str] = set()
    while pending:
        current_id = pending.pop()
        if current_id == required_revision:
            return True
        if current_id in visited:
            continue
        visited.add(current_id)
        revision = script_directory.get_revision(current_id)
        if revision is None:
            return False
        parents = revision.down_revision
        if isinstance(parents, str):
            pending.append(parents)
        elif parents is not None:
            pending.extend(parents)
    return False


def _migration_script_directory() -> ScriptDirectory:
    for root in (_SOURCE_ROOT, Path.cwd().resolve()):
        if (root / "alembic.ini").is_file() and (root / "migrations").is_dir():
            config = Config(str(root / "alembic.ini"))
            config.set_main_option("script_location", str(root / "migrations"))
            return ScriptDirectory.from_config(config)
    raise FileNotFoundError("Alembic migration files are unavailable")


def _require_runtime_privileges_safe(connection) -> None:
    revisions = (
        connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars().all()
    )
    try:
        script_directory = _migration_script_directory()
        policy_revision = script_directory.get_revision(_BOOTSTRAP_POLICY_REVISION)
        migration_is_applied = (
            bool(revisions)
            and policy_revision is not None
            and all(
                _revision_descends_from(script_directory, str(current), _BOOTSTRAP_POLICY_REVISION)
                for current in revisions
            )
        )
    except Exception:
        migration_is_applied = False

    if not migration_is_applied:
        raise BootstrapAuthorizationError(
            "Bootstrap requires migration 0014 or a later migration that "
            "retains its account-creation restriction."
        )

    runtime_role_exists = connection.scalar(text("SELECT to_regrole('bf_runtime') IS NOT NULL"))
    if not runtime_role_exists:
        raise BootstrapAuthorizationError(
            "Bootstrap requires the restricted bf_runtime database role."
        )
    runtime_users_privileges_are_safe = connection.scalar(
        text(
            """
            SELECT NOT (
              runtime.rolsuper
              OR runtime.rolbypassrls
              OR runtime.rolcreatedb
              OR runtime.rolcreaterole
              OR runtime.rolreplication
              OR runtime.rolinherit
              OR has_table_privilege('bf_runtime', 'public.users', 'INSERT')
              OR has_table_privilege('bf_runtime', 'public.users', 'UPDATE')
              OR EXISTS (
                SELECT 1
                FROM pg_roles AS candidate
                WHERE candidate.oid <> runtime.oid
                  AND pg_has_role(runtime.oid, candidate.oid, 'MEMBER')
              )
              OR EXISTS (
                SELECT 1 FROM pg_class AS users_table
                WHERE users_table.oid = 'public.users'::regclass
                  AND users_table.relowner = runtime.oid
              )
              OR has_database_privilege(runtime.oid, current_database(), 'CREATE')
              OR has_schema_privilege(runtime.oid, 'public', 'CREATE')
              OR EXISTS (
                SELECT 1
                FROM pg_attribute AS users_column
                WHERE users_column.attrelid = 'public.users'::regclass
                  AND users_column.attnum > 0
                  AND NOT users_column.attisdropped
                  AND (
                    has_column_privilege(
                      runtime.oid, 'public.users', users_column.attname::text, 'INSERT'
                    )
                    OR has_column_privilege(
                      runtime.oid, 'public.users', users_column.attname::text, 'UPDATE'
                    )
                  )
              )
              OR EXISTS (
                SELECT 1
                FROM pg_class AS users_table
                CROSS JOIN LATERAL aclexplode(
                  COALESCE(users_table.relacl, acldefault('r', users_table.relowner))
                ) AS privilege(grantor, grantee, privilege_type, is_grantable)
                WHERE users_table.oid = 'public.users'::regclass
                  AND privilege.grantee = 0
                  AND privilege.privilege_type IN ('INSERT', 'UPDATE')
              )
              OR EXISTS (
                SELECT 1
                FROM pg_attribute AS users_column
                CROSS JOIN LATERAL aclexplode(users_column.attacl)
                  AS privilege(grantor, grantee, privilege_type, is_grantable)
                WHERE users_column.attrelid = 'public.users'::regclass
                  AND users_column.attnum > 0
                  AND NOT users_column.attisdropped
                  AND privilege.grantee = 0
                  AND privilege.privilege_type IN ('INSERT', 'UPDATE')
              )
            )
            FROM pg_roles AS runtime
            WHERE runtime.rolname = 'bf_runtime'
            """
        )
    )
    if runtime_users_privileges_are_safe is not True:
        raise BootstrapAuthorizationError(
            "Bootstrap refused: bf_runtime must be NOSUPERUSER, NOBYPASSRLS, "
            "NOCREATEDB, NOCREATEROLE, NOREPLICATION, and NOINHERIT, with no role "
            "memberships and no effective INSERT or UPDATE on public.users; check "
            "role attributes, PUBLIC grants, role memberships, and table ownership."
        )


def _require_no_existing_users(connection) -> None:
    # Every valid tenant has an owner membership referencing an existing user. The users
    # table has no RLS, so bf_migrator can use this global, exact one-time sentinel safely.
    if connection.scalar(text("SELECT EXISTS (SELECT 1 FROM public.users)")):
        raise BootstrapAlreadyInitialized(
            "Bootstrap refused: this installation already contains user accounts. "
            "Existing accounts and memberships were not changed."
        )


def validate_bootstrap_target(database_url: str) -> None:
    """Fail before prompting for a password if this is not an empty operator target."""
    engine = _engine_for_bootstrap(database_url)
    try:
        with engine.connect() as connection:
            _require_authorized_operator(connection)
            _require_bootstrap_schema(connection)
            _require_runtime_privileges_safe(connection)
            _require_no_existing_users(connection)
    except BootstrapError:
        raise
    except SQLAlchemyError:
        raise BootstrapError(
            "The bootstrap database could not be checked; details were withheld."
        ) from None
    finally:
        engine.dispose()


def _validated_inputs(
    *,
    email: str,
    display_name: str,
    password: str,
    tenant_name: str,
    company_name: str,
    base_currency: str,
    timezone: str,
    locale: str,
) -> tuple[RegisterRequest, TenantCreateRequest]:
    try:
        credentials = RegisterRequest(
            email=email,
            display_name=display_name,
            password=password,
        )
        tenant = TenantCreateRequest(
            name=tenant_name,
            company={
                "legal_name": company_name,
                "base_currency": base_currency,
                "timezone": timezone,
                "locale": locale,
            },
        )
    except ValidationError:
        raise BootstrapInputError(
            "Bootstrap input is invalid; check the email, password, tenant, and company fields."
        ) from None
    return credentials, tenant


def bootstrap_owner(
    database_url: str,
    *,
    email: str,
    display_name: str,
    password: str,
    tenant_name: str,
    company_name: str,
    base_currency: str,
    timezone: str,
    locale: str,
) -> None:
    """Create the sole initial owner and tenant in one operator-authorized transaction."""
    credentials, tenant_body = _validated_inputs(
        email=email,
        display_name=display_name,
        password=password,
        tenant_name=tenant_name,
        company_name=company_name,
        base_currency=base_currency,
        timezone=timezone,
        locale=locale,
    )
    password_hash = hash_password(credentials.password)
    engine = _engine_for_bootstrap(database_url)
    try:
        with (
            Session(engine, autoflush=False, expire_on_commit=False) as session,
            session.begin(),
        ):
            connection = session.connection()
            _require_authorized_operator(connection)
            _require_bootstrap_schema(connection)
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:lock_class, :lock_id)"),
                {"lock_class": _BOOTSTRAP_LOCK_CLASS_ID, "lock_id": _BOOTSTRAP_LOCK_ID},
            )
            # Serialize against a second bootstrap and any concurrent user insertion.
            connection.execute(text("LOCK TABLE public.users IN SHARE ROW EXCLUSIVE MODE"))
            _require_runtime_privileges_safe(connection)
            _require_no_existing_users(connection)

            user = UserAccount(
                id=uuid4(),
                email=credentials.email,
                display_name=credentials.display_name,
                password_hash=password_hash,
                status="active",
            )
            session.add(user)
            session.flush()
            set_user_context(session, user.id)
            create_tenant(
                session,
                user,
                tenant_body,
                idempotency_key=f"operator-bootstrap-{uuid4().hex}",
                request_id=uuid4(),
            )
    except BootstrapError:
        raise
    except SQLAlchemyError:
        raise BootstrapError("Bootstrap failed; database details were withheld.") from None
    finally:
        engine.dispose()


def _build_parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
        prog="businessflow-admin",
        description="Privileged, non-public BusinessFlow management commands.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser(
        "bootstrap-owner",
        help="Provision the initial owner and tenant in a pristine installation.",
    )
    bootstrap.add_argument("--email", required=True)
    bootstrap.add_argument("--display-name", required=True)
    bootstrap.add_argument("--tenant-name", required=True)
    bootstrap.add_argument("--company-name", required=True)
    bootstrap.add_argument("--base-currency", required=True)
    bootstrap.add_argument("--timezone", required=True)
    bootstrap.add_argument("--locale", required=True)
    return parser


def _interactive_terminal_available() -> bool:
    return sys.stdin.isatty() and sys.stderr.isatty()


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    database_url = os.environ.get("BOOTSTRAP_DATABASE_URL")
    if not database_url:
        print(
            "Set BOOTSTRAP_DATABASE_URL to the authorized bf_migrator connection.", file=sys.stderr
        )
        return 2
    if not _interactive_terminal_available():
        print(
            "Owner bootstrap requires an interactive terminal for hidden password input.",
            file=sys.stderr,
        )
        return 2

    try:
        validate_bootstrap_target(database_url)
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                password = getpass.getpass("Initial owner password: ")
                confirmation = getpass.getpass("Confirm initial owner password: ")
        except getpass.GetPassWarning:
            raise BootstrapInputError(
                "This terminal cannot hide password input; bootstrap was aborted."
            ) from None
        if password != confirmation:
            raise BootstrapInputError("The entered passwords did not match.")
        bootstrap_owner(
            database_url,
            email=args.email,
            display_name=args.display_name,
            password=password,
            tenant_name=args.tenant_name,
            company_name=args.company_name,
            base_currency=args.base_currency,
            timezone=args.timezone,
            locale=args.locale,
        )
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        # Database driver and validation exceptions can contain credentials or SQL details.
        print("Bootstrap failed; sensitive details were withheld.", file=sys.stderr)
        return 1

    print("Initial owner and tenant provisioned. No email or invitation was sent.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
