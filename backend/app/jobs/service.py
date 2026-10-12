"""Short tenant transactions fence every worker and authorized lifecycle write."""
import copy
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import secrets

from contextlib import contextmanager
from sqlalchemy import event, select, text
from sqlalchemy.exc import IntegrityError

from app.workspace.database import tenant_transaction
from app.workspace.models import (AuditEvent, Job, JobCheckpoint, JobOperation,
    JobProgressEvent, OutboxEvent, utcnow)
from app.workspace.repository import Conflict, NotFound, ValidationError, canonical_json
from .scoped_writes import FencedWrites, fenced_callback

TERMINAL = {"succeeded", "failed", "cancelled"}
SAFE_ERRORS = {"handler_missing": "Обработка этого этапа пока недоступна.",
    "handler_failed": "Не удалось завершить обработку.",
    "attempts_exhausted": "Достигнут предел попыток обработки.",
    "infrastructure_unavailable": "Сервис обработки временно недоступен.",
    "invalid_input": "Недопустимые данные задания."}


class LeaseLost(Exception):
    """Expected stale worker outcome; contains no provider or document data."""


@dataclass(frozen=True)
class Claim:
    token: str
    attempt: int
    stage: str
    input_hash: str
    payload: dict


def public_job(job):
    return {"id": job.id, "project_id": job.project_id, "stage": job.stage,
        "status": job.status, "attempt": job.attempt, "progress": job.progress,
        "error": copy.deepcopy(job.error), "event_sequence": job.event_sequence}


class JobService:
    def __init__(self, engine, organization_id, *, clock=utcnow, lease_seconds=60, max_attempts=3):
        if not 1 <= lease_seconds <= 3600 or not 1 <= max_attempts <= 20:
            raise ValueError("Job lease and attempt policy must be bounded")
        self.engine, self.organization_id, self.clock = engine, organization_id, clock
        self.lease_seconds, self.max_attempts = lease_seconds, max_attempts

    def _job(self, db, job_id, project_id=None):
        statement = select(Job).where(Job.organization_id == self.organization_id, Job.id == job_id)
        if project_id is not None:
            statement = statement.where(Job.project_id == project_id)
        job = db.scalar(statement.with_for_update().execution_options(populate_existing=True))
        if job is None:
            raise NotFound()
        return job

    def _event(self, db, job, kind):
        job.event_sequence += 1
        db.add(JobProgressEvent(organization_id=self.organization_id, project_id=job.project_id,
            job_id=job.id, sequence=job.event_sequence, data={"kind": kind, **public_job(job)}))

    def _fence(self, db, job_id, token):
        job = self._job(db, job_id)
        if job.status != "running" or not secrets.compare_digest(job.lease_token or "", token) or \
            job.lease_expires_at is None or job.lease_expires_at <= self.clock():
            raise LeaseLost()
        self._commit_fence(db, job, token, job.lease_expires_at)
        return job

    @contextmanager
    def _transaction(self):
        try:
            with tenant_transaction(self.engine, self.organization_id) as db:
                yield db
        except IntegrityError as exc:
            if getattr(getattr(exc.orig, "diag", None), "constraint_name", None) == "job_lease_fence_valid":
                raise LeaseLost() from None
            raise

    def _commit_fence(self, db, job, token, expires_at):
        if db.info.get("job_commit_fence"):
            return  # Preserve the original deadline across extension/terminal writes.
        db.info["job_commit_fence"] = (job.id, token, expires_at)
        if self.engine.dialect.name == "postgresql":
            db.execute(text("SELECT set_config('app.job_fence_job', :job_id, true), "
                "set_config('app.job_fence_token', :token, true), "
                "set_config('app.job_fence_expiry', :expiry, true)"),
                {"job_id": job.id, "token": token, "expiry": expires_at.isoformat()})
        def validate_before_commit(session):
            session.flush()  # All domain/checkpoint/progress/terminal SQL first.
            if self.clock() >= expires_at:
                raise LeaseLost()
        event.listen(db, "before_commit", validate_before_commit)

    def _clear_lease(self, job):
        job.lease_token = job.lease_owner = job.lease_expires_at = None

    def claim(self, job_id):
        with self._transaction() as db:
            job = self._job(db, job_id)
            now = self.clock()
            if job.status != "queued" and not (job.status == "running" and job.lease_expires_at is not None and job.lease_expires_at <= now):
                return None
            if job.status == "queued":
                outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == self.organization_id,
                    OutboxEvent.job_id == job.id))
                if outbox is not None and outbox.available_at is not None and outbox.available_at > now:
                    return None
            if job.attempt >= self.max_attempts:
                self._terminal(db, job, "failed", "attempts_exhausted")
                return None
            job.status, job.attempt = "running", job.attempt + 1
            job.lease_token = secrets.token_hex(32)
            job.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
            job.started_at, job.completed_at, job.error = now, None, None
            self._event(db, job, "claimed")
            self._commit_fence(db, job, job.lease_token, job.lease_expires_at)
            return Claim(job.lease_token, job.attempt, job.stage, job.input_hash, copy.deepcopy(job.payload))

    def heartbeat(self, job_id, token):
        with self._transaction() as db:
            job = self._fence(db, job_id, token)
            job.lease_expires_at = self.clock() + timedelta(seconds=self.lease_seconds)

    def checkpoint(self, job_id, token, step, version, result, write_result=None, *, progress=None):
        if not isinstance(step, str) or not 1 <= len(step) <= 64 or not isinstance(version, str) or not version:
            raise ValidationError()
        canonical_json(result)
        if not isinstance(result, dict) or (progress is not None and not 0 <= progress <= 100):
            raise ValidationError()
        with self._transaction() as db:
            job = self._fence(db, job_id, token)
            digest = hashlib.sha256(canonical_json({"input": job.input_hash, "version": version}).encode()).hexdigest()
            checkpoint = db.scalar(select(JobCheckpoint).where(JobCheckpoint.organization_id == self.organization_id,
                JobCheckpoint.job_id == job.id, JobCheckpoint.step == step))
            if checkpoint:
                if checkpoint.input_version_hash != digest:
                    raise Conflict()
                return copy.deepcopy(checkpoint.result)
            if write_result:
                # Callback does local SQL only. A Session transaction is owned here;
                # callbacks must never commit or do provider/network work.
                fenced_callback(db, write_result, self.organization_id, job.project_id)
            self._fence(db, job_id, token)  # Also fences a callback that outlived expiry.
            db.add(JobCheckpoint(organization_id=self.organization_id, project_id=job.project_id,
                job_id=job.id, step=step, input_version_hash=digest, result=copy.deepcopy(result)))
            if progress is not None:
                job.progress = max(job.progress, progress)
            self._event(db, job, "checkpoint")
            return copy.deepcopy(result)

    def _terminal(self, db, job, state, error_code=None):
        job.status, job.completed_at = state, self.clock()
        self._clear_lease(job)
        job.error = {"code": error_code, "message": SAFE_ERRORS[error_code]} if error_code else None
        if state == "succeeded":
            job.progress = 100
        self._event(db, job, state)

    def finish(self, job_id, token, write_result=None):
        with self._transaction() as db:
            job = self._fence(db, job_id, token)
            if write_result:
                fenced_callback(db, write_result, self.organization_id, job.project_id)
            self._fence(db, job_id, token)
            self._terminal(db, job, "succeeded")

    def fail(self, job_id, token, error_code):
        if error_code not in SAFE_ERRORS:
            error_code = "handler_failed"
        with self._transaction() as db:
            self._terminal(db, self._fence(db, job_id, token), "failed", error_code)

    def defer(self, job_id, token):
        """Bounded infrastructure retry; checkpoints survive, lease is fenced."""
        with self._transaction() as db:
            job = self._fence(db, job_id, token)
            if job.attempt >= self.max_attempts:
                self._terminal(db, job, "failed", "attempts_exhausted")
                return
            job.status = "queued"
            self._clear_lease(job)
            job.error = {"code": "infrastructure_unavailable", "message": SAFE_ERRORS["infrastructure_unavailable"]}
            outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == self.organization_id,
                OutboxEvent.job_id == job.id).with_for_update())
            outbox.published_at = outbox.claim_token = outbox.claim_expires_at = None
            outbox.available_at = self.clock() + timedelta(seconds=min(300, 2 ** min(job.attempt, 8)))
            self._event(db, job, "deferred")

    def _operation(self, db, job, actor, request_key, payload, operation):
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 255:
            raise ValidationError()
        digest = hashlib.sha256(canonical_json(payload).encode()).hexdigest()
        previous = db.scalar(select(JobOperation).where(JobOperation.organization_id == self.organization_id,
            JobOperation.job_id == job.id, JobOperation.request_key == request_key))
        if previous:
            if previous.actor_id != actor or previous.operation != operation or previous.payload_digest != digest:
                raise Conflict()
            return previous, digest
        return None, digest

    def _record(self, db, job, actor, request_key, digest, operation, changed):
        response = public_job(job)
        db.add(JobOperation(organization_id=self.organization_id, project_id=job.project_id, job_id=job.id,
            actor_id=actor, request_key=request_key, operation=operation, payload_digest=digest, response=response))
        if changed:
            db.add(AuditEvent(organization_id=self.organization_id, project_id=job.project_id, actor_id=actor,
                action="job." + ("retried" if operation == "retry" else "cancelled"), entity_type="jobs", entity_id=job.id,
                after=response))
        db.flush()
        return response

    def retry_in(self, db, job_id, project_id, actor, request_key, payload):
        job = self._job(db, job_id, project_id)
        previous, digest = self._operation(db, job, actor, request_key, payload, "retry")
        if previous:
            return copy.deepcopy(previous.response)
        if job.status != "failed" or job.attempt >= self.max_attempts:
            raise Conflict()
        job.status, job.completed_at, job.error = "queued", None, None
        self._clear_lease(job)
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == self.organization_id, OutboxEvent.job_id == job.id).with_for_update())
        outbox.published_at = outbox.claim_token = outbox.claim_expires_at = outbox.available_at = outbox.error_code = None
        self._event(db, job, "retried")
        return self._record(db, job, actor, request_key, digest, "retry", True)

    def cancel_in(self, db, job_id, project_id, actor, request_key, payload):
        job = self._job(db, job_id, project_id)
        previous, digest = self._operation(db, job, actor, request_key, payload, "cancel")
        if previous:
            return copy.deepcopy(previous.response)
        if job.status == "succeeded":
            raise Conflict()
        changed = job.status != "cancelled"
        if changed:
            self._terminal(db, job, "cancelled")
        return self._record(db, job, actor, request_key, digest, "cancel", changed)

    def recover_expired(self, *, limit=100):
        with tenant_transaction(self.engine, self.organization_id) as db:
            jobs = list(db.scalars(select(Job).where(Job.organization_id == self.organization_id,
                Job.status == "running", Job.lease_expires_at <= self.clock()).order_by(Job.id)
                .limit(limit).with_for_update(skip_locked=True)))
            for job in jobs:
                if job.attempt >= self.max_attempts:
                    self._terminal(db, job, "failed", "attempts_exhausted")
                    continue
                job.status = "queued"
                self._clear_lease(job)
                outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == self.organization_id,
                    OutboxEvent.job_id == job.id).with_for_update())
                outbox.published_at = outbox.claim_token = outbox.claim_expires_at = outbox.available_at = None
                self._event(db, job, "recovered")
            return len(jobs)

    def recover_undelivered(self, *, limit=100):
        """Recover queued jobs whose delivery ended before any DB claim.

        A worker can exhaust bounded DB reconnects before claiming. A durable
        queue delayed beyond this threshold may receive a duplicate; claim
        fencing makes that harmless when the original eventually arrives.
        """
        deadline = self.clock() - timedelta(seconds=self.lease_seconds)
        recovered = 0
        with tenant_transaction(self.engine, self.organization_id) as db:
            jobs = list(db.scalars(select(Job).join(OutboxEvent, (OutboxEvent.job_id == Job.id) &
                (OutboxEvent.organization_id == Job.organization_id)).where(Job.organization_id == self.organization_id,
                Job.status == "queued", OutboxEvent.published_at <= deadline)
                .order_by(Job.id).limit(limit).with_for_update(of=Job, skip_locked=True)))
            for job in jobs:
                outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == self.organization_id,
                    OutboxEvent.job_id == job.id).with_for_update())
                if outbox.published_at is None or outbox.published_at > deadline:
                    continue
                outbox.published_at = outbox.claim_token = outbox.claim_expires_at = outbox.available_at = None
                self._event(db, job, "delivery_recovered")
                recovered += 1
        return recovered
