import os

import pytest
from sqlalchemy import text


def test_runtime_startup_requires_migrations_even_in_explicit_sqlite_test_mode():
    from app.workspace.database import create_engine, verify_runtime_database
    engine = create_engine("sqlite:///:memory:", allow_sqlite_for_tests=True)
    with pytest.raises(RuntimeError, match="migration"):
        verify_runtime_database(engine, allow_sqlite_for_tests=True)
    engine.dispose()


def test_runtime_startup_cannot_silently_allow_sqlite():
    from app.workspace.database import create_engine, verify_runtime_database
    engine = create_engine("sqlite:///:memory:", allow_sqlite_for_tests=True)
    with pytest.raises(RuntimeError, match="PostgreSQL"):
        verify_runtime_database(engine)
    engine.dispose()


def test_runtime_startup_accepts_migrated_non_owner_and_rejects_owner():
    from app.workspace.database import create_engine, verify_runtime_database
    owner_url = os.environ.get("WORKSPACE_TEST_DATABASE_URL")
    runtime_url = os.environ.get("WORKSPACE_TEST_RUNTIME_DATABASE_URL")
    if not owner_url or not runtime_url:
        pytest.skip("Dedicated PostgreSQL owner/runtime test URLs not configured")
    owner, runtime = create_engine(owner_url), create_engine(runtime_url)
    try:
        verify_runtime_database(runtime)
        with pytest.raises(RuntimeError, match="restricted"):
            verify_runtime_database(owner)
        with runtime.connect() as connection:
            assert connection.scalar(text("SELECT current_setting('app.organization_id', true)")) in (None, "")
    finally:
        owner.dispose()
        runtime.dispose()
