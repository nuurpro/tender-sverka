"""Explicit engines and a transaction-local PostgreSQL tenant context."""
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine as sqlalchemy_create_engine, event, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from .models import Base, Tenant


def create_engine(database_url: str, *, allow_sqlite_for_tests: bool = False, **options):
    url = make_url(database_url)
    if url.get_backend_name() == "sqlite":
        if not allow_sqlite_for_tests:
            raise ValueError("Production workspace requires PostgreSQL; SQLite needs an explicit test flag")
        engine = sqlalchemy_create_engine(url, **options)
        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")
        return engine
    if url.get_backend_name() != "postgresql":
        raise ValueError("Production workspace requires PostgreSQL")
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    return sqlalchemy_create_engine(url, pool_pre_ping=True, **options)


def verify_runtime_database(engine, *, allow_sqlite_for_tests: bool = False):
    """Fail closed before startup; this function never creates or migrates schema.

    Paths are resolved from this module so validation is independent of the
    process working directory. Error messages do not include connection URLs.
    """
    if engine.dialect.name != "postgresql" and not (
        engine.dialect.name == "sqlite" and allow_sqlite_for_tests
    ):
        raise RuntimeError("Runtime database requires PostgreSQL or the explicit SQLite test flag")
    backend_directory = Path(__file__).resolve().parents[2]
    config = Config(str(backend_directory / "alembic.ini"))
    expected_heads = set(ScriptDirectory.from_config(config).get_heads())
    tenant_tables = {mapper.local_table.name for mapper in Base.registry.mappers
                     if issubclass(mapper.class_, Tenant)}
    try:
        with engine.connect() as connection:
            actual_heads = set(MigrationContext.configure(connection).get_current_heads())
            if actual_heads != expected_heads:
                raise RuntimeError("Database migration revision is not current; run Alembic with the migration role")
            if engine.dialect.name == "sqlite":
                return
            elevated = connection.scalar(text("""SELECT EXISTS (
                SELECT 1 FROM pg_roles
                WHERE (rolsuper OR rolbypassrls)
                AND pg_has_role(current_user, oid, 'MEMBER')
            )"""))
            if elevated:
                raise RuntimeError("Runtime requires a restricted database role without RLS bypass privileges")
            rows = connection.execute(text("""SELECT c.relname, c.relrowsecurity, c.relforcerowsecurity,
                        pg_has_role(current_user, c.relowner, 'MEMBER') AS owner_access
                FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = current_schema()
                AND c.relname = ANY(CAST(:table_names AS text[])) AND c.relkind = 'r'"""),
                {"table_names": sorted(tenant_tables)}).all()
            if {row.relname for row in rows} != tenant_tables:
                raise RuntimeError("Database migration schema is incomplete for tenant tables")
            if any(row.owner_access for row in rows):
                raise RuntimeError("Runtime requires a restricted database role without tenant table ownership or owner-role access")
            if any(not row.relrowsecurity or not row.relforcerowsecurity for row in rows):
                raise RuntimeError("Tenant database tables require RLS and FORCE RLS")
    except SQLAlchemyError:
        raise RuntimeError("Runtime database verification failed; check migrations and restricted role configuration") from None


@contextmanager
def tenant_transaction(engine, organization_id: str):
    """Commit on success, rollback on error; never creates application schema."""
    organization_id = str(UUID(organization_id))
    with Session(engine, expire_on_commit=False) as session, session.begin():
        if engine.dialect.name == "postgresql":
            session.execute(text("SELECT set_config('app.organization_id', :organization_id, true)"),
                            {"organization_id": organization_id})
        yield session
