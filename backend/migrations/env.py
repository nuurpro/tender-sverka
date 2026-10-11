"""Migrations run with the schema owner; runtime must use a non-owner role."""
import os

from alembic import context
from sqlalchemy.engine import make_url

from app.workspace.database import create_engine
from app.workspace.models import Base

config = context.config
database_url = os.environ.get("DATABASE_URL") or config.get_main_option("sqlalchemy.url")
target_metadata = Base.metadata
sqlite_tests = os.environ.get("ALEMBIC_ALLOW_SQLITE_TESTS") == "1"
if make_url(database_url).get_backend_name() != "postgresql" and not (
    sqlite_tests and make_url(database_url).get_backend_name() == "sqlite"
):
    raise ValueError("Migrations require PostgreSQL; SQLite is only allowed by ALEMBIC_ALLOW_SQLITE_TESTS=1")


if context.is_offline_mode():
    context.configure(url=database_url, target_metadata=target_metadata, literal_binds=True,
                      dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url, allow_sqlite_for_tests=sqlite_tests)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()
