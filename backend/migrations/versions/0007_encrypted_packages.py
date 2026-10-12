"""Encrypted originals, upload attempt reconciliation, versioned inspections."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '0007_encrypted_packages'
down_revision = '0006_job_commit_fence'
branch_labels = depends_on = None
JSON = sa.JSON().with_variant(JSONB(), 'postgresql')
TABLES = ('document_key_envelopes', 'upload_intents', 'upload_attempts', 'package_inspections')


def common():
    return [sa.Column('id', sa.String(36), primary_key=True), sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('organization_id', sa.String(36), sa.ForeignKey('organizations.id'), nullable=False),
        sa.Column('project_id', sa.String(36), nullable=False),
        sa.UniqueConstraint('organization_id', 'id'),
        sa.ForeignKeyConstraint(['organization_id','project_id'], ['projects.organization_id','projects.id'])]


def package_fk():
    return sa.ForeignKeyConstraint(['organization_id','project_id','package_id'],
        ['package_revisions.organization_id','package_revisions.project_id','package_revisions.id'])


def upgrade():
    with op.batch_alter_table('documents',table_args=(sa.CheckConstraint('size >= 0',name='ck_documents_size'),)) as batch:
        batch.add_column(sa.Column('relative_path', sa.String(500), nullable=False, server_default=''))
        batch.add_column(sa.Column('provenance_role', sa.String(16), nullable=False, server_default='document'))
        batch.create_unique_constraint('uq_document_package_identity', ['organization_id','package_id','id'])
    op.create_table('document_key_envelopes', *common(), sa.Column('package_id',sa.String(36),nullable=False),
        sa.Column('document_id',sa.String(36),nullable=False), sa.Column('master_key_version',sa.String(64),nullable=False),
        sa.Column('envelope',JSON,nullable=False), package_fk(),
        sa.ForeignKeyConstraint(['organization_id','package_id','document_id'],['documents.organization_id','documents.package_id','documents.id']),
        sa.UniqueConstraint('organization_id','document_id','master_key_version'))
    op.create_table('upload_intents', *common(), sa.Column('idempotency_key',sa.String(255),nullable=False),
        sa.Column('input_digest',sa.String(64),nullable=False), sa.Column('package_id',sa.String(36)),sa.Column('response',JSON),
        sa.UniqueConstraint('organization_id','project_id','id'),
        sa.UniqueConstraint('organization_id','project_id','idempotency_key'),package_fk())
    op.create_table('upload_attempts', *common(),sa.Column('intent_id',sa.String(36),nullable=False),
        sa.Column('actor_id',sa.String(36),sa.ForeignKey('users.id'),nullable=False),
        sa.Column('attempt_token',sa.String(64),nullable=False,unique=True), sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),
        sa.Column('status',sa.String(16),nullable=False),sa.Column('objects',JSON,nullable=False),
        sa.ForeignKeyConstraint(['organization_id','project_id','intent_id'],['upload_intents.organization_id','upload_intents.project_id','upload_intents.id']),
        sa.CheckConstraint("status IN ('uploading','committed','abandoned','cleaned')"))
    op.create_table('package_inspections', *common(),sa.Column('package_id',sa.String(36),nullable=False),
        sa.Column('identity_hash',sa.String(64),nullable=False),sa.Column('parser_version',sa.String(255),nullable=False),
        sa.Column('result',JSON,nullable=False),package_fk(),sa.UniqueConstraint('organization_id','package_id','identity_hash'))
    for table in TABLES:
        op.create_index('ix_'+table+'_organization_id',table,['organization_id'])
    op.create_index('ix_upload_attempts_intent_id','upload_attempts',['intent_id'])
    op.create_index('ix_package_inspections_package_id','package_inspections',['package_id'])
    op.create_index('ix_document_key_envelopes_document_id','document_key_envelopes',['document_id'])
    if op.get_context().dialect.name != 'postgresql':
        return
    for table in TABLES:
        op.execute(f'ALTER TABLE {table} ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE {table} FORCE ROW LEVEL SECURITY')
        op.execute(f"CREATE POLICY tenant_isolation ON {table} USING (organization_id = current_setting('app.organization_id',true)) WITH CHECK (organization_id = current_setting('app.organization_id',true))")
    # Include all new original metadata; wrap rotation appends a separate envelope.
    op.execute("""CREATE OR REPLACE FUNCTION protect_original_document() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW) THEN
        RAISE EXCEPTION 'Original document provenance is immutable' USING ERRCODE = '23514';
      END IF; RETURN NEW;
    END $$;""")
    op.execute("""CREATE FUNCTION protect_package_auxiliary() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW) THEN
        RAISE EXCEPTION 'Package evidence is append-only' USING ERRCODE = '23514';
      END IF; RETURN NEW;
    END $$;""")
    for table in ('document_key_envelopes','package_inspections'):
        op.execute(f'CREATE TRIGGER {table}_immutable BEFORE UPDATE OR DELETE ON {table} FOR EACH ROW EXECUTE FUNCTION protect_package_auxiliary()')
    op.execute("""CREATE FUNCTION protect_upload_attempt_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR (to_jsonb(OLD) - 'status') IS DISTINCT FROM (to_jsonb(NEW) - 'status') OR
        (OLD.status = 'committed' AND NEW.status != OLD.status) THEN
        RAISE EXCEPTION 'Upload attempt identity is immutable' USING ERRCODE = '23514';
      END IF; RETURN NEW;
    END $$;""")
    op.execute('CREATE TRIGGER upload_attempt_identity BEFORE UPDATE OR DELETE ON upload_attempts FOR EACH ROW EXECUTE FUNCTION protect_upload_attempt_identity()')
    op.execute("""CREATE FUNCTION protect_upload_intent_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR (to_jsonb(OLD) - 'package_id' - 'response') IS DISTINCT FROM (to_jsonb(NEW) - 'package_id' - 'response') OR
        (OLD.package_id IS NOT NULL AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)) THEN
        RAISE EXCEPTION 'Upload intent identity is immutable' USING ERRCODE = '23514';
      END IF; RETURN NEW;
    END $$;""")
    op.execute('CREATE TRIGGER upload_intent_identity BEFORE UPDATE OR DELETE ON upload_intents FOR EACH ROW EXECUTE FUNCTION protect_upload_intent_identity()')


def downgrade():
    # SQLite DDL cannot roll back the removed tables when an older revision
    # later refuses a phone-account downgrade. Refuse before touching schema.
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM users WHERE email IS NULL')):
        raise RuntimeError('Cannot downgrade while phone-only accounts exist')
    if any(op.get_bind().scalar(sa.text(f'SELECT count(*) FROM {table}')) for table in TABLES):
        raise RuntimeError('Cannot downgrade while encrypted package evidence or upload ownership exists')
    if op.get_context().dialect.name == 'postgresql':
        for table, trigger in [('upload_intents','upload_intent_identity'),('upload_attempts','upload_attempt_identity')]:
            op.execute(f'DROP TRIGGER {trigger} ON {table}')
        for table in ('document_key_envelopes','package_inspections'):
            op.execute(f'DROP TRIGGER {table}_immutable ON {table}')
        for function in ('protect_package_auxiliary','protect_upload_attempt_identity','protect_upload_intent_identity'):
            op.execute(f'DROP FUNCTION {function}()')
    for table in reversed(TABLES):
        op.drop_table(table)
    with op.batch_alter_table('documents') as batch:
        batch.drop_constraint('uq_document_package_identity',type_='unique')
        batch.drop_column('relative_path'); batch.drop_column('provenance_role')
