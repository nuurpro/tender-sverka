import type { NewProject, Organization, Project, WorkspaceSession } from "./workspace-types";

export class WorkspaceApiError extends Error {
  status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

type Transport = (input: string, init: RequestInit) => Promise<Response>;

/** Credentials live in HttpOnly cookies. Only the session-bound CSRF token stays in memory. */
export class WorkspaceApi {
  private csrfToken: string | null = null;
  private credentialVersion = 0;
  private identityOperation = 0;
  private pendingSession: { identityOperation: number; credentialVersion: number; promise: Promise<WorkspaceSession> } | null = null;
  private transport: Transport;
  constructor(transport: Transport = (input, init) => fetch(input, init)) { this.transport = transport; }

  private async request<T>(path: string, method = "GET", body?: unknown, signed = true): Promise<T> {
    const credentialVersion = this.credentialVersion;
    const headers = new Headers();
    if (body !== undefined) headers.set("Content-Type", "application/json");
    if (method !== "GET" && signed) {
      if (!this.csrfToken) throw new WorkspaceApiError("Войдите в рабочую область ещё раз.", 401);
      headers.set("X-CSRF-Token", this.csrfToken);
    }
    let response: Response;
    try {
      response = await this.transport("/api" + path, {
        method, headers, credentials: "include", cache: "no-store",
        body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch { throw new WorkspaceApiError("Нет связи с сервером. Повторите попытку позже.", 0); }
    if (!response.ok) {
      if (response.status === 401 && credentialVersion === this.credentialVersion) {
        this.csrfToken = null;
        this.credentialVersion++;
      }
      const error = await response.json().catch(() => ({}));
      throw new WorkspaceApiError(typeof error.detail === "string" ? error.detail : "Не удалось выполнить запрос. Проверьте данные и повторите попытку.", response.status);
    }
    return response.json() as Promise<T>;
  }

  async session(): Promise<WorkspaceSession> {
    const identityOperation = this.identityOperation;
    const credentialVersion = this.credentialVersion;
    if (this.pendingSession?.identityOperation === identityOperation
      && this.pendingSession.credentialVersion === credentialVersion) return this.pendingSession.promise;
    const promise = this.readSession(identityOperation, credentialVersion);
    this.pendingSession = { identityOperation, credentialVersion, promise };
    try { return await promise; }
    finally { if (this.pendingSession?.promise === promise) this.pendingSession = null; }
  }
  private async readSession(identityOperation: number, credentialVersion: number): Promise<WorkspaceSession> {
    const session = await this.request<WorkspaceSession>("/auth/session");
    if (identityOperation !== this.identityOperation || credentialVersion !== this.credentialVersion) throw this.staleIdentity();
    this.csrfToken = session.csrf_token;
    this.credentialVersion++;
    return session;
  }
  requestCode(phone: string) {
    return this.request<{ challenge_id: string; expires_in: number; delivery_mode?: "development_mock" }>("/auth/request", "POST", { phone }, false);
  }
  async verifyCode(challenge_id: string, code: string): Promise<WorkspaceSession> {
    const identityOperation = ++this.identityOperation;
    const session = await this.request<WorkspaceSession>("/auth/verify", "POST", { challenge_id, code }, false);
    if (identityOperation !== this.identityOperation) throw this.staleIdentity();
    this.csrfToken = session.csrf_token;
    this.credentialVersion++;
    return session;
  }
  async logout() {
    const identityOperation = ++this.identityOperation;
    const result = await this.request<{ ok: boolean }>("/auth/logout", "POST", {});
    if (identityOperation !== this.identityOperation) throw this.staleIdentity();
    this.csrfToken = null;
    this.credentialVersion++;
    return result;
  }
  private staleIdentity() { return new WorkspaceApiError("Состояние входа изменилось. Повторите действие в текущей сессии.", 409); }
  organizations() { return this.request<Organization[]>("/organizations"); }
  createOrganization(name: string) { return this.request<Organization>("/organizations", "POST", { name }); }
  projects(organizationId: string) { return this.request<Project[]>("/organizations/" + encodeURIComponent(organizationId) + "/projects"); }
  createProject(organizationId: string, input: NewProject) {
    return this.request<Project>("/organizations/" + encodeURIComponent(organizationId) + "/projects", "POST", input);
  }
  project(organizationId: string, projectId: string) {
    return this.request<Project>("/organizations/" + encodeURIComponent(organizationId) + "/projects/" + encodeURIComponent(projectId));
  }
}
