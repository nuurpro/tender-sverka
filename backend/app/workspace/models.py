"""Normalized workspace schema. JSON columns hold evidence/policies, never whole workspaces."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint,
                        Index, Integer, JSON, String, Text, UniqueConstraint, text)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


def utcnow():
    return datetime.now(timezone.utc)


class UTCDateTime(TypeDecorator):
    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Datetime must include a timezone")
            return value.astimezone(timezone.utc)
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)
        return value


JSON_VALUE = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utcnow, nullable=False)


class Tenant:
    organization_id: Mapped[str] = mapped_column(String(36), ForeignKey("organizations.id"), nullable=False, index=True)


def tenant_keys(*constraints):
    return (UniqueConstraint("organization_id", "id"), *constraints)


def project_fk():
    return ForeignKeyConstraint(["organization_id", "project_id"], ["projects.organization_id", "projects.id"])


class Organization(Base):
    __tablename__ = "organizations"
    name: Mapped[str] = mapped_column(String(255))


class User(Base):
    __tablename__ = "users"
    email: Mapped[str | None] = mapped_column(String(320), unique=True)
    phone: Mapped[str | None] = mapped_column(String(16), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    display_name: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (CheckConstraint("attempts BETWEEN 0 AND 5"), Index("ix_otp_phone", "phone"))
    phone: Mapped[str] = mapped_column(String(16))
    code_digest: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    consumed: Mapped[bool] = mapped_column(Boolean, default=False)


class AuthBudget(Base):
    __tablename__ = "auth_budgets"
    key: Mapped[str] = mapped_column(String(128), unique=True)
    events: Mapped[list] = mapped_column(JSON_VALUE, default=list)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    verifier: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime())
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime())
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class Membership(Tenant, Base):
    __tablename__ = "memberships"
    __table_args__ = tenant_keys(UniqueConstraint("organization_id", "user_id"),
                                CheckConstraint("role IN ('admin','specialist','viewer')"))
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(16))


class Project(Tenant, Base):
    __tablename__ = "projects"
    __table_args__ = tenant_keys(CheckConstraint("mode IN ('supplier','buyer')"),
                                CheckConstraint("report_language IN ('ru','uz','en')"), CheckConstraint("revision >= 1"))
    mode: Mapped[str] = mapped_column(String(16))
    lot_name: Mapped[str] = mapped_column(String(500))
    customer: Mapped[str | None] = mapped_column(String(500))
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Tashkent")
    report_language: Mapped[str] = mapped_column(String(8), default="ru")
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Participant(Tenant, Base):
    __tablename__ = "participants"
    __table_args__ = tenant_keys(project_fk(), UniqueConstraint("organization_id", "project_id", "slot"),
                                UniqueConstraint("organization_id", "project_id", "id"),
                                CheckConstraint("slot BETWEEN 1 AND 5"))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    name: Mapped[str] = mapped_column(String(500))
    slot: Mapped[int] = mapped_column(Integer)


class PackageRevision(Tenant, Base):
    __tablename__ = "package_revisions"
    __table_args__ = tenant_keys(project_fk(),
        ForeignKeyConstraint(["organization_id", "project_id", "participant_id"],
                             ["participants.organization_id", "participants.project_id", "participants.id"]),
        UniqueConstraint("organization_id", "project_id", "id"),
        UniqueConstraint("organization_id", "project_id", "kind", "participant_id", "version"),
        Index("uq_tender_package_version", "organization_id", "project_id", "version", unique=True,
              postgresql_where=text("kind = 'tender'"), sqlite_where=text("kind = 'tender'")),
        CheckConstraint("kind IN ('tender','bid')"), CheckConstraint("version >= 1"),
        CheckConstraint("(kind = 'tender' AND participant_id IS NULL) OR (kind = 'bid' AND participant_id IS NOT NULL)"))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    participant_id: Mapped[str | None] = mapped_column(String(36))
    version: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    status: Mapped[str] = mapped_column(String(32), default="uploaded")


class Document(Tenant, Base):
    __tablename__ = "documents"
    __table_args__ = tenant_keys(
        ForeignKeyConstraint(["organization_id", "package_id"], ["package_revisions.organization_id", "package_revisions.id"]),
        UniqueConstraint("organization_id", "package_id", "object_key"), CheckConstraint("size >= 0"))
    package_id: Mapped[str] = mapped_column(String(36), index=True)
    file_hash: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(1024))
    size: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(500))


class ChecklistVersion(Tenant, Base):
    __tablename__ = "checklist_versions"
    __table_args__ = tenant_keys(project_fk(), UniqueConstraint("organization_id", "project_id", "version"),
        UniqueConstraint("organization_id", "project_id", "id"),
        ForeignKeyConstraint(["organization_id", "project_id", "parent_version_id"],
                             ["checklist_versions.organization_id", "checklist_versions.project_id", "checklist_versions.id"]),
        CheckConstraint("status IN ('draft','approved')"), CheckConstraint("version >= 1 AND revision >= 1"),
        CheckConstraint("status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)"))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="draft")
    parent_version_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    approved_by: Mapped[str | None] = mapped_column(String(36), ForeignKey("users.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class Requirement(Tenant, Base):
    __tablename__ = "requirements"
    __table_args__ = tenant_keys(
        ForeignKeyConstraint(["organization_id", "checklist_id"], ["checklist_versions.organization_id", "checklist_versions.id"]),
        UniqueConstraint("organization_id", "checklist_id", "logical_id"),
        UniqueConstraint("organization_id", "checklist_id", "display_id"),
        CheckConstraint("scope IN ('product','participant')"),
        CheckConstraint("importance IN ('mandatory','scored','desirable')"))
    checklist_id: Mapped[str] = mapped_column(String(36), index=True)
    logical_id: Mapped[str] = mapped_column(String(36), default=lambda: str(uuid4()))
    display_id: Mapped[str] = mapped_column(String(32))
    scope: Mapped[str] = mapped_column(String(64))
    item: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(128))
    parameter: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[str | None] = mapped_column(Text)
    importance: Mapped[str] = mapped_column(String(64))
    predicate: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)
    citations: Mapped[list] = mapped_column(JSON_VALUE, default=list)
    origin: Mapped[dict] = mapped_column(JSON_VALUE, default=dict)


class ComparisonRun(Tenant, Base):
    __tablename__ = "comparison_runs"
    __table_args__ = tenant_keys(project_fk(),
        ForeignKeyConstraint(["organization_id", "project_id", "checklist_id"],
                             ["checklist_versions.organization_id", "checklist_versions.project_id", "checklist_versions.id"]))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    checklist_id: Mapped[str] = mapped_column(String(36))
    package_ids: Mapped[list] = mapped_column(JSON_VALUE)
    scoring_policy: Mapped[dict] = mapped_column(JSON_VALUE)
    deadline: Mapped[datetime | None] = mapped_column(UTCDateTime())
    provider_config_versions: Mapped[dict] = mapped_column(JSON_VALUE)
    status: Mapped[str] = mapped_column(String(32), default="queued")


class Job(Tenant, Base):
    __tablename__ = "jobs"
    __table_args__ = tenant_keys(project_fk(), UniqueConstraint("organization_id", "project_id", "idempotency_key"),
                                CheckConstraint("attempt >= 0 AND progress >= 0 AND progress <= 100"))
    project_id: Mapped[str] = mapped_column(String(36), index=True)
    stage: Mapped[str] = mapped_column(String(64))
    input_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    payload: Mapped[dict] = mapped_column(JSON_VALUE)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[dict | None] = mapped_column(JSON_VALUE)
    lease_owner: Mapped[str | None] = mapped_column(String(255))
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class OutboxEvent(Tenant, Base):
    __tablename__ = "outbox_events"
    __table_args__ = tenant_keys(
        ForeignKeyConstraint(["organization_id", "job_id"], ["jobs.organization_id", "jobs.id"]),
        UniqueConstraint("organization_id", "job_id"))
    job_id: Mapped[str] = mapped_column(String(36), index=True)
    payload: Mapped[dict] = mapped_column(JSON_VALUE)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class AuditEvent(Tenant, Base):
    __tablename__ = "audit_events"
    __table_args__ = tenant_keys(project_fk())
    project_id: Mapped[str | None] = mapped_column(String(36), index=True)
    actor_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(36))
    before: Mapped[dict | None] = mapped_column(JSON_VALUE)
    after: Mapped[dict | None] = mapped_column(JSON_VALUE)
    reason: Mapped[str | None] = mapped_column(Text)
