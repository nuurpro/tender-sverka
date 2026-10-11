"""Initial normalized workspace, tenant isolation and immutable history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_workspace"
down_revision = None
branch_labels = None
depends_on = None

TENANT_TABLES = (
    "memberships", "projects", "participants", "package_revisions", "documents",
    "checklist_versions", "requirements", "comparison_runs", "jobs", "outbox_events", "audit_events",
)


def upgrade():
    # Frozen initial schema.
    op.create_table('organizations',
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('email', sa.String(length=320), nullable=False),
    sa.Column('password_hash', sa.Text(), nullable=True),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('email')
    )
    op.create_table('memberships',
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('role', sa.String(length=16), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("role IN ('admin','specialist','viewer')"),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'user_id')
    )
    op.create_index(op.f('ix_memberships_organization_id'), 'memberships', ['organization_id'], unique=False)
    op.create_table('projects',
    sa.Column('mode', sa.String(length=16), nullable=False),
    sa.Column('lot_name', sa.String(length=500), nullable=False),
    sa.Column('customer', sa.String(length=500), nullable=True),
    sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
    sa.Column('timezone', sa.String(length=64), nullable=False),
    sa.Column('report_language', sa.String(length=8), nullable=False),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("mode IN ('supplier','buyer')"),
    sa.CheckConstraint("report_language IN ('ru','uz','en')"),
    sa.CheckConstraint('revision >= 1'),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_index(op.f('ix_projects_organization_id'), 'projects', ['organization_id'], unique=False)
    op.create_table('audit_events',
    sa.Column('project_id', sa.String(length=36), nullable=True),
    sa.Column('actor_id', sa.String(length=36), nullable=False),
    sa.Column('action', sa.String(length=64), nullable=False),
    sa.Column('entity_type', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('before', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('after', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_index(op.f('ix_audit_events_organization_id'), 'audit_events', ['organization_id'], unique=False)
    op.create_index(op.f('ix_audit_events_project_id'), 'audit_events', ['project_id'], unique=False)
    op.create_table('checklist_versions',
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('parent_version_id', sa.String(length=36), nullable=True),
    sa.Column('revision', sa.Integer(), nullable=False),
    sa.Column('approved_by', sa.String(length=36), nullable=True),
    sa.Column('approved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status != 'approved' OR (approved_by IS NOT NULL AND approved_at IS NOT NULL)"),
    sa.CheckConstraint("status IN ('draft','approved')"),
    sa.CheckConstraint('version >= 1 AND revision >= 1'),
    sa.ForeignKeyConstraint(['approved_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'project_id', 'parent_version_id'], ['checklist_versions.organization_id', 'checklist_versions.project_id', 'checklist_versions.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'version')
    )
    op.create_index(op.f('ix_checklist_versions_organization_id'), 'checklist_versions', ['organization_id'], unique=False)
    op.create_index(op.f('ix_checklist_versions_project_id'), 'checklist_versions', ['project_id'], unique=False)
    op.create_table('jobs',
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('stage', sa.String(length=64), nullable=False),
    sa.Column('input_hash', sa.String(length=64), nullable=False),
    sa.Column('idempotency_key', sa.String(length=255), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('progress', sa.Integer(), nullable=False),
    sa.Column('error', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('lease_owner', sa.String(length=255), nullable=True),
    sa.Column('lease_expires_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('attempt >= 0 AND progress >= 0 AND progress <= 100'),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'idempotency_key')
    )
    op.create_index(op.f('ix_jobs_organization_id'), 'jobs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_jobs_project_id'), 'jobs', ['project_id'], unique=False)
    op.create_index(op.f('ix_jobs_status'), 'jobs', ['status'], unique=False)
    op.create_table('participants',
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('slot', sa.Integer(), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('slot BETWEEN 1 AND 5'),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'slot')
    )
    op.create_index(op.f('ix_participants_organization_id'), 'participants', ['organization_id'], unique=False)
    op.create_index(op.f('ix_participants_project_id'), 'participants', ['project_id'], unique=False)
    op.create_table('comparison_runs',
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('checklist_id', sa.String(length=36), nullable=False),
    sa.Column('package_ids', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('scoring_policy', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('deadline', sa.DateTime(timezone=True), nullable=True),
    sa.Column('provider_config_versions', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'project_id', 'checklist_id'], ['checklist_versions.organization_id', 'checklist_versions.project_id', 'checklist_versions.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_index(op.f('ix_comparison_runs_organization_id'), 'comparison_runs', ['organization_id'], unique=False)
    op.create_index(op.f('ix_comparison_runs_project_id'), 'comparison_runs', ['project_id'], unique=False)
    op.create_table('outbox_events',
    sa.Column('job_id', sa.String(length=36), nullable=False),
    sa.Column('payload', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('published_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'job_id'], ['jobs.organization_id', 'jobs.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'job_id')
    )
    op.create_index(op.f('ix_outbox_events_job_id'), 'outbox_events', ['job_id'], unique=False)
    op.create_index(op.f('ix_outbox_events_organization_id'), 'outbox_events', ['organization_id'], unique=False)
    op.create_table('package_revisions',
    sa.Column('project_id', sa.String(length=36), nullable=False),
    sa.Column('kind', sa.String(length=16), nullable=False),
    sa.Column('participant_id', sa.String(length=36), nullable=True),
    sa.Column('version', sa.Integer(), nullable=False),
    sa.Column('manifest', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('status', sa.String(length=32), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("(kind = 'tender' AND participant_id IS NULL) OR (kind = 'bid' AND participant_id IS NOT NULL)"),
    sa.CheckConstraint("kind IN ('tender','bid')"),
    sa.CheckConstraint('version >= 1'),
    sa.ForeignKeyConstraint(['organization_id', 'project_id', 'participant_id'], ['participants.organization_id', 'participants.project_id', 'participants.id'], ),
    sa.ForeignKeyConstraint(['organization_id', 'project_id'], ['projects.organization_id', 'projects.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'id'),
    sa.UniqueConstraint('organization_id', 'project_id', 'kind', 'participant_id', 'version')
    )
    op.create_index(op.f('ix_package_revisions_organization_id'), 'package_revisions', ['organization_id'], unique=False)
    op.create_index(op.f('ix_package_revisions_project_id'), 'package_revisions', ['project_id'], unique=False)
    op.create_index('uq_tender_package_version', 'package_revisions', ['organization_id', 'project_id', 'version'], unique=True, postgresql_where=sa.text("kind = 'tender'"), sqlite_where=sa.text("kind = 'tender'"))
    op.create_table('requirements',
    sa.Column('checklist_id', sa.String(length=36), nullable=False),
    sa.Column('logical_id', sa.String(length=36), nullable=False),
    sa.Column('display_id', sa.String(length=32), nullable=False),
    sa.Column('scope', sa.String(length=64), nullable=False),
    sa.Column('item', sa.Text(), nullable=True),
    sa.Column('category', sa.String(length=128), nullable=False),
    sa.Column('parameter', sa.Text(), nullable=True),
    sa.Column('condition', sa.Text(), nullable=True),
    sa.Column('importance', sa.String(length=64), nullable=False),
    sa.Column('predicate', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('citations', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('origin', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['organization_id', 'checklist_id'], ['checklist_versions.organization_id', 'checklist_versions.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'checklist_id', 'display_id'),
    sa.UniqueConstraint('organization_id', 'checklist_id', 'logical_id'),
    sa.CheckConstraint("scope IN ('product','participant')"),
    sa.CheckConstraint("importance IN ('mandatory','scored','desirable')"),
    sa.UniqueConstraint('organization_id', 'id')
    )
    op.create_index(op.f('ix_requirements_checklist_id'), 'requirements', ['checklist_id'], unique=False)
    op.create_index(op.f('ix_requirements_organization_id'), 'requirements', ['organization_id'], unique=False)
    op.create_table('documents',
    sa.Column('package_id', sa.String(length=36), nullable=False),
    sa.Column('file_hash', sa.String(length=64), nullable=False),
    sa.Column('object_key', sa.String(length=1024), nullable=False),
    sa.Column('size', sa.Integer(), nullable=False),
    sa.Column('mime_type', sa.String(length=255), nullable=False),
    sa.Column('name', sa.String(length=500), nullable=False),
    sa.Column('organization_id', sa.String(length=36), nullable=False),
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint('size >= 0'),
    sa.ForeignKeyConstraint(['organization_id', 'package_id'], ['package_revisions.organization_id', 'package_revisions.id'], ),
    sa.ForeignKeyConstraint(['organization_id'], ['organizations.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('organization_id', 'id'),
    sa.UniqueConstraint('organization_id', 'package_id', 'object_key')
    )
    op.create_index(op.f('ix_documents_organization_id'), 'documents', ['organization_id'], unique=False)
    op.create_index(op.f('ix_documents_package_id'), 'documents', ['package_id'], unique=False)

    if op.get_context().dialect.name == "postgresql":
        install_postgresql_guards()


def install_postgresql_guards():
    for table in TENANT_TABLES:
        op.execute(f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY')
        op.execute(f'ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY')
        op.execute(f"""CREATE POLICY tenant_isolation ON "{table}"
            USING (organization_id = current_setting('app.organization_id', true))
            WITH CHECK (organization_id = current_setting('app.organization_id', true))""")
    op.execute("""
    CREATE FUNCTION protect_approved_checklist() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') AND OLD.status = 'approved' THEN
            RAISE EXCEPTION 'Approved checklists are immutable' USING ERRCODE = '23514';
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        IF NEW.status = 'approved' THEN
            IF NOT EXISTS (SELECT 1 FROM requirements
                           WHERE checklist_id = NEW.id AND organization_id = NEW.organization_id) OR
               EXISTS (SELECT 1 FROM requirements
                       WHERE checklist_id = NEW.id AND organization_id = NEW.organization_id
                       AND (parameter IS NULL OR btrim(parameter) = '' OR condition IS NULL OR btrim(condition) = '')) THEN
                RAISE EXCEPTION 'Approval requires complete requirements' USING ERRCODE = '23514';
            END IF;
        END IF;
        RETURN NEW;
    END $$;
    """)
    op.execute("""CREATE TRIGGER checklist_immutable BEFORE INSERT OR UPDATE OR DELETE
        ON checklist_versions FOR EACH ROW EXECUTE FUNCTION protect_approved_checklist()""")
    op.execute("""
    CREATE FUNCTION protect_approved_requirements() RETURNS trigger LANGUAGE plpgsql AS $$
    DECLARE frozen boolean;
    BEGIN
        IF TG_OP IN ('UPDATE', 'DELETE') THEN
            SELECT status = 'approved' INTO frozen FROM checklist_versions
            WHERE id = OLD.checklist_id AND organization_id = OLD.organization_id FOR UPDATE;
            IF frozen THEN
                RAISE EXCEPTION 'Approved requirements are immutable' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP IN ('INSERT', 'UPDATE') THEN
            SELECT status = 'approved' INTO frozen FROM checklist_versions
            WHERE id = NEW.checklist_id AND organization_id = NEW.organization_id FOR UPDATE;
            IF frozen THEN
                RAISE EXCEPTION 'Approved requirements are immutable' USING ERRCODE = '23514';
            END IF;
        END IF;
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END $$;
    """)
    op.execute("""CREATE TRIGGER requirement_immutable BEFORE INSERT OR UPDATE OR DELETE
        ON requirements FOR EACH ROW EXECUTE FUNCTION protect_approved_requirements()""")
    op.execute("""
    CREATE FUNCTION protect_uploaded_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR
           (OLD.id, OLD.organization_id, OLD.project_id, OLD.kind, OLD.participant_id, OLD.version, OLD.manifest)
           IS DISTINCT FROM
           (NEW.id, NEW.organization_id, NEW.project_id, NEW.kind, NEW.participant_id, NEW.version, NEW.manifest) THEN
            RAISE EXCEPTION 'Uploaded package manifests are immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """)
    op.execute("""CREATE TRIGGER package_manifest_immutable BEFORE UPDATE OR DELETE
        ON package_revisions FOR EACH ROW EXECUTE FUNCTION protect_uploaded_manifest()""")
    op.execute("""
    CREATE FUNCTION protect_audit_history() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION 'Audit history is append only' USING ERRCODE = '23514';
    END $$;
    """)
    op.execute("""CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE
        ON audit_events FOR EACH ROW EXECUTE FUNCTION protect_audit_history()""")


def downgrade():
    # Frozen initial schema.
    op.drop_index(op.f('ix_documents_package_id'), table_name='documents')
    op.drop_index(op.f('ix_documents_organization_id'), table_name='documents')
    op.drop_table('documents')
    op.drop_index(op.f('ix_requirements_organization_id'), table_name='requirements')
    op.drop_index(op.f('ix_requirements_checklist_id'), table_name='requirements')
    op.drop_table('requirements')
    op.drop_index('uq_tender_package_version', table_name='package_revisions', postgresql_where=sa.text("kind = 'tender'"), sqlite_where=sa.text("kind = 'tender'"))
    op.drop_index(op.f('ix_package_revisions_project_id'), table_name='package_revisions')
    op.drop_index(op.f('ix_package_revisions_organization_id'), table_name='package_revisions')
    op.drop_table('package_revisions')
    op.drop_index(op.f('ix_outbox_events_organization_id'), table_name='outbox_events')
    op.drop_index(op.f('ix_outbox_events_job_id'), table_name='outbox_events')
    op.drop_table('outbox_events')
    op.drop_index(op.f('ix_comparison_runs_project_id'), table_name='comparison_runs')
    op.drop_index(op.f('ix_comparison_runs_organization_id'), table_name='comparison_runs')
    op.drop_table('comparison_runs')
    op.drop_index(op.f('ix_participants_project_id'), table_name='participants')
    op.drop_index(op.f('ix_participants_organization_id'), table_name='participants')
    op.drop_table('participants')
    op.drop_index(op.f('ix_jobs_status'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_project_id'), table_name='jobs')
    op.drop_index(op.f('ix_jobs_organization_id'), table_name='jobs')
    op.drop_table('jobs')
    op.drop_index(op.f('ix_checklist_versions_project_id'), table_name='checklist_versions')
    op.drop_index(op.f('ix_checklist_versions_organization_id'), table_name='checklist_versions')
    op.drop_table('checklist_versions')
    op.drop_index(op.f('ix_audit_events_project_id'), table_name='audit_events')
    op.drop_index(op.f('ix_audit_events_organization_id'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_projects_organization_id'), table_name='projects')
    op.drop_table('projects')
    op.drop_index(op.f('ix_memberships_organization_id'), table_name='memberships')
    op.drop_table('memberships')
    op.drop_table('users')
    op.drop_table('organizations')

    if op.get_context().dialect.name == "postgresql":
        for function in ("protect_approved_checklist", "protect_approved_requirements",
                         "protect_uploaded_manifest", "protect_audit_history"):
            op.execute(f'DROP FUNCTION "{function}"()')
