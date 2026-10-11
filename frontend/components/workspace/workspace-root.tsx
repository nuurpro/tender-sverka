"use client";

import { useEffect, useState } from "react";
import { CheckCheck, Loader2, LogOut } from "lucide-react";
import { Button } from "@/components/ui/button";
import { WorkspaceApi, WorkspaceApiError } from "@/lib/workspace-api";
import type { WorkspaceSession } from "@/lib/workspace-types";
import { WorkspaceLogin } from "./login";
import { WorkspaceDashboard } from "./workspace-dashboard";

export function WorkspaceRoot() {
  const [api] = useState(() => new WorkspaceApi());
  const [session, setSession] = useState<WorkspaceSession | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [loggingOut, setLoggingOut] = useState(false);
  useEffect(() => {
    let active = true;
    api.session().then(value => { if (active) setSession(value); })
      .catch(error => { if (active && !(error instanceof WorkspaceApiError && error.status === 401)) setError(error instanceof Error ? error.message : "Не удалось загрузить рабочую область."); })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, [api]);
  function expired() { setSession(null); setError("Сессия закончилась. Войдите снова."); }
  async function logout() {
    setLoggingOut(true); setError("");
    try { await api.logout(); setSession(null); }
    catch (error) {
      if (error instanceof WorkspaceApiError && error.status === 401) setSession(null);
      else setError(error instanceof Error ? error.message : "Не удалось выйти из аккаунта.");
    } finally { setLoggingOut(false); }
  }
  if (loading) return <div role="status" className="flex min-h-[100dvh] items-center justify-center gap-3 text-sm text-[#56645b]"><Loader2 className="size-5 animate-spin" />Открываем рабочую область…</div>;
  if (!session) return <WorkspaceLogin api={api} onLogin={value => { setSession(value); setError(""); }} initialError={error} />;
  return <div className="min-h-[100dvh]">
    <header className="border-b bg-white"><div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-3 px-5 py-4 sm:px-8"><a href="/workspace" className="flex items-center gap-3 text-lg font-semibold tracking-tight"><CheckCheck className="size-6 text-primary" />Тендер-Сверка</a><div className="flex items-center gap-3"><span className="text-sm text-[#56645b]">{session.user.display_name || session.user.phone}</span><Button variant="ghost" size="sm" onClick={logout} disabled={loggingOut}>{loggingOut ? <Loader2 className="animate-spin" /> : <LogOut />}Выйти</Button></div></div></header>
    {error && <div role="alert" className="mx-auto mt-5 max-w-7xl rounded-lg border border-red-200 bg-red-50 px-5 py-3 text-sm text-red-800">{error}</div>}
    <WorkspaceDashboard api={api} session={session} onExpired={expired} onOrganizations={organizations => setSession({ ...session, organizations })} />
  </div>;
}
