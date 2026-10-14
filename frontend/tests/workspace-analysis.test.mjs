import assert from "node:assert/strict";
import test from "node:test";

const session = { user: { id: "user", phone: null, display_name: null }, csrf_token: "analysis-csrf", organizations: [] };
const json = value => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });

test("grounded analysis retains the selected normalization attempt and action identity", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => { calls.push({ url, init }); return json(url.endsWith("/session") ? session : {}); });
  const options = { normalization_job_id: "normalization-a", normalization_attempt: 2, budget: { max_requests: 12, max_tokens: 250000, cost_ceiling: null } };
  await api.session();
  await api.aiPolicy("org/one");
  await api.setAiPolicy("org/one", { allowed: true, expected_version: 0, reason: "Разрешение администратора" });
  await api.analysisPreflight("org/one", "project/one", "package/one", options);
  await api.startAnalysis("org/one", "project/one", "package/one", options, "same-analysis-key");
  await api.startAnalysis("org/one", "project/one", "package/one", options, "same-analysis-key");
  await api.analysisRun("org/one", "project/one", "run/one");
  await api.analysisRuns("org/one", "project/one", "package/one");
  const base = "/api/organizations/org%2Fone/projects/project%2Fone";
  assert.deepEqual(calls.slice(1).map(call => call.url), [
    "/api/organizations/org%2Fone/ai-policy", "/api/organizations/org%2Fone/ai-policy",
    base + "/packages/package%2Fone/analysis/preflight", base + "/packages/package%2Fone/analysis",
    base + "/packages/package%2Fone/analysis", base + "/analysis/run%2Fone", base + "/packages/package%2Fone/analysis",
  ]);
  assert.deepEqual(JSON.parse(calls[2].init.body), { allowed: true, expected_version: 0, reason: "Разрешение администратора" });
  for (const call of calls.slice(3, 6)) {
    assert.deepEqual(JSON.parse(call.init.body), options);
    assert.equal(new Headers(call.init.headers).get("X-CSRF-Token"), session.csrf_token);
    assert.equal(call.init.credentials, "include");
  }
  assert.equal(new Headers(calls[4].init.headers).get("Idempotency-Key"), "same-analysis-key");
  assert.equal(new Headers(calls[5].init.headers).get("Idempotency-Key"), "same-analysis-key");
});

test("checklist edits and approval retain optimistic revision checks", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => { calls.push({ url, init }); return json(url.endsWith("/session") ? session : {}); });
  await api.session();
  await api.checklists("org", "project");
  await api.checklist("org", "project", "check/one");
  const input = { expected_revision: 2, reason: "Уточнение условия", requirements: [] };
  await api.saveChecklist("org", "project", "check/one", input);
  await api.approveChecklist("org", "project", "check/one", 3);
  await api.forkChecklist("org", "project", "check/one");
  assert.deepEqual(calls.slice(1).map(value => value.url), [
    "/api/organizations/org/projects/project/checklists", "/api/organizations/org/projects/project/checklists/check%2Fone",
    "/api/organizations/org/projects/project/checklists/check%2Fone", "/api/organizations/org/projects/project/checklists/check%2Fone/approve",
    "/api/organizations/org/projects/project/checklists/check%2Fone/fork",
  ]);
  assert.deepEqual(JSON.parse(calls[3].init.body), input);
  assert.deepEqual(JSON.parse(calls[4].init.body), { expected_revision: 3 });
  assert.deepEqual(JSON.parse(calls[5].init.body), {});
  assert.equal(api.checklistReportUrl("org/one","project/one","check/one",3,"xlsx"),"/api/organizations/org%2Fone/projects/project%2Fone/checklists/check%2Fone/report.xlsx?revision=3");
});
