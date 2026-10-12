"""Standalone bounded recovery/publisher loop: python -m app.jobs.dispatcher."""
import signal
import threading

from app.config import Settings
from app.workspace.database import create_engine, verify_runtime_database
from .publisher import OutboxPublisher, confirmed_sender
from .worker import create_celery_app


def run(settings=None):
    settings = settings or Settings()
    app = create_celery_app(settings)  # Reject missing broker/signing key before DB.
    engine = create_engine(settings.database_url.get_secret_value(), connect_args={"connect_timeout": 5,
        "options": "-c statement_timeout=5000 -c lock_timeout=5000 -c idle_in_transaction_session_timeout=10000"})
    stop = threading.Event()
    for number in (signal.SIGINT, signal.SIGTERM):
        signal.signal(number, lambda *args: stop.set())
    try:
        verify_runtime_database(engine)
        publisher = OutboxPublisher(engine, settings.job_signing_secret.get_secret_value(), confirmed_sender(app),
            lease_seconds=settings.job_publisher_lease_seconds)
        failures = 0
        while not stop.is_set():
            try:
                publisher.dispatch_once(max_attempts=settings.job_max_attempts,
                    worker_lease_seconds=settings.job_lease_seconds)
                failures = 0
            except Exception:
                failures += 1
                # Configuration/schema issues were checked before the loop; SQL
                # outage recovery is bounded per scan without exposing exceptions.
                print("Job dispatcher temporarily unavailable", flush=True)
            stop.wait(min(60, settings.job_dispatch_interval * (2 ** min(failures, 5))))
    finally:
        engine.dispose()
        app.close()


if __name__ == "__main__":
    run()
