"""Fair bounded orphan sweeps, including already-cleaned attempts' delayed PUTs."""
from alembic import op
import sqlalchemy as sa

revision='0008_upload_reconciliation'
down_revision='0007_encrypted_packages'
branch_labels=depends_on=None


def guard(allow_cleanup):
    subtraction=" - 'status' - 'cleanup_attempts' - 'last_cleanup_at'" if allow_cleanup else " - 'status'"
    op.execute(f"""CREATE OR REPLACE FUNCTION protect_upload_attempt_identity() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      IF TG_OP = 'DELETE' OR (to_jsonb(OLD){subtraction}) IS DISTINCT FROM (to_jsonb(NEW){subtraction}) OR
        (OLD.status = 'committed' AND to_jsonb(OLD) IS DISTINCT FROM to_jsonb(NEW)) THEN
        RAISE EXCEPTION 'Upload attempt identity is immutable' USING ERRCODE = '23514';
      END IF; RETURN NEW;
    END $$;""")


def upgrade():
    with op.batch_alter_table('upload_attempts',table_args=(sa.CheckConstraint(
        "status IN ('uploading','committed','abandoned','cleaned')",name='ck_upload_attempt_state'),)) as batch:
        batch.add_column(sa.Column('cleanup_attempts',sa.Integer(),nullable=False,server_default='0'))
        batch.add_column(sa.Column('last_cleanup_at',sa.DateTime(timezone=True)))
        batch.create_check_constraint('ck_upload_cleanup_attempts','cleanup_attempts >= 0')
    op.create_index('ix_upload_attempts_cleanup_scan','upload_attempts',['organization_id','cleanup_attempts','created_at','id'])
    if op.get_context().dialect.name=='postgresql':
        guard(True)


def downgrade():
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM users WHERE email IS NULL')):
        raise RuntimeError('Cannot downgrade while phone-only accounts exist')
    if op.get_bind().scalar(sa.text('SELECT count(*) FROM upload_attempts')):
        raise RuntimeError('Cannot downgrade while upload ownership exists')
    if op.get_context().dialect.name=='postgresql':
        guard(False)
    op.drop_index('ix_upload_attempts_cleanup_scan',table_name='upload_attempts')
    with op.batch_alter_table('upload_attempts') as batch:
        batch.drop_constraint('ck_upload_cleanup_attempts',type_='check')
        batch.drop_column('last_cleanup_at');batch.drop_column('cleanup_attempts')
