"use client";

import { useEffect, useRef, useState } from "react";
import { ArrowUpRight, Building2, CalendarDays, FolderPlus, Loader2, Plus, Search, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { WorkspaceApiError, type WorkspaceApi } from "@/lib/workspace-api";
import type { Organization, Project, WorkspaceSession } from "@/lib/workspace-types";
import { ProjectForm } from "./project-form";
import { useProjectWorkspace } from "./use-project-workspace";
import { ProjectDocuments } from "./project-documents";

const roleLabels = { admin: "Администратор", specialist: "Специалист", viewer: "Просмотр" };
const date = (value: string) => new Intl.DateTimeFormat("ru", { timeZone: "Asia/Tashkent", dateStyle: "medium", timeStyle: "short" }).format(new Date(value));

export function WorkspaceDashboard({ api, session, onExpired, onOrganizations }: { api: WorkspaceApi; session: WorkspaceSession; onExpired: () => void; onOrganizations: (organizations: Organization[]) => void }) {
  const [orgId, setOrgId] = useState(session.organizations[0]?.id || "");
  const { projects, loading, error, invalidate, createProject } = useProjectWorkspace(api, orgId, onExpired);
  const mounted = useRef(false);
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState("all");
  const [newProject, setNewProject] = useState(false);
  const [newOrg, setNewOrg] = useState(false);
  const [orgName, setOrgName] = useState("");
  const [orgBusy, setOrgBusy] = useState(false);
  const [orgError, setOrgError] = useState("");
  const [selected, setSelected] = useState<Project | null>(null);
  const organization = session.organizations.find(org => org.id === orgId);
  const canEdit = !!organization && organization.role !== "viewer";

  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => { setSelected(null); setNewProject(false); }, [orgId]);
  function chooseOrganization(id: string) { invalidate(); setSelected(null); setNewProject(false); setOrgId(id); }
  const filtered = projects.filter(project => (mode === "all" || project.mode === mode) && `${project.lot_name} ${project.customer || ""}`.toLocaleLowerCase().includes(query.toLocaleLowerCase()));

  async function createOrganization(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setOrgBusy(true); setOrgError("");
    try {
      const org = await api.createOrganization(orgName.trim());
      if (!mounted.current) return;
      onOrganizations([...session.organizations, org]); chooseOrganization(org.id); setNewOrg(false); setOrgName("");
    } catch (error) { if (mounted.current) { if (error instanceof WorkspaceApiError && error.status === 401) onExpired(); else setOrgError(error instanceof Error ? error.message : "Не удалось создать организацию."); } }
    finally { if (mounted.current) setOrgBusy(false); }
  }

  return <main className="mx-auto grid max-w-7xl gap-8 px-5 py-8 sm:px-8 lg:grid-cols-[220px_1fr] lg:py-10">
    <aside className="space-y-5"><div className="text-xs font-semibold text-[#56645b]">ОРГАНИЗАЦИЯ</div>
      {session.organizations.length ? <><label htmlFor="workspace-org" className="sr-only">Организация</label><select id="workspace-org" value={orgId} onChange={event => chooseOrganization(event.target.value)} className="h-11 w-full rounded-lg border bg-white px-3 text-sm font-medium">{session.organizations.map(org => <option value={org.id} key={org.id}>{org.name}</option>)}</select>{organization && <span className="block text-xs text-[#56645b]">{roleLabels[organization.role]}</span>}</> : <p className="text-sm leading-6 text-[#56645b]">Создайте организацию, чтобы начать работу.</p>}
      <Button variant="ghost" size="sm" className="-ml-3" onClick={() => { setNewOrg(true); setOrgError(""); }}><Plus />Добавить организацию</Button>
      <div className="hidden border-t pt-5 lg:block"><div className="flex items-center gap-3 rounded-lg bg-primary/8 px-3 py-3 text-sm font-semibold text-primary"><FolderPlus className="size-4" />Проекты</div></div>
    </aside>
    <section aria-labelledby="projects-title" className="min-w-0"><div className="flex flex-wrap items-start justify-between gap-5"><div><h1 id="projects-title" className="text-3xl font-semibold tracking-tight">Проекты</h1><p className="mt-2 text-sm leading-6 text-[#56645b]">Проверки заявок и сравнения участников вашей организации.</p></div>{canEdit && <Button onClick={() => setNewProject(true)}><Plus />Новый проект</Button>}</div>
      {orgId && <div className="mt-7 flex flex-wrap items-center gap-4 rounded-lg border bg-white px-4 py-3"><div className="relative min-w-full sm:min-w-0 sm:flex-1"><Search className="absolute left-0 top-2.5 size-4 text-[#56645b]" /><Input aria-label="Найти проект" className="border-0 pl-7 shadow-none focus:ring-0" value={query} onChange={event => setQuery(event.target.value)} placeholder="Найти по названию или заказчику" /></div><label htmlFor="project-mode-filter" className="sr-only">Сценарий</label><select id="project-mode-filter" className="h-9 rounded-md border bg-white px-3 text-sm" value={mode} onChange={event => setMode(event.target.value)}><option value="all">Все сценарии</option><option value="supplier">Поставщик</option><option value="buyer">Заказчик</option></select><span className="text-xs text-[#56645b]">{filtered.length} из {projects.length}</span></div>}
      {error && <div role="alert" className="mt-5 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
      {loading ? <div role="status" className="mt-8 flex items-center gap-3 text-sm text-[#56645b]"><Loader2 className="size-4 animate-spin" />Загружаем проекты…</div> : !filtered.length ? <div className="mt-8 rounded-xl border border-dashed bg-white/50 px-6 py-16 text-center"><FolderPlus className="mx-auto size-10 text-primary/60" /><h2 className="mt-5 text-xl font-semibold">{query || mode !== "all" ? "Проекты не найдены" : "Первый проект начинается здесь"}</h2><p className="mx-auto mt-3 max-w-sm text-sm leading-6 text-[#56645b]">{query || mode !== "all" ? "Измените поисковый запрос или выберите другой сценарий." : orgId ? "Укажите лот и выберите сценарий: проверка собственной заявки или сравнение участников." : "Создайте организацию, затем добавьте проект для своего тендера."}</p>{!orgId ? <Button className="mt-6" onClick={() => setNewOrg(true)}><Building2 />Создать организацию</Button> : canEdit && !projects.length ? <Button className="mt-6" onClick={() => setNewProject(true)}><Plus />Создать проект</Button> : null}</div> : <div className="mt-6 grid gap-4 xl:grid-cols-2">{filtered.map(project => <button key={project.id} className="surface group p-6 text-left transition-colors hover:border-primary/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary" onClick={() => setSelected(project)}><div className="flex items-center justify-between gap-3"><span className="rounded-md bg-muted px-2.5 py-1 text-xs font-medium text-[#56645b]">{project.mode === "supplier" ? "Поставщик" : "Заказчик"}</span><ArrowUpRight className="size-4 text-[#56645b] group-hover:text-primary" /></div><h2 className="mt-5 break-words text-lg font-semibold leading-7">{project.lot_name}</h2><p className="mt-2 break-words text-sm text-[#56645b]">{project.customer || "Заказчик не указан"}</p><div className="mt-6 flex items-center gap-2 border-t pt-4 text-xs text-[#56645b]"><CalendarDays className="size-4 shrink-0" />{project.deadline ? `Приём до ${date(project.deadline)}` : "Срок приёма не указан"}</div></button>)}</div>}
    </section>
    <Sheet open={newOrg} onOpenChange={setNewOrg}><SheetContent closeLabel="Закрыть создание организации"><SheetTitle className="pr-8 text-2xl font-semibold">Новая организация</SheetTitle><SheetDescription className="mt-3 text-sm leading-6 text-[#56645b]">Для проектов и документов вашей команды. Вы станете администратором этой организации.</SheetDescription><form onSubmit={createOrganization} className="mt-7 space-y-5"><div><label htmlFor="organization-name" className="mb-2 block text-sm font-medium">Название</label><Input id="organization-name" value={orgName} onChange={event => setOrgName(event.target.value)} maxLength={255} required disabled={orgBusy} /></div>{orgError && <p role="alert" className="text-sm text-red-800">{orgError}</p>}<Button disabled={orgBusy || !orgName.trim()}>{orgBusy && <Loader2 className="animate-spin" />}Создать организацию</Button></form></SheetContent></Sheet>
    <Sheet open={newProject} onOpenChange={setNewProject}><SheetContent closeLabel="Закрыть создание проекта"><SheetTitle className="pr-8 text-2xl font-semibold">Новый проект</SheetTitle><SheetDescription className="mt-3 text-sm leading-6 text-[#56645b]">{organization?.name}: требования и заявки будут доступны вашей команде.</SheetDescription><ProjectForm onCancel={() => setNewProject(false)} onSave={async input => {
      const project = await createProject(input);
      if (project) { setNewProject(false); setSelected(project); }
    }} /></SheetContent></Sheet>
    <Sheet open={!!selected} onOpenChange={open => { if (!open) setSelected(null); }}><SheetContent closeLabel="Закрыть проект" className="max-w-5xl"><SheetTitle className="pr-8 text-2xl font-semibold leading-8">{selected?.lot_name}</SheetTitle><SheetDescription className="mt-3 text-sm text-[#56645b]">{selected?.customer || "Заказчик не указан"}</SheetDescription>{selected && <><dl className="mt-7 grid grid-cols-2 gap-5 rounded-xl bg-muted/40 p-5"><div><dt className="text-xs text-[#56645b]">Сценарий</dt><dd className="mt-2 flex items-center gap-2 text-sm font-medium"><Users className="size-4 shrink-0 text-primary" />{selected.mode === "buyer" ? "Сравнение до 5 участников" : "Проверка одной заявки"}</dd></div><div><dt className="text-xs text-[#56645b]">Окончание приёма заявок, Ташкент</dt><dd className="mt-2 text-sm font-medium">{selected.deadline ? date(selected.deadline) : "Не указан"}</dd></div><div><dt className="text-xs text-[#56645b]">Язык отчёта</dt><dd className="mt-2 text-sm font-medium">{{ ru: "Русский", uz: "O‘zbekcha", en: "English" }[selected.report_language]}</dd></div><div><dt className="text-xs text-[#56645b]">Создан</dt><dd className="mt-2 text-sm font-medium">{date(selected.created_at)}</dd></div></dl><ProjectDocuments key={`${session.user.id}:${orgId}:${selected.id}`} api={api} organizationId={orgId} project={selected} canEdit={canEdit} canAdmin={organization?.role === "admin"} onExpired={onExpired} /></>}</SheetContent></Sheet>
  </main>;
}
