import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AnalysisPanel } from "../components/workspace/analysis-panel";
import { WorkspaceApiError, type WorkspaceApi } from "../lib/workspace-api";
import type { AnalysisDetail, AnalysisPreflight, AnalysisStart } from "../lib/analysis-types";
import type { NormalizationRun } from "../lib/document-types";

configure({ getElementError: message => new Error(message || "Element not found") });
afterEach(cleanup);
const normalization: NormalizationRun = { profile_hash: "profile", analysis_allowed: false, job: { id: "normalize-a", project_id: "project-a", stage: "package.normalize", status: "succeeded", attempt: 2, progress: 100, error: null, event_sequence: 4 } };
const preflight: AnalysisPreflight = { external_ai_allowed: true, policy_version: 1, frozen_identity: "frozen", input_token_upper_bound: 8000, package_text_token_upper_bound: 1000, request_count: 2, extraction_chunk_count: 2, reference_lookup_count: 0, reference_links: [], max_provider_attempts: 4, context_tokens: 32768, output_tokens: 4096, budget: { max_requests: 32, max_tokens: 1000000, cost_ceiling: null }, worst_case_run_tokens: 32000, pricing_known: false, coverage_warnings: [], artifacts: [] };
const started: AnalysisStart = { job: { ...normalization.job, id: "extract-a", stage: "analysis.extract", status: "queued", progress: 0, attempt: 0 }, run: { id: "run-a", project_id: "project-a", package_id: "package-a", participant_id: null, checklist_id: "checklist-a", expected_revision: 1, policy_version: 1, status: "draft", result: {}, created_at: "2026-10-14T08:00:00Z" } };
const props = { organizationId: "org-a", projectId: "project-a", packageId: "package-a", kind: "tender" as const, normalization, canEdit: true, canAdmin: true, onExpired: () => {} };
function api(overrides: Record<string, unknown> = {}) {
  return { aiPolicy: async () => ({ allowed: true, version: 1 }), analysisRuns: async () => [], analysisPreflight: async () => preflight, ...overrides } as unknown as WorkspaceApi;
}

test("external AI policy requires explicit admin consent before starting analysis", async () => {
  let starts = 0;
  let changed: unknown;
  const client = api({ aiPolicy: async () => ({ allowed: false, version: 0 }), setAiPolicy: async (_org: string, input: unknown) => { changed = input; return { allowed: true, version: 1 }; }, startAnalysis: async () => { starts++; return started; } });
  render(<AnalysisPanel {...props} api={client} />);
  assert.ok(await screen.findByText(/Документы передаются настроенному внешнему AI-провайдеру/));
  assert.equal((screen.getByRole("button", { name: "Начать анализ" }) as HTMLButtonElement).disabled, true);
  fireEvent.change(await screen.findByLabelText("Основание разрешения"), { target: { value: "Учебные документы разрешены" } });
  fireEvent.click(screen.getByRole("button", { name: "Разрешить передачу документов" }));
  await waitFor(() => assert.deepEqual(changed, { allowed: true, expected_version: 0, reason: "Учебные документы разрешены" }));
  assert.equal(starts, 0);
});

test("preflight displays budget and unknown pricing before an idempotent start retry", async () => {
  const requests: { key: string; options: unknown }[] = [];
  const client = api({ startAnalysis: async (_org: string, _project: string, _package: string, options: unknown, key: string) => { requests.push({ key, options }); throw new WorkspaceApiError("Ответ анализа потерян.", 503); } });
  render(<AnalysisPanel {...props} api={client} />);
  fireEvent.click(await screen.findByRole("button", { name: "Оценить объём анализа" }));
  assert.ok(await screen.findByText(/32.?000/));
  assert.ok(screen.getByText(/Стоимость не рассчитана/));
  fireEvent.click(screen.getByRole("button", { name: "Начать анализ" }));
  await screen.findByText("Ответ анализа потерян.");
  fireEvent.click(screen.getByRole("button", { name: "Начать анализ" }));
  await waitFor(() => assert.equal(requests.length, 2));
  assert.equal(requests[0].key, requests[1].key);
  assert.deepEqual(requests[0].options, { normalization_job_id: "normalize-a", normalization_attempt: 2, budget: preflight.budget });
});

test("reload restores a failed analysis and retries the existing job", async () => {
  const failed: AnalysisStart = { ...started, job: { ...started.job, status: "failed", attempt: 1, error: { code: "provider_status", message: "Ошибка провайдера." } } };
  let retried = 0;
  const client = api({ analysisRuns: async () => [failed], analysisRun: async () => ({ ...failed, frozen: {}, calls: [] }), retryJob: async (_org: string, _project: string, job: string) => { assert.equal(job, failed.job.id); retried++; return { ...failed.job, status: "queued" }; }, startAnalysis: async () => { assert.fail("restore must retry its original job"); } });
  render(<AnalysisPanel {...props} api={client} />);
  assert.ok(await screen.findByText("Ошибка провайдера."));
  fireEvent.click(screen.getByRole("button", { name: "Повторить анализ" }));
  await waitFor(() => assert.equal(retried, 1));
});

test("analysis polling reaches published results without losing original citations", async () => {
  let reads = 0;
  const final: AnalysisDetail = { ...started, job: { ...started.job, status: "succeeded", progress: 100, attempt: 1 }, run: { ...started.run, status: "published", result: { requirements: [{ scope: "product", item: "Ноутбук", category: "Технические", parameter: "Память", condition: "Не менее 16 ГБ", importance: "mandatory", predicate: { kind: "numeric", operator: "gte", value: "16", upper: null, tolerance: null, unit: "ГБ" }, review_flags: [], citations: [{ evidence_id: "ev:a", artifact_id: "artifact-a", document_id: "document-a", filename: "tender.txt", block_id: "b-a", quote: "RAM:  16 GB minimum", source_start: 0, source_end: 19, quote_kind: "native_original", confidence: null, validated: true, review_flags: [], anchor: { kind: "text", encoding: "utf-8", line_start: 7, line_end: 7, byte_start: 0, byte_end: 19, char_start: 0, char_end: 19, record: null, fields: null } }] }] } }, frozen: {}, calls: [] };
  const client = api({ analysisRuns: async () => [started], analysisRun: async () => ++reads > 1 ? final : { ...started, frozen: {}, calls: [] } });
  render(<AnalysisPanel {...props} api={client} />);
  assert.ok(await screen.findByText("Не менее 16 ГБ", {}, { timeout: 5000 }));
  fireEvent.click(screen.getByRole("button", { name: "Источник: Память" }));
  assert.ok(await screen.findByText("RAM: 16 GB minimum"));
  assert.ok(screen.getByText(/tender.txt/));
  assert.ok(screen.getByText(/Строка 7/));
  assert.ok(reads >= 2);
});

test("a delayed policy response cannot enable analysis for another organization", async () => {
  let resolve!: (value: { allowed: boolean; version: number }) => void;
  const response = new Promise<{ allowed: boolean; version: number }>(yes => { resolve = yes; });
  const client = api({ aiPolicy: async (org: string) => org === "org-a" ? response : { allowed: false, version: 0 } });
  const view = render(<AnalysisPanel {...props} api={client} />);
  view.rerender(<AnalysisPanel {...props} organizationId="org-b" projectId="project-b" packageId="package-b" api={client} />);
  await act(async () => { resolve({ allowed: true, version: 1 }); });
  assert.ok(await screen.findByRole("button", { name: "Разрешить передачу документов" }));
  assert.equal((screen.getByRole("button", { name: "Начать анализ" }) as HTMLButtonElement).disabled, true);
});

test("preflight stays locked until initial run history is restored", async () => {
  let resolve!: (value: AnalysisStart[]) => void;
  const history = new Promise<AnalysisStart[]>(yes => { resolve = yes; });
  let calls = 0;
  const client = api({ analysisRuns: async () => history, analysisPreflight: async () => { calls++; return preflight; } });
  render(<AnalysisPanel {...props} api={client} />);
  assert.equal((screen.getByRole("button", { name: "Оценить объём анализа" }) as HTMLButtonElement).disabled, true);
  fireEvent.click(screen.getByRole("button", { name: "Оценить объём анализа" }));
  assert.equal(calls, 0);
  await act(async () => { resolve([]); });
  assert.equal((screen.getByRole("button", { name: "Оценить объём анализа" }) as HTMLButtonElement).disabled, false);
});

test("another document mutation locks analysis actions", async () => {
  let calls = 0;
  const client = api({ analysisPreflight: async () => { calls++; return preflight; } });
  render(<AnalysisPanel {...props} api={client} locked />);
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Оценить объём анализа" }) as HTMLButtonElement).disabled, true));
  await act(async () => { await Promise.resolve(); });
  fireEvent.click(screen.getByRole("button", { name: "Оценить объём анализа" }));
  assert.equal(calls, 0);
});

test("failed history restoration blocks new analysis until a successful retry", async () => {
  let reads = 0;
  const client = api({ analysisRuns: async () => { if (++reads === 1) throw new WorkspaceApiError("История временно недоступна.", 503); return []; } });
  render(<AnalysisPanel {...props} api={client} />);
  await screen.findByText("История временно недоступна.");
  assert.equal((screen.getByRole("button", { name: "Оценить объём анализа" }) as HTMLButtonElement).disabled, true);
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку анализа" }));
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Оценить объём анализа" }) as HTMLButtonElement).disabled, false));
  assert.equal(reads, 2);
});

const citation = { evidence_id: "ev:b", artifact_id: "artifact-b", document_id: "document-b", filename: "certificate.pdf", block_id: "block-b", quote: "Expires 2028-01-01", source_start: 0, source_end: 18, quote_kind: "native_original" as const, confidence: null, validated: true as const, review_flags: [], anchor: { kind: "part" as const, part: "certificate", page: 2 } };
function published(result: AnalysisStart["run"]["result"]) {
  const value: AnalysisDetail = { ...started, job: { ...started.job, status: "succeeded", progress: 100 }, run: { ...started.run, status: "published", result }, frozen: {}, calls: [] };
  return api({ analysisRuns: async () => [value], analysisRun: async () => value });
}

test("review findings open their original source citations", async () => {
  render(<AnalysisPanel {...props} api={published({ findings: [{ kind: "contradiction", detail: "Документы указывают разные сроки", citations: [citation], review_flags: ["contradiction_review"] }] })} />);
  fireEvent.click(await screen.findByRole("button", { name: "Источник замечания 1" }));
  assert.ok(await screen.findByText("Expires 2028-01-01"));
  assert.ok(screen.getByText("certificate.pdf"));
  assert.ok(screen.getByText("Страница 2"));
});

test("restored results keep unavailable document coverage visible", async () => {
  render(<AnalysisPanel {...props} api={published({ coverage: [{ filename: "drawing.dwg", state: "unavailable", reason: "no_published_artifact" }, { state: "unavailable", reason: "archive_member_skipped", detail: { path: "broken.zip", code: "ARCHIVE_CORRUPTED" } }] })} />);
  assert.ok(await screen.findByText("drawing.dwg"));
  assert.ok(screen.getByText("Нет результата обработки документа"));
  assert.ok(screen.getByText("broken.zip"));
});

test("date-only bid facts retain dates and review flags in the source drawer", async () => {
  render(<AnalysisPanel {...props} kind="bid" api={published({ facts: [{ item: null, model: null, parameter: "Срок действия", value: null, unit: null, date: "2028-01-01", source_kind: "certificate", review_flags: ["internal_conflict_review"], citations: [citation] }] })} />);
  assert.ok(await screen.findByText("2028-01-01"));
  assert.ok(screen.getByText("Требует проверки"));
  fireEvent.click(screen.getByRole("button", { name: "Источник: Срок действия" }));
  assert.ok(await screen.findByText("Источник требует проверки"));
  assert.ok(screen.getAllByText("2028-01-01").length >= 2);
});
