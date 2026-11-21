from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from businessflow.database import Base


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class UserAccount(TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_users_status"),
        CheckConstraint("length(email) BETWEEN 3 AND 320", name="ck_users_email_length"),
        CheckConstraint(
            "length(display_name) BETWEEN 1 AND 120", name="ck_users_display_name_length"
        ),
        UniqueConstraint("email", name="uq_users_email"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    display_name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class Tenant(TimestampMixin, Base):
    __tablename__ = "tenants"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'disabled')", name="ck_tenants_status"),
        CheckConstraint("length(name) BETWEEN 1 AND 160", name="ck_tenants_name_length"),
        ForeignKeyConstraint(
            ["id", "primary_company_id"],
            ["companies.tenant_id", "companies.id"],
            name="fk_tenants_primary_company",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    primary_company_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class Company(TimestampMixin, Base):
    __tablename__ = "companies"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_companies_one_per_tenant"),
        UniqueConstraint("tenant_id", "id", name="uq_companies_tenant_id_id"),
        CheckConstraint("status IN ('active', 'disabled')", name="ck_companies_status"),
        CheckConstraint("base_currency ~ '^[A-Z]{3}$'", name="ck_companies_currency"),
        CheckConstraint("length(legal_name) BETWEEN 1 AND 200", name="ck_companies_legal_name"),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_companies_tenant", ondelete="RESTRICT"
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    legal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False, server_default="UTC")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, server_default="en")
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")


class TenantMembership(TimestampMixin, Base):
    __tablename__ = "tenant_memberships"
    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        UniqueConstraint("tenant_id", "id", name="uq_memberships_tenant_id_id"),
        CheckConstraint("role IN ('owner', 'admin', 'member')", name="ck_memberships_role"),
        CheckConstraint(
            "status IN ('active', 'revoked', 'suspended')", name="ck_memberships_status"
        ),
        CheckConstraint(
            "(status = 'revoked' AND revoked_at IS NOT NULL) OR "
            "(status <> 'revoked' AND revoked_at IS NULL)",
            name="ck_membership_revocation",
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_memberships_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user", ondelete="RESTRICT"
        ),
        Index("ix_memberships_user_status", "user_id", "status"),
        Index("ix_memberships_tenant_role_status", "tenant_id", "role", "status"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="active")
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry"),
        CheckConstraint(
            "(revoked_at IS NULL AND revoke_reason IS NULL) OR "
            "(revoked_at IS NOT NULL AND revoke_reason IS NOT NULL)",
            name="ck_auth_sessions_revocation",
        ),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_auth_sessions_user", ondelete="RESTRICT"
        ),
        Index("ix_auth_sessions_user_active", "user_id", "revoked_at", "expires_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(40))


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_refresh_tokens_hash"),
        UniqueConstraint("session_id", "generation", name="uq_refresh_tokens_session_generation"),
        CheckConstraint("generation >= 0", name="ck_refresh_tokens_generation"),
        CheckConstraint("token_hash ~ '^[0-9a-f]{64}$'", name="ck_refresh_tokens_hash"),
        CheckConstraint(
            "consumed_at IS NULL OR consumed_at >= issued_at", name="ck_refresh_tokens_state"
        ),
        ForeignKeyConstraint(
            ["session_id"],
            ["auth_sessions.id"],
            name="fk_refresh_tokens_session",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["replaced_by_id"],
            ["refresh_tokens.id"],
            name="fk_refresh_tokens_replaced_by",
            ondelete="RESTRICT",
        ),
        Index("ix_refresh_tokens_session", "session_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replaced_by_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))


class LoginThrottle(Base):
    __tablename__ = "login_throttles"
    __table_args__ = (
        CheckConstraint("failure_count >= 0", name="ck_login_throttles_failure_count"),
        CheckConstraint("bucket_hash ~ '^[0-9a-f]{64}$'", name="ck_login_throttles_hash"),
    )

    bucket_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )


class TenantOnboardingRequest(Base):
    __tablename__ = "tenant_onboarding_requests"
    __table_args__ = (
        UniqueConstraint("tenant_id", name="uq_onboarding_tenant"),
        ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_onboarding_user", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_onboarding_tenant",
            deferrable=True,
            initially="DEFERRED",
            ondelete="RESTRICT",
        ),
        CheckConstraint("key_hash ~ '^[0-9a-f]{64}$'", name="ck_onboarding_hash"),
        CheckConstraint("request_hash ~ '^[0-9a-f]{64}$'", name="ck_onboarding_request_hash"),
        Index("ix_onboarding_tenant", "tenant_id"),
    )

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    tenant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("length(event_type) BETWEEN 1 AND 80", name="ck_audit_events_type_length"),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'denied', 'revoked')",
            name="ck_audit_events_outcome",
        ),
        CheckConstraint(
            "jsonb_typeof(metadata) = 'object'", name="ck_audit_events_metadata_object"
        ),
        ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name="fk_audit_events_tenant", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"], name="fk_audit_events_actor", ondelete="RESTRICT"
        ),
        Index("ix_audit_events_tenant_created", "tenant_id", "created_at", "id"),
        Index("ix_audit_events_actor_created", "actor_user_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    tenant_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    actor_user_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    event_type: Mapped[str] = mapped_column(String(80), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(40))
    target_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    request_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    outcome: Mapped[str] = mapped_column(String(16), nullable=False, server_default="success")
    details: Mapped[dict[str, object]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
