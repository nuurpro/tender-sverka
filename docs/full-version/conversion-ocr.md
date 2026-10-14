# Task 4b: conversion and OCR execution plan

This is the next implementation contract after the independently reviewed
native-evidence slice. It does not mark OCR, handwriting, conversion or archive
support complete. The full `implementation-status.md` ledger remains binding.

## 1. Reproducible, isolated toolchain

Extend the existing fail-closed runner rather than calling converters on the API
host. Separate native and conversion/OCR profiles identify exact parser code,
binary versions, language-model hashes, options and resource budgets. Disabled
tools cannot produce successful coverage, and installing a binary does not
establish its integration acceptance. Existing native snapshots stay immutable;
a changed toolchain requires an explicit new processing run.

Use a dedicated Linux runtime with Tesseract RU, Uzbek Latin/Cyrillic and EN
assets, headless LibreOffice, a PDF renderer and bounded archive/image adapters.
Verification must actually invoke each tool inside the same non-root, credential-
free, networkless sandbox used for document work. No converter receives the
application's environment, database, storage, session or gateway credentials.

Expand sandbox file/pixel/process/time budgets only through versioned bounded
policy, with admission tied to actual memory/storage needs. Private RAM workspaces
must fit their budget; oversize work cannot fall back to host temporary files.
Process-group termination, descendant cleanup and temporary-data cleanup are
tested on cancellation, timeout, output flood and converter death.

## 2. Image/PDF processing and coordinate provenance

Add image decoding for JPG/PNG/TIFF/HEIC/BMP/WEBP and all TIFF frames. Validate
actual dimensions/frame counts and decompressed pixel budgets before allocating
large rasters. Render PDF scan regions without overwriting native text evidence.
Retain original page/frame identity, image dimensions and PDF crop/media/rotation.

Record EXIF orientation, rotation, resize, crop and deskew transforms. OCR word
boxes map back through the inverse transform to original coordinates. Dewarping
requires a recorded reversible mapping or explicit uncertainty; rendered boxes
cannot be labelled as original coordinates. Keep mixed-page native text and
image coverage separately visible to prevent a readable header from certifying
the rest of a scanned page.

Add OCR words, lines, paragraphs, confidence, recognized language and raster
provenance to normalized evidence. OCR output is a transcription of an image,
not a verbatim embedded text layer. Human corrections create new audited evidence
versions retaining the original transcription, confidence and crop. Numeric
acceptance thresholds are calibrated/versioned using expert data; a guessed
threshold cannot establish exact numeric quality or green mandatory results.

## 3. Office conversion with original-source binding

Add DOC/RTF/ODT and XLS/ODS conversion in a fresh private LibreOffice profile,
with macros/external resources disabled inside the OS sandbox. Preserve original
bytes and hashes, converter/filter identity and original-to-rendered provenance.
Generated PDF pages and converted cells are derivative anchors, not invented
physical pages or XML paths in the original document. A source relation that
cannot be established remains reviewable rather than silently exact.

Test text, tables, merged cells, formulas, multiple sheets, headers/footers,
embedded images, malformed inputs, external links and encrypted/unsupported
documents. Formula evaluation and stale caches need explicit provenance and
review policy; conversion alone does not certify their values.

## 4. Bounded RAR/7Z and archive evidence

Revised by the user on 14 October 2026: use standard high-level archive
libraries/tools with timeouts and try/except handling. Stop custom native
decoder builds and RAR bit-stream optimization. Preserve originals, folders and
leaf provenance within existing wire, logical/output-byte and path limits.
Parsing still uses the isolated document runtime, without application credentials
or a host-parser fallback.

Mark damaged files `ARCHIVE_CORRUPTED` and unsupported compression/formats
`UNSUPPORTED_FORMAT`. Store per-file issue logs in the tenant-scoped database and
immutable package metadata, skip problematic inputs and continue valid files.
Expose the issues in preflight/status responses. A wholly unusable package must
report no available documents rather than fabricate evidence. Replay cannot
duplicate logs. Tests cover valid+damaged/unsupported/timeout mixes, retained
originals, persisted issues and continuation to normalization.

Archive acceptance is standard-library behavior and bounded failure recovery;
native bit-level completion proofs are not a prerequisite for the working
extraction/comparison/matrix/export scenario.

## 5. Structure, classification and specialized regions

Add layout/table cells and cross-page continuation evidence with original anchors
and uncertainty, preserving independent cell values. Add document-type suggestions
and audited manual correction; classification does not change source ownership.
Decode QR/barcodes locally with source crops and decoded values; record dates and
participant details with evidence rather than treating a visual seal as proof of
legal validity.

Stamps/signatures, handwriting and drawings need explicit capability-checked
adapters and representative acceptance cases. External vision runs additionally
depend on task-5 organization permission, provider capability and token/cost
budgets. Baseline OCR must not claim these adapters passed; unresolved regions
remain visible until actual processing or expert correction resolves them.

## 6. Acceptance and handoff

Run real sandbox/resource-fault tests and actual PostgreSQL/S3 durable publication,
retry/cancel/version/cache/authorization checks for every new artifact type. Use
generated originals to verify coordinate transformations and provenance, then
the expert corpus for CER, numeric accuracy, coverage and table/handwriting
quality. No real corpus is currently available; its collection format is in
`expert-corpus.md`, and real acceptance remains pending.

Expose capabilities, unresolved coverage and bounded preflight estimates to the
task-7 workflow. The API must distinguish unavailable tooling from absent evidence
and retain immutable prior processing runs. Finish with independent specification
review, then quality review, before committing the slice or claiming support.
