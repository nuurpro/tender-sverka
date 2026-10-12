"""Durable job behavior against the real restricted PostgreSQL runtime."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import os
import threading
from uuid import uuid4

import pytest
from sqlalchemy import select, text, func
from sqlalchemy.exc import IntegrityError

from app.workspace.database import create_engine, tenant_transaction
from app.workspace.models import Organization, User, Job, AuditEvent, OutboxEvent, utcnow
from app.workspace.repository import Repository, Conflict


@pytest.fixture
def jobs():
    url = os.environ.get("WORKSPACE_TEST_RUNTIME_DATABASE_URL")
    if not url:
        pytest.skip("Explicit restricted PostgreSQL URL required")
    engine = create_engine(url)
    org, actor = str(uuid4()), str(uuid4())
    with tenant_transaction(engine, org) as db:
        db.add(Organization(id=org, name="Job test"))
        db.add(User(id=actor, phone="+998" + str(uuid4().int)[0:9]))
        db.flush()
        repo = Repository(db, org, actor)
        project = repo.create_project("buyer", "Job fixture")
        job = repo.create_job("fixture", project.id, {"version": 1}, str(uuid4()))
        project_id, job_id = project.id, job.id
    yield engine, org, actor, project_id, job_id
    engine.dispose()


def service():
    try:
        from app.jobs.service import JobService, LeaseLost
    except ImportError:
        pytest.fail("Durable fenced job service is missing")
    return JobService, LeaseLost


def test_claim_is_single_and_expired_takeover_fences_every_write(jobs):
    cls, lost = service()
    engine, org, _, _, job = jobs
    now = utcnow()
    def claim(_):
        return cls(engine, org, clock=lambda: now, lease_seconds=10).claim(job)
    with ThreadPoolExecutor(max_workers=6) as pool:
        claims = list(pool.map(claim, range(6)))
    assert len([c for c in claims if c]) == 1
    old = next(c for c in claims if c)
    active = cls(engine, org, clock=lambda: now + timedelta(seconds=11), lease_seconds=10)
    new = active.claim(job)
    assert new.token != old.token and new.attempt == 2
    for action in [lambda: active.heartbeat(job, old.token),
                   lambda: active.checkpoint(job, old.token, "extract", "v1", {}),
                   lambda: active.finish(job, old.token)]:
        with pytest.raises(lost):
            action()
    active.finish(job, new.token)
    assert active.claim(job) is None


def test_checkpoint_and_domain_callback_are_atomic_retained_on_retry(jobs):
    from app.workspace.models import Project
    cls, lost = service()
    engine, org, actor, project, job = jobs
    runner = cls(engine, org)
    claim = runner.claim(job)
    def result(db):
        db.get(Project, project).customer = "fenced result"
    assert runner.checkpoint(job, claim.token, "extract", "v1", {"count": 1}, result) == {"count": 1}
    assert runner.checkpoint(job, claim.token, "extract", "v1", {}, lambda db: pytest.fail("duplicate output")) == {"count": 1}
    with pytest.raises(Conflict):
        runner.checkpoint(job, claim.token, "extract", "v2", {})
    runner.fail(job, claim.token, "handler_failed")
    with tenant_transaction(engine, org) as db:
        response = runner.retry_in(db, job, project, actor, "retry-1", {})
        assert response["status"] == "queued"
    claim2 = runner.claim(job)
    assert runner.checkpoint(job, claim2.token, "extract", "v1", {}) == {"count": 1}
    with tenant_transaction(engine, org) as db:
        assert runner.retry_in(db, job, project, actor, "retry-1", {}) == response
        with pytest.raises(Conflict):
            runner.retry_in(db, job, project, actor, "retry-1", {"other": True})
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == job, AuditEvent.action == "job.retried")) == 1


def test_cancel_invalidates_old_callbacks_and_is_audited_once(jobs):
    cls, lost = service()
    engine, org, actor, project, job = jobs
    runner = cls(engine, org)
    claim = runner.claim(job)
    with tenant_transaction(engine, org) as db:
        first = runner.cancel_in(db, job, project, actor, "cancel-1", {})
    with tenant_transaction(engine, org) as db:
        assert runner.cancel_in(db, job, project, actor, "cancel-1", {}) == first
        assert runner.cancel_in(db, job, project, actor, "cancel-2", {})["status"] == "cancelled"
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.entity_id == job, AuditEvent.action == "job.cancelled")) == 1
    with pytest.raises(lost):
        runner.finish(job, claim.token)
    with tenant_transaction(engine, org) as db:
        with pytest.raises(Conflict):
            runner.retry_in(db, job, project, actor, "retry", {})


def test_job_inputs_database_immutable_and_new_tables_force_rls(jobs):
    cls, _ = service()
    from app.workspace.models import JobCheckpoint, JobProgressEvent, JobOperation
    engine, org, _, project, job = jobs
    runner = cls(engine, org)
    claim = runner.claim(job)
    runner.checkpoint(job, claim.token, "extract", "v1", {})
    for field, value in [("payload", {"changed": True}), ("stage", "other"), ("input_hash", "0" * 64), ("idempotency_key", "other")]:
        with pytest.raises(IntegrityError):
            with tenant_transaction(engine, org) as db:
                setattr(db.get(Job, job), field, value)
    with engine.connect() as conn:
        for table in [JobCheckpoint, JobProgressEvent, JobOperation]:
            assert conn.scalar(select(func.count()).select_from(table)) == 0
            assert conn.execute(text("SELECT relrowsecurity, relforcerowsecurity FROM pg_class WHERE relname=:name"), {"name": table.__tablename__}).one() == (True, True)
    with tenant_transaction(engine, str(uuid4())) as db:
        assert db.get(Job, job) is None
        assert list(db.scalars(select(JobCheckpoint))) == []


def test_expired_same_token_rejected_and_bounded_attempts(jobs):
    cls, lost = service()
    engine, org, actor, project, job = jobs
    now = [utcnow()]
    runner = cls(engine, org, clock=lambda: now[0], lease_seconds=5, max_attempts=2)
    first = runner.claim(job)
    now[0] += timedelta(seconds=6)
    with pytest.raises(lost):
        runner.finish(job, first.token)
    second = runner.claim(job)
    runner.fail(job, second.token, "handler_failed")
    with tenant_transaction(engine, org) as db:
        with pytest.raises(Conflict):
            runner.retry_in(db, job, project, actor, "third", {})


def test_outbox_claim_confirm_and_recovery_have_owned_fences(jobs):
    try:
        from app.jobs.publisher import OutboxPublisher
    except ImportError:
        pytest.fail("Two-phase outbox publisher missing")
    cls, _ = service()
    engine, org, _, _, job = jobs
    now = [utcnow()]
    seen = []
    def publish(message):
        with tenant_transaction(engine, org) as db:
            row = db.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job).with_for_update(nowait=True))
            assert row.published_at is None  # SQL transaction closed during broker I/O.
        seen.append(message)
    publisher = OutboxPublisher(engine, "s" * 48, publish, clock=lambda: now[0], lease_seconds=5)
    first = publisher.claim(org)
    assert first and publisher.claim(org) is None
    now[0] += timedelta(seconds=6)
    second = publisher.claim(org)
    assert not publisher.mark_published(org, first)
    publisher.send(org, second)
    assert len(seen) == 1 and set(seen[0]) == {"version", "organization_id", "job_id", "input_hash", "signature"}
    claim = cls(engine, org, clock=lambda: now[0], lease_seconds=5).claim(job)
    now[0] += timedelta(seconds=6)
    assert cls(engine, org, clock=lambda: now[0]).recover_expired() == 1
    assert publisher.claim(org)


def test_callbacks_cannot_commit_or_leave_results_after_expiry(jobs):
    from app.workspace.models import Project, JobCheckpoint
    cls, lost = service()
    engine, org, _, project, job = jobs
    now = [utcnow()]
    runner = cls(engine, org, clock=lambda: now[0], lease_seconds=5)
    claim = runner.claim(job)
    def invalid_callback(db):
        db.get(Project, project).customer = "must rollback"
        db.commit()
    with pytest.raises(RuntimeError, match="owned"):
        runner.checkpoint(job, claim.token, "commit", "v1", {}, invalid_callback)
    def expired_callback(db):
        db.get(Project, project).customer = "must rollback"
        now[0] += timedelta(seconds=6)
    with pytest.raises(lost):
        runner.checkpoint(job, claim.token, "expired", "v1", {}, expired_callback)
    with tenant_transaction(engine, org) as db:
        assert db.get(Project, project).customer is None
        assert db.scalar(select(func.count()).select_from(JobCheckpoint).where(JobCheckpoint.job_id == job)) == 0


def test_new_table_composite_fk_and_lifecycle_audit_rollback(jobs):
    from app.workspace.models import JobCheckpoint
    cls, _ = service()
    engine, org, actor, project, job = jobs
    runner = cls(engine, org)
    claim = runner.claim(job)
    runner.fail(job, claim.token, "handler_failed")
    with pytest.raises(IntegrityError):
        with tenant_transaction(engine, org) as db:
            runner.retry_in(db, job, project, str(uuid4()), "invalid-actor", {})
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).status == "failed"
        other = Repository(db, org, actor).create_project("buyer", "Other")
        other_id = other.id
    with pytest.raises(IntegrityError):
        with tenant_transaction(engine, org) as db:
            db.add(JobCheckpoint(organization_id=org, project_id=other_id, job_id=job,
                step="bad", input_version_hash="f" * 64, result={}))


def test_transient_failure_requeues_fenced_outbox_with_bounded_backoff(jobs):
    cls, lost = service()
    engine, org, _, _, job = jobs
    now = [utcnow()]
    runner = cls(engine, org, clock=lambda: now[0], max_attempts=2)
    first = runner.claim(job)
    assert hasattr(runner, "defer"), "Worker infrastructure retry transition is missing"
    runner.defer(job, first.token)
    assert runner.claim(job) is None  # Delayed duplicate cannot bypass backoff.
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).status == "queued"
        outbox = db.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job))
        assert now[0] < outbox.available_at <= now[0] + timedelta(seconds=300)
    with pytest.raises(lost):
        runner.finish(job, first.token)
    now[0] += timedelta(seconds=10)
    second = runner.claim(job)
    runner.defer(job, second.token)
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).status == "failed"
        assert db.get(Job, job).error["code"] == "attempts_exhausted"


def test_recovery_republishes_stale_queued_delivery_after_bounded_worker_reconnect(jobs):
    cls, _ = service()
    engine, org, _, _, job = jobs
    now = utcnow()
    with tenant_transaction(engine, org) as db:
        db.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job)).published_at = now - timedelta(seconds=61)
    runner = cls(engine, org, clock=lambda: now, lease_seconds=60)
    assert hasattr(runner, "recover_undelivered"), "Queued published recovery is missing"
    assert runner.recover_undelivered() == 1
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).status == "queued" and db.get(Job, job).attempt == 0
        assert db.scalar(select(OutboxEvent).where(OutboxEvent.job_id == job)).published_at is None


def test_postgres_cancellation_race_cannot_commit_a_cancelled_domain_result(jobs):
    from app.workspace.models import Project
    cls, lost = service()
    engine, org, actor, project, job = jobs
    runner = cls(engine, org)
    claim = runner.claim(job)
    start = threading.Barrier(2)
    def complete():
        start.wait(timeout=5)
        try:
            runner.finish(job, claim.token, lambda db: setattr(db.get(Project, project), "revision", 2))
            return "succeeded"
        except lost:
            return "fenced"
    def cancel():
        start.wait(timeout=5)
        try:
            with tenant_transaction(engine, org) as db:
                runner.cancel_in(db, job, project, actor, "racing-cancel", {})
            return "cancelled"
        except Conflict:
            return "already_succeeded"
    with ThreadPoolExecutor(max_workers=2) as pool:
        completion, cancellation = pool.submit(complete), pool.submit(cancel)
        outcomes = completion.result(), cancellation.result()
    assert outcomes in [("succeeded", "already_succeeded"), ("fenced", "cancelled")]
    with tenant_transaction(engine, org) as db:
        row = db.get(Job, job)
        assert db.get(Project, project).revision == (1 if row.status == "cancelled" else 2)
