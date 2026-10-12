"""Authorized nested job snapshots/actions and resumable persisted SSE."""
import asyncio
import json
import re
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.dependencies import current_user
from app.full_workspace.dependencies import scoped_repository
from app.full_workspace.schemas import Empty
from app.workspace.database import tenant_transaction
from app.workspace.models import JobProgressEvent, Membership
from app.workspace.repository import NotFound, Repository, ValidationError
from .service import JobService, TERMINAL, public_job

router = APIRouter(prefix="/api/organizations/{organization_id}/projects/{project_id}/jobs")


def nested_job(repo, project_id, job_id):
    repo.get_project(str(project_id))
    job = repo.get_job(str(job_id))
    if job.project_id != str(project_id):
        raise NotFound()
    return job


@router.get("")
def list_jobs(project_id: UUID, repo=Depends(scoped_repository)):
    return [public_job(row) for row in repo.list_jobs(str(project_id))]


@router.get("/{job_id}")
def detail(project_id: UUID, job_id: UUID, repo=Depends(scoped_repository)):
    return public_job(nested_job(repo, project_id, job_id))


@router.post("/{job_id}/retry")
def retry(project_id: UUID, job_id: UUID, body: Empty, request: Request, repo=Depends(scoped_repository)):
    nested_job(repo, project_id, job_id)
    settings = request.app.state.settings
    return JobService(request.app.state.engine, repo.organization_id, max_attempts=settings.job_max_attempts).retry_in(
        repo.session, str(job_id), str(project_id), repo.actor_id, request.headers.get("idempotency-key"), body.model_dump())


@router.post("/{job_id}/cancel")
def cancel(project_id: UUID, job_id: UUID, body: Empty, request: Request, repo=Depends(scoped_repository)):
    nested_job(repo, project_id, job_id)
    return JobService(request.app.state.engine, repo.organization_id).cancel_in(repo.session, str(job_id),
        str(project_id), repo.actor_id, request.headers.get("idempotency-key"), body.model_dump())


def replay_cursor(value, job_id):
    if value is None:
        return 0
    if not isinstance(value, str) or not re.fullmatch(re.escape(job_id) + r":[0-9]{1,12}", value):
        raise ValidationError()
    return int(value.split(":")[1])


def read_events(app, organization_id, project_id, job_id, raw_session, after):
    user = app.state.auth.authenticate(raw_session)
    with tenant_transaction(app.state.engine, organization_id) as db:
        member = db.scalar(select(Membership).where(Membership.organization_id == organization_id,
            Membership.user_id == user.id))
        if member is None:
            raise NotFound()
        job = nested_job(Repository(db, organization_id, user.id), project_id, job_id)
        if after > job.event_sequence:
            raise ValidationError()
        events = list(db.scalars(select(JobProgressEvent).where(JobProgressEvent.organization_id == organization_id,
            JobProgressEvent.job_id == job_id, JobProgressEvent.sequence > after)
            .order_by(JobProgressEvent.sequence).limit(100)))
        return [(e.sequence, e.data) for e in events], job.status


async def event_stream(request, app, organization_id, project_id, job_id, raw_session, after, *, interval=None):
    interval = app.state.settings.job_sse_interval if interval is None else interval
    while not await request.is_disconnected():
        try:
            events, status = await asyncio.to_thread(read_events, app, organization_id, project_id, job_id, raw_session, after)
        except (HTTPException, NotFound, ValidationError, SQLAlchemyError):
            return  # Session/membership revoked; the browser can use a fresh GET.
        for sequence, data in events:
            if await request.is_disconnected():
                return
            after = sequence
            yield f"id: {job_id}:{sequence}\nevent: progress\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
        if status in TERMINAL and len(events) < 100:
            return
        if len(events) == 100:
            continue
        yield ": heartbeat\n\n"
        await asyncio.sleep(interval)


@router.get("/{job_id}/events")
def events(organization_id: UUID, project_id: UUID, job_id: UUID, request: Request, user=Depends(current_user)):
    org, project, job = str(organization_id), str(project_id), str(job_id)
    after = replay_cursor(request.headers.get("last-event-id"), job)
    raw = request.cookies.get(request.app.state.cookie_name)
    read_events(request.app, org, project, job, raw, after)  # Authorize/validate before HTTP headers.
    return StreamingResponse(event_stream(request, request.app, org, project, job, raw, after),
        media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
