import assert from "node:assert/strict";
import test from "node:test";

const session = { user: { id: "user", phone: null, display_name: null }, csrf_token: "source-csrf", organizations: [] };
const job = { id: "job", project_id: "project", stage: "package.normalize", status: "queued", attempt: 0, progress: 0, error: null, event_sequence: 0 };
const respond = value => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });

test("normalization preserves server-owned profiles, scoped paths and retry identity", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => {
    calls.push({ url, init });
    return respond(url.endsWith("/session") ? session : { job, profile_hash: "profile", analysis_allowed: false });
  });
  await assert.rejects(api.normalizePackage("org", "project", "package", {}, "normalize-key"), error => error.status === 401);
  assert.equal(calls.length, 0);
  await api.session();
  const options = { encoding: "cp1251", csv_delimiter: ";" };
  for (let attempt = 0; attempt < 2; attempt++) {
    const result = await api.normalizePackage("org/one", "project/one", "package/one", options, "normalize-key");
    assert.equal(result.analysis_allowed, false);
    assert.equal(result.job.status, "queued");
  }
  for (const { url, init } of calls.slice(1)) {
    assert.equal(url, "/api/organizations/org%2Fone/projects/project%2Fone/packages/package%2Fone/normalize");
    assert.equal(init.method, "POST");
    assert.equal(new Headers(init.headers).get("X-CSRF-Token"), "source-csrf");
    assert.equal(new Headers(init.headers).get("Idempotency-Key"), "normalize-key");
    assert.equal(new Headers(init.headers).get("Authorization"), null);
    assert.deepEqual(JSON.parse(init.body), options);
    assert.equal(init.credentials, "include");
    assert.equal(init.cache, "no-store");
  }
});

test("artifact and evidence reads retain original quotes and nullable page metadata", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const original = "Требуется 16 ГБ.\n  Original  spacing.";
  const block = { id: "b-example", original_text: original, normalized_text: "Требуется 16 ГБ. Original spacing.", anchor: { kind: "docx", part: "word/document.xml", paragraph: 2 } };
  const api = new WorkspaceApi(async (url, init) => {
    calls.push({ url, init });
    return respond({ artifact_id: "artifact", document_id: "document", file_hash: "source-hash", physical_pages: null, block, analysis_allowed: false });
  });
  const ids = ["org/one", "project/one", "package/one"];
  await api.normalizationRuns(...ids);
  await api.normalizationArtifacts(...ids, "job/one");
  await api.artifactDetail(...ids, "artifact/one");
  await api.artifactBlocks(...ids, "artifact/one", 100, 25);
  const result = await api.artifactBlock(...ids, "artifact/one", "b-example");
  const base = "/api/organizations/org%2Fone/projects/project%2Fone/packages/package%2Fone";
  assert.deepEqual(calls.map(call => call.url), [base + "/normalizations", base + "/normalizations/job%2Fone/artifacts", base + "/artifacts/artifact%2Fone", base + "/artifacts/artifact%2Fone/blocks?offset=100&limit=25", base + "/artifacts/artifact%2Fone/blocks/b-example"]);
  assert.equal(result.block.original_text, original);
  assert.equal(result.block.anchor.part, "word/document.xml");
  assert.equal(result.analysis_allowed, false);
  assert.ok(calls.every(call => call.init.credentials === "include" && call.init.cache === "no-store"));
});
