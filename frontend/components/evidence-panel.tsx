"use client";
import { FileText, Quote } from "lucide-react";
import type { Block, Check, Citation, ComparisonRow, Requirement } from "@/lib/types";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "./ui/sheet";
import { StatusLabel } from "./status-label";

export type Selection = { requirement: Requirement; slot: string; row: ComparisonRow };
function Sources({ citations, blocks }: { citations: Citation[]; blocks: Block[] }) {
  if (!citations.length) return <p className="rounded-lg border border-dashed p-4 text-sm leading-6 text-muted-foreground">Подтверждение в заявке не найдено.</p>;
  return <div className="space-y-3">{citations.map((citation, i) => {
    const block = blocks.find(b => b.id === citation.block_id);
    return <div key={i} className="rounded-xl border bg-muted/30 p-4">
      <p className="mb-3 flex items-center gap-2 text-xs font-medium"><FileText className="size-3.5 shrink-0 text-primary" />{block?.filename || "Источник не найден"}</p>
      <blockquote className="whitespace-pre-wrap border-l-2 border-primary/35 pl-3 text-sm leading-6">«{citation.quote}»</blockquote>
      <p className="mt-3 text-xs text-muted-foreground">{block?.location}</p>
    </div>;
  })}</div>;
}
export function EvidencePanel({ check, selected, onClose }: { check: Check; selected: Selection | null; onClose: () => void }) {
  return <Sheet open={!!selected} onOpenChange={open => { if (!open) onClose(); }}>
    <SheetContent>
      <SheetTitle className="pr-8 text-xl font-semibold">Основание результата</SheetTitle>
      <SheetDescription className="mt-2 text-sm text-muted-foreground">Точные цитаты из документации и выбранной заявки.</SheetDescription>
      {selected && <div className="mt-7 space-y-7">
        <div>
          <p className="mb-2 font-mono text-xs text-muted-foreground">{selected.requirement.id} · {check.bids[selected.slot].name}</p>
          <h3 className="mb-4 text-lg font-semibold">{selected.requirement.parameter}</h3>
          <StatusLabel status={selected.row.status} />
        </div>
        <section><h4 className="mb-3 flex items-center gap-2 text-sm font-semibold"><Quote className="size-4 text-primary" />Требование заказчика</h4>
          <p className="mb-3 text-sm leading-6">{selected.requirement.condition}</p>
          <Sources citations={selected.requirement.citations} blocks={check.blocks} />
        </section>
        <section><h4 className="mb-3 text-sm font-semibold">Подтверждение участника</h4>
          <Sources citations={selected.row.citations} blocks={check.bids[selected.slot].blocks} />
        </section>
        <section className="rounded-xl bg-[#edf2ee] p-5"><h4 className="mb-2 text-sm font-semibold">Обоснование</h4><p className="text-sm leading-6 text-muted-foreground">{selected.row.explanation}</p></section>
      </div>}
    </SheetContent>
  </Sheet>;
}
