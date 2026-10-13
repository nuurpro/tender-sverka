"""Retain failed evidence while retry appends a version for the same source."""
from alembic import op
import sqlalchemy as sa

revision='0010_artifact_retry_versions'
down_revision='0009_normalized_artifacts'
branch_labels=depends_on=None


def upgrade():
    old=next(row for row in sa.inspect(op.get_bind()).get_unique_constraints('normalized_artifacts')
        if row['column_names']==['organization_id','job_id','document_id'])
    convention={'uq':'uq_%(table_name)s_%(column_0_name)s_%(column_1_name)s_%(column_2_name)s'}
    name=old['name'] or 'uq_normalized_artifacts_organization_id_job_id_document_id'
    with op.batch_alter_table('normalized_artifacts',naming_convention=convention) as batch:
        batch.add_column(sa.Column('run_attempt',sa.Integer(),nullable=False,server_default='1'))
        batch.drop_constraint(name,type_='unique')
        batch.create_unique_constraint('uq_normalized_artifact_attempt',['organization_id','job_id','document_id','run_attempt'])
        batch.create_check_constraint('ck_artifact_run_attempt','run_attempt BETWEEN 1 AND 20')
    if op.get_context().dialect.name=='postgresql':
        op.execute("""CREATE FUNCTION verify_artifact_run_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          IF NOT EXISTS(SELECT 1 FROM jobs WHERE organization_id=NEW.organization_id AND id=NEW.job_id
              AND attempt=NEW.run_attempt AND status='running') THEN
            RAISE EXCEPTION 'Artifact attempt version differs from its claimed job' USING ERRCODE='23514';
          END IF;
          RETURN NEW;
        END $$""")
        op.execute('CREATE TRIGGER artifact_run_attempt BEFORE INSERT ON normalized_artifacts FOR EACH ROW EXECUTE FUNCTION verify_artifact_run_attempt()')


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM users WHERE email IS NULL')):
        raise RuntimeError('Cannot downgrade while phone-only accounts exist')
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM normalized_artifacts')):
        raise RuntimeError('Cannot downgrade while normalized evidence exists')
    if op.get_context().dialect.name=='postgresql':
        op.execute('DROP TRIGGER artifact_run_attempt ON normalized_artifacts')
        op.execute('DROP FUNCTION verify_artifact_run_attempt()')
    with op.batch_alter_table('normalized_artifacts') as batch:
        batch.drop_constraint('uq_normalized_artifact_attempt',type_='unique')
        batch.drop_constraint('ck_artifact_run_attempt',type_='check')
        batch.drop_column('run_attempt')
        batch.create_unique_constraint('uq_normalized_artifact_source',['organization_id','job_id','document_id'])
