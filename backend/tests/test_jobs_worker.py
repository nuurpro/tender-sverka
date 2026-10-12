"""Strict messages/configuration, explicit registry, worker-safe failures."""
from uuid import uuid4

import pytest

from test_jobs_execution import jobs


def configured(**options):
    from app.config import Settings
    return Settings(environment="test", database_url="postgresql://restricted@localhost/test",
        session_secret="a" * 48, sms_provider="mock", broker_url="amqp://guest@localhost//", **options)


def test_worker_configuration_rejects_missing_key_sqlite_and_json_only():
    try:
        from app.jobs.worker import create_celery_app
    except ImportError:
        pytest.fail("Strict Celery worker factory missing")
    with pytest.raises(ValueError, match="signing"):
        create_celery_app(configured())
    with pytest.raises(ValueError, match="signing"):
        create_celery_app(configured(job_signing_secret="short"))
    app = create_celery_app(configured(job_signing_secret="j" * 48))
    assert app.conf.accept_content == ["json"]
    assert app.conf.task_serializer == app.conf.result_serializer == "json"
    assert app.conf.task_acks_late and app.conf.task_reject_on_worker_lost
    assert app.conf.broker_connection_max_retries <= 10


def test_envelope_tamper_unknown_fields_and_fixture_registration_are_rejected():
    from app.jobs.messages import envelope, validate_envelope
    try:
        from app.jobs.worker import HandlerRegistry
    except ImportError:
        pytest.fail("Explicit handler registry missing")
    data = envelope(str(uuid4()), str(uuid4()), "f" * 64, "j" * 48)
    assert validate_envelope(data, "j" * 48) == data
    assert validate_envelope({**data, "stage": "os.system"}, "j" * 48) is None
    assert validate_envelope({**data, "job_id": str(uuid4())}, "j" * 48) is None
    assert validate_envelope(data, "k" * 48) is None
    with pytest.raises(ValueError):
        HandlerRegistry("production").register_fixture("fixture", lambda context: None)


def test_worker_loads_frozen_db_stage_and_missing_handler_fails_safe(jobs):
    from app.jobs.messages import envelope
    try:
        from app.jobs.worker import execute_message, HandlerRegistry
    except ImportError:
        pytest.fail("Durable task executor missing")
    from app.workspace.database import tenant_transaction
    from app.workspace.models import Job
    engine, org, _, _, job = jobs
    with tenant_transaction(engine, org) as db:
        digest = db.get(Job, job).input_hash
    message = envelope(org, job, digest, "j" * 48)
    registry = HandlerRegistry("test")
    assert execute_message(engine, configured(job_signing_secret="j" * 48), registry, {**message, "payload": {"private": True}}) == "invalid_message"
    assert execute_message(engine, configured(job_signing_secret="j" * 48), registry, message) == "failed"
    with tenant_transaction(engine, org) as db:
        row = db.get(Job, job)
        assert row.error["code"] == "handler_missing" and row.status == "failed"
