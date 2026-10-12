import assert from "node:assert/strict";
import test from "node:test";

const session = { user: { id: "user-1", phone: "+998901234567", display_name: null }, csrf_token: "session-csrf", organizations: [] };
const respond = (value, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("phone login sends cookies but exposes no session credential in a header", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => { calls.push({ url, init }); return respond({ challenge_id: "challenge", expires_in: 300 }); });
  await api.requestCode("+998901234567");
  assert.equal(calls[0].url, "/api/auth/request");
  assert.equal(calls[0].init.credentials, "include");
  assert.equal(calls[0].init.cache, "no-store");
  assert.equal(new Headers(calls[0].init.headers).get("Authorization"), null);
  assert.equal(new Headers(calls[0].init.headers).get("X-CSRF-Token"), null);
  assert.deepEqual(JSON.parse(calls[0].init.body), { phone: "+998901234567" });
});

test("workspace mutation is blocked locally until a session supplies CSRF", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  let count = 0;
  const api = new WorkspaceApi(async () => { count++; return respond({}); });
  await assert.rejects(api.createOrganization("Organization"), error => error.status === 401);
  assert.equal(count, 0);
});

test("session-bound CSRF accompanies authenticated mutations", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const calls = [];
  const api = new WorkspaceApi(async (url, init) => { calls.push({ url, init }); return respond(url.endsWith("session") ? session : { id: "org-1", name: "Org", role: "admin" }); });
  await api.session();
  await api.createOrganization("Org");
  assert.equal(new Headers(calls[1].init.headers).get("X-CSRF-Token"), "session-csrf");
  assert.equal(calls[1].init.credentials, "include");
});

test("server session rejection invalidates the CSRF credential", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  let count = 0;
  const api = new WorkspaceApi(async () => { count++; return count === 1 ? respond(session) : respond({ detail: "Session expired" }, 401); });
  await api.session();
  await assert.rejects(api.createOrganization("Org"), error => error.status === 401);
  await assert.rejects(api.createOrganization("Org"), error => error.status === 401);
  assert.equal(count, 2);
});

test("unexpected structured errors are replaced with a safe user message", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const api = new WorkspaceApi(async () => respond({ detail: [{ input: "sensitive-value" }] }, 422));
  await assert.rejects(api.requestCode("phone"), error => error.status === 422 && !error.message.includes("sensitive-value"));
});

test("failed logout can be retried without falsely assuming server revocation", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  let count = 0;
  const api = new WorkspaceApi(async () => {
    count++;
    if (count === 1) return respond(session);
    if (count === 2) throw new Error("network failed");
    return respond({ ok: true });
  });
  await api.session();
  await assert.rejects(api.logout(), error => error.status === 0);
  await api.logout();
  assert.equal(count, 3);
  await assert.rejects(api.createOrganization("Org"), error => error.status === 401);
});

function deferred() {
  let resolve;
  const promise = new Promise(yes => { resolve = yes; });
  return { promise, resolve };
}

test("an old request's delayed 401 cannot clear a newly verified session", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const old = deferred();
  let sentToken;
  const api = new WorkspaceApi(async (path, init) => {
    if (path.endsWith("/session")) return respond(session);
    if (path.endsWith("/projects")) return old.promise;
    if (path.endsWith("/verify")) return respond({ ...session, csrf_token: "new-session-csrf" });
    sentToken = new Headers(init.headers).get("X-CSRF-Token");
    return respond({ id: "org-new" });
  });
  await api.session();
  const failed = assert.rejects(api.projects("org-old"), error => error.status === 401);
  await api.verifyCode("new-challenge", "123456");
  old.resolve(respond({ detail: "Old session expired" }, 401));
  await failed;
  await api.createOrganization("New organization");
  assert.equal(sentToken, "new-session-csrf");
});

test("a delayed session read cannot replace a subsequent verified login", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const old = deferred();
  let sentToken;
  const api = new WorkspaceApi(async (path, init) => {
    if (path.endsWith("/session")) return old.promise;
    if (path.endsWith("/verify")) return respond({ ...session, csrf_token: "new-session-csrf" });
    sentToken = new Headers(init.headers).get("X-CSRF-Token");
    return respond({ id: "org-new" });
  });
  const ignored = assert.rejects(api.session(), error => error.status === 409);
  await api.verifyCode("new-challenge", "123456");
  old.resolve(respond(session));
  await ignored;
  await api.createOrganization("New organization");
  assert.equal(sentToken, "new-session-csrf");
});

test("a delayed logout completion cannot overwrite a subsequent login", async () => {
  const { WorkspaceApi } = await import("../lib/workspace-api.ts");
  const old = deferred();
  let sentToken;
  const api = new WorkspaceApi(async (path, init) => {
    if (path.endsWith("/session")) return respond(session);
    if (path.endsWith("/logout")) return old.promise;
    if (path.endsWith("/verify")) return respond({ ...session, csrf_token: "new-session-csrf" });
    sentToken = new Headers(init.headers).get("X-CSRF-Token");
    return respond({ id: "org-new" });
  });
  await api.session();
  const ignored = assert.rejects(api.logout(), error => error.status === 409);
  await api.verifyCode("new-challenge", "123456");
  old.resolve(respond({ ok: true }));
  await ignored;
  await api.createOrganization("New organization");
  assert.equal(sentToken, "new-session-csrf");
});
