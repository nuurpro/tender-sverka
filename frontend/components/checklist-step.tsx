"use client";
import { useEffect, useState } from "react";
import { ArrowRight, FileText, Loader2, Trash2 } from "lucide-react";
import type { Block, Requirement } from "@/lib/types";
import { Button } from "./ui/button";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "./ui/table";
import { importanceLabels } from "./status-label";

export function ChecklistStep({ requirements, blocks, busy, onApprove }: {
  requirements: Requirement[]; blocks: Block[]; busy: boolean; onApprove: (rows: Requirement[]) => Promise<void>;
}) {
  const signature = JSON.stringify(requirements);
  const [rows, setRows] = useState(requirements);
  const [scope, setScope] = useState("all");
  useEffect(() => { setRows(JSON.parse(signature)); }, [signature]);
  const visible = rows.filter(r => scope === "all" || r.scope === scope);
  function edit(id: string, patch: Partial<Requirement>) { setRows(current => current.map(r => r.id === id ? { ...r, ...patch } : r)); }
  return <section className="surface overflow-hidden">
    <div className="flex flex-wrap items-end justify-between gap-5 border-b p-6 sm:p-8">
      <div><span className="eyebrow">ШАГ 02 · {rows.length} ТРЕБОВАНИЙ</span><h2 className="mt-2 text-xl font-semibold">Проверьте чек-лист</h2><p className="mt-2 text-sm text-muted-foreground">Уточните условия и обязательность. Исходные цитаты сохранены ниже.</p></div>
      <div className="flex rounded-lg border bg-muted/50 p-1">
        {[["all", "Все"], ["product", "Товар"], ["participant", "Участник"]].map(([value, label]) =>
          <button key={value} type="button" onClick={() => setScope(value)} className={"rounded-md px-3 py-2 text-xs " + (scope === value ? "bg-white font-medium shadow-sm" : "text-muted-foreground")}>{label}</button>)}
      </div>
    </div>
    <Table>
      <TableHeader><TableRow className="bg-muted/40"><TableHead className="w-20">ID</TableHead><TableHead className="min-w-48">Параметр</TableHead><TableHead className="min-w-80">Условие и источник</TableHead><TableHead className="w-44">Тип требования</TableHead><TableHead className="w-12"><span className="sr-only">Действия</span></TableHead></TableRow></TableHeader>
      <TableBody>{visible.map(row => <TableRow key={row.id}>
        <TableCell><span className="font-mono text-xs text-muted-foreground">{row.id}</span></TableCell>
        <TableCell><input aria-label={"Параметр " + row.id} disabled={busy} value={row.parameter} onChange={e => edit(row.id, { parameter: e.target.value })} className="w-full rounded-md border border-transparent bg-transparent p-1 font-medium outline-none hover:border-input focus:border-primary" />
          <div className="mt-1 text-xs text-muted-foreground">{row.item || (row.scope === "product" ? "Товар" : "Участник")}</div></TableCell>
        <TableCell><textarea aria-label={"Условие " + row.id} value={row.condition} disabled={busy} rows={2} onChange={e => edit(row.id, { condition: e.target.value })} className="w-full resize-y rounded-md border border-transparent p-1 text-sm leading-6 outline-none hover:border-input focus:border-primary" />
          {row.citations.map((citation, i) => {
            const block = blocks.find(b => b.id === citation.block_id);
            return <details key={i} className="mt-2 text-xs"><summary className="flex cursor-pointer items-center gap-1.5 text-muted-foreground hover:text-primary"><FileText className="size-3.5" />{block?.filename} · {block?.location}</summary><blockquote className="mt-2 border-l-2 pl-3 text-muted-foreground">«{citation.quote}»</blockquote></details>;
          })}</TableCell>
        <TableCell><select aria-label={"Тип " + row.id} value={row.importance} disabled={busy} onChange={e => edit(row.id, { importance: e.target.value as Requirement["importance"] })} className="h-9 w-full rounded-md border bg-white px-2 text-xs">{Object.entries(importanceLabels).map(([key, value]) => <option key={key} value={key}>{value}</option>)}</select></TableCell>
        <TableCell><Button size="icon" variant="ghost" disabled={busy} aria-label={"Удалить " + row.id} onClick={() => setRows(current => current.filter(r => r.id !== row.id))}><Trash2 className="text-muted-foreground" /></Button></TableCell>
      </TableRow>)}</TableBody>
    </Table>
    {!rows.length && <p className="p-6 text-sm text-muted-foreground">Чек-лист пуст. Вернитесь к загрузке документации.</p>}
    <div className="flex flex-wrap items-center justify-between gap-4 border-t p-6">
      <p className="text-xs text-muted-foreground">Изменения сохранятся при подтверждении чек-листа.</p>
      <Button disabled={busy || !rows.length || rows.some(r => !r.condition.trim() || !r.parameter.trim())} onClick={() => onApprove(rows)}>{busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}Подтвердить и продолжить</Button>
    </div>
  </section>;
}
