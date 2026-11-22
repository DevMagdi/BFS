from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_secret_key: SecretStr = Field(validation_alias="APP_SECRET_KEY")
    app_env: Literal["development", "test", "production"] = Field(
        default="development", validation_alias="APP_ENV"
    )
    database_url: str = Field(validation_alias="DATABASE_URL")
    access_token_ttl_seconds: int = Field(
        default=300, ge=60, le=900, validation_alias="ACCESS_TOKEN_TTL_SECONDS"
    )
    refresh_session_days: int = Field(
        default=30, ge=1, le=90, validation_alias="REFRESH_SESSION_DAYS"
    )
    login_failure_limit: int = Field(default=5, ge=3, le=20, validation_alias="LOGIN_FAILURE_LIMIT")
    login_ip_failure_limit: int = Field(
        default=20, ge=5, le=100, validation_alias="LOGIN_IP_FAILURE_LIMIT"
    )
    login_lockout_minutes: int = Field(
        default=15, ge=1, le=120, validation_alias="LOGIN_LOCKOUT_MINUTES"
    )
    db_pool_size: int = Field(default=5, ge=1, le=50, validation_alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=5, ge=0, le=50, validation_alias="DB_MAX_OVERFLOW")
    import_max_file_bytes: int = Field(
        default=25 * 1024 * 1024,
        ge=1024,
        le=100 * 1024 * 1024,
        validation_alias="IMPORT_MAX_FILE_BYTES",
    )
    import_max_rows: int = Field(
        default=10_000, ge=1, le=100_000, validation_alias="IMPORT_MAX_ROWS"
    )
    import_staging_ttl_hours: int = Field(
        default=24, ge=1, le=168, validation_alias="IMPORT_STAGING_TTL_HOURS"
    )

    @field_validator("app_secret_key")
    @classmethod
    def validate_secret_key(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value()
        if len(secret) < 32 or "replace_with" in secret.lower() or "change-me" in secret.lower():
            raise ValueError(
                "APP_SECRET_KEY must be a non-placeholder secret of at least 32 characters"
            )
        return value

    @field_validator("database_url")
    @classmethod
    def require_postgresql(cls, value: str) -> str:
        if not value.startswith("postgresql+psycopg://"):
            raise ValueError("DATABASE_URL must use the PostgreSQL psycopg driver")
        if "replace-me" in value.lower() or "change-me" in value.lower():
            raise ValueError("DATABASE_URL contains a placeholder credential")
        return value

    @model_validator(mode="after")
    def validate_development_secrets(self) -> Settings:
        secret = self.app_secret_key.get_secret_value().lower()
        if self.app_env == "production" and secret.startswith(("dev-", "test-", "ci-")):
            raise ValueError("Development and test secrets are not allowed in production")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
