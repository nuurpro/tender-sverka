"""Test subprocess only: registered fixtures never imported by deployment."""
import os
import time

from app.config import Settings
from app.jobs import worker
from app.workspace.database import tenant_transaction
from app.workspace.models import Job, Project

settings = Settings()
if settings.environment != "test":
    raise RuntimeError("Fixture subprocess requires explicit test environment")
registry = worker.HandlerRegistry("test")


def fixture(context):
    project_id = context.payload["project_id"]
    def domain_result(db):
        db.get(Project, project_id).revision += 1
    context.checkpoint("fixture-output", "1", {"written": True}, domain_result, progress=50)
    if context.claim.stage == "fixture.wait" and context.claim.attempt == 1:
        time.sleep(60)


for stage in ("fixture.normal", "fixture.wait", "fixture.ack_loss"):
    registry.register_fixture(stage, fixture)

original = worker.execute_message
def lose_ack(engine, settings, registry, message):
    result = original(engine, settings, registry, message)
    if result == "succeeded":
        with tenant_transaction(engine, message["organization_id"]) as db:
            ack_loss = db.get(Job, message["job_id"]).stage == "fixture.ack_loss"
        if ack_loss:
            os._exit(43)  # Commit complete, AMQP task still unacknowledged.
    return result
worker.execute_message = lose_ack
app = worker.create_celery_app(settings, registry=registry)

if __name__ == "__main__":
    app.worker_main(["worker", "--pool=solo", "--concurrency=1", "--loglevel=WARNING",
        "--without-gossip", "--without-mingle", "--without-heartbeat", "--queues=" + settings.job_queue])
