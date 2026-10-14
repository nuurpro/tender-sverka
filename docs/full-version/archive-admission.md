# Standard archive processing — task 4b.2

Status: standard-adapter component implemented and verified. The user stopped native decoder
builds and RAR4/RAR5 optimization on 14 October 2026. Historical investigations
remain in `implementation-status.md`; they are not current acceptance gates.

## Adapters and limits

Use standard high-level libraries: `zipfile` for ZIP/Office ZIP containers,
`rarfile` with the ready-made UnRAR executable for RAR, and `py7zr` for 7Z.
`stat` handles link/type metadata. No custom native decoder or bit-stream auditor
is required. Libraries run inside the existing non-root, networkless document
sandbox without database, storage, session or gateway credentials.

Keep the package limits: 20 wire files, 50,000,000 wire bytes, 20 logical
usable documents, 50,000,000 expanded bytes, bounded entries/paths and three
archive levels. Read outputs in bounded chunks. Reject unsafe paths and links
through standard metadata checks. Each container has a timeout and try/except
handling; cancellation still stops the operation.

The standard admission policy is `archive-admission-v4`. It records basic intake
limits and the selected library/tool identity. Its archive-only temporary
workspace is 67,108,864 bytes, file limit 50,000,000 bytes and output envelope
100,262,148 bytes; native/OCR defaults stay unchanged. These are ordinary resource
bounds, not claims of perfect compressed-bit validation.

## Per-file errors and continuation

- `ARCHIVE_CORRUPTED`: the standard library reports a damaged/unreadable archive,
  or its member paths cannot safely preserve unique package source identities.
- `UNSUPPORTED_FORMAT`: unavailable or unsupported compression/format/tool, or
  processing outside the supported limits/time budget.

Keep the original and a bounded `{path, code, message}` issue, skip the failed
file and continue other valid files. Independent per-container processing keeps
one timeout or decoder failure from discarding valid siblings. An incomplete
failed expansion does not publish its partial children as successful sources.
Wire paths are reserved before expansion. A colliding archive is skipped in
either input order; the uploaded file and archive original remain available.

Upload publication stores issues in immutable package metadata and appends
`archive.file.skipped` events to the existing tenant-scoped database audit trail.
A replay of the same upload does not duplicate logs. Current authorization,
revocation checks and encrypted object-storage publication still apply.

## API contract

New standard-archive manifests use `schema_version: 3`, with `issues`, directory
accounting and outermost-first document `source_chain` metadata. Historical
package schemas 1/2 and admission policies v2/v3 remain readable unchanged.
Preflight returns `issues` and the `archive_files_skipped` warning.

If every file is skipped, retain a visible package revision and its issue logs.
Return `inspection_job_id: null`; the inspected preflight result has zero usable
documents and `no_available_documents`. Do not create a doomed inspection job or
invent extracted evidence. Valid files in mixed packages continue to inspection
and normalization; source errors stay visible in later workflow stages.

## Dependencies and verification

`scripts/document_runtime_requirements.txt` pins rarfile 4.5, py7zr 1.1.4 and
their dependency closure. Install binary wheels only; no native build is needed.
The configured standard UnRAR executable is
`/runtime/opt/unrar/usr/bin/unrar-nonfree`, extracted from the verified Ubuntu
package in the dedicated runtime. Installing dependencies alone is not proof
that the end-to-end flow works.

Acceptance focuses on ZIP/RAR/7Z positives, valid+corrupt/unsupported/timeout
mixes, all-skipped results, persisted audit codes, encrypted retained originals,
replay and access isolation, then continuation to normalization. Finish this
stage and proceed to extraction, comparison/scoring, the five-bid matrix and
exports. Specialized parser research does not delay that delivery path.

The component passed independent specification/quality review, 27 focused actual
archive/database/storage checks plus both filename-collision orders, and the
parent's 551-case backend gate. Final whitespace-only source identity
`d8eb2706ede1c93153c6cb299fc50d4b1c0710cac48b6c0b67af8e4e96746665`
passed a fresh 36-case source/archive/native/OCR/publication check. Exact stage
boundaries and counts are recorded in `implementation-status.md`; they do not
establish full-product, expert-corpus or universal-codec acceptance.
