# Durable execution: implementation contract (task 3a)

This task supplies the execution substrate for the document/extraction/comparison
pipeline. It does not replace the full PRD with synthetic jobs or establish real
document quality. Encrypted storage/package preflight follows as task 3b; every
business stage must subsequently use this substrate and pass its own gates.

## Database and state transitions

- Add an Alembic migration; do not rewrite existing migration history. Freeze
  job input identity, organization/project, stage, input hash, payload and
  idempotency key at the database boundary. Lifecycle fields remain mutable.
- States: `queued`, `running`, `succeeded`, `failed`, `cancelled`. Only `queued`
  or an expired `running` lease can be claimed. A claim issues a fresh random
  lease token, increments the attempt, and records a bounded expiry atomically.
- Every heartbeat, checkpoint and terminal write checks the exact lease token
  and that it has not expired. Cancellation invalidates the lease immediately.
  A delayed worker cannot publish results after cancellation or lease takeover.
- Capture the original token/deadline under the job row lock. Flush all pending
  SQL before the final application commit check. A deferred PostgreSQL guard
  checks the deadline again during actual COMMIT; extending a heartbeat or
  clearing a terminal lease cannot substitute a new deadline for the original.
- Add tenant-qualified checkpoints and ordered progress events with composite
  job/project foreign keys and FORCE RLS. Append events under the same job row
  lock as the transition; event IDs never depend on process memory.
- Checkpoints are unique per job/step and carry a canonical input/version hash.
  Retry retains usable checkpoints; changed inputs create a new immutable job.
  Checkpoint/result writes occur in the same fenced transaction. Handlers never
  commit domain results independently of the lease fence.
- Domain callbacks receive detached records through an explicit project-scoped
  model allowlist, including validated package/checklist ancestry. They cannot
  receive a Session, run arbitrary SQL, change tenant context, access global
  identity/control models or reassign scope/parent identities. Validate reads,
  inserts, updates and deletes; row-level organization policy alone is not a
  project boundary. New pipeline models require an explicit scope adapter.
- Failed jobs can be explicitly retried within a bounded attempt policy. Retry
  requeues the existing outbox record transactionally and audits the action;
  terminal success/cancellation is not silently rerun. Cancellation is repeatable
  and cannot be overwritten by an old success/failure callback.

## Publisher and worker

- Claim unpublished outbox events with `FOR UPDATE SKIP LOCKED`, a publisher
  token and expiry; do not hold SQL transactions while publishing to RabbitMQ.
- Use durable queues/messages and publisher confirms. Mark published only after
  broker confirmation, and only for the still-owned outbox claim. A crash between
  confirmation and marking may duplicate delivery; worker fencing makes that safe.
- Minimal messages contain validated organization/job IDs and a versioned HMAC
  envelope. Documents, quotes, session credentials and provider keys stay out of
  task arguments, broker events and logs. The stage comes from the frozen DB job,
  never from an arbitrary message-supplied handler/import path.
- Celery uses JSON-only serialization, late acknowledgement, worker-loss
  rejection and bounded broker reconnect. A task obtains its own restricted DB
  engine/tenant transaction; there is no SQLite or in-process production fallback.
- Handler registration is an explicit allowlist. Missing handlers fail with a
  safe error; no placeholder handler is allowed to report a real pipeline stage
  as successful. Unit/integration fixtures are clearly separate from deployment.
- The dispatcher/recovery loop enumerates organization IDs through the internal
  service boundary, then applies transaction-local tenant context for each scan.
  Expired publisher/worker leases are recoverable after process restart. Duplicate
  deliveries cannot increment attempt while an unexpired lease is already active.
- Use bounded backoff for transient infrastructure failures. Persist safe error
  codes/messages, not raw exception/provider payloads; configuration errors and
  invalid inputs must not produce an infinite retry loop.

## Authorized API and progress

- Nested project job list/detail/retry/cancel endpoints recheck current
  membership and project ownership. Viewers may read but cannot retry/cancel.
  The browser cannot select arbitrary task payloads or pipeline handler names.
- SSE replays ordered persisted events using a job-qualified `Last-Event-ID`.
  Validate the replay ID, recheck membership during a long connection, close on
  logout/revocation/terminal completion, and emit bounded heartbeat messages.
  Open no SQL transaction for the lifetime of the connection. Disconnecting the
  browser does not cancel the job. HTTP GET progress remains the recovery path.

## Verification gates

Behavior tests first, then actual PostgreSQL restricted-role concurrency tests:
one claimant, expired takeover, stale token rejection, cancellation race,
checkpoint retry, immutable inputs, foreign tenant/project rejection and audit
atomicity. Exercise authenticated HTTP permissions and SSE replay/revocation.

Actual RabbitMQ/Celery tests must include duplicate delivery, publisher failure,
lost acknowledgement, worker restart and recovery without duplicate checkpoint
or domain output. Eager-mode/memory transport fixtures do not satisfy this gate.
Then independently review specification and quality before task 3b integration.
