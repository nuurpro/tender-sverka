"""Nested authorized job API and finite persisted SSE replay."""
import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import select

from test_full_auth_api import full_api, login, auth_headers
from test_full_api_postgres import pg_api, pg_login
from test_full_workspace_api import create_org, project
from app.workspace.database import tenant_transaction
from app.workspace.models import Membership
from app.workspace.repository import Repository


@pytest.fixture(params=["sqlite", "postgres"])
def jobs_api(request):
    return request.getfixturevalue("full_api" if request.param == "sqlite" else "pg_api")


def setup_job(api):
    client, engine, *_ = api
    actor = (pg_login(api) if engine.dialect.name == "postgresql" else login(api).json())["user"]["id"]
    org = create_org(client)
    project_id = project(client, org)
    with tenant_transaction(engine, org) as db:
        job = Repository(db, org, actor).create_job("unavailable-real-stage", project_id, {}, str(uuid4()))
        job_id = job.id
    return org, project_id, job_id, actor


def test_nested_jobs_permissions_safe_snapshot_and_idempotent_actions(jobs_api):
    from app.jobs.service import JobService
    client, engine, *_ = jobs_api
    org, project_id, job, actor = setup_job(jobs_api)
    base = f"/api/organizations/{org}/projects/{project_id}/jobs"
    assert client.get(base).status_code == 200
    detail = client.get(base + "/" + job)
    assert detail.status_code == 200
    assert "payload" not in detail.json() and "lease_token" not in detail.json()
    assert client.post(base, json={"stage": "extract"}, headers=auth_headers(client)).status_code == 405
    other = project(client, org)
    assert client.get(base.replace(project_id, other) + "/" + job).status_code == 404
    runner = JobService(engine, org)
    claim = runner.claim(job)
    runner.fail(job, claim.token, "handler_missing")
    path = base + "/" + job + "/retry"
    assert client.post(path, json={}).status_code == 403
    assert client.post(path, json={}, headers=auth_headers(client)).status_code == 422
    headers = {**auth_headers(client), "Idempotency-Key": "retry-once"}
    first = client.post(path, json={}, headers=headers)
    assert first.status_code == 200
    second_claim = runner.claim(job)
    assert client.post(path, json={}, headers=headers).json() == first.json()
    with tenant_transaction(engine, org) as db:
        db.scalar(select(Membership).where(Membership.user_id == actor)).role = "viewer"
    assert client.post(base + "/" + job + "/cancel", json={}, headers={**auth_headers(client), "Idempotency-Key": "cancel"}).status_code == 403
    assert client.get(base + "/" + job).status_code == 200


def test_sse_replay_is_job_qualified_persisted_terminal_and_auth_checked(jobs_api):
    from app.jobs.service import JobService
    client, engine, *_ = jobs_api
    org, project_id, job, actor = setup_job(jobs_api)
    runner = JobService(engine, org)
    claim = runner.claim(job)
    runner.checkpoint(job, claim.token, "one", "v1", {}, progress=50)
    runner.finish(job, claim.token)
    base = f"/api/organizations/{org}/projects/{project_id}/jobs/{job}/events"
    assert client.get(base, headers={"Last-Event-ID": f"{uuid4()}:1"}).status_code == 422
    assert client.get(base, headers={"Last-Event-ID": f"{job}:999"}).status_code == 422
    replay = client.get(base, headers={"Last-Event-ID": f"{job}:1"})
    assert replay.status_code == 200
    assert f"id: {job}:1" not in replay.text
    assert f"id: {job}:2" in replay.text and f"id: {job}:3" in replay.text
    assert "succeeded" in replay.text and replay.headers["cache-control"] == "no-store"
    with tenant_transaction(engine, org) as db:
        db.delete(db.scalar(select(Membership).where(Membership.user_id == actor)))
    assert client.get(base).status_code == 404


@pytest.mark.parametrize("reason", ["membership", "logout", "disconnect"])
def test_sse_rechecks_session_and_membership_between_polls(jobs_api, reason):
    try:
        from app.jobs.routes import event_stream
    except ImportError:
        pytest.fail("Persisted SSE stream missing")
    client, engine, _, _, app = jobs_api
    org, project_id, job, actor = setup_job(jobs_api)
    raw = client.cookies.get("tender_session")
    disconnected = [False]
    class Request:
        async def is_disconnected(self):
            return disconnected[0]
    async def read():
        stream = event_stream(Request(), app, org, project_id, job, raw, 0, interval=0.001)
        assert "heartbeat" in await anext(stream)
        if reason == "membership":
            with tenant_transaction(engine, org) as db:
                db.delete(db.scalar(select(Membership).where(Membership.user_id == actor)))
        elif reason == "logout":
            app.state.auth.logout(raw)
        else:
            disconnected[0] = True
        with pytest.raises(StopAsyncIteration):
            await anext(stream)
    asyncio.run(read())
    from app.workspace.models import Job
    with tenant_transaction(engine, org) as db:
        assert db.get(Job, job).status == "queued"


def test_job_cors_allows_request_id_and_event_replay_headers(jobs_api):
    client, *_ = jobs_api
    response = client.options("/api/organizations", headers={"Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "Idempotency-Key,Last-Event-ID,X-CSRF-Token"})
    assert response.status_code == 200
