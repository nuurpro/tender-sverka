# Grounded analysis contract — tasks 5–6

Status: implementation contract, not a completed feature. The next delivery
connects the existing authenticated package/normalization/job substrate to
extraction, checklist approval and comparison. The full PRD ledger still applies.
The user has no expert corpus; synthetic execution checks do not certify quality.

## Inputs and evidence

Select an explicit package revision and terminal normalization attempt. Use its
verifiable published artifacts; a partially failed attempt may continue with
valid documents while retaining every failed/unavailable document as a coverage
limitation. Do not treat running or cancelled attempts as complete evidence.
Freeze artifact IDs, source hashes, processing profiles, package manifest,
report language and tender deadline. A retry uses these inputs, not the latest
upload. Never read arbitrary object keys or documents supplied by the AI/browser.

Each AI input block receives a server-owned evidence identifier bound to exactly
one artifact and local block. AI citations contain that identifier and a quote.
Resolve package/document IDs, anchors, original filenames and processing
provenance from the frozen authorized artifact. Validate the quote against the
appropriate source after whitespace normalization; preserve its original
language and distinguish OCR transcription from native original text. A wrong
artifact, nonexistent block or fabricated quote cannot establish compliance.

Carry unreadable/skipped archive files, incomplete coverage, unsupported regions,
formula values and OCR review flags into analysis. A package can continue with
valid evidence while those limitations remain visible. Missing evidence after
complete inspection and unavailable source regions are different results.

## Gateway and organization policy

Use the configured AgentRouter gateway through Chat Completions JSON mode and
local Pydantic validation. Use existing `CODEX_GATEWAY_API_KEY` configuration;
never switch automatically to official OpenAI or the synthetic provider.
No document can be transmitted until its organization permits external AI.
Admins change that versioned policy through the authenticated organization API.
Check current permission and actor access before each request and publication.

Freeze provider URL, model, protocol, prompt/schema versions, context/output
limits and pricing policy in each analysis job. Credentials remain runtime
secrets. Model IDs alone do not establish JSON/vision/context capabilities.
Use explicit profiles; unsupported gateway requests fail with a bounded useful
error. Do not save SDK exceptions, credentials or full document prompts in logs.

Preflight must present package token upper bounds, per-request context limits,
total-run request/token limits and the configured cost ceiling before starting.
Reserve the worst-case input/output budget before a call, reconcile returned
usage, and stop further calls when the run budget is exhausted. Missing provider
usage stays explicitly unknown; never present a fabricated exact price.
Chunk whole evidence blocks, splitting oversized blocks with retained source
offsets; do not silently truncate the package. Cache only inside the authorized
organization/project with frozen inputs, profile, prompt and schema identities.

## Extraction and checklist publication

Requirements are atomic and distinguish product from participant. Preserve lot
item, category, condition, importance (`mandatory`, `scored`, `desirable`), exact
evidence and typed predicate. Numeric values/tolerances use decimal strings and
explicit units/operators; dates retain deadline basis/timezone. Unknown values
remain unknown rather than being guessed. Stable display IDs use the existing
`T-*` / `U-*` convention; server-assigned logical IDs survive checklist forks.

Repeated requirements combine evidence conservatively. Conflicting conditions,
ambiguous references and potentially restrictive procurement language become
explicit review findings with sources; they cannot be silently merged away.
Resolve cross-references over the complete frozen package. Extract bid facts
separately for each participant, preserving item/model/value/date/source kind,
evidence and internal conflicts; a declaration is not a certificate or registry
verification. Record source priority as a versioned rule.

Create an auditable draft as the extraction target. Publication checks its
expected revision/status and uses the existing job lease fence; a concurrent
manual edit or approval cannot be overwritten. Checkpoint completed chunks and
publish the finished checklist once. Retry/cancellation preserves completed
checkpoints and avoids duplicate requirements, drafts or audit events. Empty
checklists and document-derived requirements with invalid evidence cannot be
approved. Manual additions record their origin/reason and remain distinguishable
from document-derived requirements; they cannot invent a document citation.

## Evaluation and downstream contracts

A comparison run freezes an approved checklist, one supplier bid or up to five
buyer bids, normalized inputs, deadline and scoring/source policies. Produce
exactly one row per requirement/participant; evidence is scoped to that bid and
the tender. Deterministic Decimal rules evaluate supported numeric/unit/date/set
predicates. The gateway handles semantic cases; incomplete coverage, uncertain
numbers, contradictions and ungrounded claims route to review.

Python alone calculates weights 3/2/1, coefficients 1/0.5/0.3/0, known-row scores
and min/max ranges for review rows. Mandatory failures set critical risk;
mandatory review prevents a ready-to-submit verdict. Include scope/category/item
scores and counts. Versioned manual overrides require a reason and retained
before/after evidence. Recommendations identify concrete actions and affected
requirements; deterministic impact counts their union once.

The matrix, source drawer, recommendations and exports consume the same frozen
run representation. Replacing a bid or editing a checklist creates new inputs;
historical runs and reports remain readable. PDF/XLSX reports must agree with
the displayed snapshot, including review ranges, risks, citations and audit IDs.

## Module boundaries and verification

Create `backend/app/analysis/` modules for strict schemas, evidence binding,
gateway/budgets, extraction orchestration, routes and job handlers. Add versioned
organization AI policy, analysis runs/call accounting and bid-fact persistence
through Alembic with composite tenant/project ancestry, FORCE RLS and the
existing restricted runtime role. Extend `jobs/scoped_writes.py` explicitly for
new project-domain writes; provider/network work stays outside SQL transactions.
Keep `backend/app/ai.py` and the MVP entrypoint independent.

Follow with `backend/app/evaluation/` for pure rules, scoring, comparison jobs,
overrides and recommendations. Register real stages in the existing worker and
mount authenticated nested routes in `full_main.py`. Frontend uses typed APIs,
job progress and the approved run contract; report modules consume snapshots.

Verification must exercise the actual SDK with an HTTP fixture (JSON mode,
invalid/truncated/refused responses, timeout/rate-limit handling, budget and
no-fallback behavior), exact evidence binding and coverage routing, actual
restricted PostgreSQL tenant isolation/audits/replay/concurrent edits, and real
durable delivery. A separate configured AgentRouter smoke test uses synthetic
content and records model/protocol/usage without exposing its key. Full workflow
acceptance then covers upload → normalization → editable draft → approval → five
independent bids → matrix/evidence → PDF/XLSX, including a skipped bad archive.
