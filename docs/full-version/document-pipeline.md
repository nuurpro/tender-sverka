# Full document pipeline: task 4 delivery contract

Task 3b provides immutable encrypted originals and durable job execution. Task 4
turns those originals into versioned, verifiable source material. The complete
F01–F50 ledger remains the acceptance scope; these delivery slices describe
dependencies rather than exceptions to the PRD.

## 4a: normalized evidence and native readers

Introduce strict normalized-document, block, anchor, coverage and processing
profile schemas. Native PDF anchors preserve original page coordinates and
rotation/crop information. DOCX anchors identify the actual XML part, paragraph
or table row/cell; spreadsheet anchors identify the original sheet/cell/range;
text/CSV anchors preserve original line/record offsets. Missing physical page
coordinates are nullable, never fabricated from logical row numbers.

Blocks retain original text, source identity, structural type and stable local
IDs. Normalized text is a separate representation with a source mapping. A
coverage ledger distinguishes extracted text, regions requiring OCR/vision,
unavailable adapters, failures and human corrections. A text layer on one page
does not establish coverage of embedded pictures or scanned pages elsewhere.

Native readers cover PDF, DOCX, XLSX, TXT and CSV. DOCX includes document order,
tables, merged cells and header/footer or other relevant document parts; unknown
embedded content remains visible as pending coverage. XLSX preserves raw formula
text, available original cached values, merged ranges and sparse cell addresses.
No macros, formulas, external links or document instructions are executed.
Missing/untrusted cached values remain reviewable rather than invented numbers.
Encoding, malformed XML, compressed content, declared spreadsheet dimensions,
actual cells/blocks, output size and runtime are bounded independently.

The native stage establishes the isolated runner described below before exposing
normalization of uploaded files. The local Windows development process may use
the verified Linux WSL/bubblewrap runner; production uses its Linux runner. An
unavailable or failed sandbox blocks the stage instead of falling back to a
host parser. Converter/OCR binaries extend this boundary in slice 4b.

The previously reviewed task-3b ZIP admission/expansion path is bounded but still
runs on the API host. This is an explicit remaining boundary: slice 4a isolates
native document interpretation, not every intake operation. Slice 4b must move
ZIP as well as RAR/7Z admission into a verified sandbox before claiming that all
untrusted parsing is isolated. Exact physical expanded-byte validation applies
at the current admission boundary too; declared ZIP sizes cannot truncate sources.

Persist normalized payloads and later derivatives as encrypted S3 artifacts.
Append-only PostgreSQL metadata links each artifact to an authorized immutable
package/document and a frozen parser/policy/language/options profile. Artifact
publication and durable checkpoints are atomic under the existing job fence;
storage calls remain outside SQL. Owned-object reconciliation must preserve
committed original and artifact references, including ambiguous commits.
On retry, healthy document checkpoints are reused while failed documents are
processed again. Failed artifacts remain immutable history; their replacements
carry a new execution attempt. A failed summary cannot prevent recovery on the
next attempt, and successful duplicate deliveries must not duplicate artifacts.

Caches are internal, tenant/project qualified and keyed by source content plus
the complete transform profile. Reusing content never authorizes access to an
original in another project. Versioned artifacts and exact original sources
remain available after a new processing profile is used.

Expose authenticated nested start/status/artifact/block endpoints with role,
Origin/CSRF, session refresh, idempotency and no-store protections. Parsing alone
does not grant external AI permission or certify ready-to-submit results. Exact
provider tokenization, model context and whole-run budgets remain task 5 gates.

## 4b: isolated conversion, images, OCR and source mapping

All untrusted parsers/converters run in a bounded process sandbox: no network,
no database/storage/session/gateway credentials or host private folders; explicit
CPU, address-space, process, pixel, output and wall-time limits. Production
readers require a verified runner. Memory-only tests do not establish isolation.
Conversion workspaces are private RAM-backed or encrypted ephemeral storage and
are removed on success, error, cancellation and process death.

Add DOC/RTF/ODT and XLS/ODS conversion with a fresh LibreOffice profile, disabled
macros/external resources and preserved original-to-rendered provenance. Use the
documented [headless conversion interface](https://help.libreoffice.org/latest/en-US/text/shared/guide/start_parameters.html);
headless mode by itself is not a security sandbox.

Handle JPG/PNG/TIFF/HEIC/BMP/WEBP, multipage TIFF, PDF rasterization, EXIF/rotation,
deskew/dewarp and mixed text/scan pages. Actual OCR word boxes and confidence
must map back through recorded transforms to original coordinates. Tesseract's
[TSV/hOCR interfaces](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html)
provide word locations and confidence for the baseline engine. Language assets
cover RU, Uzbek Latin/Cyrillic and EN; model/version assets are reproducible.

Add bounded RAR/7Z expansion retaining folders and archive originals under the
same logical/actual-byte/depth budgets. Add layout/table structure and cross-page
continuations, explicit numeric review thresholds, classification corrections,
QR/barcodes, stamps/signatures, dates and participant details. Handwriting and
drawings use capability-checked adapters; their unavailable/low-confidence regions
remain explicit review work. A synthetic fixture does not satisfy OCR accuracy.

## 4c: drawing and CAD evidence

PDF/image drawings retain title blocks, callouts, dimensions, tolerances and
original-coordinate anchors. Add DXF entities and a configured DWG conversion
adapter with converter/version/source provenance. No placeholder converter or
model-name inference can report CAD/vision success. Integration and expert
acceptance remain explicit gates until exercised on representative originals.

## Verification gates

Each slice receives independent specification and quality review. Exercise real
restricted PostgreSQL/private S3 artifacts, retry/cancel/ambiguous commit and
cross-tenant/project reads; verify ciphertext rather than mock successful writes.
Use original fixtures with mixed pages, tables, sparse/merged/formula cells and
embedded content; compare extracted values and anchors with the originals.
Exercise the actual sandbox against network/private-file access, oversized
dimensions, output floods, timeout and cancellation. Keep pending coverage
visible throughout the application. The real expert corpus, numeric accuracy,
OCR CER, performance and production deployment gates remain in the full ledger.
No real expert corpus is currently available. The agreed collection and
annotation format is recorded in `expert-corpus.md`; the adjacent JSON template
is intentionally unfilled and cannot be treated as acceptance evidence.
