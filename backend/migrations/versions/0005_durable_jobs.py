"""Fenced leases, immutable inputs, persisted progress and retry request identity."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005_durable_jobs"
down_revision = "0004_phone_auth"
branch_labels = None
depends_on = None


def upgrade():
    # SQLite's explicit test adapter cannot reflect unnamed CHECK constraints.
    with op.batch_alter_table("jobs", table_args=(sa.CheckConstraint(
            "attempt >= 0 AND progress >= 0 AND progress <= 100", name="ck_jobs_bounds"),)) as batch:
        batch.add_column(sa.Column("lease_token", sa.String(64)))
        batch.add_column(sa.Column("event_sequence", sa.Integer(), nullable=False, server_default="0"))
        batch.create_unique_constraint("uq_jobs_project_identity", ["organization_id", "project_id", "id"])
        batch.create_check_constraint("ck_jobs_state", "status IN ('queued','running','succeeded','failed','cancelled')")
    with op.batch_alter_table("outbox_events") as batch:
        batch.add_column(sa.Column("claim_token", sa.String(64)))
        batch.add_column(sa.Column("claim_expires_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("available_at", sa.DateTime(timezone=True)))
        batch.add_column(sa.Column("publish_attempt", sa.Integer(), nullable=False, server_default="0"))
        batch.add_column(sa.Column("error_code", sa.String(64)))
    json_type = sa.JSON().with_variant(JSONB(), "postgresql")
    def common():
        return [sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("organization_id", sa.String(36), sa.ForeignKey("organizations.id"), nullable=False),
            sa.Column("project_id", sa.String(36), nullable=False),
            sa.Column("job_id", sa.String(36), nullable=False)]
    def constraints():
        return [sa.UniqueConstraint("organization_id", "id"),
            sa.ForeignKeyConstraint(["organization_id", "project_id"], ["projects.organization_id", "projects.id"]),
            sa.ForeignKeyConstraint(["organization_id", "project_id", "job_id"], ["jobs.organization_id", "jobs.project_id", "jobs.id"])]
    op.create_table("job_checkpoints", *common(),
        sa.Column("step", sa.String(64), nullable=False),
        sa.Column("input_version_hash", sa.String(64), nullable=False),
        sa.Column("result", json_type, nullable=False), *constraints(),
        sa.UniqueConstraint("organization_id", "job_id", "step"))
    op.create_table("job_progress_events", *common(),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("data", json_type, nullable=False), *constraints(),
        sa.UniqueConstraint("organization_id", "job_id", "sequence"), sa.CheckConstraint("sequence >= 1"))
    op.create_table("job_operations", *common(),
        sa.Column("actor_id", sa.String(36), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("request_key", sa.String(255), nullable=False),
        sa.Column("operation", sa.String(16), nullable=False),
        sa.Column("payload_digest", sa.String(64), nullable=False),
        sa.Column("response", json_type, nullable=False), *constraints(),
        sa.UniqueConstraint("organization_id", "job_id", "request_key"))
    for table in ("job_checkpoints", "job_progress_events", "job_operations"):
        op.create_index("ix_" + table + "_job_id", table, ["job_id"])
        op.create_index("ix_" + table + "_organization_id", table, ["organization_id"])
        if op.get_context().dialect.name == "postgresql":
            op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
            op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
            op.execute(f"""CREATE POLICY tenant_isolation ON {table}
                USING (organization_id = current_setting('app.organization_id', true))
                WITH CHECK (organization_id = current_setting('app.organization_id', true))""")
    if op.get_context().dialect.name == "postgresql":
        op.execute("""CREATE FUNCTION protect_job_input() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF (OLD.id, OLD.organization_id, OLD.project_id, OLD.stage, OLD.input_hash, OLD.payload, OLD.idempotency_key)
                IS DISTINCT FROM
                (NEW.id, NEW.organization_id, NEW.project_id, NEW.stage, NEW.input_hash, NEW.payload, NEW.idempotency_key) THEN
                RAISE EXCEPTION 'Job inputs are immutable' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END $$""")
        op.execute("CREATE TRIGGER job_input_immutable BEFORE UPDATE ON jobs FOR EACH ROW EXECUTE FUNCTION protect_job_input()")


def downgrade():
    # Preflight the older auth downgrade before SQLite's nontransactional DDL
    # can remove durable tables during a multi-revision downgrade request.
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM users WHERE email IS NULL")):
        raise RuntimeError("Cannot downgrade while phone-only accounts exist")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM jobs")):
        raise RuntimeError("Cannot downgrade while durable jobs exist")
    if op.get_context().dialect.name == "postgresql":
        op.execute("DROP TRIGGER job_input_immutable ON jobs")
        op.execute("DROP FUNCTION protect_job_input()")
    for table in ("job_operations", "job_progress_events", "job_checkpoints"):
        op.drop_table(table)
    with op.batch_alter_table("outbox_events") as batch:
        for column in ("claim_token", "claim_expires_at", "available_at", "publish_attempt", "error_code"):
            batch.drop_column(column)
    with op.batch_alter_table("jobs") as batch:
        batch.drop_constraint("ck_jobs_state", type_="check")
        batch.drop_constraint("uq_jobs_project_identity", type_="unique")
        batch.drop_column("lease_token")
        batch.drop_column("event_sequence")
