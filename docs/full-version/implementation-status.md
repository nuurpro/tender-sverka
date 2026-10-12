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
| F02 | Tenant/project/participant isolation including search, cache and downloads | Repository + API + actual PG RLS verified; search/cache/downloads pending |
| F03 | Dashboard, lots, deadlines, supplier and buyer modes | Dashboard/project creation/deadline/report language/modes verified; lot integrations pending |
| F04 | Immutable tender/bid revisions and source originals | Pending |
| F05 | PostgreSQL schema, Alembic, FORCE RLS with restricted application role | Foundation + migration/startup role checks verified on PG14; PG18 deployment pending |
| F06 | Durable jobs, transactional outbox, leases, retry/cancel and SSE progress | Substrate/API verified on actual PG/RabbitMQ; real document stages pending |
| F07 | Idempotent mutations and optimistic edit conflicts | Checklist revisions/job creation + HTTP retry/cancel replay verified; remaining mutations/pipeline stages pending |
| F08 | 20 files/50 MB, expanded archives, page/token/context/budget preflight | Pending |
| F09 | PDF text/scans, DOC/DOCX/RTF/ODT/TXT | MVP text PDF/DOCX only |
| F10 | XLS/XLSX/ODS/CSV, merged cells and safe formula values | MVP XLSX only; formulas excluded |
| F11 | JPG/PNG/TIFF/HEIC/BMP/WEBP, phone photos and multipage TIFF | Pending |
| F12 | ZIP/RAR/7Z, preserved folders and safe bounded extraction | Pending |
| F13 | Pasted text and authorized lot-link import | Pending |
| F14 | Document-type classification and manual correction | Pending |
| F15 | OCR, layout, dewarp/rotation, tables across pages, handwriting | Pending |
| F16 | OCR confidence, expert corrections and numeric review thresholds | Pending |
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
| F27 | Exact evidence from both packages, coordinates, translations and viewer | MVP exact text + page/row only |
| F28 | Verified missing evidence vs unsupported claim; incomplete coverage -> review | Pending |
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
| F43 | Content-addressed tenant-isolated parser/extraction/comparison caches | Pending |
| F44 | Gateway capability profiles, budgets, bounded failure recovery | MVP chat/responses adapter only |
| F45 | Reproducibility via snapshots/cache and explicit new evaluation runs | Pending |
| F46 | TLS, AES-256 at rest, retention (12 months), deletion incl. derived data | Pending |
| F47 | TenderZone/TenderGPT lot/SSO/commission integrations | Pending; external contracts required |
| F48 | Company/bad-performer/license registry checks with as-of provenance | Pending; external contracts required |
| F49 | Dictionaries, templates, types, policy and provider administration | Pending |
| F50 | Observability, backup/restore, redundant services and availability | Pending |

## Acceptance gates (not satisfied by synthetic demo documents)

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
