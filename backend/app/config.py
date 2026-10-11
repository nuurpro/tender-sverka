"""Validated configuration for the full application, separate from legacy MVP.

No production database fallback or global permission to send tenant documents
to external AI is provided. Organization policy is checked by the AI service.
"""
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="APP_", env_file=None, extra="ignore", populate_by_name=True,
        hide_input_in_errors=True,
    )

    environment: Literal["development", "test", "production"] = "development"
    database_url: SecretStr = Field(validation_alias=AliasChoices("DATABASE_URL", "database_url"))
    session_secret: SecretStr
    public_origin: str = "http://localhost:3000"
    sms_endpoint: str | None = None
    sms_token: SecretStr | None = None
    broker_url: SecretStr | None = Field(default=None, validation_alias=AliasChoices("CELERY_BROKER_URL", "broker_url"))
    redis_url: SecretStr | None = Field(default=None, validation_alias=AliasChoices("REDIS_URL", "redis_url"))
    gateway_api_base: str = "https://agentrouter.org/v1"
    gateway_api_key: SecretStr = Field(default="", validation_alias=AliasChoices("CODEX_GATEWAY_API_KEY", "gateway_api_key"))
    gateway_api_style: Literal["chat", "responses"] = "chat"
    gateway_model: str = "deepseek-v4-flash"
    external_ai_default_allowed: Literal[False] = False
    max_package_files: int = Field(default=20, ge=1, le=20)
    max_package_bytes: int = Field(default=50_000_000, ge=1, le=50_000_000)
    max_package_pages: int = Field(default=1000, ge=1, le=1000)
    max_package_tokens: int = Field(default=250_000, ge=1)
    max_buyer_participants: Literal[5] = 5
    max_supplier_participants: Literal[1] = 1
    retention_months: int = Field(default=12, ge=1)

    @field_validator("gateway_api_base")
    @classmethod
    def validate_gateway(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("gateway URL requires HTTPS and must not contain credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("sms_endpoint")
    @classmethod
    def validate_sms_endpoint(cls, value: str | None) -> str | None:
        if value is None:
            return None
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("SMS endpoint requires HTTPS without credentials, query or fragment")
        return value

    @field_validator("public_origin")
    @classmethod
    def validate_origin(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"}:
            raise ValueError("public origin must be a plain HTTP(S) origin")
        return value.rstrip("/")

    @model_validator(mode="after")
    def validate_environment(self):
        try:
            database = make_url(self.database_url.get_secret_value())
        except Exception:
            raise ValueError("A valid PostgreSQL database URL is required") from None
        if database.get_backend_name() != "postgresql":
            if self.environment != "test" or database.get_backend_name() != "sqlite":
                raise ValueError("PostgreSQL is required outside the explicit test environment")
        if self.environment == "production":
            secret = self.session_secret.get_secret_value()
            if len(secret) < 32 or secret.lower().startswith(("change-me", "example", "replace")):
                raise ValueError("production session secret must be a generated secret of at least 32 characters")
            if urlsplit(self.public_origin).scheme != "https":
                raise ValueError("production public origin requires HTTPS")
            if not database.username or database.username.lower() in {"postgres", "root"}:
                raise ValueError("production requires a restricted application role, not a database administrator")
        elif urlsplit(self.public_origin).scheme == "http" and urlsplit(self.public_origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("HTTP development origins must use a loopback hostname")
        return self
