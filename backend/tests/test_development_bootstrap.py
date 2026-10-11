import importlib.util
import os
from pathlib import Path

import pytest


def bootstrap_module():
    path = Path(__file__).resolve().parents[2] / "scripts" / "dev_services.py"
    spec = importlib.util.spec_from_file_location("dev_services_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_helper_uses_owner_and_restores_runtime_environment(monkeypatch):
    module = bootstrap_module()
    owner = "postgresql+psycopg://owner:private-migration-password@db/tenders"
    monkeypatch.setenv("DATABASE_URL", "runtime-url-before")
    observed = []

    def upgrade(config, revision):
        observed.append((os.environ["DATABASE_URL"], revision, config.get_main_option("script_location")))

    monkeypatch.setattr("alembic.command.upgrade", upgrade)
    module.migrate_workspace({"MIGRATION_DATABASE_URL": owner, "DATABASE_URL": "runtime-must-not-migrate"})
    assert len(observed) == 1
    assert observed[0][:2] == (owner, "head")
    assert Path(observed[0][2]) == module.ROOT / "backend" / "migrations"
    assert os.environ["DATABASE_URL"] == "runtime-url-before"


def test_migration_failure_restores_environment_and_masks_private_errors(monkeypatch):
    module = bootstrap_module()
    monkeypatch.delenv("DATABASE_URL", raising=False)

    def fail(*_):
        raise RuntimeError("private-migration-password")

    monkeypatch.setattr("alembic.command.upgrade", fail)
    with pytest.raises(RuntimeError, match="Migration failed") as error:
        module.migrate_workspace({"MIGRATION_DATABASE_URL": "postgresql://owner:private-migration-password@db/tenders"})
    assert "private-migration-password" not in str(error.value)
    assert "DATABASE_URL" not in os.environ


def test_migration_helper_requires_explicit_owner_connection(monkeypatch):
    module = bootstrap_module()
    monkeypatch.setenv("DATABASE_URL", "runtime-url-before")
    with pytest.raises(ValueError, match="MIGRATION_DATABASE_URL"):
        module.migrate_workspace({"DATABASE_URL": "runtime-must-not-migrate"})
    assert os.environ["DATABASE_URL"] == "runtime-url-before"
