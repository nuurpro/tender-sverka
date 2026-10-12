import assert from "node:assert/strict";
import test from "node:test";

const session = { user: { id: "user-1", phone: "+998901234567", display_name: null }, csrf_token: "package-csrf", organizations: [] };
const respond = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("package uploads retain actual files, folder paths and authenticated multipart headers across retry", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  let attempt = 0;
  const api = new WorkspaceApi(async (url, init) => {
    calls.push({ url, init });
    if (url.endsWith("/session")) return respond(session);
    attempt++;
    return attempt === 1 ? respond({ detail: "Storage unavailable" }, 503) : respond({ package: { id: "package-1" }, inspection_job_id: "job-1" });
  });
  const bytes = new Uint8Array([0, 1, 2, 255]);
  const file = new File([bytes], "bid.pdf", { type: "application/pdf" });
  const input = { kind: "bid", participantId: "participant-1", files: [{ file, relativePath: "certificates/bid.pdf" }] };
  await assert.rejects(api.uploadPackage("org-1", "project-1", input, "same-upload-key"), error => error.status === 401);
  assert.equal(calls.length, 0);
  await api.session();
  await assert.rejects(api.uploadPackage("org-1", "project-1", input, "same-upload-key"), error => error.status === 503);
  const result = await api.uploadPackage("org-1", "project-1", input, "same-upload-key");
  assert.equal(result.package.id, "package-1");
  for (const { url, init } of calls.slice(1)) {
    assert.equal(url, "/api/organizations/org-1/projects/project-1/packages");
    assert.equal(init.method, "POST");
    assert.equal(init.credentials, "include");
    assert.equal(init.cache, "no-store");
    const headers = new Headers(init.headers);
    assert.equal(headers.get("X-CSRF-Token"), "package-csrf");
    assert.equal(headers.get("Idempotency-Key"), "same-upload-key");
    assert.equal(headers.get("Content-Type"), null);
    assert.equal(headers.get("Authorization"), null);
    assert.ok(init.body instanceof FormData);
    assert.equal(init.body.get("kind"), "bid");
    assert.equal(init.body.get("participant_id"), "participant-1");
    assert.equal(init.body.getAll("files").length, 1);
    const uploaded = init.body.get("files");
    assert.equal(uploaded.name, "certificates/bid.pdf");
    assert.deepEqual(new Uint8Array(await uploaded.arrayBuffer()), bytes);
    const serialized = new Request("http://127.0.0.1" + url, init);
    assert.match(serialized.headers.get("Content-Type"), /^multipart\/form-data; boundary=/);
  }
});

test("package history, preflight and download paths retain organization/project scope", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => { calls.push({ url, init }); return respond({ page_count: null, token_count: null, analysis_allowed: false }); });
  await api.packages("org/one", "project/one");
  await api.packageDetail("org/one", "project/one", "package/one");
  const preflight = await api.packagePreflight("org/one", "project/one", "package/one");
  const base = "/api/organizations/org%2Fone/projects/project%2Fone/packages";
  assert.deepEqual(calls.map(call => call.url), [base, base + "/package%2Fone", base + "/package%2Fone/preflight"]);
  assert.equal(preflight.page_count, null);
  assert.equal(preflight.token_count, null);
  assert.equal(preflight.analysis_allowed, false);
  assert.equal(api.documentDownloadUrl("org/one", "project/one", "package/one", "document/one"), base + "/package%2Fone/documents/document%2Fone/download");
  assert.ok(calls.every(call => call.init.credentials === "include" && call.init.cache === "no-store"));
});
