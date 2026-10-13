"""Generate ignored local secrets, and optionally run an isolated Windows PG.

No Docker volume, database, existing configuration or production data is removed.
Use `init`, then `docker compose --env-file infrastructure/.env
-f infrastructure/compose.yaml up -d`. Local PG is a development verification
option, not a replacement for the PostgreSQL 18 container deployment gate.
"""
import argparse
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infrastructure"
ENV = INFRA / ".env"
DOCUMENT_RUNNER_FIELDS = ("DOCUMENT_RUNNER_MODE", "DOCUMENT_RUNTIME_PYTHON",
                          "DOCUMENT_RUNTIME_PYTHON_SHA256", "DOCUMENT_PARSER_CODE_PATH", "DOCUMENT_WSL_DISTRIBUTION")
DOCUMENT_OCR_FIELDS = ("APP_DOCUMENT_OCR_MODE", "APP_DOCUMENT_OCR_MAX_FRAME_PIXELS",
                       "APP_DOCUMENT_OCR_MAX_TOTAL_PIXELS", "APP_DOCUMENT_OCR_MAX_RASTER_PIXELS",
                       "APP_DOCUMENT_OCR_RASTER_DPI", "APP_DOCUMENT_OCR_PAGE_SEGMENTATION",
                       "APP_DOCUMENT_OCR_LOW_CONFIDENCE_REVIEW", "APP_DOCUMENT_OCR_DOCUMENT_SECONDS")


def initialize():
    INFRA.mkdir(exist_ok=True)
    if not ENV.exists():
        values = {key: secrets.token_hex(32) for key in (
            "POSTGRES_PASSWORD", "TENDER_APPLICATION_PASSWORD", "RABBITMQ_PASSWORD",
            "REDIS_PASSWORD", "S3_SECRET_KEY", "APP_SESSION_SECRET", "DOCUMENT_ENCRYPTION_KEY",
            "GARAGE_RPC_SECRET", "GARAGE_ADMIN_TOKEN", "GARAGE_METRICS_TOKEN",
            "APP_JOB_SIGNING_SECRET",
        )}
        values["S3_ACCESS_KEY"] = "GK" + secrets.token_hex(16)
        values["MIGRATION_DATABASE_URL"] = (
            "postgresql+psycopg://tender_owner:" + quote(values["POSTGRES_PASSWORD"], safe="")
            + "@127.0.0.1:55432/tender_sverka"
        )
        values["DATABASE_URL"] = (
            "postgresql+psycopg://tender_app:" + quote(values["TENDER_APPLICATION_PASSWORD"], safe="")
            + "@127.0.0.1:55432/tender_sverka"
        )
        values["CELERY_BROKER_URL"] = "amqp://tender:" + values["RABBITMQ_PASSWORD"] + "@127.0.0.1:5673/tender"
        values["REDIS_URL"] = "redis://:" + values["REDIS_PASSWORD"] + "@127.0.0.1:56379/0"
        values["S3_ENDPOINT_URL"] = "http://127.0.0.1:53900"
        values["S3_REGION"] = "garage"
        values["S3_BUCKET"] = "tender-documents"
        ENV.write_text("".join(f"{k}={v}\n" for k, v in values.items()), encoding="utf-8")
        if os.name != "nt":
            ENV.chmod(0o600)
    values = dict(line.split("=", 1) for line in ENV.read_text(encoding="utf-8").splitlines() if line and not line.startswith("#"))
    if not values.get("APP_JOB_SIGNING_SECRET"):
        values["APP_JOB_SIGNING_SECRET"] = secrets.token_hex(32)
        with ENV.open("a", encoding="utf-8") as existing:
            existing.write("\nAPP_JOB_SIGNING_SECRET=" + values["APP_JOB_SIGNING_SECRET"] + "\n")
    config = INFRA / "garage.local.toml"
    if not config.exists():
        config.write_text(
            'metadata_dir = "/var/lib/garage/meta"\n'
            'data_dir = "/var/lib/garage/data"\n'
            'db_engine = "sqlite"\nreplication_factor = 1\n'
            'rpc_bind_addr = "0.0.0.0:3901"\nrpc_public_addr = "127.0.0.1:3901"\n'
            f'rpc_secret = "{values["GARAGE_RPC_SECRET"]}"\n'
            '[s3_api]\ns3_region = "garage"\napi_bind_addr = "0.0.0.0:3900"\n'
            'root_domain = ".s3.garage.localhost"\n'
            '[admin]\napi_bind_addr = "127.0.0.1:3903"\n'
            f'admin_token = "{values["GARAGE_ADMIN_TOKEN"]}"\n'
            f'metrics_token = "{values["GARAGE_METRICS_TOKEN"]}"\n', encoding="utf-8",
        )
        if os.name != "nt":
            config.chmod(0o600)
    return values


def local_postgres(bin_directory: Path):
    import psycopg
    from psycopg import sql

    config = initialize()
    runtime = ROOT / "backend" / "data" / "local-postgres"
    runtime.mkdir(parents=True, exist_ok=True)
    data = runtime / "cluster"
    binary_suffix = ".exe" if os.name == "nt" else ""
    initdb = bin_directory / ("initdb" + binary_suffix)
    pg_ctl = bin_directory / ("pg_ctl" + binary_suffix)
    if not initdb.is_file() or not pg_ctl.is_file():
        raise SystemExit("PostgreSQL binaries not found at the specified directory")
    if not (data / "PG_VERSION").exists():
        password_file = runtime / "bootstrap-password"
        try:
            password_file.write_text(config["POSTGRES_PASSWORD"], encoding="utf-8")
            if os.name != "nt":
                password_file.chmod(0o600)
            subprocess.run([str(initdb), "-D", str(data), "-U", "tender_owner", "--encoding=UTF8",
                            "--locale=C", "--auth-host=scram-sha-256", "--auth-local=scram-sha-256",
                            "--pwfile=" + str(password_file)], check=True)
        finally:
            password_file.unlink(missing_ok=True)
    status = subprocess.run([str(pg_ctl), "-D", str(data), "status"], capture_output=True)
    if status.returncode:
        subprocess.run([str(pg_ctl), "-D", str(data), "-l", str(runtime / "server.log"),
                        "-o", "-p 55432 -h 127.0.0.1", "-w", "start"], check=True)
    owner_args = dict(host="127.0.0.1", port=55432, user="tender_owner", password=config["POSTGRES_PASSWORD"])
    with psycopg.connect(dbname="postgres", autocommit=True, **owner_args) as connection:
        if not connection.execute("SELECT 1 FROM pg_database WHERE datname='tender_sverka'").fetchone():
            connection.execute('CREATE DATABASE tender_sverka OWNER tender_owner')
        if not connection.execute("SELECT 1 FROM pg_roles WHERE rolname='tender_app'").fetchone():
            connection.execute(sql.SQL(
                'CREATE ROLE tender_app LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS'
            ).format(sql.Literal(config["TENDER_APPLICATION_PASSWORD"])))
    with psycopg.connect(dbname="tender_sverka", autocommit=True, **owner_args) as connection:
        connection.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        connection.execute("GRANT USAGE ON SCHEMA public TO tender_app")
        connection.execute("ALTER DEFAULT PRIVILEGES FOR ROLE tender_owner IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO tender_app")
        connection.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO tender_app")
        version = connection.execute("SHOW server_version").fetchone()[0]
    print(f"Isolated development PostgreSQL {version} ready on 127.0.0.1:55432")


def migrate_workspace(values):
    """Only the explicit owner connection is used for schema changes."""
    from alembic import command
    from alembic.config import Config

    owner = values.get("MIGRATION_DATABASE_URL")
    if not owner:
        raise ValueError("MIGRATION_DATABASE_URL is required; runtime credentials cannot migrate")
    previous = os.environ.get("DATABASE_URL")
    try:
        os.environ["DATABASE_URL"] = owner
        config = Config(str(ROOT / "backend" / "alembic.ini"))
        command.upgrade(config, "head")
    except Exception:
        raise RuntimeError("Migration failed; check service availability and owner configuration") from None
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous


def verify_workspace(values, *, broker=False, storage=False, documents=False):
    """Run integration tests without passing private connection URLs as arguments."""
    if not values.get("MIGRATION_DATABASE_URL") or not values.get("DATABASE_URL"):
        raise ValueError("Owner and runtime connection configuration is required")
    if broker and not values.get("CELERY_BROKER_URL"):
        raise ValueError("CELERY_BROKER_URL is required for real broker verification")
    storage_fields = ("S3_ENDPOINT_URL", "S3_REGION", "S3_BUCKET", "S3_ACCESS_KEY",
                      "S3_SECRET_KEY", "DOCUMENT_ENCRYPTION_KEY")
    if storage and any(not values.get(key) for key in storage_fields):
        raise ValueError("Storage integration configuration is required for actual S3 verification")
    if documents and (values.get("DOCUMENT_RUNNER_MODE") not in {"bubblewrap", "wsl-bubblewrap"}
                      or any(not values.get(key) for key in DOCUMENT_RUNNER_FIELDS)):
        raise ValueError("Document runner configuration is required for actual sandbox verification")
    environment = {**os.environ, "WORKSPACE_TEST_DATABASE_URL": values["MIGRATION_DATABASE_URL"],
                   "WORKSPACE_TEST_RUNTIME_DATABASE_URL": values["DATABASE_URL"]}
    for key in tuple(environment):
        if key.startswith("WORKSPACE_TEST_DOCUMENT_"):
            environment.pop(key, None)
    environment.pop("WORKSPACE_TEST_BROKER_URL", None)
    if broker:
        environment["WORKSPACE_TEST_BROKER_URL"] = values["CELERY_BROKER_URL"]
    for key in storage_fields:
        environment.pop("WORKSPACE_TEST_" + key, None)
        if storage:
            environment["WORKSPACE_TEST_" + key] = values[key]
    environment.pop("DOCUMENT_RUNNER_INTEGRATION", None)
    environment.pop("DOCUMENT_OCR_INTEGRATION", None)
    for key in DOCUMENT_RUNNER_FIELDS:
        if documents:
            environment["WORKSPACE_TEST_" + key] = values[key]
    if documents:
        environment["DOCUMENT_RUNNER_INTEGRATION"] = "1"
        for key in DOCUMENT_OCR_FIELDS:
            if key in values:
                environment["WORKSPACE_TEST_" + key.removeprefix("APP_")] = values[key]
        if values.get("APP_DOCUMENT_OCR_MODE") == "tesseract":
            environment["DOCUMENT_OCR_INTEGRATION"] = "1"
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT / "backend",
                   env=environment, check=True)


def runtime_environment(values):
    """Share development runtime settings without migration/admin credentials."""
    if not values.get("DATABASE_URL") or not values.get("APP_SESSION_SECRET"):
        raise ValueError("Runtime DATABASE_URL and APP_SESSION_SECRET are required")
    environment = {**os.environ, **{key: value for key, value in values.items()
        if key.startswith(("APP_", "S3_", "CODEX_GATEWAY_"))
        or key in {"DATABASE_URL", "CELERY_BROKER_URL", "REDIS_URL", "DOCUMENT_ENCRYPTION_KEY", *DOCUMENT_RUNNER_FIELDS}}}
    for key in ("MIGRATION_DATABASE_URL", "POSTGRES_PASSWORD", "TENDER_APPLICATION_PASSWORD",
                "WORKSPACE_TEST_DATABASE_URL", "WORKSPACE_TEST_RUNTIME_DATABASE_URL", "WORKSPACE_TEST_BROKER_URL",
                "GARAGE_ADMIN_TOKEN", "GARAGE_METRICS_TOKEN", "GARAGE_RPC_SECRET"):
        environment.pop(key, None)
    for key in tuple(environment):
        if key.startswith("WORKSPACE_TEST_DOCUMENT_") or key in {"DOCUMENT_RUNNER_INTEGRATION", "DOCUMENT_OCR_INTEGRATION"}:
            environment.pop(key, None)
    environment.update(APP_ENVIRONMENT="development", APP_SMS_PROVIDER="mock")
    return environment


def run_api(values, *, port=8100, origin="http://127.0.0.1:3001"):
    """Start only the local authenticated API, without handing it owner secrets."""
    url = urlsplit(origin)
    if (url.scheme != "http" or url.hostname not in {"localhost", "127.0.0.1", "::1"}
            or url.username or url.password or url.path not in {"", "/"}
            or url.query or url.fragment or not 1 <= port <= 65535):
        raise ValueError("Local API requires a plain HTTP loopback origin and a valid port")
    environment = runtime_environment(values)
    environment["APP_PUBLIC_ORIGIN"] = origin.rstrip("/")
    subprocess.run([sys.executable, "-m", "uvicorn", "app.full_main:create_app", "--factory",
        "--no-proxy-headers", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT / "backend", env=environment, check=True)


def run_jobs(values, *, action):
    """Local job processes use the same restricted runtime role as the API."""
    if action not in {"worker", "dispatcher"}:
        raise ValueError("Job action must be worker or dispatcher")
    if not values.get("APP_JOB_SIGNING_SECRET") or not values.get("CELERY_BROKER_URL"):
        raise ValueError("APP_JOB_SIGNING_SECRET and CELERY_BROKER_URL are required")
    command = [sys.executable, "-m", "app.jobs." + action]
    if action == "worker":
        # Windows has no supported prefork pool; production uses its own launcher.
        command.extend(["--pool=solo", "--concurrency=1", "--loglevel=WARNING"])
    subprocess.run(command, cwd=ROOT / "backend", env=runtime_environment(values), check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "postgres", "migrate", "verify", "api", "worker", "dispatcher"])
    parser.add_argument("--bin-dir", type=Path)
    parser.add_argument("--port", type=int, default=8100)
    parser.add_argument("--origin", default="http://127.0.0.1:3001")
    parser.add_argument("--broker", action="store_true", help="Enable actual RabbitMQ process tests with verify")
    parser.add_argument("--storage", action="store_true", help="Enable actual encrypted S3 tests with verify")
    parser.add_argument("--documents", action="store_true", help="Enable actual isolated document runner tests with verify")
    args = parser.parse_args()
    if (args.broker or args.storage or args.documents) and args.action != "verify":
        parser.error("--broker, --storage and --documents apply only to verify")
    if args.action == "init":
        initialize()
        print("Ignored local infrastructure configuration ready; existing secrets preserved.")
    elif args.action == "postgres":
        if args.bin_dir is None:
            parser.error("postgres requires --bin-dir")
        local_postgres(args.bin_dir)
    elif args.action == "migrate":
        migrate_workspace(initialize())
        print("Workspace migrations applied using the owner connection; runtime configuration preserved.")
    elif args.action == "api":
        run_api(initialize(), port=args.port, origin=args.origin)
    elif args.action in {"worker", "dispatcher"}:
        run_jobs(initialize(), action=args.action)
    else:
        verify_workspace(initialize(), broker=args.broker, storage=args.storage, documents=args.documents)


if __name__ == "__main__":
    main()
