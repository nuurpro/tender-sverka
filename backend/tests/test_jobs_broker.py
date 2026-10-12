"""Real RabbitMQ/Celery process tests. Own random queues/processes only."""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from kombu import Connection, Queue
from sqlalchemy import select, func

from test_jobs_execution import jobs
from app.workspace.database import tenant_transaction
from app.workspace.models import Job, JobCheckpoint, Project, OutboxEvent
from app.workspace.repository import Repository


def wait_until(predicate, timeout=25):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.1)
    pytest.fail("Owned integration process did not reach the expected durable state")


@pytest.fixture
def rabbit(jobs, tmp_path):
    broker = os.environ.get("WORKSPACE_TEST_BROKER_URL")
    if not broker:
        pytest.skip("Explicit real RabbitMQ integration URL required")
    with Connection(broker, connect_timeout=5) as conn:
        try:
            conn.ensure_connection(max_retries=0)
        except Exception:
            pytest.fail("Explicit RabbitMQ endpoint is unavailable")
    from app.config import Settings
    from app.jobs.worker import create_celery_app
    engine, org, actor, project, _ = jobs
    queue = "tender.test." + uuid4().hex
    secret = "integration-" + uuid4().hex + uuid4().hex
    settings = Settings(environment="test", database_url=os.environ["WORKSPACE_TEST_RUNTIME_DATABASE_URL"],
        session_secret="s" * 48, sms_provider="mock", broker_url=broker,
        job_signing_secret=secret, job_queue=queue, job_lease_seconds=5)
    app = create_celery_app(settings)
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PYTHONPATH"}
    environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    environment.update(APP_ENVIRONMENT="test", DATABASE_URL=os.environ["WORKSPACE_TEST_RUNTIME_DATABASE_URL"],
        APP_SESSION_SECRET="s" * 48, APP_SMS_PROVIDER="mock", CELERY_BROKER_URL=broker,
        APP_JOB_SIGNING_SECRET=secret, APP_JOB_QUEUE=queue, APP_JOB_LEASE_SECONDS="5",
        PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    processes, logs = [], []
    def start():
        log = (tmp_path / ("worker-" + str(len(processes)) + ".log")).open("wb")
        process = subprocess.Popen([sys.executable, str(Path(__file__).with_name("jobs_fixture_worker.py"))],
            cwd=Path(__file__).resolve().parents[1], env=environment, stdout=log, stderr=subprocess.STDOUT)
        processes.append(process)
        logs.append(log)
        return process
    try:
        yield settings, app, start
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        with Connection(broker, connect_timeout=5) as conn:
            Queue(queue, durable=True).bind(conn).delete(if_unused=False, if_empty=False)
        app.close()


def fixture_job(jobs, stage):
    engine, org, actor, project, _ = jobs
    with tenant_transaction(engine, org) as db:
        job = Repository(db, org, actor).create_job(stage, project, {"project_id": project}, str(uuid4()))
        return job.id


def state(jobs, job_id):
    engine, org, *_ = jobs
    with tenant_transaction(engine, org) as db:
        job = db.get(Job, job_id)
        return job.status, job.attempt


def publisher(jobs, rabbit):
    try:
        from app.jobs.publisher import OutboxPublisher, confirmed_sender
    except ImportError:
        pytest.fail("Confirmed RabbitMQ sender missing")
    settings, app, _ = rabbit
    return OutboxPublisher(jobs[0], settings.job_signing_secret.get_secret_value(), confirmed_sender(app))


def assert_single_output(jobs, job):
    engine, org, _, project, _ = jobs
    with tenant_transaction(engine, org) as db:
        assert db.scalar(select(func.count()).select_from(JobCheckpoint).where(JobCheckpoint.job_id == job)) == 1
        assert db.get(Project, project).revision == 2


def publish_job(pub, jobs, job, *, confirm_only=False):
    # The setup's unused job can be an earlier unpublished row.
    for _ in range(10):
        claim = pub.claim(jobs[1])
        assert claim
        if claim.message["job_id"] == job and confirm_only:
            pub.publish(claim.message)
        else:
            assert pub.send(jobs[1], claim)
        if claim.message["job_id"] == job:
            return claim
    pytest.fail("Fixture job did not receive an outbox claim")


def test_real_rabbit_duplicate_confirm_then_mark_crash(jobs, rabbit):
    pub = publisher(jobs, rabbit)
    job = fixture_job(jobs, "fixture.normal")
    rabbit[2]()
    claim = publish_job(pub, jobs, job, confirm_only=True)
    with tenant_transaction(jobs[0], jobs[1]) as db:
        assert db.get(OutboxEvent, claim.id).published_at is None
    # Confirmed copy followed by a duplicate models crash before SQL mark.
    pub.publish(claim.message)
    assert pub.mark_published(jobs[1], claim)
    wait_until(lambda: state(jobs, job)[0] == "succeeded")
    time.sleep(1)
    assert state(jobs, job) == ("succeeded", 1)
    assert_single_output(jobs, job)


def test_real_rabbit_lost_ack_redelivery_after_process_restart(jobs, rabbit):
    pub = publisher(jobs, rabbit)
    job = fixture_job(jobs, "fixture.ack_loss")
    first = rabbit[2]()
    publish_job(pub, jobs, job)
    wait_until(lambda: first.poll() == 43)
    assert state(jobs, job) == ("succeeded", 1)
    rabbit[2]()
    time.sleep(3)
    assert state(jobs, job) == ("succeeded", 1)
    assert_single_output(jobs, job)


def test_real_worker_restart_recovers_expired_lease_without_duplicate_output(jobs, rabbit):
    from app.jobs.service import JobService
    pub = publisher(jobs, rabbit)
    job = fixture_job(jobs, "fixture.wait")
    first = rabbit[2]()
    publish_job(pub, jobs, job)
    engine, org, *_ = jobs
    def checkpointed():
        with tenant_transaction(engine, org) as db:
            return db.scalar(select(func.count()).select_from(JobCheckpoint).where(JobCheckpoint.job_id == job)) == 1
    wait_until(checkpointed)
    time.sleep(6)  # Real background heartbeats keep a five-second lease alive.
    assert JobService(engine, org, lease_seconds=5).claim(job) is None
    first.terminate()
    first.wait(timeout=10)
    time.sleep(6)
    assert JobService(engine, org, lease_seconds=5).recover_expired() == 1
    rabbit[2]()
    publish_job(pub, jobs, job)
    wait_until(lambda: state(jobs, job)[0] == "succeeded")
    assert state(jobs, job) == ("succeeded", 2)
    assert_single_output(jobs, job)


def test_broker_connection_failure_leaves_outbox_unpublished(jobs, rabbit):
    from app.jobs.publisher import OutboxPublisher, confirmed_sender
    from app.jobs.worker import create_celery_app
    settings = rabbit[0].model_copy(update={"broker_url": type(rabbit[0].broker_url)("amqp://guest:guest@127.0.0.1:1//")})
    broken = create_celery_app(settings)
    broken.conf.broker_connection_timeout = 0.2
    broken.conf.task_publish_retry = False
    pub = OutboxPublisher(jobs[0], settings.job_signing_secret.get_secret_value(), confirmed_sender(broken))
    claim = pub.claim(jobs[1])
    assert pub.send(jobs[1], claim) is False
    with tenant_transaction(jobs[0], jobs[1]) as db:
        outbox = db.get(OutboxEvent, claim.id)
        assert outbox.published_at is None and outbox.error_code == "broker_unavailable"
        assert outbox.claim_token is None and outbox.available_at is not None
    broken.close()
