"""Preserve original document metadata and package creation timestamps."""
from alembic import op

revision = "0003_source_provenance"
down_revision = "0002_comparison_snapshot"
branch_labels = None
depends_on = None


def _replace_package_guard(*, include_created_at):
    columns = ("id", "organization_id", "project_id", "kind", "participant_id", "version", "manifest")
    if include_created_at:
        columns += ("created_at",)
    old_values = ", ".join(f"OLD.{column}" for column in columns)
    new_values = ", ".join(f"NEW.{column}" for column in columns)
    op.execute(f"""
    CREATE OR REPLACE FUNCTION protect_uploaded_manifest() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR ({old_values}) IS DISTINCT FROM ({new_values}) THEN
            RAISE EXCEPTION 'Uploaded package provenance is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """)


def upgrade():
    if op.get_context().dialect.name != "postgresql":
        return
    _replace_package_guard(include_created_at=True)
    op.execute("""
    CREATE FUNCTION protect_original_document() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
        IF TG_OP = 'DELETE' OR
           (OLD.id, OLD.organization_id, OLD.package_id, OLD.file_hash, OLD.object_key,
            OLD.size, OLD.mime_type, OLD.name, OLD.created_at)
           IS DISTINCT FROM
           (NEW.id, NEW.organization_id, NEW.package_id, NEW.file_hash, NEW.object_key,
            NEW.size, NEW.mime_type, NEW.name, NEW.created_at) THEN
            RAISE EXCEPTION 'Original document provenance is immutable' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END $$;
    """)
    op.execute("""CREATE TRIGGER original_document_immutable BEFORE UPDATE OR DELETE
        ON documents FOR EACH ROW EXECUTE FUNCTION protect_original_document()""")


def downgrade():
    if op.get_context().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER original_document_immutable ON documents")
    op.execute("DROP FUNCTION protect_original_document()")
    _replace_package_guard(include_created_at=False)
