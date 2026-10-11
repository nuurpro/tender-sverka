"use client";
import { useEffect, useState } from "react";
import { AlertCircle, ArrowLeft, ArrowRight, Check as CheckIcon, FileCheck2, Loader2, Plus, RotateCcw } from "lucide-react";
import { api } from "@/lib/api";
import type { Check, Health, Requirement } from "@/lib/types";
import { UploadStep } from "@/components/upload-step";
import { ChecklistStep } from "@/components/checklist-step";
import { BidsStep } from "@/components/bids-step";
import { ComparisonMatrix } from "@/components/comparison-matrix";
import { Button } from "@/components/ui/button";

const steps = ["Документация", "Чек-лист", "Заявки", "Сравнение"];
const STORAGE = "tender-sverka-check";

export default function Page() {
  const [checkId, setCheckId] = useState<string | null>(null);
  const [check, setCheck] = useState<Check | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [step, setStep] = useState(1);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [connectionError, setConnectionError] = useState("");

  useEffect(() => {
    const saved = localStorage.getItem(STORAGE);
    if (saved) setCheckId(saved);
    const loadHealth = () => api.health().then(data => { setHealth(data); setConnectionError(""); }).catch(err => setConnectionError(err.message));
    loadHealth();
    const timer = setInterval(loadHealth, 5000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!checkId) return;
    let cancelled = false;
    let first = true;
    async function poll() {
      try {
        const data = await api.get(checkId!);
        if (cancelled) return;
        setCheck(data); setConnectionError("");
        if (first) {
          setStep(Object.keys(data.results).length ? 4 : data.approved ? 3 : data.requirements.length ? 2 : 1);
          first = false;
        } else {
          setStep(current => current === 1 && data.requirements.length && data.status === "done" ? 2 :
            current === 3 && data.stage === "compare" && Object.keys(data.results).length ? 4 : current);
        }
      } catch (err) {
        if (!cancelled) setConnectionError(err instanceof Error ? err.message : "Не удалось получить проверку");
      }
    }
    poll();
    const timer = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [checkId]);

  const busy = pending || check?.status === "queued" || check?.status === "running";
  async function perform(action: () => Promise<void>) {
    setPending(true); setError("");
    try { await action(); return true; }
    catch (err) { setError(err instanceof Error ? err.message : "Не удалось выполнить действие"); return false; }
    finally { setPending(false); }
  }
  async function refresh() { if (checkId) setCheck(await api.get(checkId)); }
  async function upload(files: File[]) {
    await perform(async () => {
      const result = await api.create(files);
      localStorage.setItem(STORAGE, result.id); setCheck(null); setCheckId(result.id); setStep(1);
    });
  }
  async function approve(rows: Requirement[]) {
    await perform(async () => { await api.approve(checkId!, rows); await refresh(); setStep(3); });
  }
  async function saveBid(slot: number, name: string, files: File[]) {
    return perform(async () => { await api.bid(checkId!, slot, name, files); await refresh(); });
  }
  async function compare() {
    await perform(async () => { await api.compare(checkId!); await refresh(); });
  }
  async function exportMatrix() { await perform(() => api.export(checkId!)); }
  function reset() {
    localStorage.removeItem(STORAGE); setCheckId(null); setCheck(null); setStep(1); setError("");
  }
  function available(value: number) {
    return value === 1 || value === 2 && !!check?.requirements.length ||
      value === 3 && !!check?.approved || value === 4 && !!Object.keys(check?.results || {}).length;
  }
  const displayedError = error || connectionError || check?.error;
  const progress = check?.stage === "compare"
    ? "Сравнение заявок · " + Object.keys(check.results).length + " из " + Object.keys(check.bids).length + " готово"
    : "Извлекаем требования из документации";

  return <div className="min-h-screen">
    <header className="border-b bg-white">
      <div className="mx-auto flex h-20 max-w-7xl items-center justify-between gap-4 px-5 sm:px-8">
        <a href="/" className="flex items-center gap-3" aria-label="Тендер-Сверка — главная"><span className="flex size-10 items-center justify-center rounded-xl bg-primary text-white"><FileCheck2 className="size-5" /></span><span><span className="block text-lg font-semibold tracking-tight">Тендер-Сверка</span><span className="block text-[11px] text-muted-foreground">Проверка тендерных заявок</span></span></a>
        <div className="flex items-center gap-4">
          <span className="hidden items-center gap-2 rounded-full border bg-muted/30 px-3 py-1.5 text-[11px] text-muted-foreground sm:flex"><span className={"size-1.5 rounded-full " + (health ? "bg-primary" : "bg-stone-300")} />{health?.ai_mode === "demo" ? "Учебный режим" : health ? (health.provider || "OpenAI") : "Подключение к серверу"}</span>
          <div className="flex items-center gap-2 border-l pl-4"><span className="flex size-8 items-center justify-center rounded-full bg-[#eceee6] text-[10px] font-semibold">DM</span><span className="hidden text-xs sm:block">Демо</span></div>
        </div>
      </div>
    </header>
    <main className="mx-auto max-w-7xl px-5 py-8 sm:px-8 sm:py-10">
      <div className="mb-8 flex flex-wrap items-start justify-between gap-4">
        <div><p className="eyebrow">РАБОЧЕЕ ПРОСТРАНСТВО {check && <span className="ml-2 font-mono tracking-normal">/ {check.id.slice(0, 8)}</span>}</p><h1 className="mt-3 text-2xl font-semibold tracking-tight sm:text-3xl">Проверка тендерной заявки</h1><p className="mt-3 text-sm text-muted-foreground">Требования заказчика, предложения участников и основания каждого вывода.</p></div>
        {checkId && <Button variant="outline" size="sm" disabled={!!busy} onClick={reset}><Plus />Новая проверка</Button>}
      </div>
      <nav className="mb-8 grid grid-cols-2 gap-2 rounded-xl border bg-white p-2 sm:grid-cols-4" aria-label="Этапы проверки">
        {steps.map((label, index) => {
          const value = index + 1;
          return <button key={label} disabled={!available(value) || !!busy} aria-current={step === value ? "step" : undefined} onClick={() => { setError(""); setStep(value); }}
            className={"flex items-center gap-3 rounded-lg px-3 py-4 text-left transition-colors disabled:cursor-default sm:px-5 " + (step === value ? "bg-[#edf2ee]" : "hover:bg-muted/50")}>
            <span className={"flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-medium " + (step === value ? "border-primary bg-primary text-white" : value < step ? "border-primary/20 text-primary" : "border-input text-muted-foreground")}>{value < step ? <CheckIcon className="size-3.5" /> : value}</span>
            <span className={"text-xs sm:text-sm " + (step === value ? "font-semibold" : "text-muted-foreground")}>{label}</span>
            {step === value && <ArrowRight className="ml-auto hidden size-4 text-primary lg:block" />}
          </button>;
        })}
      </nav>
      {health?.ai_mode === "demo" && <p className="mb-6 rounded-lg border border-primary/10 bg-primary/5 px-4 py-3 text-xs leading-5 text-primary">Учебный режим: используются подготовленные примеры, запросы в OpenAI не выполняются. Для проверки собственных документов подключите API-ключ.</p>}
      {displayedError && <div role="alert" className="mb-6 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-red-200 bg-red-50 p-4">
        <p className="flex flex-1 items-start gap-2 text-sm text-red-800"><AlertCircle className="mt-0.5 size-4 shrink-0" />{displayedError}</p>
        {check?.status === "failed" && ["extract", "compare"].includes(check.stage) && <Button variant="outline" size="sm" disabled={!!busy} onClick={() => perform(async () => { await api.retry(checkId!); await refresh(); })}><RotateCcw />Повторить</Button>}
        {connectionError && checkId && !check && <Button variant="outline" size="sm" onClick={reset}>Начать заново</Button>}
      </div>}
      {busy && <div role="status" className="mb-6 flex items-center gap-3 rounded-xl border bg-white px-5 py-4"><Loader2 className="size-5 animate-spin text-primary" /><div><p className="text-sm font-medium">{pending ? "Сохраняем данные" : progress}</p><p className="mt-1 text-xs text-muted-foreground">Результат появится автоматически. Страницу можно оставить открытой.</p></div></div>}
      {step === 1 && <UploadStep onUpload={upload} busy={!!busy} demo={health?.ai_mode === "demo"} />}
      {step === 2 && check && <ChecklistStep requirements={check.requirements} blocks={check.blocks} busy={!!busy} onApprove={approve} />}
      {step === 3 && check && <BidsStep bids={check.bids} busy={!!busy} onSave={saveBid} onCompare={compare} />}
      {step === 4 && check && <ComparisonMatrix check={check} busy={!!busy} onExport={exportMatrix} />}
      {step > 1 && <button disabled={!!busy} className="mt-6 inline-flex items-center gap-2 text-xs text-muted-foreground hover:text-primary disabled:opacity-40" onClick={() => setStep(step - 1)}><ArrowLeft className="size-3.5" />Назад к предыдущему шагу</button>}
      <footer className="mt-12 flex flex-wrap items-center justify-between gap-3 border-t pt-5 text-[11px] text-muted-foreground"><span>Тендер-Сверка · MVP</span><span>Точные цитаты. Проверяемые результаты.</span></footer>
    </main>
  </div>;
}
