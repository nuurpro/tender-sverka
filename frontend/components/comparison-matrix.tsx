"use client";
import { useState } from "react";
import { AlertTriangle, Check, Download, Filter, Loader2 } from "lucide-react";
import type { Check as CheckData, Status } from "@/lib/types";
import { Button } from "./ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import { EvidencePanel, type Selection } from "./evidence-panel";
import { importanceLabels, StatusLabel } from "./status-label";

function percent(value: number | null) { return value === null ? "—" : value.toLocaleString("ru-RU") + "%"; }
export function ComparisonMatrix({ check, busy, onExport }: { check: CheckData; busy: boolean; onExport: () => Promise<void> }) {
  const [problemsOnly, setProblemsOnly] = useState(false);
  const [selected, setSelected] = useState<Selection | null>(null);
  const slots = Object.keys(check.bids).sort();
  const lookup = Object.fromEntries(slots.map(slot => [slot, Object.fromEntries((check.results[slot]?.comparison.rows || []).map(r => [r.requirement_id, r]))]));
  const requirements = check.requirements.filter(r => !problemsOnly || slots.some(s => lookup[s]?.[r.id]?.status !== "compliant"));
  const complete = check.status === "done" && Object.keys(check.results).length === slots.length;
  return <section className="surface overflow-hidden">
    <div className="flex flex-wrap items-center justify-between gap-5 border-b p-6 sm:p-8">
      <div><span className="eyebrow">ШАГ 04 · {slots.length} УЧАСТНИКА</span><h2 className="mt-2 text-xl font-semibold">Матрица соответствия</h2><p className="mt-2 text-sm text-muted-foreground">Нажмите на статус, чтобы посмотреть цитаты и обоснование.</p></div>
      <Button variant="outline" disabled={busy || !complete} onClick={onExport}><Download />Экспорт XLSX</Button>
    </div>
    <div className="flex flex-wrap items-center justify-between gap-3 border-b px-6 py-4">
      <div className="flex flex-wrap items-center gap-2">{(["compliant", "partial", "unconfirmed", "non_compliant", "review"] as Status[]).map(status => <StatusLabel key={status} status={status} className="border-transparent px-2 py-1 text-[10px]" />)}</div>
      <button aria-pressed={problemsOnly} onClick={() => setProblemsOnly(!problemsOnly)} className={"flex items-center gap-2 rounded-md px-3 py-2 text-xs " + (problemsOnly ? "bg-primary/10 font-medium text-primary" : "text-muted-foreground hover:bg-muted")}><Filter className="size-3.5" />Только проблемы</button>
    </div>
    <Table>
      <TableHeader><TableRow className="bg-muted/35">
        <TableHead className="min-w-72 max-w-md px-6 py-6 align-bottom"><span className="eyebrow">ТРЕБОВАНИЯ ЗАКАЗЧИКА</span><p className="mt-2 text-sm font-normal">{check.requirements.length} пунктов · веса 3 / 2 / 1</p></TableHead>
        {slots.map(slot => {
          const result = check.results[slot];
          return <TableHead key={slot} className="min-w-60 border-l p-6">
            <p className="eyebrow">УЧАСТНИК {String(slot).padStart(2, "0")}</p>
            <p className="mt-2 max-w-56 truncate text-sm font-semibold text-foreground" title={check.bids[slot].name}>{check.bids[slot].name}</p>
            <p className="mt-4 text-3xl font-semibold tracking-tight text-foreground">{result ? percent(result.score.percent) : "—"}</p>
            {result ? <>
              <p className={"mt-2 flex items-center gap-1 text-xs " + (result.score.risk ? "text-red-700" : result.score.review_count ? "text-stone-600" : "text-primary")}>
                {result.score.risk ? <AlertTriangle className="size-3.5" /> : <Check className="size-3.5" />}
                {result.score.risk ? "Риск отклонения" : result.score.review_count ? "Нужна ручная проверка" : "Обязательные выполнены"}
              </p>
              {result.score.review_count > 0 && <p className="mt-2 text-[11px] font-normal text-muted-foreground">Диапазон {percent(result.score.min_percent)}–{percent(result.score.max_percent)} · на проверке: {result.score.review_count}</p>}
            </> : <p className="mt-2 flex items-center gap-1.5 text-xs"><Loader2 className="size-3 animate-spin" />Ожидает обработки</p>}
          </TableHead>;
        })}
      </TableRow></TableHeader>
      <TableBody>{requirements.map(requirement => <TableRow key={requirement.id}>
        <TableCell className="max-w-md px-6"><div className="mb-2 flex items-center gap-2"><span className="font-mono text-[11px] text-muted-foreground">{requirement.id}</span><span className="rounded bg-muted px-2 py-0.5 text-[10px] text-muted-foreground">{importanceLabels[requirement.importance]}</span></div><p className="font-medium">{requirement.parameter}</p><p className="mt-1.5 text-xs leading-5 text-muted-foreground">{requirement.condition}</p></TableCell>
        {slots.map(slot => {
          const row = lookup[slot]?.[requirement.id];
          return <TableCell key={slot} className="border-l px-6 align-middle">{row ? <button onClick={() => setSelected({ requirement, slot, row })} className="rounded-md text-left outline-none transition-opacity hover:opacity-75 focus-visible:ring-2 focus-visible:ring-ring" aria-label={requirement.id + " · " + check.bids[slot].name + " · Источники"}><StatusLabel status={row.status} /><span className="mt-2 block text-[11px] text-muted-foreground">{row.citations.length ? row.citations.length + " источн. · Подробнее" : "Подробнее"}</span></button> : <span className="text-muted-foreground">—</span>}</TableCell>;
        })}
      </TableRow>)}</TableBody>
    </Table>
    {!requirements.length && <p className="p-10 text-center text-sm text-muted-foreground">Проблем по выбранному фильтру не найдено.</p>}
    <div className="border-t px-6 py-4 text-xs leading-5 text-muted-foreground">Процент показывает соответствие чек-листу. Невыполненное обязательное требование отмечается как риск независимо от оценки.</div>
    <EvidencePanel check={check} selected={selected} onClose={() => setSelected(null)} />
  </section>;
}
