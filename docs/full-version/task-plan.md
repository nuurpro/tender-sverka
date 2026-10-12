# Execution order for the full application

This sequence implements the accepted full PRD; the F01–F50 ledger and its real
acceptance gates remain the definition of completion. Each implementation task
uses behavior tests first, independent specification review, then quality review.
This document records dependencies, not a reduction of scope.

1. **Versioned persistence and runtime checks.** PostgreSQL/Alembic, restricted
   tenant role, FORCE RLS, draft/approve/fork checklist history, immutable package
   and comparison inputs, transactionally audited changes and idempotent outbox.
   Gate: actual PostgreSQL runtime-role tamper/isolation/concurrency tests.
2. **Authentication and workspace API.** Phone OTP through an explicit SMS
   provider adapter, bounded attempts/rates, one-time consumption, opaque hashed
   server sessions, cookie/CSRF protections, active user/membership checks, role
   enforcement, organizations, projects, participants, checklist history/editing
   and audit endpoints. OIDC is a later adapter to the same identity/session flow.
   Gate: unauthenticated/foreign-tenant/viewer/CSRF requests cannot mutate or read
   restricted resources; expired/revoked sessions and consumed codes are rejected.
3. **Durable execution and object storage.** Celery/RabbitMQ outbox publisher,
   leases, checkpoint/retry/cancel, progress SSE, encrypted S3 originals and
   derivatives, tenant-qualified hashes, package preflight and manifests.
4. **Document pipeline.** Safe archive expansion; all office/image formats;
   conversion; OCR/layout/tables/dewarp/handwriting; confidence and corrections;
   classification; original-coordinate anchors; drawings then DWG/DXF adapter.
5. **Extraction and evidence.** Structured requirements, predicates, lot items,
   references/contradictions/duplicates; bid facts and source precedence; gateway
   capability profiles, organization permission, token/cost budgets and caches.
6. **Evaluation.** Decimal unit/numeric/date rules and semantic comparison;
   evidence/coverage validation; exact per-requirement results; five bids with
   frozen input snapshots; scoring intervals/categories/readiness; versioned
   overrides, recommendation actions and deterministic improvement estimates.
7. **Workspace UI.** Phone login, organizations and project dashboard; workflow
   steps; versioned checklist editor; five-bid matrix; document split-view/Sheet;
   source highlights/translations, charts, filters, RU/UZ/EN and accessibility.
8. **Reports and change workflows.** Snapshot PDF/XLSX/JSON, DOCX clarification,
   one-page summary, audit/report versions, amendments, conservative dependency
   invalidation and authorized project chat.
9. **Administration and integrations.** Dictionaries/templates/policies/model
   capabilities; lot links, platform OIDC, TenderZone/TenderGPT and registries,
   each with as-of provenance and explicit integration acceptance.
10. **Production qualification.** Retention/deletion and key rotation; storage in
    Uzbekistan; redundant deployment, metrics, backups and restore drill; real
    expert data, OCR/extraction/status/recommendation quality gates, load and
    end-to-end timing, pilots and processor/privacy review.

## Authentication design decisions for task 2

The browser never supplies a trusted organization, role or user identity. An
opaque session identifies the user; every organization selection rechecks
membership inside a tenant transaction. Admins manage organization policy and
membership; admins/specialists edit projects; viewers can only read/export.
Request headers cannot substitute for a verified session.

Development is explicitly configured with `MockSmsProvider` and fixed code
`123456`, as requested by the user; no real SMS connection is used. Production
rejects mock mode. Eskiz is the planned production adapter, not a connected
service. The following protections apply to real-provider authentication.

Phone challenges store a keyed verifier, expiry and attempt counter; plaintext
codes are passed only to the SMS adapter, never returned or logged. Provider
failure cannot create a usable challenge. Consumption, user creation and session
creation occur atomically. Resending invalidates earlier codes; throttles apply
to normalized phone and client address, with no forwarded-header trust by default.
Production SMS/OIDC acceptance requires real configured providers.

Session cookies carry random verifiers; the database stores only their hashes.
Production cookies are Secure, HttpOnly and SameSite with no Domain attribute.
Unsafe browser methods require a session-bound CSRF token and exact allowed
Origin; SameSite alone is insufficient. Sessions expire and can be revoked,
and disabled users/revoked memberships lose access immediately. These decisions
follow the [OWASP session guidance](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

The new API has its own entrypoint. The unauthenticated MVP routes are not
mounted into the production application. Legacy tests still exercise the
preserved MVP, not proof of authentication for the new API.
