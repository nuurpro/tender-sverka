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
    sms_provider: Literal["http", "mock", "eskiz"] = "http"
    sms_endpoint: str | None = None
    sms_token: SecretStr | None = None
    broker_url: SecretStr | None = Field(default=None, validation_alias=AliasChoices("CELERY_BROKER_URL", "broker_url"))
    job_signing_secret: SecretStr = SecretStr("")
    job_lease_seconds: int = Field(default=60, ge=5, le=3600)
    job_max_attempts: int = Field(default=3, ge=1, le=20)
    job_publisher_lease_seconds: int = Field(default=30, ge=5, le=3600)
    job_dispatch_interval: float = Field(default=2, ge=0.1, le=60)
    job_sse_interval: float = Field(default=2, ge=0.1, le=15)
    job_queue: str = Field(default="tender.jobs", pattern=r"^[a-zA-Z0-9_.-]{1,100}$")
    redis_url: SecretStr | None = Field(default=None, validation_alias=AliasChoices("REDIS_URL", "redis_url"))
    gateway_api_base: str = "https://agentrouter.org/v1"
    gateway_api_key: SecretStr = Field(default="", validation_alias=AliasChoices("CODEX_GATEWAY_API_KEY", "gateway_api_key"))
    gateway_api_style: Literal["chat", "responses"] = "chat"
    gateway_model: str = "deepseek-v4-flash"
    external_ai_default_allowed: Literal[False] = False
    max_package_files: int = Field(default=20, ge=1, le=20)
    max_package_bytes: int = Field(default=50_000_000, ge=1, le=50_000_000)
    max_package_pages: int = Field(default=1000, ge=1, le=1000)
    max_package_tokens: int = Field(default=250_000, ge=1, le=250_000)
    document_encryption_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("DOCUMENT_ENCRYPTION_KEY", "document_encryption_key"))
    document_master_key_version: str = Field(default="v1", pattern=r"^[a-zA-Z0-9_.-]{1,64}$")
    document_runner_mode: Literal["disabled", "bubblewrap", "wsl-bubblewrap"] = Field(default="disabled", validation_alias=AliasChoices("DOCUMENT_RUNNER_MODE", "APP_DOCUMENT_RUNNER_MODE", "document_runner_mode"))
    document_runtime_python: str = Field(default="/home/acer/.local/share/tender-sverka/document-runtime/bin/python", validation_alias=AliasChoices("DOCUMENT_RUNTIME_PYTHON", "APP_DOCUMENT_RUNTIME_PYTHON", "document_runtime_python"))
    document_parser_code_path: str = Field(default="/home/acer/.local/share/tender-sverka/document-parser/app/documents", validation_alias=AliasChoices("DOCUMENT_PARSER_CODE_PATH", "APP_DOCUMENT_PARSER_CODE_PATH", "document_parser_code_path"))
    document_wsl_distribution: str = Field(default="Ubuntu", pattern=r"^[a-zA-Z0-9_.-]{1,80}$", validation_alias=AliasChoices("DOCUMENT_WSL_DISTRIBUTION", "APP_DOCUMENT_WSL_DISTRIBUTION", "document_wsl_distribution"))
    document_runner_timeout_seconds: float = Field(default=30, ge=0.1, le=120, validation_alias=AliasChoices("DOCUMENT_RUNNER_TIMEOUT_SECONDS", "APP_DOCUMENT_RUNNER_TIMEOUT_SECONDS", "document_runner_timeout_seconds"))
    document_runner_cpu_seconds: int = Field(default=20, ge=1, le=60, validation_alias=AliasChoices("DOCUMENT_RUNNER_CPU_SECONDS", "APP_DOCUMENT_RUNNER_CPU_SECONDS", "document_runner_cpu_seconds"))
    document_runner_memory_bytes: int = Field(default=805_306_368, ge=67_108_864, le=1_073_741_824, validation_alias=AliasChoices("DOCUMENT_RUNNER_MEMORY_BYTES", "APP_DOCUMENT_RUNNER_MEMORY_BYTES", "document_runner_memory_bytes"))
    document_runner_process_limit: int = Field(default=32, ge=4, le=64, validation_alias=AliasChoices("DOCUMENT_RUNNER_PROCESS_LIMIT", "APP_DOCUMENT_RUNNER_PROCESS_LIMIT", "document_runner_process_limit"))
    document_runner_file_limit: int = Field(default=64, ge=16, le=128, validation_alias=AliasChoices("DOCUMENT_RUNNER_FILE_LIMIT", "APP_DOCUMENT_RUNNER_FILE_LIMIT", "document_runner_file_limit"))
    document_runner_file_bytes: int = Field(default=16_777_216, ge=1024, le=16_777_216, validation_alias=AliasChoices("DOCUMENT_RUNNER_FILE_BYTES", "APP_DOCUMENT_RUNNER_FILE_BYTES", "document_runner_file_bytes"))
    document_runner_output_bytes: int = Field(default=16_777_216, ge=1024, le=16_777_216, validation_alias=AliasChoices("DOCUMENT_RUNNER_OUTPUT_BYTES", "APP_DOCUMENT_RUNNER_OUTPUT_BYTES", "document_runner_output_bytes"))
    s3_endpoint_url: str | None = Field(default=None, validation_alias=AliasChoices("S3_ENDPOINT_URL", "s3_endpoint_url"))
    s3_region: str = Field(default="garage", validation_alias=AliasChoices("S3_REGION", "s3_region"))
    s3_bucket: str | None = Field(default=None, validation_alias=AliasChoices("S3_BUCKET", "s3_bucket"))
    s3_access_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("S3_ACCESS_KEY", "s3_access_key"))
    s3_secret_key: SecretStr | None = Field(default=None, validation_alias=AliasChoices("S3_SECRET_KEY", "s3_secret_key"))
    max_active_uploads: int = Field(default=2, ge=1, le=4)
    max_buyer_participants: Literal[5] = 5
    max_supplier_participants: Literal[1] = 1
    retention_months: int = Field(default=12, ge=1)

    @field_validator("document_runtime_python", "document_parser_code_path")
    @classmethod
    def validate_document_runtime_path(cls, value: str, info) -> str:
        from pathlib import PurePosixPath
        path = PurePosixPath(value)
        if not value.startswith("/") or "\\" in value or "\x00" in value or ".." in path.parts or str(path) != value:
            raise ValueError("document runtime paths must be canonical absolute Linux paths")
        if info.field_name == "document_runtime_python":
            if path.name not in {"python", "python3"} or path.parent.name != "bin" or len(path.parts) < 5:
                raise ValueError("document Python must be in a dedicated Linux virtual environment")
        elif path.name != "documents" or len(path.parts) < 5:
            raise ValueError("document parser path must be a dedicated documents-only directory")
        return value

    @field_validator("gateway_api_base")
    @classmethod
    def validate_gateway(cls, value: str) -> str:
        url = urlsplit(value)
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError("gateway URL requires HTTPS and must not contain credentials, query or fragment")
        return value.rstrip("/")

    @field_validator("document_encryption_key")
    @classmethod
    def validate_document_key(cls, value):
        if value is not None:
            import re
            if not re.fullmatch(r"[0-9a-fA-F]{64}", value.get_secret_value()):
                raise ValueError("Document encryption requires a generated 256-bit hex key")
        return value

    @field_validator("s3_endpoint_url")
    @classmethod
    def validate_s3_endpoint(cls, value):
        if value is None:
            return value
        url = urlsplit(value)
        if not url.hostname or url.username or url.password or url.query or url.fragment or url.path not in {"", "/"} or (
            url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("S3 endpoint requires HTTPS or local loopback without credentials")
        return value.rstrip("/")

    @field_validator("sms_endpoint")
    @classmethod
    def validate_sms_endpoint(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
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
            if self.sms_provider == "mock":
                raise ValueError("production forbids the mock SMS provider")
            secret = self.session_secret.get_secret_value()
            if len(secret) < 32 or secret.lower().startswith(("change-me", "example", "replace")):
                raise ValueError("production session secret must be a generated secret of at least 32 characters")
            if urlsplit(self.public_origin).scheme != "https":
                raise ValueError("production public origin requires HTTPS")
            if not database.username or database.username.lower() in {"postgres", "root"}:
                raise ValueError("production requires a restricted application role, not a database administrator")
            if self.s3_endpoint_url and urlsplit(self.s3_endpoint_url).scheme != "https":
                raise ValueError("production S3 endpoint requires HTTPS")
        elif urlsplit(self.public_origin).scheme == "http" and urlsplit(self.public_origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("HTTP development origins must use a loopback hostname")
        return self
