"""Deferred PostgreSQL lease deadline guard at the actual transaction commit."""
from alembic import op
import sqlalchemy as sa

revision = "0006_job_commit_fence"
down_revision = "0005_durable_jobs"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "postgresql":
        return  # Explicit SQLite tests use the application flush/commit fence.
    op.execute("""CREATE FUNCTION verify_job_commit_lease() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE guarded_job text;
            written_job text;
            deadline text;
    BEGIN
        guarded_job := nullif(current_setting('app.job_fence_job', true), '');
        IF TG_TABLE_NAME = 'jobs' THEN written_job := NEW.id;
        ELSE written_job := NEW.job_id;
        END IF;
        IF guarded_job IS NOT NULL AND guarded_job = written_job THEN
            deadline := nullif(current_setting('app.job_fence_expiry', true), '');
            IF deadline IS NULL OR
               nullif(current_setting('app.job_fence_token', true), '') IS NULL OR
               NEW.organization_id IS DISTINCT FROM current_setting('app.organization_id', true) OR
               deadline::timestamptz <= clock_timestamp() THEN
                RAISE EXCEPTION 'Job lease expired before commit'
                    USING ERRCODE = '23514', CONSTRAINT = 'job_lease_fence_valid';
            END IF;
        END IF;
        RETURN NEW;
    END $$""")
    for table in ("jobs", "job_checkpoints", "job_progress_events"):
        op.execute(f"""CREATE CONSTRAINT TRIGGER {table}_commit_lease
            AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION verify_job_commit_lease()""")


def downgrade():
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM users WHERE email IS NULL")):
        raise RuntimeError("Cannot downgrade while phone-only accounts exist")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM jobs")):
        raise RuntimeError("Cannot downgrade while durable jobs exist")
    if op.get_context().dialect.name == "postgresql":
        for table in ("jobs", "job_checkpoints", "job_progress_events"):
            op.execute(f"DROP TRIGGER {table}_commit_lease ON {table}")
        op.execute("DROP FUNCTION verify_job_commit_lease()")
