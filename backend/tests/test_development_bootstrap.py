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


def test_api_helper_uses_runtime_credentials_only_and_never_exposes_them_in_arguments(monkeypatch):
    module = bootstrap_module()
    monkeypatch.setenv("APP_SMS_PROVIDER", "http")
    monkeypatch.setenv("MIGRATION_DATABASE_URL", "inherited-owner-password")
    monkeypatch.setenv("POSTGRES_PASSWORD", "inherited-admin-password")
    observed = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: observed.append((command, kwargs)))
    module.run_api({"DATABASE_URL": "runtime-password", "APP_SESSION_SECRET": "private-session-key",
        "MIGRATION_DATABASE_URL": "private-owner-password", "POSTGRES_PASSWORD": "admin-password",
        "APP_SMS_PROVIDER": "eskiz"},
        port=8100, origin="http://127.0.0.1:3001")
    command, options = observed[0]
    assert "app.full_main:create_app" in command and "--factory" in command
    assert "--no-proxy-headers" in command
    assert str(module.ROOT / "backend") == str(options["cwd"])
    assert options["env"]["DATABASE_URL"] == "runtime-password"
    assert options["env"]["APP_SESSION_SECRET"] == "private-session-key"
    assert options["env"]["APP_ENVIRONMENT"] == "development"
    assert options["env"]["APP_SMS_PROVIDER"] == "mock"
    assert options["env"]["APP_PUBLIC_ORIGIN"] == "http://127.0.0.1:3001"
    assert "MIGRATION_DATABASE_URL" not in options["env"]
    assert "POSTGRES_PASSWORD" not in options["env"]
    assert all("password" not in arg and "private" not in arg for arg in command)


@pytest.mark.parametrize("origin", ["https://public.example", "http://127.0.0.1:3001/path", "http://user:password@localhost"])
def test_local_api_helper_rejects_non_loopback_or_non_origin_configuration(monkeypatch, origin):
    module = bootstrap_module()
    calls = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(ValueError, match="loopback"):
        module.run_api({}, origin=origin)
    assert calls == []


def test_initialize_adds_missing_job_signing_key_without_rotating_existing_secrets(tmp_path, monkeypatch):
    module = bootstrap_module()
    infrastructure = tmp_path / "infrastructure"
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "INFRA", infrastructure)
    monkeypatch.setattr(module, "ENV", infrastructure / ".env")
    first = module.initialize()
    assert len(first["APP_JOB_SIGNING_SECRET"]) >= 32
    saved = {key: value for key, value in first.items() if key != "APP_JOB_SIGNING_SECRET"}
    module.ENV.write_text("".join(f"{key}={value}\n" for key, value in saved.items()), encoding="utf-8")
    upgraded = module.initialize()
    assert len(upgraded["APP_JOB_SIGNING_SECRET"]) >= 32
    assert {key: value for key, value in upgraded.items() if key != "APP_JOB_SIGNING_SECRET"} == saved
    assert module.initialize() == upgraded


def test_verify_helper_requires_explicit_broker_opt_in_and_keeps_urls_out_of_arguments(monkeypatch):
    module = bootstrap_module()
    monkeypatch.setenv("WORKSPACE_TEST_BROKER_URL", "inherited-broker-must-not-be-used")
    observed = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: observed.append((command, kwargs)))
    values = {"MIGRATION_DATABASE_URL": "private-owner", "DATABASE_URL": "private-runtime",
              "CELERY_BROKER_URL": "private-broker"}
    module.verify_workspace(values)
    inherited_transport_removed = "WORKSPACE_TEST_BROKER_URL" not in observed[0][1]["env"]
    assert inherited_transport_removed
    module.verify_workspace(values, broker=True)
    command, options = observed[1]
    assert options["env"]["WORKSPACE_TEST_BROKER_URL"] == "private-broker"
    assert options["env"]["WORKSPACE_TEST_DATABASE_URL"] == "private-owner"
    assert options["env"]["WORKSPACE_TEST_RUNTIME_DATABASE_URL"] == "private-runtime"
    assert all("private" not in argument for argument in command)


def test_verify_with_broker_rejects_missing_transport_configuration_before_running(monkeypatch):
    module = bootstrap_module()
    observed = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: observed.append(args))
    with pytest.raises(ValueError, match="CELERY_BROKER_URL"):
        module.verify_workspace({"MIGRATION_DATABASE_URL": "owner", "DATABASE_URL": "runtime"}, broker=True)
    assert observed == []


@pytest.mark.parametrize("action, module_name", [("worker", "app.jobs.worker"), ("dispatcher", "app.jobs.dispatcher")])
def test_job_launcher_uses_only_runtime_secrets_and_explicit_development_settings(monkeypatch, action, module_name):
    module = bootstrap_module()
    for key in ("MIGRATION_DATABASE_URL", "WORKSPACE_TEST_DATABASE_URL", "GARAGE_ADMIN_TOKEN"):
        monkeypatch.setenv(key, "inherited-private-owner")
    observed = []
    monkeypatch.setattr(module.subprocess, "run", lambda command, **kwargs: observed.append((command, kwargs)))
    module.run_jobs({"DATABASE_URL": "private-runtime", "APP_SESSION_SECRET": "private-session",
        "APP_JOB_SIGNING_SECRET": "private-job-signing-key", "CELERY_BROKER_URL": "private-broker",
        "MIGRATION_DATABASE_URL": "private-owner", "GARAGE_ADMIN_TOKEN": "private-admin"}, action=action)
    command, options = observed[0]
    assert module_name in command
    if action == "worker":
        assert "--pool=solo" in command and "--concurrency=1" in command
    assert options["env"]["DATABASE_URL"] == "private-runtime"
    assert options["env"]["APP_JOB_SIGNING_SECRET"] == "private-job-signing-key"
    assert options["env"]["APP_ENVIRONMENT"] == "development"
    for key in ("MIGRATION_DATABASE_URL", "WORKSPACE_TEST_DATABASE_URL", "GARAGE_ADMIN_TOKEN"):
        removed = key not in options["env"]
        assert removed
    assert all("private" not in argument for argument in command)


def test_job_launcher_rejects_unknown_action_before_any_process_start(monkeypatch):
    module = bootstrap_module()
    observed = []
    monkeypatch.setattr(module.subprocess, "run", lambda *args, **kwargs: observed.append(args))
    with pytest.raises(ValueError, match="worker or dispatcher"):
        module.run_jobs({}, action="arbitrary.module")
    assert observed == []
