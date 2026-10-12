"""JSON Celery tasks and explicit stage allowlist with process-owned DB engines."""
from contextlib import contextmanager
from dataclasses import dataclass
import os
import threading

from celery import Celery, bootsteps
from kombu import Queue
from sqlalchemy.exc import SQLAlchemyError

from app.workspace.database import create_engine, verify_runtime_database, tenant_transaction
from app.workspace.models import Job
from app.workspace.repository import NotFound, ValidationError
from .messages import signing_key, validate_envelope
from .service import JobService, LeaseLost

TASK_NAME = "tender.execute_job"


class TransientInfrastructureFailure(Exception):
    """Explicit retryable adapter outage; exception text is never exposed."""


class HandlerRegistry:
    def __init__(self, environment):
        self.environment, self.handlers = environment, {}

    def register(self, stage, handler):
        if not stage or len(stage) > 64 or stage in self.handlers or not callable(handler):
            raise ValueError("Invalid or duplicate registered stage")
        self.handlers[stage] = handler

    def register_fixture(self, stage, handler):
        if self.environment != "test":
            raise ValueError("Fixture handlers require the explicit test environment")
        self.register(stage, handler)

    def get(self, stage):
        return self.handlers.get(stage)


@dataclass(frozen=True)
class ExecutionContext:
    service: JobService
    job_id: str
    claim: object

    @property
    def payload(self):
        return self.claim.payload

    def checkpoint(self, step, version, result, write_result=None, *, progress=None):
        return self.service.checkpoint(self.job_id, self.claim.token, step, version, result, write_result, progress=progress)


@contextmanager
def heartbeat(service, job_id, token):
    stop = threading.Event()
    lost = threading.Event()
    def renew():
        while not stop.wait(max(0.1, service.lease_seconds / 3)):
            try:
                service.heartbeat(job_id, token)
            except (LeaseLost, SQLAlchemyError):
                lost.set()
                return
    thread = threading.Thread(target=renew, name="job-lease-heartbeat", daemon=True)
    thread.start()
    try:
        yield lost
    finally:
        stop.set()
        thread.join(timeout=2)


def execute_message(engine, settings, registry, message):
    validated = validate_envelope(message, settings.job_signing_secret.get_secret_value())
    if validated is None:
        return "invalid_message"
    org, job_id = validated["organization_id"], validated["job_id"]
    with tenant_transaction(engine, org) as db:
        job = db.get(Job, job_id)
        if job is None or job.organization_id != org or job.input_hash != validated["input_hash"]:
            return "invalid_message"
    service = JobService(engine, org, lease_seconds=settings.job_lease_seconds, max_attempts=settings.job_max_attempts)
    try:
        claim = service.claim(job_id)
    except NotFound:
        return "invalid_message"
    if claim is None:
        return "duplicate"
    handler = registry.get(claim.stage)  # Frozen DB stage, never a message import path.
    if handler is None:
        service.fail(job_id, claim.token, "handler_missing")
        return "failed"
    try:
        with heartbeat(service, job_id, claim.token) as lost:
            handler(ExecutionContext(service, job_id, claim))
            if lost.is_set():
                return "lease_lost"
            service.finish(job_id, claim.token)
        return "succeeded"
    except LeaseLost:
        return "lease_lost"
    except (SQLAlchemyError, TransientInfrastructureFailure):
        try:
            service.defer(job_id, claim.token)
        except LeaseLost:
            return "lease_lost"
        return "deferred"
    except Exception as exc:
        # No exception messages, URLs or document data are persisted or logged.
        code = "invalid_input" if isinstance(exc, ValidationError) else "handler_failed"
        try:
            service.fail(job_id, claim.token, code)
        except LeaseLost:
            return "lease_lost"
        return "failed"


def require_worker_settings(settings):
    signing_key(settings.job_signing_secret.get_secret_value())
    if not settings.broker_url or not settings.broker_url.get_secret_value():
        raise ValueError("RabbitMQ broker configuration is required")
    from sqlalchemy.engine import make_url
    if make_url(settings.database_url.get_secret_value()).get_backend_name() != "postgresql":
        raise ValueError("Durable workers require PostgreSQL")
    if make_url(settings.broker_url.get_secret_value()).get_backend_name() not in {"amqp", "pyamqp"}:
        raise ValueError("Durable workers require RabbitMQ AMQP transport")


def create_celery_app(settings=None, *, registry=None):
    if settings is None:
        from app.config import Settings
        settings = Settings()
    require_worker_settings(settings)
    if registry is None:
        registry = HandlerRegistry(settings.environment)
        from app.packages.inspection import register_handlers
        register_handlers(registry,settings)
    if registry.environment != settings.environment:
        raise ValueError("Registry environment does not match worker environment")
    app = Celery("tender", broker=settings.broker_url.get_secret_value())
    app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"],
        result_accept_content=["json"], task_acks_late=True, task_reject_on_worker_lost=True,
        task_ignore_result=True, worker_prefetch_multiplier=1,
        broker_connection_retry_on_startup=True, broker_connection_max_retries=5,
        broker_connection_timeout=5, broker_transport_options={"confirm_publish": True},
        task_default_queue=settings.job_queue, task_queues=(Queue(settings.job_queue, durable=True),),
        task_default_delivery_mode="persistent", task_publish_retry=True,
        task_publish_retry_policy={"max_retries": 3, "interval_start": 0, "interval_step": 1, "interval_max": 3},
        worker_hijack_root_logger=False)
    runtime = {"pid": None, "engine": None}
    def own_engine():
        if runtime["pid"] != os.getpid():
            if runtime["engine"] is not None:
                runtime["engine"].dispose(close=False)
            engine = create_engine(settings.database_url.get_secret_value(), connect_args={"connect_timeout": 5,
                "options": "-c statement_timeout=5000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"},
                execution_options={"logging_token": "job-runtime"})
            verify_runtime_database(engine)
            runtime.update(pid=os.getpid(), engine=engine)
        return runtime["engine"]
    class VerifyDatabase(bootsteps.StartStopStep):
        def start(self, worker):
            own_engine()
        def stop(self, worker):
            if runtime["engine"] is not None:
                runtime["engine"].dispose()
    app.steps["worker"].add(VerifyDatabase)
    @app.task(name=TASK_NAME, bind=True, max_retries=3)
    def execute(self, message):
        try:
            return execute_message(own_engine(), settings, registry, message)
        except SQLAlchemyError:
            if self.request.retries < self.max_retries:
                raise self.retry(countdown=min(30, 2 ** self.request.retries)) from None
            return "infrastructure_unavailable"
    return app


if __name__ == "__main__":
    import sys
    create_celery_app().worker_main(["worker", *sys.argv[1:]])
