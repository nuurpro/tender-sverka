import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { act, cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProjectDocuments } from "../components/workspace/project-documents";
import { WorkspaceApiError, type WorkspaceApi } from "../lib/workspace-api";
import type { PackageDetail, PackagePreflight } from "../lib/package-types";
import type { Project } from "../lib/workspace-types";
import type { NormalizationRun } from "../lib/document-types";

configure({ getElementError: message => new Error(message || "Element not found") });
afterEach(cleanup);
const project: Project = { id: "project-a", organization_id: "org-a", mode: "buyer", lot_name: "Учебный лот", customer: null, deadline: null, timezone: "Asia/Tashkent", report_language: "ru", revision: 1, created_at: "2026-10-14T08:00:00Z" };
const packet: PackageDetail = { id: "package-a", project_id: project.id, kind: "tender", participant_id: null, version: 1, status: "uploaded", created_at: project.created_at, documents: [], manifest: { schema_version: 1, documents: [], accounting: { wire_files: 1, wire_bytes: 7, logical_files: 0, logical_bytes: 0, archive_containers: 1, expanded_bytes: 0 } } };
const preflight: PackagePreflight = { ...packet.manifest.accounting, inspection_status: "inspected", analysis_allowed: false, formats: [], page_count: 0, token_count: 0, token_upper_bound: 0, blockers: ["no_available_documents"], warnings: ["archive_files_skipped"], issues: [{ path: "bad.zip", code: "ARCHIVE_CORRUPTED", message: "Archive could not be decoded." }] };
function api(overrides: Record<string, unknown> = {}) {
  return { participants: async () => [], packages: async () => [packet], packageDetail: async () => packet, packagePreflight: async () => preflight, normalizationRuns: async () => [], aiPolicy: async () => ({ allowed: false, version: 0 }), analysisRuns: async () => [], checklists: async () => [], ...overrides } as unknown as WorkspaceApi;
}

test("all-skipped packages show the file issue and cannot start normalization", async () => {
  let calls = 0;
  render(<ProjectDocuments api={api({ normalizePackage: async () => { calls++; } })} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  assert.ok(await screen.findByText("bad.zip"));
  assert.ok(screen.getByText("Архив повреждён"));
  assert.equal((screen.getByRole("button", { name: "Обработать документы" }) as HTMLButtonElement).disabled, true);
  fireEvent.click(screen.getByRole("button", { name: "Обработать документы" }));
  assert.equal(calls, 0);
});

test("upload retry keeps the same files and idempotency key", async () => {
  const calls: { key: string; files: File[] }[] = [];
  const client = api({ packages: async () => [], uploadPackage: async (_org: string, _project: string, input: { files: { file: File }[] }, key: string) => {
    calls.push({ key, files: input.files.map(entry => entry.file) });
    if (calls.length === 1) throw new WorkspaceApiError("Хранилище временно недоступно.", 503);
    return { package: packet, inspection_job_id: null };
  } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  const file = new File(["public synthetic source"], "tender.txt");
  fireEvent.change(await screen.findByLabelText("Документы пакета"), { target: { files: [file] } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пакет" }));
  await screen.findByText("Хранилище временно недоступно.");
  fireEvent.click(screen.getByRole("button", { name: "Повторить загрузку" }));
  await waitFor(() => assert.equal(calls.length, 2));
  assert.equal(calls[0].key, calls[1].key);
  assert.deepEqual(calls[0].files, [file]);
  assert.deepEqual(calls[1].files, [file]);
});

test("viewer can read package issues but has no upload or participant mutations", async () => {
  render(<ProjectDocuments api={api()} organizationId="org-a" project={project} canEdit={false} onExpired={() => {}} />);
  assert.ok(await screen.findByText("bad.zip"));
  assert.equal(screen.queryByLabelText("Документы пакета"), null);
  assert.equal(screen.queryByRole("button", { name: "Добавить участника" }), null);
  assert.equal(screen.queryByRole("button", { name: "Обработать документы" }), null);
});

test("oversized wire packages are rejected before any upload", async () => {
  let calls = 0;
  render(<ProjectDocuments api={api({ packages: async () => [], uploadPackage: async () => { calls++; } })} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  const file = new File(["public"], "large.txt");
  Object.defineProperty(file, "size", { value: 50_000_001 });
  fireEvent.change(await screen.findByLabelText("Документы пакета"), { target: { files: [file] } });
  assert.ok(await screen.findByText(/Общий размер пакета превышает 50 МБ/));
  assert.equal((screen.getByRole("button", { name: "Сохранить пакет" }) as HTMLButtonElement).disabled, true);
  assert.equal(calls, 0);
});

const usable: PackagePreflight = { ...preflight, logical_files: 1, logical_bytes: 20, blockers: [], warnings: [], issues: [] };
const queued: NormalizationRun = { profile_hash: "profile", analysis_allowed: false, job: { id: "job-a", project_id: project.id, stage: "normalization", status: "queued", attempt: 1, progress: 0, error: null, event_sequence: 0 } };

test("starting normalization begins polling a previously idle package", async () => {
  let historyReads = 0;
  let started = false;
  const client = api({ packagePreflight: async () => usable, normalizePackage: async () => { started = true; return queued; }, normalizationRuns: async () => {
    historyReads++;
    return !started ? [] : [{ ...queued, job: { ...queued.job, status: historyReads > 2 ? "succeeded" : "running", progress: historyReads > 2 ? 100 : 40 } }];
  } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Обработать документы" }) as HTMLButtonElement).disabled, false));
  fireEvent.click(screen.getByRole("button", { name: "Обработать документы" }));
  assert.ok(await screen.findByText("Обработано", {}, { timeout: 5000 }));
  assert.ok(historyReads >= 3);
});

test("an ambiguous normalization response retries the same action identity", async () => {
  const keys: string[] = [];
  const client = api({ packagePreflight: async () => usable, normalizePackage: async (_org: string, _project: string, _package: string, _options: unknown, key: string) => {
    keys.push(key);
    throw new WorkspaceApiError("Ответ потерян, повторите запуск.", 503);
  } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Обработать документы" }) as HTMLButtonElement).disabled, false));
  fireEvent.click(screen.getByRole("button", { name: "Обработать документы" }));
  await screen.findByText("Ответ потерян, повторите запуск.");
  fireEvent.click(screen.getByRole("button", { name: "Обработать документы" }));
  await waitFor(() => assert.equal(keys.length, 2));
  assert.equal(keys[0], keys[1]);
});

test("failed normalization retries its job rather than creating another run", async () => {
  let calls = 0;
  const failed: NormalizationRun = { ...queued, job: { ...queued.job, status: "failed", error: { code: "TEMPORARY_FAILURE", message: "Временная ошибка обработки." } } };
  const client = api({ packagePreflight: async () => usable, normalizationRuns: async () => [failed], retryJob: async (_org: string, _project: string, id: string) => { assert.equal(id, queued.job.id); calls++; return queued.job; }, normalizePackage: async () => { assert.fail("must retry the original job"); } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  fireEvent.click(await screen.findByRole("button", { name: "Повторить обработку" }));
  await waitFor(() => assert.equal(calls, 1));
});

test("a delayed upload cannot populate a different project", async () => {
  let resolve!: (value: { package: PackageDetail; inspection_job_id: null }) => void;
  const response = new Promise<{ package: PackageDetail; inspection_job_id: null }>(yes => { resolve = yes; });
  let uploads = 0;
  const client = api({ packages: async () => [], uploadPackage: async () => { uploads++; return response; } });
  const onExpired = () => {};
  const view = render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={onExpired} />);
  fireEvent.change(await screen.findByLabelText("Документы пакета"), { target: { files: [new File(["public"], "old-project.txt")] } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пакет" }));
  await waitFor(() => assert.equal(uploads, 1));
  view.rerender(<ProjectDocuments api={client} organizationId="org-b" project={{ ...project, id: "project-b", organization_id: "org-b" }} canEdit onExpired={onExpired} />);
  await act(async () => { resolve({ package: packet, inspection_job_id: null }); });
  assert.equal(screen.queryByLabelText("Версия пакета"), null);
  assert.equal(screen.queryByText("old-project.txt"), null);
});

test("a delayed initial package list cannot erase a saved package", async () => {
  let resolve!: (value: PackageDetail[]) => void;
  const response = new Promise<PackageDetail[]>(yes => { resolve = yes; });
  const client = api({ packages: async () => response, uploadPackage: async () => ({ package: packet, inspection_job_id: null }) });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  fireEvent.change(screen.getByLabelText("Документы пакета"), { target: { files: [new File(["public"], "tender.txt")] } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пакет" }));
  assert.ok(await screen.findByLabelText("Версия пакета"));
  await act(async () => { resolve([]); });
  assert.ok(screen.getByLabelText("Версия пакета"));
});

test("a delayed participant list cannot erase a created participant or change its upload kind", async () => {
  let resolve!: (value: []) => void;
  const response = new Promise<[]>(yes => { resolve = yes; });
  const person = { id: "person-a", organization_id: "org-a", project_id: project.id, name: "Компания А", slot: 1, created_at: project.created_at };
  const client = api({ participants: async () => response, createParticipant: async () => person });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  fireEvent.change(screen.getByLabelText("Участник 1 из 5"), { target: { value: person.name } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить участника" }));
  await waitFor(() => assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).value, person.id));
  await act(async () => { resolve([]); });
  assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).value, person.id);
  assert.ok(screen.getByRole("option", { name: "1. Компания А" }));
});

test("packet owner stays locked during upload and its saved result remains available", async () => {
  let resolve!: (value: { package: PackageDetail; inspection_job_id: null }) => void;
  const response = new Promise<{ package: PackageDetail; inspection_job_id: null }>(yes => { resolve = yes; });
  const person = { id: "person-a", organization_id: "org-a", project_id: project.id, name: "Компания А", slot: 1, created_at: project.created_at };
  const bid: PackageDetail = { ...packet, id: "bid-a", kind: "bid", participant_id: person.id };
  let started = false;
  const client = api({ participants: async () => [person], packages: async () => [], packageDetail: async () => bid, uploadPackage: async () => { started = true; return response; } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  await screen.findByRole("option", { name: "1. Компания А" });
  fireEvent.change(screen.getByLabelText("Пакет документов"), { target: { value: person.id } });
  fireEvent.change(screen.getByLabelText("Документы пакета"), { target: { files: [new File(["public"], "bid.txt")] } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пакет" }));
  await waitFor(() => assert.ok(started));
  assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).disabled, true);
  fireEvent.change(screen.getByLabelText("Пакет документов"), { target: { value: "" } });
  assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).value, person.id);
  await act(async () => { resolve({ package: bid, inspection_job_id: null }); });
  assert.equal((await screen.findByLabelText("Версия пакета") as HTMLSelectElement).value, bid.id);
  assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).disabled, false);
  fireEvent.change(screen.getByLabelText("Пакет документов"), { target: { value: "" } });
  assert.ok(screen.queryByLabelText("Версия пакета") === null);
  fireEvent.change(screen.getByLabelText("Пакет документов"), { target: { value: person.id } });
  assert.equal((await screen.findByLabelText("Версия пакета") as HTMLSelectElement).value, bid.id);
});

test("a recovered package read clears its transport error", async () => {
  let reads = 0;
  const client = api({ packagePreflight: async () => {
    if (++reads === 1) throw new WorkspaceApiError("Временная ошибка чтения.", 503);
    return usable;
  } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  await screen.findByText("Временная ошибка чтения.");
  await waitFor(() => assert.equal((screen.getByRole("button", { name: "Обработать документы" }) as HTMLButtonElement).disabled, false), { timeout: 5000 });
  assert.ok(screen.queryByText("Временная ошибка чтения.") === null);
});

test("permanently unavailable packages do not cause automatic request loops", async () => {
  let reads = 0;
  const client = api({ packagePreflight: async () => { reads++; throw new WorkspaceApiError("Пакет недоступен.", 404); } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  await screen.findByText("Пакет недоступен.");
  await act(async () => { await new Promise(resolve => setTimeout(resolve, 2100)); });
  assert.equal(reads, 1);
});

test("participant creation locks uploads until automatic owner selection has finished", async () => {
  let resolve!: (value: { id: string; organization_id: string; project_id: string; name: string; slot: number; created_at: string }) => void;
  const response = new Promise<{ id: string; organization_id: string; project_id: string; name: string; slot: number; created_at: string }>(yes => { resolve = yes; });
  const person = { id: "person-a", organization_id: "org-a", project_id: project.id, name: "Компания А", slot: 1, created_at: project.created_at };
  let uploads = 0;
  let created = false;
  const client = api({ packages: async () => [], createParticipant: async () => { created = true; return response; }, uploadPackage: async () => { uploads++; return { package: packet, inspection_job_id: null }; } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  fireEvent.change(screen.getByLabelText("Документы пакета"), { target: { files: [new File(["public"], "tender.txt")] } });
  fireEvent.change(screen.getByLabelText("Участник 1 из 5"), { target: { value: person.name } });
  fireEvent.click(screen.getByRole("button", { name: "Добавить участника" }));
  await waitFor(() => assert.ok(created));
  assert.equal((screen.getByRole("button", { name: "Сохранить пакет" }) as HTMLButtonElement).disabled, true);
  fireEvent.click(screen.getByRole("button", { name: "Сохранить пакет" }));
  assert.equal(uploads, 0);
  await act(async () => { resolve(person); });
  assert.equal((screen.getByLabelText("Пакет документов") as HTMLSelectElement).value, person.id);
  assert.equal((screen.getByLabelText("Документы пакета") as HTMLInputElement).disabled, false);
});

test("completed normalization exposes the scoped analysis workflow", async () => {
  const paths: string[][] = [];
  const client = api({ packagePreflight: async () => usable, normalizationRuns: async () => [{ ...queued, job: { ...queued.job, status: "succeeded", progress: 100 } }], analysisRuns: async (org: string, projectId: string, packageId: string) => { paths.push([org, projectId, packageId]); return []; } });
  render(<ProjectDocuments api={client} organizationId="org-a" project={project} canEdit onExpired={() => {}} />);
  assert.ok(await screen.findByRole("region", { name: "Анализ документов" }));
  await waitFor(() => assert.deepEqual(paths, [["org-a", project.id, packet.id]]));
  assert.ok(screen.getByText("Извлечение требований"));
});

test("project documents include the versioned checklist editor",async()=>{
  render(<ProjectDocuments api={api()} organizationId="org-a" project={project} canEdit onExpired={()=>{}}/>);
  assert.ok(await screen.findByRole("region",{name:"Редактор чек-листа"}));
  assert.ok(screen.getByRole("button",{name:"Обновить версии"}));
});
