import assert from "node:assert/strict";
import { afterEach, test } from "node:test";
import { StrictMode } from "react";
import { act, cleanup, configure, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkspaceRoot } from "../components/workspace/workspace-root";
import { WorkspaceLogin } from "../components/workspace/login";
import { WorkspaceApi } from "../lib/workspace-api";
import type { Project, WorkspaceSession } from "../lib/workspace-types";

const originalFetch = globalThis.fetch;
configure({ getElementError: message => new Error(message || "Element not found") });
afterEach(() => { cleanup(); globalThis.fetch = originalFetch; });
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(yes => { resolve = yes; });
  return { promise, resolve };
}
const json = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
const session: WorkspaceSession = {
  user: { id: "user-1", phone: "+998900000001", display_name: null }, csrf_token: "session-csrf-1",
  organizations: [{ id: "org-a", name: "Команда А", role: "admin" }, { id: "org-b", name: "Команда Б", role: "admin" }],
};
const project: Project = {
  id: "project-a", organization_id: "org-a", mode: "buyer", lot_name: "Поздний проект А",
  customer: null, deadline: null, timezone: "Asia/Tashkent", report_language: "ru",
  revision: 1, created_at: "2026-10-12T08:00:00Z",
};
function transport(handler: (path: string, init?: RequestInit) => Promise<Response>) {
  globalThis.fetch = ((input: string | URL | Request, init?: RequestInit) => {
    const path = String(input);
    if (path === "/api/auth/session") return Promise.resolve(json(session));
    if (path === "/api/auth/logout") return Promise.resolve(json({ ok: true }));
    return handler(path, init);
  }) as typeof fetch;
}
async function submitProject() {
  fireEvent.click(await screen.findByRole("button", { name: "Новый проект" }));
  fireEvent.change(screen.getByLabelText("Название лота"), { target: { value: project.lot_name } });
  fireEvent.click(screen.getByRole("button", { name: "Создать проект" }));
}

test("a delayed project response cannot populate another organization's workspace", async () => {
  const created = deferred<Response>();
  transport(async (_path, init) => init?.method === "POST" ? created.promise : json([]));
  render(<WorkspaceRoot />);
  await submitProject();
  fireEvent.click(screen.getByRole("button", { name: "Закрыть создание проекта" }));
  fireEvent.change(screen.getByLabelText("Организация"), { target: { value: "org-b" } });
  await waitFor(() => assert.equal((screen.getByLabelText("Организация") as HTMLSelectElement).value, "org-b"));
  await act(async () => { created.resolve(json(project)); });
  assert.equal(screen.queryAllByText(project.lot_name).length, 0);
  assert.equal(screen.queryByRole("button", { name: /Поздний проект А/ }), null);
});

test("a list response started before creation cannot erase the created project", async () => {
  const listed = deferred<Response>();
  transport(async (_path, init) => init?.method === "POST" ? json(project) : listed.promise);
  render(<WorkspaceRoot />);
  await submitProject();
  fireEvent.click(await screen.findByRole("button", { name: "Закрыть проект" }));
  await act(async () => { listed.resolve(json([])); });
  assert.ok(screen.getByRole("button", { name: /Поздний проект А/ }));
});

test("a delayed organization response cannot restore a logged-out workspace", async () => {
  const created = deferred<Response>();
  transport(async (path, init) => path === "/api/organizations" && init?.method === "POST" ? created.promise : json([]));
  render(<WorkspaceRoot />);
  fireEvent.click(await screen.findByRole("button", { name: "Добавить организацию" }));
  fireEvent.change(screen.getByLabelText("Название"), { target: { value: "Новая команда" } });
  fireEvent.click(screen.getByRole("button", { name: "Создать организацию" }));
  fireEvent.click(screen.getByRole("button", { name: "Закрыть создание организации" }));
  fireEvent.click(screen.getByRole("button", { name: "Выйти" }));
  await screen.findByRole("heading", { name: "Вход в рабочую область" });
  await act(async () => { created.resolve(json({ id: "org-c", name: "Новая команда", role: "admin" })); });
  assert.ok(screen.getByRole("heading", { name: "Вход в рабочую область" }));
  assert.equal(screen.queryByRole("heading", { name: "Проекты" }), null);
});

test("login explicitly labels the fixed-code development mock after a challenge", async () => {
  const api = new WorkspaceApi(async () => json({ challenge_id: "dev-challenge", expires_in: 300, delivery_mode: "development_mock" }));
  render(<WorkspaceLogin api={api} onLogin={() => {}} />);
  fireEvent.change(screen.getByLabelText("Номер телефона"), { target: { value: "+998900000001" } });
  fireEvent.click(screen.getByRole("button", { name: "Получить код" }));
  assert.ok(await screen.findByText(/Dev-режим: код 123456/));
});

test("Strict Mode mount replay keeps a valid workspace session", async () => {
  let sessionReads = 0;
  globalThis.fetch = (async (path: string) => {
    if (path === "/api/auth/session") { sessionReads++; return json(session); }
    return json([]);
  }) as typeof fetch;
  render(<StrictMode><WorkspaceRoot /></StrictMode>);
  assert.ok(await screen.findByRole("heading", { name: "Проекты" }));
  assert.equal(screen.queryByRole("heading", { name: "Вход в рабочую область" }), null);
  assert.equal(sessionReads, 1);
});

test("opening a project displays its scoped documents workspace", async () => {
  const paths: string[] = [];
  transport(async path => {
    paths.push(path);
    return json(path === "/api/organizations/org-a/projects" ? [project] : []);
  });
  render(<WorkspaceRoot />);
  fireEvent.click(await screen.findByRole("button", { name: /Поздний проект А/ }));
  assert.ok(await screen.findByRole("region", { name: "Документы проекта" }));
  await waitFor(() => assert.ok(paths.includes("/api/organizations/org-a/projects/project-a/packages")));
  assert.ok(paths.includes("/api/organizations/org-a/projects/project-a/participants"));
  fireEvent.click(screen.getByRole("button", { name: "Закрыть проект" }));
  assert.equal(screen.queryByRole("region", { name: "Документы проекта" }), null);
});

test("the workspace administrator can grant AI policy from processed project documents", async () => {
  const packet = { id: "package-a", project_id: project.id, kind: "tender", participant_id: null, version: 1, status: "uploaded", created_at: project.created_at, documents: [] };
  transport(async path => {
    if (path === "/api/organizations/org-a/projects") return json([project]);
    if (path.endsWith("/packages")) return json([packet]);
    if (path.endsWith("/packages/package-a")) return json(packet);
    if (path.endsWith("/preflight")) return json({ logical_files: 1, logical_bytes: 10, page_count: 1, token_upper_bound: 10, issues: [], inspection_status: "inspected" });
    if (path.endsWith("/normalizations")) return json([{ profile_hash: "p", analysis_allowed: false, job: { id: "job-a", project_id: project.id, stage: "package.normalize", status: "succeeded", progress: 100, attempt: 1, error: null, event_sequence: 1 } }]);
    if (path.endsWith("/ai-policy")) return json({ allowed: false, version: 0 });
    return json([]);
  });
  render(<WorkspaceRoot />);
  fireEvent.click(await screen.findByRole("button", { name: /Поздний проект А/ }));
  assert.ok(await screen.findByRole("button", { name: "Разрешить передачу документов" }));
});
