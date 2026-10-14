"""Keep grounded inputs/accounting ancestry immutable after creation."""
from alembic import op

revision='0012_analysis_immutability'
down_revision='0011_grounded_analysis'
branch_labels=depends_on=None


def upgrade():
    if op.get_context().dialect.name!='postgresql':return
    op.execute("""CREATE FUNCTION protect_grounded_analysis() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE r analysis_runs;
    BEGIN
      IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'Grounded analysis history is immutable' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME='analysis_runs' THEN
        IF (to_jsonb(NEW)-'status'-'result') IS DISTINCT FROM (to_jsonb(OLD)-'status'-'result')
          OR (OLD.status='published' AND NEW IS DISTINCT FROM OLD)
          OR NEW.status NOT IN ('draft','published')
          OR (OLD.status='draft' AND NEW.status='draft' AND NEW.result IS DISTINCT FROM OLD.result) THEN
          RAISE EXCEPTION 'Frozen analysis inputs/publication cannot change' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='analysis_calls' THEN
        SELECT * INTO r FROM analysis_runs WHERE organization_id=NEW.organization_id AND id=NEW.run_id;
        IF NEW.job_id IS DISTINCT FROM r.job_id THEN
          RAISE EXCEPTION 'Analysis call has another job' USING ERRCODE='23514';
        END IF;
        IF TG_OP='UPDATE' AND ((to_jsonb(NEW)-'status'-'usage'-'cost_actual'-'error_code')
              IS DISTINCT FROM (to_jsonb(OLD)-'status'-'usage'-'cost_actual'-'error_code')
              OR OLD.status<>'reserved' AND NEW IS DISTINCT FROM OLD) THEN
          RAISE EXCEPTION 'Analysis reservation or reconciliation cannot change' USING ERRCODE='23514';
        END IF;
      ELSIF TG_TABLE_NAME='participant_facts' THEN
        SELECT * INTO r FROM analysis_runs WHERE organization_id=NEW.organization_id AND id=NEW.run_id;
        IF NEW.participant_id IS DISTINCT FROM r.participant_id OR r.frozen->>'kind'<>'bid'
          OR TG_OP='UPDATE' AND NEW IS DISTINCT FROM OLD THEN
          RAISE EXCEPTION 'Bid fact provenance cannot change' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER analysis_runs_immutable BEFORE UPDATE OR DELETE ON analysis_runs FOR EACH ROW EXECUTE FUNCTION protect_grounded_analysis()')
    for table in ('analysis_calls','participant_facts'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE INSERT OR UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_grounded_analysis()')


def downgrade():
    if op.get_context().dialect.name!='postgresql':return
    for table in ('analysis_runs','analysis_calls','participant_facts'):
        op.execute(f'DROP TRIGGER {table}_immutable ON {table}')
    op.execute('DROP FUNCTION protect_grounded_analysis()')
