from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def trim_noncredential_strings(cls, value: Any, info: ValidationInfo) -> Any:
        if info.field_name in {"password", "refresh_token"} or not isinstance(value, str):
            return value
        return value.strip()


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RegisterRequest(StrictModel):
    email: EmailStr
    display_name: Annotated[str, Field(min_length=1, max_length=120)]
    password: Annotated[str, Field(min_length=12, max_length=1024, repr=False)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()

    @field_validator("password")
    @classmethod
    def reject_whitespace_only_password(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("password must contain non-whitespace characters")
        return value


class LoginRequest(StrictModel):
    email: EmailStr
    password: Annotated[str, Field(min_length=1, max_length=1024, repr=False)]

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class RefreshRequest(StrictModel):
    refresh_token: Annotated[str, Field(min_length=32, max_length=256, repr=False)]


class CompanyProfileCreate(StrictModel):
    legal_name: Annotated[str, Field(min_length=1, max_length=200)]
    base_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "UTC"
    locale: Annotated[str, Field(min_length=2, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")] = "en"

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a recognized IANA timezone") from exc
        return value


class TenantCreateRequest(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]
    company: CompanyProfileCreate


class CompanyProfileUpdate(StrictModel):
    legal_name: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    base_currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] | None = None
    timezone: Annotated[str, Field(min_length=1, max_length=64)] | None = None
    locale: (
        Annotated[str, Field(min_length=2, max_length=16, pattern=r"^[A-Za-z0-9_-]+$")] | None
    ) = None

    @field_validator("timezone")
    @classmethod
    def known_timezone(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                ZoneInfo(value)
            except ZoneInfoNotFoundError as exc:
                raise ValueError("timezone must be a recognized IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def require_change(self) -> CompanyProfileUpdate:
        if not any(getattr(self, field) is not None for field in self.model_fields_set):
            raise ValueError("at least one non-null field is required")
        return self


class TenantUpdateRequest(StrictModel):
    name: Annotated[str, Field(min_length=1, max_length=160)]


class TokenResponse(BaseModel):
    access_token: str
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int
    refresh_token: str
    session_expires_at: datetime


class ErrorObject(BaseModel):
    code: str
    message: str
    request_id: UUID


class UserResponse(ORMModel):
    id: UUID
    email: EmailStr
    display_name: str
    created_at: datetime


class CompanyResponse(ORMModel):
    id: UUID
    tenant_id: UUID
    legal_name: str
    base_currency: str
    timezone: str
    locale: str
    status: str
    created_at: datetime
    updated_at: datetime


class TenantResponse(BaseModel):
    id: UUID
    name: str
    status: str
    created_at: datetime
    updated_at: datetime
    company: CompanyResponse
    role: Literal["owner", "admin", "member"]


class TenantListResponse(BaseModel):
    items: list[TenantResponse]


class MembershipCreateRequest(StrictModel):
    email: EmailStr
    role: Literal["owner", "admin", "member"] = "member"

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: EmailStr) -> str:
        return str(value).strip().lower()


class MembershipUpdateRequest(StrictModel):
    role: Literal["owner", "admin", "member"] | None = None
    status: Literal["active", "revoked"] | None = None

    @model_validator(mode="after")
    def require_change(self) -> MembershipUpdateRequest:
        if self.role is None and self.status is None:
            raise ValueError("role or status must be provided")
        return self


class MembershipResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    email: EmailStr
    display_name: str
    role: Literal["owner", "admin", "member"]
    status: Literal["active", "revoked", "suspended"]
    created_at: datetime
    revoked_at: datetime | None


class MembershipPage(BaseModel):
    items: list[MembershipResponse]
    total: int
    limit: int
    offset: int


class AuditEventResponse(BaseModel):
    id: UUID
    tenant_id: UUID
    actor_user_id: UUID | None
    event_type: str
    target_type: str | None
    target_id: UUID | None
    outcome: str
    metadata: dict[str, object]
    request_id: UUID | None
    created_at: datetime


class AuditEventPage(BaseModel):
    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int


class MeResponse(BaseModel):
    user: UserResponse
    tenants: TenantListResponse


class HealthResponse(BaseModel):
    status: Literal["ok"]
