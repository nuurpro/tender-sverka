# Encrypted storage and package preflight: contract for task 3b

This task follows the reviewed durable execution substrate. It supplies real
document upload, immutable package manifests and authorized original downloads.
It does not establish OCR, extraction or production storage acceptance. The
F01–F50 ledger remains the complete scope.

## Upload and immutable revisions

- Authenticated nested project endpoints accept tender packages or the bid
  package of a participant belonging to that project. Admins/specialists upload;
  viewers only read. Supplier projects have one participant, buyer projects five.
  Check session, membership, role, exact Origin and CSRF before consuming files.
- Enforce at most 20 files and 50,000,000 bytes for the entire package, including
  archive contents. Count bytes actually received; Content-Length is only an
  early refusal. Reject oversized headers/fields, truncated multipart bodies,
  empty packages, duplicate normalized paths and unsafe filenames explicitly.
- Originals retain their exact bytes, original names, normalized relative paths,
  detected content type, byte length and SHA-256. Extensions and client MIME
  values cannot establish a safe parser or authorize execution.
- Preserve archive originals and folder provenance. Expansion is bounded by
  file count, actual expanded bytes, depth and time; reject traversal, absolute
  paths, symlinks, encrypted members and ambiguous duplicate paths. ZIP support
  is exercised here; RAR/7Z adapters remain part of task 4 until actually tested.
  An unavailable adapter must refuse analysis rather than silently omit files.
- Serialize revision allocation under the project row lock. Create a new tender
  or participant bid revision; replacing files never mutates a historical
  manifest. Document rows, package manifest, audit and inspection job/outbox
  commit together. An explicit Idempotency-Key replays the original response;
  the same key with different input is a conflict.
- Upload intent/reconciliation records cover the S3/SQL boundary. Network calls
  do not hold project locks. Failed/uncommitted uploads are reclaimable; an
  ambiguous SQL commit must be reconciled before deleting any uploaded object.
  A stale uploader cannot finalize another attempt's intent.

## Encryption and object access

- Store originals and every later derivative as authenticated AES-256 ciphertext.
  Generate independent random data keys/nonces; wrap data keys with a versioned
  master key. Bind organization, project, document identity and object kind with
  authenticated associated data. Key material stays in secret configuration,
  never the manifest, logs, broker messages, API response or repository.
- Use the library's Cobblestone-256 streaming recipe, available in the installed
  cryptography 50.0.2, for authenticated chunks and truncation detection. Wrap
  each fresh 32-byte data key with AES-GCM under the versioned master key and a
  fresh nonce. Avoid inventing a chunk cipher. The official
  [streaming recipe](https://cryptography.io/en/latest/cobblestone/) requires
  successful finalize before accepting a complete document; an authenticated
  prefix alone cannot establish complete coverage. Use AESGCM's documented
  [associated data](https://cryptography.io/en/latest/hazmat/primitives/aead/#cryptography.hazmat.primitives.ciphers.aead.AESGCM)
  to bind the wrapped key to the same scope and envelope version.
- A bounded upload stream must not spill plaintext into Starlette's default
  multipart temporary files. Stream through encrypted staging, or use an
  explicitly bounded memory-only receiver. Temporary files contain ciphertext;
  cancellation/failure closes streams and schedules safe cleanup.
- Version the envelope and authenticate lengths, chunk order and end-of-stream.
  Reject swapped/truncated/tampered ciphertext and wrong tenant/project/key
  before exposing unverified bytes. Never treat a storage checksum as encryption.
- Object keys are generated server-side within organization/project namespaces;
  user paths cannot become S3 keys. Buckets are private and callers cannot supply
  arbitrary object keys. Every download rechecks the document's package/project
  and current membership before decryption; no unauthenticated signed URL bypass.
- Downloads preserve safe Content-Disposition names and use no-store/nosniff.
  Transport failures produce safe errors without leaking credentials or object
  data. Production endpoints require TLS; local HTTP is loopback-only.
- Tenant-qualified, versioned content/transform keys support later caches without
  exposing a cross-tenant file-existence oracle. Source authorization still
  applies when a result is reused. Cache identity includes parser/model/policy
  versions and relevant language/options, not just the filename.

## Preflight and stage boundaries

- Return measured file/byte limits and per-document format support before the
  user starts analysis. Known page counts are bounded at 1,000/package. Unknown
  counts are visibly pending, never reported as zero or accepted coverage.
- A persisted inspection result separately records page/token counts, warnings,
  converter/parser versions and coverage. The uploaded manifest remains frozen.
  Task 4 supplies exact converted-page/OCR counts for nontrivial formats.
- Enforce 250,000 extracted tokens/package before extraction AI calls. Request
  context and whole-run budgets are separate. Until the requisite parser/count
  stage exists, the corresponding analysis stage cannot report successful
  preflight or send the unchecked package to an external model.
- Organization processor permission remains required for external AI; uploading
  documents alone does not grant it. Retries reuse validated inspection/cache
  results only for identical immutable inputs and implementation versions.

## Code boundaries and verification

Use dedicated storage envelope/adapter, encrypted upload receiver, manifest and
preflight services, nested package/document routes and Alembic models. Storage
I/O stays outside SQL transactions; committed domain writes use the reviewed
job fence. Tests must exercise the contract, not a fake successful pipeline.

Required gates: real private S3 round trip with ciphertext inspected; tamper,
truncation, wrong key/tenant/project rejection; actual restricted PostgreSQL
foreign access/revision/idempotency races; unauthenticated/viewer/CSRF downloads
and uploads; actual-byte/archive limit bypass attempts; upload/storage failure
and ambiguous-commit reconciliation without loss of committed originals.

Single-node development storage is not Uzbekistan residency, key rotation,
retention/deletion, redundant storage or backup/restore acceptance. Those remain
explicit production tasks, including derivatives, caches and backup retention.
