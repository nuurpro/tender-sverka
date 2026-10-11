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
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
INFRA = ROOT / "infrastructure"
ENV = INFRA / ".env"


def initialize():
    INFRA.mkdir(exist_ok=True)
    if not ENV.exists():
        values = {key: secrets.token_hex(32) for key in (
            "POSTGRES_PASSWORD", "TENDER_APPLICATION_PASSWORD", "RABBITMQ_PASSWORD",
            "REDIS_PASSWORD", "S3_SECRET_KEY", "APP_SESSION_SECRET", "DOCUMENT_ENCRYPTION_KEY",
            "GARAGE_RPC_SECRET", "GARAGE_ADMIN_TOKEN", "GARAGE_METRICS_TOKEN",
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


def verify_workspace(values):
    """Run integration tests without passing private connection URLs as arguments."""
    if not values.get("MIGRATION_DATABASE_URL") or not values.get("DATABASE_URL"):
        raise ValueError("Owner and runtime connection configuration is required")
    environment = {**os.environ, "WORKSPACE_TEST_DATABASE_URL": values["MIGRATION_DATABASE_URL"],
                   "WORKSPACE_TEST_RUNTIME_DATABASE_URL": values["DATABASE_URL"]}
    subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=ROOT / "backend",
                   env=environment, check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["init", "postgres", "migrate", "verify"])
    parser.add_argument("--bin-dir", type=Path)
    args = parser.parse_args()
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
    else:
        verify_workspace(initialize())


if __name__ == "__main__":
    main()
