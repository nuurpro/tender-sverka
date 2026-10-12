"""Two-phase SQL ownership around confirmed broker I/O; no network under locks."""
from dataclasses import dataclass
from datetime import timedelta
import secrets

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.workspace.database import tenant_transaction
from app.workspace.models import Job, Organization, OutboxEvent, utcnow
from .messages import envelope, signing_key
from .service import JobService


def confirmed_sender(app):
    """Publish the Celery JSON task through AMQP confirm_select.

    Kombu pyamqp waits for Basic.Ack/Basic.Nack before returning publish when
    confirm_publish is enabled. A failure propagates to the durable outbox.
    """
    from kombu import Producer
    from .worker import TASK_NAME
    if not app.conf.broker_transport_options.get("confirm_publish"):
        raise ValueError("Publisher confirms are required")
    def publish(message):
        with app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=3, interval_start=0, interval_step=1, interval_max=3)
            with Producer(connection) as producer:
                app.send_task(TASK_NAME, args=[message], serializer="json", delivery_mode=2,
                    queue=app.conf.task_default_queue, producer=producer,
                    timeout=5, confirm_timeout=5,
                    retry=app.conf.task_publish_retry, retry_policy=app.conf.task_publish_retry_policy)
    return publish


@dataclass(frozen=True)
class OutboxClaim:
    id: str
    token: str
    message: dict


class OutboxPublisher:
    def __init__(self, engine, secret, publish, *, clock=utcnow, lease_seconds=30):
        signing_key(secret)
        if not 1 <= lease_seconds <= 3600:
            raise ValueError("Publisher lease must be bounded")
        self.engine, self.secret, self.publish, self.clock = engine, secret, publish, clock
        self.lease_seconds = lease_seconds

    def claim(self, organization_id):
        with tenant_transaction(self.engine, organization_id) as db:
            now = self.clock()
            row = db.scalar(select(OutboxEvent).join(Job, (Job.id == OutboxEvent.job_id) &
                (Job.organization_id == OutboxEvent.organization_id)).where(
                OutboxEvent.organization_id == organization_id, OutboxEvent.published_at.is_(None),
                Job.status.in_(["queued", "running"]),
                or_(OutboxEvent.claim_expires_at.is_(None), OutboxEvent.claim_expires_at <= now),
                or_(OutboxEvent.available_at.is_(None), OutboxEvent.available_at <= now))
                .order_by(OutboxEvent.created_at, OutboxEvent.id).limit(1).with_for_update(skip_locked=True, of=OutboxEvent))
            if row is None:
                return None
            job = db.get(Job, row.job_id)
            row.claim_token, row.claim_expires_at = secrets.token_hex(32), now + timedelta(seconds=self.lease_seconds)
            row.publish_attempt += 1
            return OutboxClaim(row.id, row.claim_token, envelope(organization_id, job.id, job.input_hash, self.secret))

    def mark_published(self, organization_id, claim):
        with tenant_transaction(self.engine, organization_id) as db:
            row = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == organization_id,
                OutboxEvent.id == claim.id).with_for_update())
            if row is None or row.claim_token != claim.token or row.claim_expires_at is None or row.claim_expires_at <= self.clock():
                return False
            row.published_at, row.claim_token, row.claim_expires_at, row.error_code = self.clock(), None, None, None
            return True

    def send(self, organization_id, claim):
        try:
            self.publish(claim.message)  # Returns only after broker confirmation.
        except Exception:
            with tenant_transaction(self.engine, organization_id) as db:
                row = db.scalar(select(OutboxEvent).where(OutboxEvent.organization_id == organization_id,
                    OutboxEvent.id == claim.id).with_for_update())
                if row and row.claim_token == claim.token:
                    row.available_at = self.clock() + timedelta(seconds=min(300, 2 ** min(row.publish_attempt, 8)))
                    row.claim_token = row.claim_expires_at = None
                    row.error_code = "broker_unavailable"
            return False
        return self.mark_published(organization_id, claim)

    def dispatch_once(self, *, per_org_limit=100, max_attempts=3, worker_lease_seconds=60):
        # Organization identities are the internal service enumeration boundary.
        # Tenant rows are scanned only in transaction-local tenant contexts.
        with Session(self.engine) as db:
            organizations = list(db.scalars(select(Organization.id).order_by(Organization.id)))
        sent = 0
        for org in organizations:
            service = JobService(self.engine, org, clock=self.clock, max_attempts=max_attempts,
                lease_seconds=worker_lease_seconds)
            service.recover_expired()
            service.recover_undelivered()
            for _ in range(per_org_limit):
                claim = self.claim(org)
                if claim is None:
                    break
                sent += self.send(org, claim)
        return sent
