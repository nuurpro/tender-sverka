"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AlertCircle, FileSearch, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { WorkspaceApiError, type WorkspaceApi } from "@/lib/workspace-api";
import type { AiPolicy, AnalysisCoverage, AnalysisOptions, AnalysisPreflight, AnalysisStart, EvidenceCitation } from "@/lib/analysis-types";
import type { NormalizationRun, SourceAnchor } from "@/lib/document-types";
import type { PackageKind } from "@/lib/package-types";

type Props = {
  api: WorkspaceApi; organizationId: string; projectId: string; packageId: string; kind: PackageKind;
  normalization: NormalizationRun | null; canEdit: boolean; canAdmin: boolean; onExpired: () => void;
  locked?: boolean;
  onMutationChange?: (busy: boolean) => void;
};
type SelectedEvidence = { parameter: string; condition: string; citations: EvidenceCitation[]; flags: string[] };
const number = (value: number) => value.toLocaleString("ru");
const labels = { queued: "Анализ в очереди", running: "Извлекаем требования и факты", succeeded: "Анализ завершён", failed: "Анализ остановлен", cancelled: "Анализ отменён" };
const recoverable = (reason: unknown) => reason instanceof WorkspaceApiError && (reason.status === 0 || reason.status === 429 || reason.status >= 500);

export function AnalysisPanel({ api, organizationId, projectId, packageId, kind, normalization, canEdit, canAdmin, locked = false, onExpired, onMutationChange }: Props) {
  const [policy, setPolicy] = useState<AiPolicy | null>(null);
  const [reason, setReason] = useState("");
  const [requests, setRequests] = useState("32");
  const [tokens, setTokens] = useState("1000000");
  const [preflight, setPreflight] = useState<AnalysisPreflight | null>(null);
  const [run, setRun] = useState<AnalysisStart | null>(null);
  const [ownBusy, setBusy] = useState(false);
  const busy = ownBusy || locked;
  const [loading, setLoading] = useState(true);
  const [ready, setReady] = useState(false);
  const [loadRefresh, setLoadRefresh] = useState(0);
  const [error, setError] = useState("");
  const [readError, setReadError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [selected, setSelected] = useState<SelectedEvidence | null>(null);
  const scope = useRef<object | null>(null);
  const action = useRef<{ identity: string; key: string } | null>(null);
  const options = useMemo<AnalysisOptions>(() => ({ normalization_job_id: normalization?.job.id || "", normalization_attempt: normalization?.job.attempt || 0, budget: { max_requests: Number(requests), max_tokens: Number(tokens), cost_ceiling: null } }), [normalization?.job.id, normalization?.job.attempt, requests, tokens]);
  const inputValid = !!options.normalization_job_id && options.normalization_attempt > 0 && Number.isSafeInteger(options.budget.max_requests) && options.budget.max_requests >= 1 && options.budget.max_requests <= 500 && Number.isSafeInteger(options.budget.max_tokens) && options.budget.max_tokens >= 1 && options.budget.max_tokens <= 20000000;
  const terminal = normalization?.job.status === "succeeded" || normalization?.job.status === "failed";
  const pending = run?.job.status === "queued" || run?.job.status === "running";
  const withinBudget = !!preflight && preflight.worst_case_run_tokens <= options.budget.max_tokens && preflight.max_provider_attempts <= options.budget.max_requests;
  const runId = run?.run.id;

  function failure(value: unknown) {
    if (value instanceof WorkspaceApiError && value.status === 401) onExpired();
    else setError(value instanceof Error ? value.message : "Не удалось выполнить анализ.");
  }
  useEffect(() => {
    const identity = {}; scope.current = identity;
    setPolicy(null); setRun(null); setPreflight(null); setError(""); setReadError(""); setSelected(null); setBusy(false); setLoading(true); setReady(false); action.current = null;
    Promise.all([api.aiPolicy(organizationId), api.analysisRuns(organizationId, projectId, packageId)]).then(([allowed, history]) => {
      if (scope.current !== identity) return;
      setPolicy(allowed); setRun(history[0] || null); setReady(true);
    }).catch(value => { if (scope.current === identity) failure(value); }).finally(() => { if (scope.current === identity) setLoading(false); });
    return () => { if (scope.current === identity) scope.current = null; };
  }, [api, organizationId, projectId, packageId, onExpired, loadRefresh]);
  useEffect(() => { setPreflight(null); action.current = null; }, [options]);
  useEffect(() => { onMutationChange?.(ownBusy); return () => onMutationChange?.(false); }, [ownBusy, onMutationChange]);
  useEffect(() => {
    if (!runId) return;
    const identity = scope.current;
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    async function read() {
      try {
        const value = await api.analysisRun(organizationId, projectId, runId!);
        if (!active || scope.current !== identity) return;
        setRun(value); setReadError("");
        if (value.job.status === "queued" || value.job.status === "running") timer = setTimeout(read, 2000);
      } catch (value) {
        if (!active || scope.current !== identity) return;
        if (value instanceof WorkspaceApiError && value.status === 401) onExpired();
        else setReadError(value instanceof Error ? value.message : "Не удалось прочитать анализ.");
        if (recoverable(value)) timer = setTimeout(read, 2000);
      }
    }
    void read();
    return () => { active = false; clearTimeout(timer); };
  }, [api, organizationId, projectId, runId, refresh, onExpired]);

  async function consent(event: React.FormEvent) {
    event.preventDefault(); if (!canAdmin || !policy || busy || !reason.trim()) return;
    const identity = scope.current; setBusy(true); setError("");
    try {
      const value = await api.setAiPolicy(organizationId, { allowed: true, expected_version: policy.version, reason: reason.trim() });
      if (scope.current === identity) { setPolicy(value); setReason(""); setPreflight(null); }
    } catch (value) { if (scope.current === identity) failure(value); }
    finally { if (scope.current === identity) setBusy(false); }
  }
  async function inspect() {
    if (!canEdit || !ready || loading || busy || pending || !terminal || !inputValid) return;
    const identity = scope.current; setBusy(true); setError("");
    try {
      const value = await api.analysisPreflight(organizationId, projectId, packageId, options);
      if (scope.current === identity) { setPreflight(value); setPolicy({ allowed: value.external_ai_allowed, version: value.policy_version }); }
    } catch (value) { if (scope.current === identity) failure(value); }
    finally { if (scope.current === identity) setBusy(false); }
  }
  async function execute(retry: boolean) {
    if (!canEdit || !ready || loading || busy || pending || (!retry && (!terminal || !withinBudget || !policy?.allowed))) return;
    if (retry && run?.job.status !== "failed") return;
    const identity = scope.current; setBusy(true); setError("");
    const inputIdentity = retry ? `retry:${run!.job.id}` : `start:${JSON.stringify(options)}`;
    const key = action.current?.identity === inputIdentity ? action.current.key : crypto.randomUUID();
    action.current = { identity: inputIdentity, key };
    try {
      const value = retry ? { ...run!, job: await api.retryJob(organizationId, projectId, run!.job.id, key) } : await api.startAnalysis(organizationId, projectId, packageId, options, key);
      if (scope.current === identity) { setRun(value); action.current = null; setRefresh(value => value + 1); }
    } catch (value) { if (scope.current === identity) failure(value); }
    finally { if (scope.current === identity) setBusy(false); }
  }

  return <section aria-label="Анализ документов" className="space-y-5 rounded-xl border bg-white p-5">
    <div className="flex items-start gap-3"><FileSearch className="mt-0.5 size-5 shrink-0 text-primary" /><div><h3 className="font-semibold">{kind === "tender" ? "Извлечение требований" : "Факты заявки"}</h3><p className="mt-2 text-xs leading-5 text-muted-foreground">Документы передаются настроенному внешнему AI-провайдеру. Цитаты проверяются по сохранённым источникам; спорные пункты требуют проверки.</p></div></div>
    {!terminal && <p className="text-sm text-muted-foreground">Сначала обработайте документы пакета.</p>}
    {policy && !policy.allowed && (canAdmin ? <form onSubmit={consent} className="space-y-3 rounded-lg bg-amber-50 p-4"><p className="text-sm text-amber-950">Передача документов во внешнюю AI-службу пока не разрешена для организации.</p><label className="block text-xs font-medium" htmlFor="ai-consent-reason">Основание разрешения</label><Input id="ai-consent-reason" value={reason} onChange={event => setReason(event.target.value)} maxLength={2000} disabled={busy} placeholder="Какие документы разрешено передавать" /><Button variant="outline" disabled={busy || !reason.trim()}>Разрешить передачу документов</Button></form> : <p className="rounded-lg bg-amber-50 p-4 text-sm text-amber-950">Передачу документов внешнему AI-провайдеру должен разрешить администратор организации.</p>)}
    {canEdit && <><div className="grid gap-4 sm:grid-cols-2"><div><label htmlFor="analysis-request-budget" className="mb-2 block text-xs font-medium">Лимит запросов, включая повторы</label><Input id="analysis-request-budget" type="number" min="1" max="500" value={requests} disabled={busy || pending} onChange={event => setRequests(event.target.value)} /></div><div><label htmlFor="analysis-token-budget" className="mb-2 block text-xs font-medium">Лимит токенов на анализ</label><Input id="analysis-token-budget" type="number" min="1" max="20000000" value={tokens} disabled={busy || pending} onChange={event => setTokens(event.target.value)} /></div></div>
      {preflight && <div className="space-y-2 rounded-lg bg-muted/40 p-4 text-xs leading-5"><p className="font-medium">До {number(preflight.worst_case_run_tokens)} токенов · до {number(preflight.max_provider_attempts)} запросов с повторами</p><p>{preflight.extraction_chunk_count} частей документов · {preflight.reference_lookup_count} проверок ссылок</p>{!preflight.pricing_known && <p className="text-muted-foreground">Стоимость не рассчитана: тариф модели не задан. Действуют указанные лимиты запросов и токенов.</p>}{!withinBudget && <p className="text-red-800">Оценка превышает установленный лимит. Увеличьте лимит и повторите оценку.</p>}{preflight.coverage_warnings.length > 0 && <p className="flex items-center gap-2 text-amber-900"><AlertCircle className="size-4 shrink-0" />{preflight.coverage_warnings.length} участков или файлов требуют проверки. Неполное покрытие будет отмечено в результатах.</p>}</div>}
      <div className="flex flex-wrap gap-3"><Button variant="outline" disabled={!ready || loading || busy || pending || !terminal || !inputValid} onClick={inspect}>Оценить объём анализа</Button><Button disabled={!ready || loading || busy || pending || !withinBudget || !policy?.allowed || !terminal} onClick={() => execute(false)}>{busy && <Loader2 className="animate-spin" />}Начать анализ</Button></div></>}
    {run && <div role="status" className="space-y-3 border-t pt-4"><div className="flex items-center justify-between gap-3 text-sm"><span className="font-medium">{labels[run.job.status]}</span><span className="tabular-nums text-muted-foreground">{run.job.progress}%</span></div>{run.job.error && <p className="text-sm text-red-800">{run.job.error.message}</p>}{run.job.status === "failed" && canEdit && <Button variant="outline" disabled={busy} onClick={() => execute(true)}><RefreshCw />Повторить анализ</Button>}
      {run.run.result.requirements?.map((value, index) => <div key={index} className="flex items-start justify-between gap-3 rounded-lg border p-3 text-sm"><div>{value.item && <p className="mb-2 text-xs text-muted-foreground">{value.item}</p>}<p className="font-medium">{value.parameter}</p><p className="mt-1 text-muted-foreground">{value.condition}</p>{!!value.review_flags.length && <span className="mt-2 block text-xs text-amber-900">Требует проверки</span>}</div><Button variant="ghost" size="sm" aria-label={`Источник: ${value.parameter}`} onClick={() => setSelected({ parameter: value.parameter, condition: value.condition, citations: value.citations, flags: value.review_flags })}>Источник</Button></div>)}
      {run.run.result.facts?.map((value, index) => <div key={index} className="flex items-start justify-between gap-3 rounded-lg border p-3 text-sm"><div>{(value.item || value.model) && <p className="mb-2 text-xs text-muted-foreground">{[value.item, value.model].filter(Boolean).join(" · ")}</p>}<p className="font-medium">{value.parameter}</p><p className="mt-1 text-muted-foreground">{[value.value || value.date || "Значение не указано", value.unit].filter(Boolean).join(" ")}</p>{!!value.review_flags.length && <span className="mt-2 block text-xs text-amber-900">Требует проверки</span>}</div><Button variant="ghost" size="sm" aria-label={`Источник: ${value.parameter}`} onClick={() => setSelected({ parameter: value.parameter, condition: [value.value || value.date || "Значение не указано", value.unit].filter(Boolean).join(" "), citations: value.citations, flags: value.review_flags })}>Источник</Button></div>)}
      {run.run.result.findings?.map((value, index) => <div key={index} className="flex items-start justify-between gap-3 rounded-lg bg-amber-50 p-3 text-xs leading-5 text-amber-950"><p>{value.detail}</p>{!!value.citations.length && <Button variant="ghost" size="sm" aria-label={`Источник замечания ${index + 1}`} onClick={() => setSelected({ parameter: "Замечание к документам", condition: value.detail, citations: value.citations, flags: value.review_flags || [] })}>Источник</Button>}</div>)}
      {run.run.result.coverage?.filter(value => !["text_extracted", "extracted", "corrected"].includes(value.state)).map((value, index) => <div key={index} className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs leading-5 text-amber-950"><p className="break-words font-medium">{coverageName(value)}</p><p className="mt-1">{coverageReason(value.reason)}</p>{value.anchor && <p className="mt-1">{location(value.anchor)}</p>}</div>)}
    </div>}
    {error && <p role="alert" className="rounded-lg bg-red-50 p-3 text-sm text-red-800">{error}</p>}
    {!ready && !loading && <Button variant="outline" disabled={busy} onClick={() => setLoadRefresh(value => value + 1)}><RefreshCw />Повторить загрузку анализа</Button>}
    {readError && <div role="alert" className="space-y-3 rounded-lg bg-red-50 p-3 text-sm text-red-800"><p>{readError}</p><Button variant="outline" size="sm" onClick={() => setRefresh(value => value + 1)}>Повторить чтение анализа</Button></div>}
    <Sheet open={!!selected} onOpenChange={open => { if (!open) setSelected(null); }}><SheetContent closeLabel="Закрыть источник анализа"><SheetTitle className="pr-8 text-xl font-semibold">{selected?.parameter}</SheetTitle><SheetDescription className="mt-3 text-sm leading-6">{selected?.condition}</SheetDescription><div className="mt-6 space-y-5">{selected && (!!selected.flags.length || selected.citations.some(value => value.review_flags.length > 0)) && <p className="rounded-lg bg-amber-50 p-3 text-xs text-amber-950">Источник требует проверки</p>}{selected?.citations.map((citation, index) => <article key={index} className="rounded-xl border p-4"><p className="break-words text-sm font-medium">{citation.filename}</p><p className="mt-2 text-xs text-muted-foreground">{location(citation.anchor)}</p><blockquote className="mt-4 whitespace-pre-wrap break-words border-l-2 border-primary/40 pl-4 text-sm leading-6">{citation.quote}</blockquote>{citation.quote_kind === "ocr_transcription" && <p className="mt-4 text-xs text-amber-900">Распознанный текст: сверьте с изображением документа.</p>}{citation.confidence !== null && <p className="mt-2 text-xs text-muted-foreground">Уверенность распознавания: {citation.confidence.toFixed(1)}%</p>}<p className="mt-4 break-all text-[11px] text-muted-foreground">Блок: {citation.block_id}</p></article>)}</div></SheetContent></Sheet>
  </section>;
}

function coverageName(value: AnalysisCoverage): string {
  if (value.filename) return value.filename;
  if (value.detail && typeof value.detail === "object" && "path" in value.detail && typeof value.detail.path === "string") return value.detail.path;
  return "Область документа";
}

function coverageReason(reason: string | null | undefined): string {
  const messages: Record<string, string> = { no_published_artifact: "Нет результата обработки документа", archive_member_skipped: "Файл архива пропущен", adapter_unavailable: "Формат документа пока не обработан", parser_failed: "Не удалось прочитать документ", ocr_pending: "Текст изображения ещё не распознан", vision_pending: "Изображение требует проверки", low_confidence: "Низкая уверенность распознавания" };
  return reason ? messages[reason] || reason : "Участок требует проверки";
}

function location(anchor: SourceAnchor): string {
  if (anchor.kind === "pdf") return `Страница ${anchor.page}`;
  if (anchor.kind === "docx") return anchor.table !== null ? `Таблица ${anchor.table}, строка ${anchor.row ?? "—"}` : `Абзац ${anchor.paragraph ?? "—"}`;
  if (anchor.kind === "xlsx") return `Лист ${anchor.sheet}, ячейка ${anchor.cell}, строка ${anchor.row}`;
  if (anchor.kind === "text" || anchor.kind === "csv") return anchor.line_start === anchor.line_end ? `Строка ${anchor.line_start}` : `Строки ${anchor.line_start}–${anchor.line_end}`;
  if (anchor.kind === "raster") return anchor.page !== null ? `Страница ${anchor.page}` : `Изображение ${anchor.frame}`;
  if (anchor.kind === "part") return anchor.page !== null ? `Страница ${anchor.page}` : "Блок документа";
  return "Блок документа";
}
