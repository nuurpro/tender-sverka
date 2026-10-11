# Full-version development environment

The `main` branch and `mvp-baseline` tag preserve the runnable MVP. Full-version
development is on `codex/full-version`. The new modules are not yet a complete
replacement for the MVP entrypoint; use the acceptance ledger for current scope.

## Infrastructure

Install backend dependencies, then generate local configuration:

```powershell
python -m pip install -r backend/requirements.txt
python scripts/dev_services.py init
docker compose --env-file infrastructure/.env -f infrastructure/compose.yaml config --quiet
docker compose --env-file infrastructure/.env -f infrastructure/compose.yaml up -d --wait --wait-timeout 180
python scripts/dev_services.py migrate
python scripts/dev_services.py verify
```

`init` preserves existing configuration and generates passwords instead of
shipping shared credentials. `infrastructure/.env`, `garage.local.toml`, uploaded
documents and PostgreSQL data are ignored by Git. Do not copy them into reports.
The migration connection uses `tender_owner`; application requests use
`tender_app`, which has neither superuser nor BYPASSRLS privileges. Never use the
migration connection for the application. Schema migrations are explicit, not
`create_all` at startup. Alembic reads `DATABASE_URL` from the process environment.
The `migrate` helper reads the ignored local owner URL, temporarily assigns it
only during Alembic execution, and restores the prior environment even on error.
The saved runtime URL is not overwritten. `verify` supplies both URLs privately
to the PostgreSQL tests and includes the runtime startup verification checks.
Run these helpers from the repository root with the backend virtualenv Python.

The Compose stack binds ports to localhost: PostgreSQL 55432, RabbitMQ 5673,
Redis 56379, S3 53900. Generated service URLs refer to those host ports; containers
must use their service names when API/worker images are added.

For independent Windows PostgreSQL verification, an installed PostgreSQL can
start a dedicated cluster under this checkout's ignored `backend/data/`:

```powershell
python scripts/dev_services.py postgres --bin-dir 'C:\Program Files\PostgreSQL\14\bin'
python scripts/dev_services.py migrate
python scripts/dev_services.py verify
```

This does not modify another PostgreSQL cluster. It uses port 55432, so use either
native PostgreSQL or the Compose PostgreSQL service at a time. PostgreSQL 14
validates repository/RLS/trigger behavior locally; the planned PostgreSQL 18
deployment must still be verified on its actual image.

## Persistence checks

From `backend/`, run `python -m pytest -q`. PostgreSQL-specific tests require
`WORKSPACE_TEST_DATABASE_URL` (owner bootstrap) and
`WORKSPACE_TEST_RUNTIME_DATABASE_URL` (restricted role) pointing to a disposable
development database that has already been migrated. SQLite tests are explicit;
they do not prove RLS, database triggers or concurrent row locking. PostgreSQL
tests create isolated random tenant records and must never target production.

## Storage and deployment boundaries

Garage supplies the S3 API for local integration. The official
[quick start](https://garagehq.deuxfleurs.fr/documentation/quick-start/) supports
the single-node configuration used here. Bucket policy/ACL support is limited;
the application must authorize each object access itself. This single-node stack
does not provide production redundancy or encrypt document content at rest.
AES-256 document encryption, rotation, retention, backups and restore drills
remain separate acceptance items. MinIO was not introduced as a new dependency:
its [upstream repository](https://github.com/minio/minio) reports that it is no
longer maintained.

The production deployment must run in Uzbekistan with TLS, restricted roles,
encrypted data and backups, redundant services, and approved external processor
terms. No region or local test establishes those requirements by itself.

The full application uses AgentRouter Chat Completions JSON mode with Pydantic
validation; it does not require official OpenAI credits. External document
transmission remains disabled until permitted by organization policy. Model
names are configuration values, not proof of Responses/vision/schema support.
