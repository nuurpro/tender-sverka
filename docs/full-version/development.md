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
python scripts/dev_services.py verify --broker
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
`verify --broker` additionally supplies the ignored AMQP URL to real RabbitMQ
process tests. An explicitly configured unavailable broker fails those tests;
plain `verify` omits the transport tests rather than proving broker recovery.
For encrypted package integration, `verify --storage` supplies the generated
S3 endpoint/bucket/credentials and document key through private test environment
variables. Use `verify --broker --storage` for both actual services. These flags
are explicit: inherited test endpoints are discarded unless selected, and
incomplete selected configuration fails before the test process starts.
Run these helpers from the repository root with the backend virtualenv Python.
`init` also adds a missing `APP_JOB_SIGNING_SECRET` to an older local file without
rotating existing database/session/storage credentials. Worker/publisher startup
requires that separate secret; it must never fall back to the session key.

`verify --documents` explicitly enables the actual isolated parser tests using
the four saved `DOCUMENT_RUNNER_*`/path/distribution settings. It refuses a
disabled or incomplete selection before starting pytest and removes inherited
runner test flags when this option is absent. Combine it with `--broker
--storage` once the document stage is ready. Ordinary unit tests do not qualify
the operating-system isolation boundary.

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

For this development host, RabbitMQ 4.0.5 is additionally running in the Ubuntu
WSL distribution. Its AMQP listener is `127.0.0.1:5673`, with the local `tender`
vhost and generated credentials already in ignored `infrastructure/.env`.
Windows Kombu authentication was verified. The ignored
`infrastructure/rabbitmq.local.conf` supplied the native configuration; no shared
default password was committed. This is a local broker for real transport tests,
not production HA/TLS qualification. The separate Erlang CLI/cluster listener
still requires production network policy. Native service and Compose RabbitMQ
must not compete for the same AMQP port. The configuration follows RabbitMQ's
[listener settings](https://www.rabbitmq.com/docs/networking) and
[initial user/vhost settings](https://www.rabbitmq.com/docs/configure).

On this Windows host, WSL stops when its last attached process exits. Keep the
owned local broker in a foreground terminal during transport tests:

```powershell
wsl.exe -d Ubuntu -u root -- bash -lc 'exec runuser -u rabbitmq -- /usr/sbin/rabbitmq-server'
```

This assumes the isolated native configuration has already been installed. Do
not start a second instance if the owned systemd RabbitMQ service is running;
stop that local service first, or use the Compose broker instead. This recipe
does not configure an existing shared RabbitMQ deployment.

## Durable job processes

After migrations and the broker are available, start each development process
from the checkout root in its own terminal:

```powershell
python scripts/dev_services.py worker
python scripts/dev_services.py dispatcher
```

Both launchers exclude migration/admin credentials and force the development
environment. The worker uses a single-process pool on Windows; this does not
qualify the production Linux prefork deployment. The dispatcher publishes the
transactional outbox with broker confirmations and recovers expired leases.
The allowlisted real document stages are still being implemented: missing
handlers fail explicitly and no synthetic deployment handler reports success.
Tests register separate fixture stages only in the test environment.

When all encrypted-storage settings are configured, the dispatcher also starts
an independent bounded upload reconciliation loop. It discovers durable upload
attempts, verifies that their objects have no committed document references and
removes abandoned ciphertext. Storage outages use retry backoff; this loop does
not hold SQL locks during S3 calls or block outbox delivery. Keep the dispatcher
running for automatic cleanup after an API process crash. Partially configured
storage is refused instead of silently disabling cleanup.

The real `package.inspect` handler measures supported PDF/office/text probes
and stores a versioned inspection separately from the immutable upload manifest.
Unknown page counts, unavailable adapters and pending extraction/tokenization
remain explicit blockers. An inspection success is not permission to start AI
analysis; OCR, complete extraction and token budgets still belong to later steps.

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

The local helper forces `APP_SMS_PROVIDER=mock`, even if a provider is inherited
from the shell or local file, following the instruction to connect no real SMS
in this development version. The development template also selects mock:
`MockSmsProvider` uses the fixed code **123456**, sends no SMS, and the challenge
screen explicitly labels this development mode. Production rejects this provider.
This development choice follows the user's instruction; it does not establish
production phone identity or SMS delivery acceptance.

For a future real-provider deployment, use its own launcher with
`APP_SMS_PROVIDER=http` and configure
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
does not provide production redundancy or encrypt document content by itself.
The application's AES-256 envelope encryption now protects source originals
and normalized evidence artifacts. Further conversion/OCR derivatives, rotation
operations, retention, backups and restore drills remain separate acceptance
items. MinIO was not introduced as a new dependency:
its [upstream repository](https://github.com/minio/minio) reports that it is no
longer maintained.

This host now also has a dedicated Garage **v2.4.1** binary in Ubuntu WSL under
`/var/lib/tender-sverka-dev/garage/`, running as the `tender-garage` system user.
It uses the generated ignored S3 credentials, persistent local data directories
and loopback-only listeners: S3 53900, RPC 53901, admin 53903. A real boto3
put/get/delete round trip passed and removed its own random test object. This
verifies the private storage transport. Subsequent application tests verified
encrypted original uploads/downloads, authorization, immutable revisions and
failure reconciliation against this instance. The binary came from the official
[download page](https://garagehq.deuxfleurs.fr/download/).

For the configured local instance, keep its foreground process attached:

```powershell
wsl.exe -d Ubuntu -u root -- bash -lc 'exec runuser -u tender-garage -- python3 /var/lib/tender-sverka-dev/garage/launch.py'
```

The local launcher reads private configuration from its restricted directory;
credentials are not supplied in command-line arguments. Do not run this native
instance and the Compose storage service on port 53900 simultaneously.

## Isolated native document runtime (task 4a verified)

The Windows development application uses a dedicated non-root Ubuntu WSL
runtime. This host now has Python 3.14.4 and a parser virtual environment at
`/home/acer/.local/share/tender-sverka/document-runtime`. Its PDF/DOCX/XLSX/schema
libraries run inside bubblewrap with no home/Windows mounts. Native readers,
encrypted artifact publication, retry history and sandbox fault checks passed
independent specification/quality review and the actual 390-test backend suite.
Conversion/OCR/CAD, isolated archive admission and real quality/performance
acceptance remain subsequent delivery work.

To prepare a comparable fresh WSL runtime, install `python3.14-venv` and
`bubblewrap` from its distribution, then use the ordinary WSL user:

```powershell
wsl.exe -d Ubuntu -- python3 -m venv /home/acer/.local/share/tender-sverka/document-runtime
wsl.exe -d Ubuntu -- /home/acer/.local/share/tender-sverka/document-runtime/bin/python -m pip install pdfplumber python-docx openpyxl pydantic
python scripts/stage_document_parser.py --distribution Ubuntu
```

The staging helper copies only `__init__.py`, `entrypoint.py`, `readers.py` and
`schemas.py`. It preserves exact bytes, computes the same content hash as the
runner, preserves old code versions and atomically selects the new version.
It never stages server modules, credentials, configuration or uploaded files.
Use the paths returned by that helper in ignored `infrastructure/.env`:

```dotenv
DOCUMENT_RUNNER_MODE=wsl-bubblewrap
DOCUMENT_RUNTIME_PYTHON=/home/acer/.local/share/tender-sverka/document-runtime/bin/python
DOCUMENT_PARSER_CODE_PATH=/home/acer/.local/share/tender-sverka/document-parser/app/documents
DOCUMENT_WSL_DISTRIBUTION=Ubuntu
```

The local API/worker launcher carries these runtime fields while excluding test
flags and migration/admin credentials. Re-stage after changing pure parser code;
an old stage cannot satisfy the current code hash. Actual dependency versions,
Python build, code identity and resource policy belong to frozen processing
profiles. Disabled/unavailable isolation blocks normalization. Production uses
its verified Linux runner and its own deployment configuration.

The production deployment must run in Uzbekistan with TLS, restricted roles,
encrypted data and backups, redundant services, and approved external processor
terms. No region or local test establishes those requirements by itself.

The full application uses AgentRouter Chat Completions JSON mode with Pydantic
validation; it does not require official OpenAI credits. External document
transmission remains disabled until permitted by organization policy. Model
names are configuration values, not proof of Responses/vision/schema support.
