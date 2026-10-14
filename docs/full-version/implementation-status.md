# Full-version implementation and acceptance ledger

The approved plan is the full Tender-Sverka application described in the original
PRD (draft 0.1, 8 October 2026), plus the agreed five-participant comparison UI.
The `mvp-baseline` tag preserves the previous application. This ledger records
evidence, not a smaller definition of completion.

## User-directed archive change — 14 October 2026

The user stopped native decoder builds and RAR4/RAR5 optimization. That work is
terminated; no task-owned build/probe processes remain. The diagnostic prototype
is retained outside the production runtime and is not selected or accepted.

The archive implementation now uses standard high-level libraries/tools with
timeouts and try/except handling. A damaged archive receives
`ARCHIVE_CORRUPTED`; an unsupported format/codec receives `UNSUPPORTED_FORMAT`.
The file is skipped, its issue is recorded in the tenant-scoped database/audit
trail, and valid files continue through the pipeline. Originals and visible
issue provenance are retained; a package with no usable documents must report
that result explicitly. Replay must not duplicate issue logs.

Earlier native bit-consumption, compressed-tail and ideal-parser acceptance
criteria below are historical investigations, superseded by this instruction.
They do not block the standard-adapter implementation. Basic upload/output
limits, path safety, isolation, authorization and truthful per-file errors remain.

Delivery priority is now: finish this standard archive/error-handling stage,
then extraction, comparison/scoring, five-bid matrix and exports as one working
End-to-End scenario. Remaining PRD features stay in the ledger and follow that
scenario; specialized decoder research is not a prerequisite for it.

## Agreed operating defaults

- One engineer and a tender expert; standalone application, then integrations.
- Permanent storage in Uzbekistan. External AI requires organization permission
  and acceptable processor terms; the region alone is not a compliance claim.
- Each tender/bid package: 20 documents, 50 MB total, at most 1000 pages.
  Archive expansion cannot bypass the file/size limits.
- Five participants in buyer mode; one in supplier mode.
- Initial extracted-token cap: 250,000/package, with separate request-context and
  total-run budgets. Report/UX languages: RU, Uzbek Latin, EN; document OCR also
  handles Uzbek Cyrillic and mixed languages.
- PostgreSQL, S3-compatible storage, Celery/RabbitMQ, Redis quotas, pgvector.
  SQLite is permitted as an isolated test backend, never a production fallback.
- AgentRouter Chat Completions JSON mode remains the default gateway protocol.
  Model names do not establish available capabilities or access.

## Requirement traceability

| ID | Deliverable | Current evidence/state |
|---|---|---|
| F01 | Organizations, phone OTP, sessions, roles, platform OIDC | Auth/roles/API verified; authorized dev mock 123456; production SMS/OIDC pending |
| F02 | Tenant/project/participant isolation including search, cache and downloads | Repository/API/RLS, original/artifact downloads and native-parser cache isolation verified; full search/extraction/comparison caches pending |
| F03 | Dashboard, lots, deadlines, supplier and buyer modes | Dashboard/project creation/deadline/report language/modes verified; lot integrations pending |
| F04 | Immutable tender/bid revisions and source originals | Encrypted immutable uploads/history/original downloads verified on actual PG/private S3 |
| F05 | PostgreSQL schema, Alembic, FORCE RLS with restricted application role | Foundation + migration/startup role checks verified on PG14; PG18 deployment pending |
| F06 | Durable jobs, transactional outbox, leases, retry/cancel and SSE progress | Substrate/API, native/baseline OCR normalization and artifact retry history verified; extraction/remaining OCR/comparison stages pending |
| F07 | Idempotent mutations and optimistic edit conflicts | Checklist/job/HTTP retry-cancel/upload replay verified; remaining mutations/stages pending |
| F08 | 20 files/50 MB, expanded archives, page/token/context/budget preflight | Actual wire/logical/expanded ZIP limits and known PDF page probes verified; full counts/token/context/run budgets pending |
| F09 | PDF text/scans, DOC/DOCX/RTF/ODT/TXT | Native PDF/DOCX/TXT and baseline scan/mixed-PDF OCR evidence verified; DOC/RTF/ODT conversion and specialized visual coverage pending |
| F10 | XLS/XLSX/ODS/CSV, merged cells and safe formula values | Isolated XLSX/CSV, sparse/merged cells, exact CSV records and raw formula/cache review verified; XLS/ODS conversion and accepted formula values pending |
| F11 | JPG/PNG/TIFF/HEIC/BMP/WEBP, phone photos and multipage TIFF | Isolated decoding/OCR and multipage TIFF/HEIC with source geometry verified; real-photo quality acceptance pending |
| F12 | ZIP/RAR/7Z, preserved folders and safe bounded extraction | Standard adapters, bounded isolation, retained originals, per-file database issues/audits and skip/continue verified; native decoder research stopped by user; unsupported variants remain explicit |
| F13 | Pasted text and authorized lot-link import | Pending |
| F14 | Document-type classification and manual correction | Pending |
| F15 | OCR, layout, dewarp/rotation, tables across pages, handwriting | Baseline OCR, recorded rotation/deskew and sequential bounded frames verified; layout/dewarp/cross-page tables/handwriting pending |
| F16 | OCR confidence, expert corrections and numeric review thresholds | Actual word/line confidence and conservative numeric/unknown-region review verified; audited corrections and expert-calibrated acceptance pending |
| F17 | Seals/signatures, QR/barcodes, dates and participant details | Pending |
| F18 | PDF/image drawings, stamps, labels, dimensions/tolerances and vision | Pending |
| F19 | DWG/DXF conversion, entities and source anchors | Pending; PRD expansion stage |
| F20 | Structured product/participant requirements and lot positions | MVP basic requirements only |
| F21 | Cross-references, duplicates, contradictions and competition concerns | Pending |
| F22 | Add/edit/delete/merge/group, approve, version diff and retained history | Repository + API draft/replace/approve/fork/history verified; editor/merge/diff pending |
| F23 | Separately extracted bid facts and evidence/source priorities | Pending |
| F24 | Internal bid conflicts, validity at tender deadline and model matching | Pending |
| F25 | Deterministic Decimal numeric/unit/date/set rules | Pending |
| F26 | Semantic and multilingual comparison, equivalence and review routing | MVP basic LLM comparison only |
| F27 | Exact evidence from both packages, coordinates, translations and viewer | Native anchors, explicit OCR transcription/inverse raster geometry and authenticated source APIs verified; AI citations/translations/viewer pending |
| F28 | Verified missing evidence vs unsupported claim; incomplete coverage -> review | Native/visual/low-confidence/numeric OCR coverage stays explicitly pending; requirement-level missing-evidence/claim evaluation pending |
| F29 | Five bids compared independently with consistent snapshots | MVP three bids only |
| F30 | Overall/product/participant/category/item scores, counts and intervals | MVP overall score only |
| F31 | Risk for mandatory failure; readiness requires all mandatory green | MVP risk; full verdict pending |
| F32 | Versioned admin weights/coefficient policies and official evaluation points | Pending |
| F33 | Concrete recommendations, severity, actions and deterministic impact | Pending |
| F34 | One-page analytical summary, strengths, risks and action checklist | Pending |
| F35 | Clarification draft with original-source evidence; DOCX export | Pending |
| F36 | Amendments and conservatively correct incremental recomparison | Pending |
| F37 | Manual status overrides with reason, audit and original AI decisions | Pending |
| F38 | Polished responsive steps/dashboard/matrix/Sheet/split-view and filters | Responsive workspace dashboard/create flows verified; full matrix/split-view pending |
| F39 | Comparison/category charts, history, accessibility and reduced motion | Pending |
| F40 | RU/UZ/EN interface and selected report language; original quotes retained | MVP Russian only |
| F41 | Project chat restricted to authorized versioned uploaded sources | Pending |
| F42 | Snapshot-consistent PDF, XLSX, DOCX and JSON/API exports | MVP XLSX only |
| F43 | Content-addressed tenant-isolated parser/extraction/comparison caches | Frozen native/OCR profiles, scoped parser reuse and current-original binding verified; extraction/comparison caches pending |
| F44 | Gateway capability profiles, budgets, bounded failure recovery | MVP chat/responses adapter only |
| F45 | Reproducibility via snapshots/cache and explicit new evaluation runs | Native/OCR code/assets/runtime/policy/options snapshots, source-only execution and explicit reprocessing verified; extraction/comparison snapshots pending |
| F46 | TLS, AES-256 at rest, retention (12 months), deletion incl. derived data | AES-256 authenticated originals/normalized artifacts verified; further derivatives/DB volumes/TLS/rotation/retention/deletion pending |
| F47 | TenderZone/TenderGPT lot/SSO/commission integrations | Pending; external contracts required |
| F48 | Company/bad-performer/license registry checks with as-of provenance | Pending; external contracts required |
| F49 | Dictionaries, templates, types, policy and provider administration | Pending |
| F50 | Observability, backup/restore, redundant services and availability | Pending |

## Acceptance gates (not satisfied by synthetic demo documents)

The user confirmed that no ready real-document corpus is available. Continue
implementation with clearly labelled synthetic fixtures; real quality gates
remain pending. `expert-corpus.md` and `expert-case.template.json` define the
collection/annotation handoff, not measured acceptance results.

- Expert-annotated 20–30 real tenders covering goods/works/services, RU/UZ/EN,
  scans/photos/tables/drawings. Keep a held-out acceptance split.
- Mandatory extraction recall 100%; known-status accuracy 100%; review <=15%;
  zero false-green unmet mandatory requirements; 100% verifiable sources.
- OCR CER <=2% on >=200 dpi scans and <=5% on photos; accepted numeric values
  exactly match original ground truth; recommendations applicable in >=80%.
- End-to-end <=10 minutes/100 pages, <=30 minutes/500 pages; >=50 concurrent
  checks on declared hardware and an available gateway quota.
- Availability 99.5%/month; initial RPO 15 minutes, RTO 1 hour; restore drill.
- Real SMS/OIDC/gateway/registry/platform/CAD integration acceptance, privacy
  and processor-term review. Mock tests are not integration acceptance.
- Full requirement-by-requirement completion audit before marking goal complete.

## Execution log

- Baseline: commit `1e88147`, private repository `nuurpro/tender-sverka`,
  tag `mvp-baseline`, 54 backend tests and TypeScript check passed.
- Development: branch `codex/full-version` in a managed isolated worktree.
- Started normalized PostgreSQL repository, migration and invariant tests.
- Docker Desktop was installed but its Linux daemon was stopped; starting it
  for actual PostgreSQL/broker integration verification.
- Native isolated PostgreSQL 14.3 started on localhost:55432 after Docker
  Desktop failed during its inference socket initialization. No Docker factory
  reset or existing database removal was performed. PG18/broker/S3 remain unverified.
- Migrations `0001_workspace`, `0002_comparison_snapshot` and
  `0003_source_provenance` applied to the
  isolated development cluster. Actual runtime-role checks verify tenant reads,
  foreign-write rejection, immutable approved checklists/packages/comparison
  snapshots, concurrent job idempotency, and startup refusal of unsafe DB roles.
- Spec review found mutable comparison inputs; the second migration now freezes
  them while allowing lifecycle status changes. Independent re-review passed.
- Quality review found mutable original document metadata; the third migration
  now freezes all document provenance and package creation timestamps. Its
  eleven tamper cases fail against the old state and pass against the new state.
- Backend: 120 tests passed with actual PostgreSQL integration enabled, no skips.
  Frontend dependencies installed in the worktree and TypeScript check passed.
  Independent spec and quality re-reviews passed for the foundation scope;
  downstream application integration is still pending.
- Added explicit local `migrate`/`verify` commands; migration credentials never
  overwrite the saved runtime configuration and errors do not echo credentials.
- Saved a development checkpoint for phone authentication, organization roles,
  project/checklist APIs, migration `0004_phone_auth`, and the separate
  `/workspace` frontend. This is work in progress; independent review, real SMS
  provider acceptance and the remaining full-version requirements are pending.
  Backend tests: 144 passed against the local PostgreSQL setup. Frontend API
  tests: 6 passed; TypeScript check passed. The MVP baseline remains
  on `main` and is retained by the `mvp-baseline` tag.
- Task 2 now passes independent specification and quality review. Backend:
  157 tests passed with actual PostgreSQL integration; frontend: 9 API tests,
  5 DOM tests and TypeScript checking passed; optimized Next.js build passed.
  Deferred-response regressions cover organization changes, pending lists,
  logout/new-login races and Strict Mode. Actual HTTP cookie tests prove that
  delayed logout cannot erase a newer login and revoked credentials are rejected.
- Supported API startup disables forwarded-header trust; actual Uvicorn
  middleware tests reject the 21st attempt even with rotating forwarded IPs.
  The development launcher excludes owner credentials and defaults to the
  user-authorized mock code 123456. Production rejects mock mode; Eskiz is a
  future unconfigured adapter, and no real SMS was sent.
- Browser checks verified organization/project creation, buyer mode, Uzbek
  report language, Tashkent deadline, persistence after reload, search and
  logout. Mobile/tablet/desktop screenshots were inspected. The ordinary
  non-injected local launcher also passed mock login in the optimized build.
- Task 3a execution contract recorded in `durable-execution.md`; actual durable
  pipeline integration and broker recovery verification remain next.
- Local RabbitMQ 4.0.5 installed in Ubuntu WSL and AMQP authentication verified
  from Windows. A separate job-signing key was added without rotating existing
  local secrets. Task 3a implementation is in progress, with first restricted-PG
  claim/takeover/cancellation/checkpoint/outbox behavior tests exercised; it has
  not yet passed independent review or broker recovery acceptance.
- Real RabbitMQ/Celery process tests exercised duplicate confirmed delivery,
  connection failure, lost acknowledgement, worker restart, heartbeat and
  checkpoint reuse without duplicate output. Independent full verification
  passed 188 tests on restricted PostgreSQL and the actual broker. Specification
  review nevertheless reproduced two missing safeguards: callback writes could
  escape project scope, and final SQL flush could outlive the worker lease.
  Task 3a remains in review while these gaps are fixed; green tests alone did
  not satisfy its acceptance contract.
- Local launchers now support `worker`, `dispatcher` and `verify --broker`
  without passing private connection strings on the command line or granting
  owner credentials to worker/API processes. Bootstrap behavior tests passed
  13 cases, including explicit transport opt-in and preserved local secrets.
- A private native Garage v2.4.1 instance started in Ubuntu WSL on loopback S3
  port 53900 with its own restricted system user. Actual boto3 put/get/delete
  passed and removed the random test object. This is transport evidence only;
  encrypted originals, package preflight and production storage gates remain
  pending. Their task 3b contract is recorded in `storage-preflight.md`.
- A separate storage experiment persisted Cobblestone-256 ciphertext, verified
  its round trip, rejected wrong scope and truncation, and confirmed anonymous
  object GET returns 403. The owned test object was removed. This validates the
  chosen primitive/transport combination, not application upload authorization,
  envelope key wrapping or complete document storage integration.
- Task 3a specification re-review passed after reproducing and fixing both
  scope/commit safeguards. New regressions failed against the earlier code;
  restricted-PG reproductions now reject cross-project/context changes and roll
  back expired output. Detached domain records remain usable across repeated
  flushes and package/checklist ancestry operations. Migration
  `0006_job_commit_fence` checks the original lease deadline during COMMIT.
- Root verification through `scripts/dev_services.py verify --broker` passed
  **216 tests**, no skips, on actual PostgreSQL and RabbitMQ. The independent
  specification reviewer additionally passed 49 focused cases and its own
  scope/expiry reproductions. Quality review is the remaining task 3a gate.
- Task 3a quality review found comparison JSON references escaping project scope
  and mutable draft-parent identity. Actual PostgreSQL regressions reproduced
  both; shared ancestry validation now covers existing/staged/deleted parents,
  reads and writes, and freezes reference identities. Legitimate staged forks
  and comparisons remain supported. Specification and quality re-reviews passed,
  each independently exercising the 10 new boundary cases.
- Final backend suite: **226 passed** with actual PostgreSQL and RabbitMQ.
  Root additionally verified 44 current fence/substrate cases. Task 3a is
  complete within its contract; encrypted storage/package preflight and every
  remaining full-version ledger requirement continue as planned. The live
  authenticated API was restarted and verifies migration head 0006; anonymous
  session/job access is rejected. The original MVP remains clean at its baseline.
- Task 3b is implementing encrypted originals, immutable package manifests,
  bounded input/archive preflight, upload intents/reconciliation and authorized
  downloads. Initial actual restricted-PG/private-S3 checks exercise encryption,
  anonymous refusal, tamper/scope/key rejection, upload revision/idempotency
  races, ambiguous commit and the durable inspection handler. This task has not
  yet passed independent specification/quality review.
- Added explicit `verify --storage` opt-in without private values in argv;
  bootstrap tests: 15 passed. Client upload/history/preflight/download methods
  preserve cookies, CSRF, native multipart bytes and retry idempotency. Frontend:
  11 API tests, 5 DOM tests and TypeScript checking passed. These client methods
  do not establish the still-pending full document workflow UI.
- Independent root verification of task 3b passed **276 backend tests** against
  actual PostgreSQL, RabbitMQ and private S3; the optimized frontend build also
  passed. Specification review nevertheless reproduced a revoked-session upload
  replay and eager ZIP-directory allocation before limits. Both are required
  corrections before this task can pass review or be committed as complete.
- Task 3b passed independent specification re-review and quality review after
  both corrections. Root final verification: **281 backend tests passed**, no
  skips, against actual PostgreSQL/RabbitMQ/private S3. Frontend: **11 API +
  5 DOM tests**, TypeScript and optimized build passed. The inspection stage
  preserves explicit pending OCR/extraction/tokenization gates.
- Quality review recorded nonblocking maintenance hardening for task 10:
  quarantine internally corrupted upload ownership metadata without deleting
  its objects, and continue bounded cleanup of otherwise valid attempts. API
  uploads generate valid immutable ownership records; this observation does
  not establish a current external deletion or access bypass.
- Task 4a is implementing native normalized evidence, immutable encrypted
  artifacts and authenticated source APIs. Root exercised the actual non-root
  WSL/bubblewrap fault suite (41 tests passed) and re-staged the later MIME
  correction; the updated real runner probe and public-text parsing passed with
  nine material runtime identities. The production path has no host parser
  fallback. OCR/conversion/CAD and model tokenization remain later gates.
- Root task-4 client checks passed 13 API tests, 5 DOM tests, TypeScript and the
  optimized build; bootstrap/staging checks passed 20 tests. These are client
  methods, not the still-pending source-viewer UI. Artifact retry recovery and
  actual PostgreSQL/S3 acceptance are in progress; independent task-4a review
  has not yet passed.
- Root full task-4a verification passed **351 backend tests**, zero skips,
  against actual PostgreSQL/RabbitMQ/S3/WSL; current frontend 13 API + 5 DOM,
  TypeScript and optimized build also passed. Independent specification review
  nevertheless reproduced two blocking coverage gaps through the actual runner:
  PDF catalog attachments and unknown XLSX cell XML were incorrectly reported
  as fully covered. Task 4a remains unapproved while those gaps are corrected.
  The reviewer independently passed 60 actual focused tests and additionally
  verified process and aggregate temporary-filesystem resource limits.
- Specification re-review passed after bounded PDF Names/associated-file
  coverage and recursive unknown spreadsheet-content guards were added. Root
  passed 43 reader unit cases and four actual WSL coverage regressions. Quality
  review independently verified actual PG/S3 publication/retry/authorization,
  but identified CSV Unicode record-boundary corruption and stale DOCX vertical
  merge origins across a missing row. Both are required corrections before
  quality approval or task-4a commit; the earlier green suites are not approval.
- Task 4a passed specification and quality re-review after all confirmed source
  integrity corrections. Root final actual verification passed **390 backend
  tests**, zero skips, with PostgreSQL/RabbitMQ/private S3/WSL enabled. Root also
  independently exercised seven actual CSV/DOCX regression cases; the quality
  reviewer reran six original/variant WSL fixtures, preserving CSV source bytes,
  rejecting broken merges and retaining valid adjacent merge anchors.
- Native evidence, encrypted artifacts, versioned retry history, scoped source
  APIs and the client SDK are complete within the task-4a contract. Full document
  support is not complete: ZIP admission still needs OS isolation, and conversion,
  OCR/layout/handwriting, CAD, external AI budgets, the full workflow UI and all
  remaining F01–F50 acceptance gates continue. Mock SMS remains fixed `123456`,
  with no real provider connected; no real expert corpus is available yet.
- Task 4b.1 is in progress: versioned image/PDF OCR with explicit transcription
  evidence, bounded sequential frames and reversible coordinate transforms.
  A real isolated Tesseract probe recognized a public printed fixture, and
  actual RU/UZ Latin/UZ Cyrillic/EN language assets are installed. This is basic
  runtime evidence, not application OCR or expert accuracy acceptance. The
  pure-code staging whitelist now includes the two OCR modules without server
  credentials; its maximum six-file payload passed a real isolated staging test.
- Parent client contracts distinguish OCR transcription from native source text;
  TypeScript checking rejects `original_text` on OCR blocks. Bootstrap tests
  passed **20 cases**, including explicit OCR option forwarding and removal of
  inherited test settings. The OCR image matrix, encrypted publication,
  backward compatibility, independent reviews and final verification remain
  in progress. OCR stays disabled by default; the full task-4b contract in
  `conversion-ocr.md` is unchanged.
- Root additionally read a literal preexisting task-4a encrypted artifact through
  the current document service and restricted PostgreSQL role/private S3. Its
  original native-v1 profile hash and schema-1 payload remained valid; no old
  artifact was rewritten. This is actual stored backward-compatibility evidence,
  separate from the still-pending full image/OCR qualification.
- Root's first complete task-4b.1 actual run passed **459 backend tests**, zero
  skips, with PostgreSQL/RabbitMQ/S3/WSL and OCR enabled for verification only.
  Independent specification review nevertheless identified incomplete frozen
  system dependency coverage: selected Python/stdlib and adapter ELF roots
  include dependencies beyond the Tesseract closure. This must be corrected and
  re-reviewed before the slice is approved, committed or enabled in the API.
- The same fresh specification review reproduced a TIFF source-geometry error
  in the actual sandbox: EXIF orientation 6 was applied by Pillow and then a
  second time by the adapter. The correction must retain encoded TIFF geometry
  and record the decoder transform once, including mirrored orientations and
  later-frame cases. Native-v1 stored compatibility remains verified; the OCR
  slice is still unapproved while these two findings are corrected.
- Root then reproduced hidden RGB becoming OCR-visible when alpha was dropped
  from a fully transparent PNG inside the actual sandbox. Specification review
  independently confirmed RGBA/LA/palette PNG, WebP and TIFF variants. Rendering
  must use the explicitly frozen visible-frame/matte policy while retaining
  original geometry. This third source-integrity correction is also required
  before approval; earlier passing tests do not close these findings.
- Task 4b.1 passed specification re-review after freezing the Python/stdlib and
  adapter dependency closure, recording TIFF decoder orientation once and
  rendering alpha against the versioned white-paper matte. The final staged
  source at that review was `e7a0d5fe70a75b70009dcb1a2afc72951c294a14404b2a0f0912881e8ca4fcce`.
  Actual focused regressions and stored native-v1 / earlier OCR-v2 reads passed;
  the first 459-test run predates these corrections and is not final acceptance.
- Fresh independent quality review then reproduced an execution-identity gap
  inside Bubblewrap: Python `-I` can execute a valid timestamp/size-matching
  cached `.pyc` even though generated bytecode is excluded from the frozen
  source identity. The same fingerprint therefore executed different code.
  Source-only startup or verified executable bytecode, plus an actual-sandbox
  regression, is required before quality approval, commit or API enablement.
  No other Critical or Important issue was substantiated in that review.
- The bytecode repair adds a seventh allowlisted pure-source bootstrap. Parent
  staging verification passed all three cases, including the largest legal
  seven-file payload in a private sandbox. The selected
  `f7a4a7df4060b0f7c6a4b78ba231e19e2ce76c4817b9647b079e4baa3fbb6dc3`
  stage passed both real OS/parser readiness probes with the explicit
  `source-only-v1-no-site-no-zip-no-sourceless` execution identity. Parent frontend
  verification again passed 13 API tests, 5 DOM tests and TypeScript. Actual
  tampered-cache regressions, independent re-reviews and the final full backend
  run remain required before slice approval.
- Specification re-review of the bytecode repair reproduced another earliest
  startup path: a namespace-only sibling `/usr/lib/python314.zip` containing
  `encodings` executes before the inline loader policy, despite isolated/no-site
  flags and the redirected cache prefix. The later ZIP-hook removal therefore
  does not establish source-only interpreter startup. Real runtime files were
  not changed by the repro. This path must be excluded before interpreter startup
  and independently re-tested; the slice remains unapproved.
- The final startup repair adds a non-Python guard, an explicitly qualified
  public interpreter SHA-256 pin and required encoding source landmarks before
  either interpreter can start. Source-only hooks and explicit UTF-8 mode remain
  active afterward. Guard binaries/loader dependencies and venv configuration
  now enter new OCR identities; historical serialized profiles are unchanged.
  Parent verified the selected
  `0da7d95256d5b6bf3276cc9f1caeccd4b6c20b20a264f65f98413c08c9babd38`
  stage with both actual readiness probes. Helper/staging verification passed
  24 cases, including maximum seven-file payload and explicit pin forwarding.
  The repair awaits independent specification and quality re-review followed
  by the final complete actual backend run.
- Specification re-review passed for that final stage: eight actual startup,
  cache and loader regressions passed with no skips, both runner probes matched,
  and literal native-v1 / prior OCR-v2 encrypted artifacts remained readable with
  their original hashes. The missing/wrong public pin, early ZIP/dangling ZIP and
  initial sourceless-codec paths now refuse before Python executes. Quality
  re-review and root's complete actual integration run are in progress.
- Quality re-review passed on the same final stage: eight actual startup/cache
  regressions passed with zero skips and the original bytecode-identity finding
  is closed. No remaining Critical, Important or actionable Minor finding was
  reported for this slice. Root's complete integration run is still in progress;
  reviews alone do not complete that gate or the remaining full PRD.
- Root's complete actual run executed all 498 backend cases with zero skips:
  497 passed and the encrypted inspection-worker case failed. Its older storage
  fixture still forwarded only four runner settings and omitted the newly
  required public interpreter pin, so isolation correctly refused startup.
  This integration fixture must be corrected and re-qualified; the failing run
  is not a passing whole-suite result and the slice is not yet committed.
- The missing-pin fixture was corrected without changing product code or
  assertions. Its complete actual PG/S3/WSL storage suite passed 12 cases;
  specification and quality reviews approved the fixture correction. Root final
  actual qualification passed **497 tests, zero skips, one deselected** in
  556.30 seconds. The sole deselected case was the unchanged 100-page PDF test,
  already passed in the earlier complete run against the identical final parser
  code. Together these runs verify **all 498 cases**; this is combined coverage,
  not a claim that the earlier failing command passed. Frontend 13 API + 5 DOM
  tests and TypeScript also passed. Task 4b.1 is accepted within its intermediate
  image/OCR contract; remaining task-4b/F01–F50 and real acceptance gates stay open.
- Task 4b.1 was committed and pushed as `41afdd0`; the qualified local full API
  was restarted with baseline OCR enabled. The committed configuration example
  stays disabled until its own runtime is qualified. `main` / `mvp-baseline`
  remain at `1e88147c`. Task 4b.2 now implements isolated ZIP/RAR/7Z admission;
  its nine-file pure-code staging whitelist and maximum legal payload passed
  three helper checks, including an actual private sandbox. No archive support
  is yet accepted. The first nine-file archive implementation has now been
  staged as `d4cfafaf220d6bd879c9874ee71f01a19313fde3be2dec6571978d8b6d2b6e58`
  for actual qualification; the previous accepted OCR stage is preserved.
  Selecting this trial stage does not approve archive support or restart the API.
- The frontend now distinguishes historical package manifest v1 from archive
  manifest v2, including frozen admission identity, cumulative accounting and
  outermost-first source chains. A compile-time consumer test failed before
  these types existed and now passes; historical manifests do not acquire
  invented provenance fields. Archive implementation and reviews remain open.
- Root qualified the updated trial stage
  `80d832069766cab0a8fee27b1dd5b929a11f3b29e4d38cdd42ffe75a8e8252ad`:
  actual archive admission plus package input/API checks passed **58 cases,
  zero skips** in 149.35 seconds, including the nearly 50 MB expanded envelope.
  Staging/bootstrap checks passed **24 cases**, including actual private-tmpfs
  nine-file staging. This stage also rejects a file/empty-descendant-directory
  ambiguity found during implementation self-review. These are intermediate
  results; archive PostgreSQL/S3, remaining boundary coverage and independent
  specification/quality reviews are not yet complete. The preserved MVP is
  unchanged and this archive slice has not been committed or pushed.
- At that same trial stage, root's actual native artifact/storage/sandbox
  regression passed **48 cases, zero skips** in 122.15 seconds. The archive
  implementation's actual restricted PostgreSQL/private-S3 pair also passed:
  nested originals/source chains and idempotent replay were preserved; foreign
  scope and revoked actors were refused; revoke-after-PUT cleaned all failed
  originals. Compressed RAR fixtures identified a standard RAR5 common-header
  flag compatibility gap; its targeted repair and the remaining archive
  boundary tests precede independent review. No whole-slice acceptance is claimed.
- The standard RAR5 SKIP-header repair was staged as
  `76a5de34cf706ab6cba700dfa07fc7f7c4f42148ffc478531e7f7748ba2dae6f`.
  Additional actual-engine adversarial probes then proved that a checksummed 7Z
  unreferenced physical gap and a duplicated RAR5 MAIN header could still be
  accepted. These findings are open and require physical-layout/header-order
  repairs and regression coverage before specification/quality review. Native
  decoder success alone is not evidence of complete framing validation. The
  current archive qualification run at this intermediate stage is not final
  slice acceptance; no archive commit, push or API restart has occurred.
- That intermediate archive run ended after 35 passing cases with a synthetic
  corruption-test failure: it changed inert ZIP version metadata rather than
  corrupting physical payload bytes. The test needs the intended payload mutation;
  this command is not a passing qualification result. The two actual framing REDs
  above remain product defects being repaired, independently of that fixture issue.
- The framing repair adds explicit decoded-metadata/dictionary budgets. New
  snapshots use nested admission policy `archive-admission-v3`; package manifest
  schema remains 2. The already persisted intermediate v2 snapshots retain their
  original fields and identity. Frontend types now discriminate v2/v3 policies
  and require the new budgets only for v3. Its compile-time consumer test failed
  before the update and now passes, including a literal earlier-v2 shape. Actual
  framing/ordinary-archive qualification and independent reviews remain pending.
- An additional actual-engine RED showed libarchive accepting only a 512-byte
  prefix of a larger LZMA-compressed 7Z file when folder size, prefix CRC and
  header checksums were forged. Complete physical spans alone do not prove
  source completeness. The repair now requires independently bounded payload
  finish/consumption checks and separately records a 67,108,864-byte payload
  dictionary cap in v3. Frontend's added budget failed its compile-time consumer
  check before the type update. PPMd/branching BCJ2/unknown codec graphs remain
  explicit compatibility work; they cannot be accepted through a declared-size
  libarchive-only path, and their refusal is not full archive/PRD completion.
- Actual sandbox feasibility checks for the installed fixed 7zip integrity
  checker accepted genuine PPMd/BCJ2 fixtures and rejected their forged size/CRC
  variants, as well as the LZMA forgery. Integration is in progress with an
  anonymous seekable input, separate archive-only 50 MB file-size limit,
  unchanged 16 MiB tmpfs/native/OCR defaults, bounded single-thread child and
  explicit aggregate memory accounting. V3 frontend policy types were updated
  after compile-time REDs for those resource fields and now pass. Feasibility on
  small fixtures is not full near-limit or whole-slice qualification. The hard
  Linux decode watchdog is also being qualified; independent reviews remain open.
- The repaired first-v3 stage
  `4058e862579658318acb583e6b42d34f6f29dd6bb1b4c2cc47aee82d3abc56a5`
  was published for actual qualification without restarting the API. Root's
  staging/bootstrap suite passed 24 cases against this stage, including private
  tmpfs maximum-source staging. Runner unit checks passed 14 selected cases
  (15 actual-runner cases deselected), preserving native defaults. Those narrow
  checks do not replace the ongoing archive/PG/S3/near-limit qualification,
  independent reviews or final whole-backend regression. New archive v3 has not
  been accepted, committed or pushed.
- On the first-v3 stage, three actual near-limit/watchdog checks passed:
  seekable PPMd/BCJ2 mixed inputs near 49.8 MB preserved exact originals within
  the combined memory budget, and the hard watchdog interrupted a long C sleep.
  The main run instead found a real stored-RAR CRC defect; emitted-byte CRC
  verification and mandatory 7zip per-file integrity verification were added.
  These repairs were staged as
  `b39e5c471399be024f91e841afe60495fb2b29a7e422cda0f0bfa1eb4cfb876b`
  for fresh qualification. Earlier partial commands are not final passing gates.
- Root then reproduced a real hidden-name defect in that stage: a non-Unicode
  RAR4 filename containing an embedded NUL was accepted under only its prefix.
  The matching RAR5 case was refused. RAR4 raw-name validation and a regression
  are required, while legitimate RAR4 Unicode names must retain their supported
  encoding behavior. This finding remains open; archive acceptance/reviews,
  commit/push and API restart have not occurred.
- The complete actual archive/PG/S3/input/API command at `b39e5c47` subsequently
  finished with **90 passed, zero skips** in 333.12 seconds. This includes the
  previously fixed CRC/size/layout cases, near-limit seekable inputs, hard C
  watchdog, cleanup, scope/replay checks and a literal synthetic earlier-v2
  contract fixture read through actual PostgreSQL/S3. That fixture is not a
  requalification of the previous decoder or proof of reading a previously
  retained package; actual retained-history inspection remains a separate check.
  It does not cover or close root's additional RAR4 raw-NUL finding: the new
  behavior regression reproduced acceptance and remains RED until the raw-name
  and legitimate-Unicode repair is qualified. Independent review has not begun.
- Root independently read the actual retained prior-v2 package
  `02169cf0-1efb-429d-8dd9-7e76f88f87e9` from the earlier `80d832` qualification,
  through restricted PostgreSQL/S3 and a temporary authorized fixture viewer.
  All four encrypted originals matched their stored hashes; manifest bytes before
  and after remained identical, SHA-256
  `1c26175fa9cf2d798ff87856338a26ef83f4a74cac7ed63765ff070d0570bfa5`.
  Policy remains v2 with no invented v3 budgets. The temporary viewer was removed;
  no package/original/profile was rewritten. This is retained-history read
  evidence, not a rerun or approval of the older decoder's known behavior.
- The RAR4 raw-name/Unicode repair was staged as
  `a66b7f39aa056dc20a7d33d606366fa69bed774a22c6e9cbeee0e88d3e214d16`.
  Its six selected behavior cases passed (five actual sandbox cases plus one
  pure regression; 57 deselected), including the NUL refusals, exact Cyrillic
  UTF8/encoded names and an attributed genuine Japanese Unicode fixture.
  Full archive/PG/S3/input/API qualification is running against this stable stage.
  A possible extended RAR4 END-header control-data ambiguity is being investigated;
  it is not yet proven sandbox acceptance and no speculative acceptance claim is
  made. Independent specification/quality reviews remain pending.
- Root independently ran the actual native and Tesseract readiness probes on
  that same `a66b7f39` stage; both passed and returned the exact selected code
  hash. This checks the shared runtime boundary after the archive changes, not
  a substitute for native/OCR document regressions or whole-backend acceptance.
- The full actual archive/PG/S3/input/API run on `a66b7f39` completed with
  **96 passed, zero skips** in 375.02 seconds, including the new raw-name and
  Unicode cases. Subsequent actual-engine discovery confirmed the extended
  RAR4 END ambiguity: unused control bytes with a matching header checksum were
  accepted. This is a new concrete RED requiring documented flag/field-layout,
  archive-prefix CRC and reserved-data checks, while preserving legal optional
  footer records. The passing command remains intermediate evidence; no slice
  approval, commit/push or API restart has occurred.
- The concrete RAR4 END repair was staged as
  `853f653fbc3a0761e69c71c9bf265f4867b0364337040a657cb0a34cdd465bdb`.
  Four unit behavior regressions first reproduced acceptance, then passed after
  exact flags/field length/order, prefix CRC, zero reserved bytes and zero-volume
  checks. The non-actual selected suite passed 61 cases (71 deselected).
  Actual footer/name/Unicode qualification and a fresh full archive/PG/S3/input/
  API run are pending. Optional footer positives are explicitly derived from a
  genuine compressed fixture and attributed UnRAR definitions; no unchanged
  upstream standalone optional-footer example was obtained. Independent reviews,
  whole-backend acceptance, commit/push and API restart remain pending.
- Actual selected footer/name/Unicode qualification on `853f653f` finished with
  **25 passed, 57 deselected** in 59.25 seconds. The fresh complete four-suite
  archive/PG/S3/input/API command is running with no fail-fast option. These
  targeted results do not replace independent review or full-backend regression.
- The already running final archive/PG/S3/input/API command was collected after
  a model-usage interruption without restarting it: **115 passed, zero skips**,
  exit 0 in 400.84 seconds on exact stage `853f653f`. Only the existing Starlette
  deprecation warning remained. Implementer handoff is complete; a fresh
  read-only specification review is now underway. Quality review and parent
  full regression still precede acceptance, commit/push and API restart.
- Parent's frontend TypeScript contract check and all 13 API/5 DOM tests passed.
  Fresh independent SPEC review then found a concrete ZIP local/directory
  disagreement: with bit 3 clear, changed local CRC or either size was accepted
  in the actual sandbox. Parent independently reproduced all three on a public
  synthetic pure fixture. The original emitted bytes remained intact, but the
  required inconsistent-layout refusal was missing. This is an open repair gate;
  the passing 115-case command does not close it. Correct ordinary ZIP64 and
  data-descriptor layouts must remain supported during the repair.
- Fresh SPEC review on `853f653f` completed with that one confirmed P2 blocker;
  its independent actual selection passed **16 cases, 90 deselected, zero skips**
  in 53.66 seconds. Two additional stored-RAR4 tail mutations were refused.
  The implementer is repairing ZIP local-record consistency with behavior-first
  regressions, preserving legal local ZIP64 and data-descriptor layouts. SPEC
  re-review must pass on the repaired exact source before quality review begins.
- The ZIP local-record repair is staged as
  `64a264eae92939de5057996b5f2845469bb09aa39c4217ed384fc4e5e4308717`.
  Behavior-first tests reproduced 13 failures, then the selected non-actual
  archive/input tests passed **66 cases, 63 deselected**. Local CRC/sizes are
  checked against central metadata, complete bounded ZIP64 extra fields are
  resolved, and data-descriptor placeholders or exact matching filled values
  remain supported. No budget or admission-policy version was changed. Actual
  targeted qualification and independent SPEC re-review are pending.
- Targeted qualification on exact `64a264ea` completed: **35 passed, 100
  deselected, zero skips**, exit 0 in 73.23 seconds. This includes 14 actual
  sandbox cases and 21 unit cases, with corrected-header refusals, canonical
  normal/ZIP64 seekable/streaming positives, malformed extras, fixed ZIP64 end
  records, inert prefixes and nested DOCX originals. The same independent SPEC
  reviewer is rechecking the repair; no full-suite result is claimed for this
  new hash before the final parent gate.
- Independent SPEC re-review ran 13 fresh direct actual-sandbox probes on
  `64a264ea`: the three original local-field mismatches now refuse, and ordinary/
  ZIP64 and matching filled signed/unsigned descriptors preserve exact bytes.
  A separate bounded public CRC-collision probe identified one remaining P2
  compatibility defect: an unsigned descriptor whose CRC equals `0x08074b50`
  was mistaken for a signature and refused, while its signed counterpart passed.
  This is a valid-input refusal, not source truncation. Length-first descriptor
  parsing and normal/ZIP64 signed/unsigned collision regressions are being added.
  The 13 probes are not a passing full-suite command; quality review remains
  pending until this compatibility repair passes SPEC re-review.
- The bounded descriptor-signature repair is staged as
  `451c545c7dd012c4dd8ba89f152054be1a47b6230b9d502371837d2ae49b12a9`.
  Unsigned 12/20-byte records retain their CRC bytes; signed 16/24-byte records
  require and strip the marker before exact CRC/size comparison. Two unsigned
  collision tests reproduced failures before the change; signed controls passed.
  The repaired non-actual archive/input suite passed **70 cases, 73 deselected**.
  Fresh selected actual qualification and SPEC re-review precede quality review.
  Admission budgets, snapshot versions and native/OCR defaults remain unchanged.
- Fresh target on exact `451c545c` finished with **24 passed, 98 deselected,
  zero skips**, exit 0 in 59.46 seconds: 11 actual sandbox cases and 13 unit
  cases. All signed/unsigned normal/ZIP64 collision originals, earlier local
  mismatches and legal streaming/seekable cases passed their expected behavior.
  The same independent SPEC reviewer is rechecking this final bounded repair.
- Independent SPEC re-review approved Task 4b.2 on exact `451c545c` after seven
  fresh direct actual-sandbox probes (exit 0): all four collision originals were
  preserved and all three contradictory local fields refused. Both confirmed
  blockers are resolved. A fresh independent QUALITY review is underway; parent
  whole-backend verification, commit/push and API restart remain pending. This
  component approval does not satisfy expert quality, every archive variant or
  the full F01–F50 application scope.
- Fresh QUALITY review independently confirmed an additional physical-tail
  defect on `451c545c`: genuine compressed RAR4 admitted 23 appended public
  nonzero bytes or 16 zero bytes inside its declared packed member span after
  updating packed length/header CRC, while still emitting the exact 20-byte leaf.
  This violates the full compressed-stream consumption gate despite preserving
  that leaf's bytes. Unchanged RAR4/RAR5 controls, the RAR5 counterpart and a
  bounded independent decoder check are being investigated before choosing a
  repair. No quality approval, full acceptance or commit/push has occurred.
- QUALITY review completed with one Important/P2 blocker: compressed RAR4
  packed tails can be silently skipped on next-header traversal. Unchanged
  compressed RAR4/RAR5 controls pass; equivalent RAR5 tails refuse. The fixed
  installed 7zip tool cannot serve as a RAR repair oracle because it reports
  Unsupported Method on genuine compressed controls. An actual public
  `archive_filter_bytes` probe measured original file EOF at packed end 108,
  but forged file EOF 114 before declared packed end 131; subsequent traversal
  advanced past the unread tail. The implementer is checking completion before
  traversal with bounded short/long-tail and ordinary/solid positive controls.
  Counter read-ahead alone is not yet proof of bit-level completion. A minor
  liblzma ABI-setup duplication was also noted. No quality approval is claimed;
  final whole-backend regression, commit/push and API restart remain pending.
- Fourteen actual RAR4 tail regressions on `451c545c` reproduced failures
  (14 failed, 122 deselected, 83.67 seconds): zero/nonzero tails of 1, 2, 4, 6,
  7, 16 and 23 bytes were all accepted. An independent bounded counter matrix
  showed 1/2/4/6-byte tails fully counted as consumed; 7/16/23-byte tails left
  1/10/17 unread bytes. A counter-only change cannot close the whole finding.
  Logical bit-consumption/format-end and legal padding need a qualified deeper
  decoder adapter; a bounded pinned-source feasibility trace is the next step.
  Existing system libraries and accepted runtime assets must remain preserved.
  Pinned libarchive source also explicitly lacks RAR4 solid decoding; no RAR4
  solid positive or full F12 completion is claimed from other-format tests.
- Root prepared a dedicated user-owned native prototype builder without package
  installation or changes to `/usr`/accepted runtime: portable Zig 0.16.0
  (official tar SHA-256 `70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00`)
  and extracted Ubuntu GNU Make 4.4.1-3
  (`a86f39d57a32b7c919c0ad721fc2f17ab533a42fda348c8d81a4eea1577a014f`).
  Both actual version commands passed; builder inputs/binaries/provenance are
  recorded in its local manifest. Child environments contain no application
  credentials. The public libarchive 3.8.5 release source
  (`d68068e74beee3a0ec0dd04aee9037d5757fcc651591a6dcf1b6d542fb15a703`)
  has a RAR translation unit matching pinned upstream `dd897a78` byte-for-byte.
  A compiled public trace API against actual source structs is being built in a
  separate prototype directory; no private ABI offsets are guessed and no
  production library/profile/policy has been selected from this experiment.
- User superseded the native-decoder branch: all native prototype sessions were
  verified terminal with no matching descendants. No services or system/runtime
  libraries were killed, replaced or restarted. Current pure stage remains
  `451c545c`; the native prototype remains unselected. The next implementation
  replaces bespoke archive internals with standard adapters, records per-file
  errors in the database, and continues valid documents. Obsolete native-tail
  experiments are retired from product acceptance; the revised contract above
  is authoritative. No full application completion is claimed.
- Standard dependencies were installed from binary wheels only: rarfile 4.5,
  py7zr 1.1.4 and their pinned dependency closure. All previously installed
  runtime package versions were constrained and verified unchanged. The ready-made
  Ubuntu UnRAR package 1:7.2.4-1 was checksum-verified and extracted under the
  document runtime without system installation or compilation; its executable
  is `/runtime/opt/unrar/usr/bin/unrar-nonfree` (actual banner UnRAR 7.20).
  The agreed new contract is package manifest schema 3 with `issues`, admission
  policy v4 with basic limits, and nullable `inspection_job_id` for an all-skipped
  package. Existing schema 1/2 and policy v2/v3 snapshots remain literal history.
  Parent's compile-consumer regression first failed on missing issue/schema/null
  handling, then TypeScript passed with the new discriminated types. Backend
  standard-adapter/audit/continuation implementation and actual checks are pending.
- The user reconfirmed that no real expert-labelled tender/bid files are
  available. The existing expert-case template parses as valid JSON and remains
  explicitly unfilled (`dataset_kind: template`); synthetic documents continue
  to support development, not real quality acceptance. Fresh parent frontend
  verification passed TypeScript and all 18 tests (13 API-contract, 5 DOM),
  including the schema-3/v4 and nullable-job compile consumer. The installation
  recipe now requires binary wheels and the standard distribution UnRAR tool,
  without native decoder compilation.
- Standard pure source `b29509a2` was staged. Parent's first actual archive/
  PostgreSQL target ended with **17 passed, 2 failed, 5 setup errors**, exit 1.
  ZIP/RAR4/RAR5/7Z positive controls and the 50 MB expanded envelope passed.
  The timeout cleanup observation exceeded its 5-second WSL observation bound;
  a two-second whole-process budget also skipped the subsequent valid archive.
  Neither failure is accepted as passing. PostgreSQL was unavailable during
  the five integration setups; the existing cluster and local S3/broker were
  restored and restricted-role/migration/FORCE-RLS health verified afterward.
  Independent SPEC review also confirmed resource/time refusals were labelled
  `ARCHIVE_CORRUPTED` instead of the documented `UNSUPPORTED_FORMAT`; exact-code
  regressions and a bounded repair precede requalification. No native decoder
  research is resumed.
- Resource-classification repair was staged as pure hash
  `f8eaead35f0b9680c81d28e93be058826eaf19c3cf0b4907e62e44a12d55904e`.
  Known process signal exits are retained as bounded internal provenance;
  native/OCR public error codes remain unchanged. Archive resource/time/size
  refusals now map to `UNSUPPORTED_FORMAT`. The timeout continuation test gives
  the subsequent valid file its normal process budget, and descendant observation
  retains a separate bounded WSL allowance. Fresh parent actual target passed
  **all 27 cases, zero skips**, exit 0 in 119.56 seconds. This includes the four
  formats, 50 MB output, actual timeout/cancel/death cleanup, three resource-signal
  controls, restricted PostgreSQL/private S3 originals and exact-once audits,
  all-bad inspection, literal historical reads, and normalization of only the
  surviving archived document with duplicate delivery. Independent re-review and
  the broader shared-runner regression gate precede the archive checkpoint.
- Independent SPEC re-review approved the standard resource-classification
  repair after 16 selected source checks and two fresh actual integration checks.
  Parent then reproduced a basic cross-wire/archive-member path collision: one
  input order failed the whole package; the other published duplicate paths.
  A seven-line orchestration repair reserves wire paths and discards colliding
  container children before publication or counter charging, retaining the
  archive original and valid wire file. Its final pure stage is
  `0176cdba88438280d99532f8b30234f384c4164fc697e67eb4c9bc118c7691bf`.
  The implementer reports RED two-case reproduction, then **54 source tests
  passed** and **two actual collision cases passed**, zero skips, exit 0.
  Final independent review and parent regression remain pending; earlier 27-case
  qualification belongs to the intermediate `f8eaead3` stage.
- Final independent SPEC and QUALITY reviews approved the standard-adapter
  component at `0176cdba`. Parent's full backend gate passed **551 tests, zero
  skips**, exit 0 in 1144.52 seconds, with actual restricted PostgreSQL, RabbitMQ,
  private S3, native parsers and OCR enabled. Frontend TypeScript, all 18 tests
  and the optimized Next.js production build passed. Only the existing Starlette
  deprecation warning remains.
- Staging exposed encoded UU padding whitespace and an extra source EOF blank
  line. Fixture-specific `.gitattributes` preserves the encoded records; the
  source whitespace cleanup has an identical AST, independently checked against
  the retained approved runtime. The selected final pure hash is
  `d8eb2706ede1c93153c6cb299fc50d4b1c0710cac48b6c0b67af8e4e96746665`.
  Fresh parent verification on this identity passed **36 tests, zero skips**,
  exit 0 in 60.37 seconds: standard-unit behavior, all four format controls,
  both collision orders, actual surviving-file normalization, native encrypted
  artifact publication and PNG OCR. The broader 551-case result belongs to the
  behaviorally identical preceding source; it is not a second full-suite run.
  Cached whitespace checks and a private-configuration-value scan passed.
  `analysis-contract.md` defines the next grounded extraction/evaluation delivery;
  no full application or expert-quality completion is claimed.
