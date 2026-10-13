# Full-version implementation and acceptance ledger

The approved plan is the full Tender-Sverka application described in the original
PRD (draft 0.1, 8 October 2026), plus the agreed five-participant comparison UI.
The `mvp-baseline` tag preserves the previous application. This ledger records
evidence, not a smaller definition of completion.

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
| F12 | ZIP/RAR/7Z, preserved folders and safe bounded extraction | Bounded ZIP physical-output/CRC/path validation, folders and originals verified; isolated ZIP admission and RAR/7Z pending |
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
