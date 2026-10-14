"use client";

import { useEffect, useRef, useState } from "react";
import { AlertCircle, Download, FileText, Loader2, Plus, RefreshCw, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { WorkspaceApiError, type WorkspaceApi } from "@/lib/workspace-api";
import type { PackageDetail, PackagePreflight, PackageSummary, PackageKind } from "@/lib/package-types";
import type { NormalizationRun } from "@/lib/document-types";
import type { Participant, Project } from "@/lib/workspace-types";
import { AnalysisPanel } from "./analysis-panel";
import { ChecklistEditor } from "./checklist-editor";

type Props = { api: WorkspaceApi; organizationId: string; project: Project; canEdit: boolean; canAdmin?: boolean; onExpired: () => void };
const bytes = (size: number) => size >= 1_000_000 ? `${(size / 1_000_000).toFixed(1)} МБ` : `${Math.ceil(size / 1000)} КБ`;
const statusLabels = { queued: "В очереди", running: "Обработка", succeeded: "Обработано", failed: "Обработка остановлена", cancelled: "Отменено" };

export function ProjectDocuments(props: Props) {
  const { api, organizationId, project, canEdit, onExpired } = props;
  const [participants, setParticipants] = useState<Participant[]>([]);
  const [packages, setPackages] = useState<PackageSummary[]>([]);
  const [selected, setSelected] = useState("");
  const [name, setName] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [documentBusy, setDocumentBusy] = useState(false);
  const scope = useRef<object | null>(null);
  const committed = useRef<{ participants: Participant[]; packages: PackageSummary[] }>({ participants: [], packages: [] });
  useEffect(() => {
    const current = {}; scope.current = current;
    committed.current = { participants: [], packages: [] };
    setParticipants([]); setPackages([]); setSelected(""); setError(""); setName(""); setBusy(false); setDocumentBusy(false);
    Promise.all([api.participants(organizationId, project.id), api.packages(organizationId, project.id)]).then(([people, files]) => {
      if (scope.current === current) {
        setParticipants([...new Map([...people, ...committed.current.participants].map(value => [value.id, value])).values()]);
        setPackages([...new Map([...files, ...committed.current.packages].map(value => [value.id, value])).values()]);
      }
    }).catch(reason => {
      if (scope.current !== current) return;
      if (reason instanceof WorkspaceApiError && reason.status === 401) onExpired();
      else setError(reason instanceof Error ? reason.message : "Не удалось открыть документы.");
    });
    return () => { if (scope.current === current) scope.current = null; };
  }, [api, organizationId, project.id, onExpired]);
  const limit = project.mode === "buyer" ? 5 : 1;
  const slot = Array.from({ length: limit }, (_, i) => i + 1).find(value => !participants.some(person => person.slot === value));
  async function addParticipant(event: React.FormEvent) {
    event.preventDefault(); if (!canEdit || !slot || !name.trim() || busy || documentBusy) return;
    const current = scope.current; setBusy(true); setError("");
    try {
      const participant = await api.createParticipant(organizationId, project.id, name.trim(), slot);
      if (scope.current === current) { committed.current.participants.push(participant); setParticipants(values => [...values, participant].sort((a, b) => a.slot - b.slot)); setSelected(participant.id); setName(""); }
    } catch (reason) {
      if (scope.current === current) { if (reason instanceof WorkspaceApiError && reason.status === 401) onExpired(); else setError(reason instanceof Error ? reason.message : "Не удалось добавить участника."); }
    } finally { if (scope.current === current) setBusy(false); }
  }
  const participant = participants.find(value => value.id === selected);
  const kind: PackageKind = participant ? "bid" : "tender";
  const scoped = packages.filter(value => value.project_id === project.id && value.kind === kind && value.participant_id === (participant?.id || null));
  return <section aria-label="Документы проекта" className="mt-7 space-y-6">
    <div className="flex flex-wrap items-center justify-between gap-4 border-y py-4">
      <div><h2 className="text-lg font-semibold">Пакеты документов</h2><p className="mt-1 text-xs text-muted-foreground">Заказчик и {limit === 1 ? "одна заявка поставщика" : "до пяти участников"} · до 20 файлов и 50 МБ на пакет</p></div>
      <div><label className="sr-only" htmlFor="packet-owner">Пакет документов</label><select id="packet-owner" value={selected} disabled={busy || documentBusy} onChange={event => { if (!busy && !documentBusy) setSelected(event.target.value); }} className="h-10 max-w-full rounded-lg border bg-white px-3 text-sm"><option value="">Документация заказчика</option>{participants.map(value => <option value={value.id} key={value.id}>{value.slot}. {value.name}</option>)}</select></div>
    </div>
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    <PackagePanel key={`${organizationId}:${project.id}:${kind}:${participant?.id || "tender"}`} {...props} kind={kind} participantId={participant?.id} participantBusy={busy} packages={scoped} onMutationChange={setDocumentBusy} onUploaded={value => { committed.current.packages.push(value); setPackages(items => [...items.filter(item => item.id !== value.id), value]); }} />
    {canEdit && slot && <form onSubmit={addParticipant} className="flex flex-wrap items-end gap-3 rounded-xl border bg-muted/30 p-4"><div className="min-w-48 flex-1"><label htmlFor="participant-name" className="mb-2 block text-sm font-medium">{project.mode === "buyer" ? `Участник ${slot} из 5` : "Название поставщика"}</label><Input id="participant-name" value={name} onChange={event => setName(event.target.value)} maxLength={500} disabled={busy || documentBusy} placeholder="Название компании" /></div><Button variant="outline" disabled={busy || documentBusy || !name.trim()}>{busy ? <Loader2 className="animate-spin" /> : <Plus />}Добавить участника</Button></form>}
    <ChecklistEditor key={`${organizationId}:${project.id}`} api={api} organizationId={organizationId} projectId={project.id} canEdit={canEdit} onExpired={onExpired}/>
  </section>;
}

function PackagePanel({ api, organizationId, project, canEdit, canAdmin = false, onExpired, kind, participantId, participantBusy, packages, onUploaded, onMutationChange }: Props & { kind: PackageKind; participantId?: string; participantBusy: boolean; packages: PackageSummary[]; onUploaded: (value: PackageSummary) => void; onMutationChange: (busy: boolean) => void }) {
  const newest = [...packages].sort((a, b) => b.version - a.version)[0]?.id || "";
  const [selection, setSelection] = useState("");
  const packageId = selection || newest;
  const [detail, setDetail] = useState<PackageDetail | null>(null);
  const [preflight, setPreflight] = useState<PackagePreflight | null>(null);
  const [runs, setRuns] = useState<NormalizationRun[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [fileError, setFileError] = useState("");
  const [ownBusy, setBusy] = useState(false);
  const [analysisBusy, setAnalysisBusy] = useState(false);
  const busy = ownBusy || participantBusy || analysisBusy;
  const [uploadFailed, setUploadFailed] = useState(false);
  const [refresh, setRefresh] = useState(0);
  const uploadIdentity = useRef<string | null>(null);
  const normalizationIdentity = useRef<{ packageId: string; jobId: string | null; key: string } | null>(null);
  const current = useRef<object | null>(null);
  const mounted = useRef<object | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { onMutationChange(ownBusy || analysisBusy); return () => onMutationChange(false); }, [ownBusy, analysisBusy, onMutationChange]);
  useEffect(() => { const identity = {}; mounted.current = identity; return () => { if (mounted.current === identity) mounted.current = null; }; }, []);
  const latest = runs.at(-1);
  function failure(reason: unknown) { if (reason instanceof WorkspaceApiError && reason.status === 401) onExpired(); else setError(reason instanceof Error ? reason.message : "Не удалось выполнить действие."); }
  useEffect(() => {
    const scope = {}; current.current = scope;
    if (!refresh) { setDetail(null); setPreflight(null); setRuns([]); }
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      if (!packageId) return;
      try {
        const [document, checked, history] = await Promise.all([api.packageDetail(organizationId, project.id, packageId), api.packagePreflight(organizationId, project.id, packageId), api.normalizationRuns(organizationId, project.id, packageId)]);
        if (current.current !== scope) return;
        setDetail(document); setPreflight(checked); setRuns(history); setReadError("");
        if (checked.inspection_status === "pending" || history.some(value => value.job.status === "queued" || value.job.status === "running")) timer = setTimeout(read, 2000);
      } catch (reason) {
        if (current.current === scope) {
          if (reason instanceof WorkspaceApiError && reason.status === 401) onExpired();
          else setReadError(reason instanceof Error ? reason.message : "Не удалось прочитать пакет.");
          if (reason instanceof WorkspaceApiError && (reason.status === 0 || reason.status === 429 || reason.status >= 500)) timer = setTimeout(read, 2000);
        }
      }
    }
    void read();
    return () => { if (current.current === scope) current.current = null; clearTimeout(timer); };
    // The parent supplies a stable session-expiration callback.
  }, [api, organizationId, project.id, packageId, onExpired, refresh]);
  useEffect(() => { setDetail(null); setPreflight(null); setRuns([]); setError(""); setReadError(""); normalizationIdentity.current = null; }, [packageId]);
  function changeFiles(next: File[]) {
    const message = next.length > 20 ? "В пакете может быть не больше 20 файлов." : next.reduce((total, file) => total + file.size, 0) > 50_000_000 ? "Общий размер пакета превышает 50 МБ." : next.some(file => !file.size) ? "Удалите пустые файлы из пакета." : "";
    setFileError(message); setFiles(next); setUploadFailed(false); uploadIdentity.current = null;
  }
  async function upload() {
    if (!canEdit || busy || !files.length || fileError) return;
    const scope = mounted.current; setBusy(true); setError("");
    const key = uploadIdentity.current ||= crypto.randomUUID();
    try {
      const result = await api.uploadPackage(organizationId, project.id, { kind, participantId, files: files.map(file => ({ file, relativePath: file.webkitRelativePath || file.name })) }, key);
      if (mounted.current !== scope) return;
      setBusy(false); onUploaded(result.package); setSelection(result.package.id); setFiles([]); setUploadFailed(false); uploadIdentity.current = null;
    } catch (reason) { if (mounted.current === scope) { setUploadFailed(true); failure(reason); } }
    finally { if (mounted.current === scope) setBusy(false); }
  }
  async function normalize() {
    if (!canEdit || busy || !preflight?.logical_files || !packageId) return;
    const scope = current.current; setBusy(true); setError("");
    const jobId = latest?.job.status === "failed" ? latest.job.id : null;
    const identity = normalizationIdentity.current;
    const key = identity?.packageId === packageId && identity.jobId === jobId ? identity.key : crypto.randomUUID();
    normalizationIdentity.current = { packageId, jobId, key };
    try {
      const value = jobId && latest ? { ...latest, job: await api.retryJob(organizationId, project.id, jobId, key) } : await api.normalizePackage(organizationId, project.id, packageId, {}, key);
      if (current.current === scope) {
        setRuns(items => [...items.filter(item => item.job.id !== value.job.id), value]);
        normalizationIdentity.current = null; setBusy(false); setRefresh(value => value + 1);
      }
    } catch (reason) { if (current.current === scope) failure(reason); }
    finally { if (current.current === scope) setBusy(false); }
  }
  return <div className="space-y-5">
    {packages.length > 0 && <div className="flex items-center justify-between gap-3"><label htmlFor="package-version" className="text-xs font-medium text-muted-foreground">Версия пакета</label><select id="package-version" value={packageId} disabled={busy} onChange={event => setSelection(event.target.value)} className="rounded-md border px-3 py-2 text-xs">{[...packages].sort((a, b) => b.version - a.version).map(value => <option key={value.id} value={value.id}>Версия {value.version} · {new Date(value.created_at).toLocaleDateString("ru")}</option>)}</select></div>}
    {detail && <div className="rounded-xl border bg-white"><div className="flex items-center justify-between border-b px-4 py-3 text-sm"><span className="font-medium">Сохранённые документы</span><span className="text-xs text-muted-foreground">{detail.documents.length} файлов</span></div><ul className="divide-y">{detail.documents.map(value => <li key={value.id} className="flex items-center gap-3 px-4 py-3"><FileText className="size-4 shrink-0 text-primary" /><span className="min-w-0 flex-1 break-words text-sm">{value.relative_path || value.name}{value.provenance_role === "archive" && <span className="ml-2 text-xs text-muted-foreground">архив</span>}</span><span className="text-xs text-muted-foreground">{bytes(value.size)}</span><a href={api.documentDownloadUrl(organizationId, project.id, detail.id, value.id)} aria-label={`Скачать ${value.name}`} className="rounded p-2 text-primary hover:bg-muted"><Download className="size-4" /></a></li>)}</ul></div>}
    {!!preflight?.issues.length && <div className="rounded-xl border border-amber-200 bg-amber-50 p-4"><p className="mb-3 text-sm font-medium text-amber-950">Некоторые файлы пропущены</p><ul className="space-y-2">{preflight.issues.map((issue, index) => <li key={index} className="flex items-start gap-2 text-xs text-amber-900"><AlertCircle className="mt-0.5 size-3.5 shrink-0" /><span><span className="break-all font-medium">{issue.path}</span><span className="block mt-1">{issue.code === "ARCHIVE_CORRUPTED" ? "Архив повреждён" : "Формат или пределы обработки не поддерживаются"}</span></span></li>)}</ul></div>}
    {preflight && <div className="rounded-lg bg-muted/40 px-4 py-3 text-xs leading-6 text-muted-foreground"><span className="font-medium text-foreground">{preflight.logical_files} документов для обработки · {bytes(preflight.logical_bytes)}</span>{preflight.page_count !== null && <span className="ml-3">{preflight.page_count} страниц</span>}{preflight.token_upper_bound !== null && <span className="ml-3">До {preflight.token_upper_bound.toLocaleString("ru")} токенов</span>}{!preflight.logical_files && <p className="text-amber-900">Нет пригодных документов. Загрузите исправный пакет.</p>}</div>}
    {latest && <div role="status" className="rounded-lg border p-4"><div className="flex items-center justify-between text-sm"><span>{statusLabels[latest.job.status]}</span><span className="tabular-nums text-muted-foreground">{latest.job.progress}%</span></div><div className="mt-3 h-1.5 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary transition-[width] motion-reduce:transition-none" style={{ width: `${latest.job.progress}%` }} /></div>{latest.job.error && <p className="mt-3 text-xs text-red-800">{latest.job.error.message}</p>}</div>}
    {canEdit && <><div className="rounded-xl border border-dashed bg-muted/20 p-5 text-center" onDragOver={event => event.preventDefault()} onDrop={event => { event.preventDefault(); if (!busy) changeFiles([...files, ...Array.from(event.dataTransfer.files)]); }}><Upload className="mx-auto size-6 text-primary" /><p className="mt-3 text-sm font-medium">{detail ? "Загрузить новую версию пакета" : "Добавьте документацию"}</p><p className="mt-2 text-xs leading-5 text-muted-foreground">Документы, таблицы, изображения и архивы · до 20 файлов, 50 МБ</p><input ref={input} type="file" multiple aria-label="Документы пакета" className="sr-only" disabled={busy} onChange={event => { changeFiles([...files, ...Array.from(event.target.files || [])]); event.target.value = ""; }} /><Button variant="outline" className="mt-4" disabled={busy} onClick={() => input.current?.click()}><Plus />Выбрать файлы</Button></div>{!!files.length && <ul className="divide-y rounded-lg border">{files.map((file, index) => <li className="flex items-center gap-2 px-3 py-2" key={index}><span className="min-w-0 flex-1 break-all text-xs">{file.webkitRelativePath || file.name}</span><span className="text-xs text-muted-foreground">{bytes(file.size)}</span><button aria-label={`Удалить ${file.name}`} disabled={busy} onClick={() => changeFiles(files.filter((_, i) => i !== index))} className="rounded p-2 hover:bg-muted"><X className="size-3" /></button></li>)}</ul>}{fileError && <p role="alert" className="text-sm text-red-800">{fileError}</p>}<div className="flex flex-wrap gap-3"><Button disabled={busy || !files.length || !!fileError} onClick={upload}>{busy ? <Loader2 className="animate-spin" /> : <Upload />}{uploadFailed ? "Повторить загрузку" : "Сохранить пакет"}</Button><Button variant="outline" disabled={busy || !preflight?.logical_files || latest?.job.status === "running" || latest?.job.status === "queued"} onClick={normalize}><RefreshCw />{latest?.job.status === "failed" ? "Повторить обработку" : "Обработать документы"}</Button></div></>}
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {readError && <div role="alert" className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-red-50 p-3 text-sm text-red-800"><span>{readError}</span><Button variant="outline" size="sm" disabled={busy} onClick={() => setRefresh(value => value + 1)}><RefreshCw />Повторить чтение</Button></div>}
    {latest && packageId && (latest.job.status === "succeeded" || latest.job.status === "failed") && <AnalysisPanel key={packageId} api={api} organizationId={organizationId} projectId={project.id} packageId={packageId} kind={kind} normalization={latest} canEdit={canEdit} canAdmin={canAdmin} locked={ownBusy || participantBusy} onExpired={onExpired} onMutationChange={setAnalysisBusy} />}
  </div>;
}
