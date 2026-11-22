from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    pass


def create_database_engine(
    database_url: str, *, pool_size: int = 5, max_overflow: int = 5
) -> Engine:
    return create_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_reset_on_return="rollback",
        hide_parameters=True,
    )


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def set_user_context(session: Session, user_id: UUID) -> None:
    """Bind the verified principal to this transaction for own-membership RLS reads."""
    session.execute(
        text("SELECT set_config('app.user_id', :user_id, true)"),
        {"user_id": str(user_id)},
    )
    session.info["app.user_id"] = user_id


def set_tenant_context(session: Session, tenant_id: UUID) -> None:
    """Bind a membership-verified tenant to this transaction only."""
    session.execute(
        text("SELECT set_config('app.tenant_id', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )
    session.info["app.tenant_id"] = tenant_id


def get_tenant_context(session: Session) -> UUID | None:
    value = session.info.get("app.tenant_id")
    return value if isinstance(value, UUID) else None


def set_company_context(session: Session, company_id: UUID) -> None:
    """Bind the company resolved from a membership-verified tenant for company RLS."""
    session.execute(
        text("SELECT set_config('app.company_id', :company_id, true)"),
        {"company_id": str(company_id)},
    )
    session.info["app.company_id"] = company_id


def transaction_session(factory: sessionmaker[Session]) -> Iterator[Session]:
    """Yield one explicit unit of work; commit on success and rollback on any exception."""
    with factory() as session, session.begin():
        yield session
