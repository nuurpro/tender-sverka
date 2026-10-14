"""Versioned consent and project-bound grounded analysis accounting."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
revision='0011_grounded_analysis'
down_revision='0010_artifact_retry_versions'
branch_labels=depends_on=None
JSON=sa.JSON().with_variant(JSONB(),'postgresql')
TABLES=('organization_ai_policies','analysis_runs','analysis_calls','participant_facts')


def common(project=False):
    cols=[sa.Column('id',sa.String(36),primary_key=True),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.Column('organization_id',sa.String(36),sa.ForeignKey('organizations.id'),nullable=False),sa.UniqueConstraint('organization_id','id')]
    if project:cols += [sa.Column('project_id',sa.String(36),nullable=False),sa.ForeignKeyConstraint(['organization_id','project_id'],['projects.organization_id','projects.id'])]
    return cols


def fk(table,name):
    return sa.ForeignKeyConstraint(['organization_id','project_id',name], [table+'.organization_id',table+'.project_id',table+'.id'])


def upgrade():
    op.create_table(TABLES[0],*common(),sa.Column('version',sa.Integer(),nullable=False),sa.Column('allowed',sa.Boolean(),nullable=False),sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('reason',sa.Text(),nullable=False),sa.UniqueConstraint('organization_id','version'),sa.CheckConstraint('version>=1'))
    op.create_table(TABLES[1],*common(True),sa.Column('job_id',sa.String(36),nullable=False),sa.Column('package_id',sa.String(36),nullable=False),sa.Column('participant_id',sa.String(36)),sa.Column('checklist_id',sa.String(36)),sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),sa.Column('expected_revision',sa.Integer(),nullable=False),sa.Column('policy_version',sa.Integer(),nullable=False),sa.Column('frozen',JSON,nullable=False),sa.Column('cache_key',sa.String(64),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('result',JSON,nullable=False),sa.UniqueConstraint('organization_id','project_id','id'),sa.UniqueConstraint('organization_id','job_id'),fk('jobs','job_id'),fk('package_revisions','package_id'),fk('participants','participant_id'),fk('checklist_versions','checklist_id'),sa.CheckConstraint("status IN ('draft','published')"),sa.CheckConstraint('expected_revision>=1'))
    op.create_table(TABLES[2],*common(True),sa.Column('job_id',sa.String(36),nullable=False),sa.Column('run_id',sa.String(36),nullable=False),sa.Column('sequence',sa.Integer(),nullable=False),sa.Column('chunk',sa.String(64),nullable=False),sa.Column('input_upper',sa.Integer(),nullable=False),sa.Column('output_upper',sa.Integer(),nullable=False),sa.Column('status',sa.String(16),nullable=False),sa.Column('usage',JSON),sa.Column('cost_upper',sa.String(64)),sa.Column('cost_actual',sa.String(64)),sa.Column('error_code',sa.String(64)),fk('jobs','job_id'),fk('analysis_runs','run_id'),sa.UniqueConstraint('organization_id','run_id','sequence'),sa.CheckConstraint('sequence>=1 AND input_upper>=0 AND output_upper>=0'))
    op.create_table(TABLES[3],*common(True),sa.Column('participant_id',sa.String(36),nullable=False),sa.Column('run_id',sa.String(36),nullable=False),sa.Column('logical_id',sa.String(36),nullable=False),sa.Column('data',JSON,nullable=False),fk('participants','participant_id'),fk('analysis_runs','run_id'),sa.UniqueConstraint('organization_id','run_id','logical_id'))
    for table in TABLES:
        op.create_index('ix_'+table+'_organization_id',table,['organization_id'])
    op.create_index('ix_analysis_scope_cache','analysis_runs',['organization_id','project_id','cache_key','status'])
    if op.get_context().dialect.name!='postgresql':return
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (organization_id=current_setting('app.organization_id',true)) WITH CHECK (organization_id=current_setting('app.organization_id',true))")
    op.execute('CREATE TRIGGER ai_policy_immutable BEFORE UPDATE OR DELETE ON organization_ai_policies FOR EACH ROW EXECUTE FUNCTION protect_package_auxiliary()')
    op.execute("""CREATE FUNCTION verify_analysis_actor() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE r analysis_runs; role_value text; consent boolean;
    BEGIN
      IF TG_TABLE_NAME='analysis_runs' THEN r:=NEW;
      ELSE SELECT * INTO r FROM analysis_runs WHERE organization_id=NEW.organization_id AND id=NEW.run_id FOR UPDATE; END IF;
      PERFORM 1 FROM organizations WHERE id=r.organization_id FOR UPDATE;
      SELECT role INTO role_value FROM memberships WHERE organization_id=r.organization_id AND user_id=r.actor_id FOR UPDATE;
      SELECT allowed INTO consent FROM organization_ai_policies WHERE organization_id=r.organization_id ORDER BY version DESC LIMIT 1;
      IF role_value IS NULL OR role_value='viewer' OR NOT coalesce(consent,false) OR NOT EXISTS(SELECT 1 FROM users WHERE id=r.actor_id AND active) THEN
        RAISE EXCEPTION 'Analysis actor or policy is no longer authorized' USING ERRCODE='23514'; END IF;
      IF TG_TABLE_NAME='analysis_calls' OR TG_TABLE_NAME='participant_facts' OR (TG_TABLE_NAME='analysis_runs' AND TG_OP='UPDATE') THEN
        IF NOT EXISTS(SELECT 1 FROM jobs j WHERE j.organization_id=r.organization_id AND j.id=r.job_id AND j.project_id=r.project_id AND j.stage='analysis.extract' AND j.status='running' AND j.payload->>'actor_id'=r.actor_id AND j.lease_token=nullif(current_setting('app.job_fence_token',true),'') AND j.id=nullif(current_setting('app.job_fence_job',true),'') AND j.lease_expires_at>clock_timestamp()) THEN
          RAISE EXCEPTION 'Analysis lease is invalid' USING ERRCODE='23514'; END IF;
      END IF;
      RETURN NEW;
    END $$""")
    for table in TABLES[1:]:op.execute(f'CREATE TRIGGER {table}_actor BEFORE INSERT OR UPDATE ON {table} FOR EACH ROW EXECUTE FUNCTION verify_analysis_actor()')
    for table in ('analysis_runs','analysis_calls'):
        # Starts are authorized API writes; mutations are worker-fenced.
        op.execute(f'CREATE CONSTRAINT TRIGGER {table}_commit_lease AFTER UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verify_job_commit_lease()')


def downgrade():
    if any(op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table}')) for table in TABLES):raise RuntimeError('Cannot downgrade while analysis history exists')
    for table in reversed(TABLES):op.drop_table(table)
    if op.get_context().dialect.name=='postgresql':op.execute('DROP FUNCTION verify_analysis_actor()')
