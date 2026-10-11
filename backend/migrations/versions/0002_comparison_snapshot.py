"""Freeze comparison input snapshots while permitting lifecycle status changes."""
from alembic import op

revision = "0002_comparison_snapshot"
down_revision = "0001_workspace"
branch_labels = None
depends_on = None


def upgrade():
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute("""
    CREATE FUNCTION protect_comparison_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR
           (OLD.id, OLD.organization_id, OLD.project_id, OLD.created_at, OLD.checklist_id,
            OLD.package_ids, OLD.scoring_policy, OLD.deadline, OLD.provider_config_versions)
           IS DISTINCT FROM
           (NEW.id, NEW.organization_id, NEW.project_id, NEW.created_at, NEW.checklist_id,
            NEW.package_ids, NEW.scoring_policy, NEW.deadline, NEW.provider_config_versions) THEN
            RAISE EXCEPTION 'Comparison input snapshots are immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """)
    op.execute("""CREATE TRIGGER comparison_snapshot_immutable BEFORE UPDATE OR DELETE
        ON comparison_runs FOR EACH ROW EXECUTE FUNCTION protect_comparison_snapshot()""")


def downgrade():
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER comparison_snapshot_immutable ON comparison_runs")
    op.execute("DROP FUNCTION protect_comparison_snapshot()")
