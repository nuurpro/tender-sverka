"""Append-only normalized evidence with durable encrypted object ownership."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0009_normalized_artifacts'
down_revision = '0008_upload_reconciliation'
branch_labels = depends_on = None
JSON = sa.JSON().with_variant(JSONB(), 'postgresql')
TABLES = ('artifact_upload_attempts', 'normalized_artifacts')


def common():
    return [sa.Column('id',sa.String(36),primary_key=True),
        sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('organization_id',sa.String(36),sa.ForeignKey('organizations.id'),nullable=False),
        sa.Column('project_id',sa.String(36),nullable=False),
        sa.Column('job_id',sa.String(36),nullable=False),
        sa.Column('package_id',sa.String(36),nullable=False),
        sa.Column('document_id',sa.String(36),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.UniqueConstraint('organization_id','id'),
        sa.ForeignKeyConstraint(['organization_id','project_id'],['projects.organization_id','projects.id']),
        sa.ForeignKeyConstraint(['organization_id','project_id','job_id'],['jobs.organization_id','jobs.project_id','jobs.id']),
        sa.ForeignKeyConstraint(['organization_id','project_id','package_id'],['package_revisions.organization_id','package_revisions.project_id','package_revisions.id']),
        sa.ForeignKeyConstraint(['organization_id','package_id','document_id'],['documents.organization_id','documents.package_id','documents.id']),
        sa.Column('object_key',sa.String(1024),nullable=False,unique=True),
        sa.Column('content_size',sa.Integer(),nullable=False),
        sa.Column('content_hash',sa.String(64),nullable=False),sa.Column('envelope',JSON,nullable=False)]


def upgrade():
    op.create_table(TABLES[0],*common(),
        sa.Column('artifact_id',sa.String(36),nullable=False,unique=True),
        sa.Column('attempt_token',sa.String(64),nullable=False,unique=True),
        sa.Column('lease_token',sa.String(64),nullable=False),
        sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),
        sa.Column('cleanup_attempts',sa.Integer(),nullable=False,server_default='0'),
        sa.Column('last_cleanup_at',sa.DateTime(timezone=True)),
        sa.UniqueConstraint('organization_id','project_id','id'),
        sa.CheckConstraint("status IN ('uploading','committed','abandoned','cleaned')",name='ck_artifact_upload_state'),
        sa.CheckConstraint('cleanup_attempts >= 0 AND content_size > 0 AND content_size <= 16777216',name='ck_artifact_upload_budget'))
    op.create_table(TABLES[1],*common(),
        sa.Column('attempt_id',sa.String(36),nullable=False),
        sa.Column('file_hash',sa.String(64),nullable=False),sa.Column('profile_hash',sa.String(64),nullable=False),
        sa.Column('profile',JSON,nullable=False),sa.Column('cache_key',sa.String(64),nullable=False),
        sa.Column('cache_eligible',sa.Boolean(),nullable=False),sa.Column('status',sa.String(16),nullable=False),
        sa.Column('format',sa.String(16),nullable=False),sa.Column('block_count',sa.Integer(),nullable=False),
        sa.Column('physical_pages',sa.Integer()),sa.Column('coverage_summary',JSON,nullable=False),
        sa.ForeignKeyConstraint(['organization_id','project_id','attempt_id'],['artifact_upload_attempts.organization_id','artifact_upload_attempts.project_id','artifact_upload_attempts.id']),
        sa.UniqueConstraint('organization_id','job_id','document_id'),
        sa.CheckConstraint("status IN ('complete','partial','unavailable')",name='ck_normalized_artifact_state'),
        sa.CheckConstraint('block_count >= 0 AND content_size > 0 AND content_size <= 16777216',name='ck_normalized_artifact_budget'))
    for table in TABLES:
        op.create_index('ix_'+table+'_organization_id',table,['organization_id'])
    op.create_index('ix_artifact_upload_attempts_job_id',TABLES[0],['job_id'])
    op.create_index('ix_artifact_attempt_cleanup',TABLES[0],['organization_id','cleanup_attempts','created_at','id'])
    op.create_index('ix_normalized_artifact_cache',TABLES[1],['organization_id','project_id','cache_key','cache_eligible'])
    if op.get_context().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (organization_id=current_setting('app.organization_id',true)) WITH CHECK (organization_id=current_setting('app.organization_id',true))")
        op.execute(f"CREATE CONSTRAINT TRIGGER {table}_commit_lease AFTER INSERT OR UPDATE ON {table} DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION verify_job_commit_lease()")
    op.execute('CREATE TRIGGER normalized_artifact_immutable BEFORE UPDATE OR DELETE ON normalized_artifacts FOR EACH ROW EXECUTE FUNCTION protect_package_auxiliary()')
    op.execute("""CREATE FUNCTION protect_artifact_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP='DELETE' OR (to_jsonb(OLD)-'status'-'cleanup_attempts'-'last_cleanup_at') IS DISTINCT FROM
         (to_jsonb(NEW)-'status'-'cleanup_attempts'-'last_cleanup_at') OR
         (OLD.status='committed' AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)) THEN
        RAISE EXCEPTION 'Artifact ownership is immutable' USING ERRCODE='23514';
      END IF;
      IF NEW.status='committed' AND NOT EXISTS(SELECT 1 FROM normalized_artifacts a
          WHERE a.organization_id=NEW.organization_id AND a.attempt_id=NEW.id AND a.id=NEW.artifact_id) THEN
        RAISE EXCEPTION 'Artifact publication is incomplete' USING ERRCODE='23514';
      END IF;
      RETURN NEW;
    END $$""")
    op.execute('CREATE TRIGGER artifact_attempt_identity BEFORE UPDATE OR DELETE ON artifact_upload_attempts FOR EACH ROW EXECUTE FUNCTION protect_artifact_attempt()')
    op.execute("""CREATE FUNCTION verify_artifact_publication() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE j jobs; a artifact_upload_attempts; role_value text; active_user boolean;
    BEGIN
      SELECT * INTO j FROM jobs WHERE organization_id=NEW.organization_id AND id=NEW.job_id FOR UPDATE;
      IF NOT FOUND OR j.project_id<>NEW.project_id OR j.stage<>'package.normalize' OR j.status<>'running' OR
         j.lease_token IS DISTINCT FROM nullif(current_setting('app.job_fence_token',true),'') OR
         j.id IS DISTINCT FROM nullif(current_setting('app.job_fence_job',true),'') OR
         j.lease_expires_at<=clock_timestamp() OR j.payload->>'package_id' IS DISTINCT FROM NEW.package_id OR
         j.payload->>'actor_id' IS DISTINCT FROM NEW.actor_id THEN
        RAISE EXCEPTION 'Artifact job fence is invalid' USING ERRCODE='23514';
      END IF;
      PERFORM 1 FROM organizations WHERE id=NEW.organization_id FOR UPDATE;
      SELECT role INTO role_value FROM memberships WHERE organization_id=NEW.organization_id AND user_id=NEW.actor_id FOR UPDATE;
      SELECT active INTO active_user FROM users WHERE id=NEW.actor_id FOR SHARE;
      IF role_value IS NULL OR role_value='viewer' OR NOT coalesce(active_user,false) THEN
        RAISE EXCEPTION 'Artifact actor is no longer authorized' USING ERRCODE='23514';
      END IF;
      IF TG_TABLE_NAME='artifact_upload_attempts' THEN
        IF NEW.lease_token IS DISTINCT FROM j.lease_token OR NEW.status<>'uploading' OR
           NEW.expires_at<=clock_timestamp() OR NEW.object_key IS DISTINCT FROM
           ('org/'||NEW.organization_id||'/project/'||NEW.project_id||'/artifact-attempt/'||NEW.attempt_token||'/'||NEW.artifact_id) THEN
          RAISE EXCEPTION 'Artifact attempt is invalid' USING ERRCODE='23514';
        END IF;
      ELSE
        SELECT * INTO a FROM artifact_upload_attempts WHERE organization_id=NEW.organization_id AND id=NEW.attempt_id FOR UPDATE;
        IF NOT FOUND OR a.status<>'uploading' OR a.expires_at<=clock_timestamp() OR a.lease_token IS DISTINCT FROM j.lease_token OR
           a.artifact_id<>NEW.id OR a.job_id<>NEW.job_id OR a.package_id<>NEW.package_id OR a.document_id<>NEW.document_id OR
           a.actor_id<>NEW.actor_id OR a.object_key<>NEW.object_key OR a.content_hash<>NEW.content_hash OR
           a.content_size<>NEW.content_size OR a.envelope IS DISTINCT FROM NEW.envelope OR
           NEW.profile IS DISTINCT FROM j.payload->'profile' OR NEW.profile_hash IS DISTINCT FROM j.payload->>'profile_hash' OR
           NOT EXISTS(SELECT 1 FROM documents d WHERE d.organization_id=NEW.organization_id AND d.package_id=NEW.package_id
              AND d.id=NEW.document_id AND d.file_hash=NEW.file_hash) THEN
          RAISE EXCEPTION 'Artifact provenance is invalid' USING ERRCODE='23514';
        END IF;
      END IF;
      RETURN NEW;
    END $$""")
    for table in TABLES:
        op.execute(f'CREATE TRIGGER {table}_publication BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION verify_artifact_publication()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM users WHERE email IS NULL')):
        raise RuntimeError('Cannot downgrade while phone-only accounts exist')
    if any(op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table}')) for table in TABLES):
        raise RuntimeError('Cannot downgrade while normalized evidence or ownership exists')
    for table in reversed(TABLES):
        op.drop_table(table)
    if op.get_context().dialect.name == 'postgresql':
        op.execute('DROP FUNCTION verify_artifact_publication()')
        op.execute('DROP FUNCTION protect_artifact_attempt()')
