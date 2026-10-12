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

## Authenticated workspace during development

The full API is a separate process. After applying migrations, run from the
checkout root with the backend Python environment activated:

```powershell
python scripts/dev_services.py api --port 8100 --origin http://127.0.0.1:3001
```

In a second terminal, from `frontend/`:

```powershell
npm ci
npm run dev -- --port 3001
```

Open `http://127.0.0.1:3001/workspace`. Next.js proxies `/api/` to
`FULL_BACKEND_URL` (default `http://127.0.0.1:8100`); set this server-side variable
before launching Next.js if the API port changes. The API's `APP_PUBLIC_ORIGIN`
must exactly match the browser origin, including hostname and port. The local
helper uses only runtime credentials from ignored `infrastructure/.env` and
does not pass migration/admin credentials into the API process. Production must
use its own service launcher and HTTPS settings, not this loopback helper.
All supported Uvicorn launches must use `--no-proxy-headers`: a default Uvicorn
launch trusts loopback forwarded headers and would undermine the IP login quota.
With the Next.js proxy, the default IP quota conservatively applies to the proxy's
socket address; deployment-specific trusted proxy handling requires an explicit
policy and separate verification before enabling it.

The local helper and development template select `APP_SMS_PROVIDER=mock`:
`MockSmsProvider` uses the fixed code **123456**, sends no SMS, and the challenge
screen explicitly labels this development mode. Production rejects this provider.
This development choice follows the user's instruction; it does not establish
production phone identity or SMS delivery acceptance.

For a real provider, select `APP_SMS_PROVIDER=http` and configure
`APP_SMS_ENDPOINT` and `APP_SMS_TOKEN` in the ignored local configuration. The
vendor-neutral HTTPS adapter posts `{phone,message}`
JSON with a Bearer token, refuses redirects, and times out after ten seconds.
Its endpoint must return 2xx only when delivery was accepted; provider-specific
response validation belongs in that provider's adapter. Missing settings return
a clear refusal. Test senders can only be
injected into an app explicitly configured as `environment=test`; they do not
establish real SMS delivery acceptance. The future Uzbekistan provider is Eskiz,
whose API is `https://notify.eskiz.uz/api/`. It is not connected in this development
version; provider-specific authentication and delivery semantics remain a later
integration gate. Platform OIDC is still pending.

Use Node.js 24+ for `npm test` (native TypeScript loading), and `npm run check`
for TypeScript verification. The `/` route remains the preserved MVP. The
workspace currently covers login, organizations and project metadata; document
processing, full comparison and the other ledger items are still in progress.

Logout revokes the current session verifier on the server. Its response does not
delete the shared session cookie: a delayed logout response must not erase a
newer login's cookie. A retained revoked verifier cannot authenticate and expires
normally or is replaced by the next login. The client drops its in-memory CSRF
credential and session view; late responses are tied to the identity operation
and credential version that issued them.

## Persistence verification

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
