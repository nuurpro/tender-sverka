"use client";
import { useState } from "react";
import { ArrowRight, Check, Download, FileText, Loader2, Users } from "lucide-react";
import type { Bid } from "@/lib/types";
import { FileDropzone } from "./file-dropzone";
import { Input } from "./ui/input";
import { Button } from "./ui/button";

function BidSlot({ slot, saved, busy, onSave }: { slot: number; saved?: Bid; busy: boolean; onSave: (slot: number, name: string, files: File[]) => Promise<boolean> }) {
  const [name, setName] = useState(saved?.name || "");
  const [files, setFiles] = useState<File[]>([]);
  const [editing, setEditing] = useState(!saved);
  const samples = ["bid-a.docx", "bid-b.pdf", "bid-c.xlsx"];
  return <section className="surface flex flex-col p-5">
    <div className="mb-5 flex items-center justify-between">
      <div className="flex items-center gap-2.5"><span className="flex size-7 items-center justify-center rounded-md bg-muted text-xs font-semibold">{String(slot).padStart(2, "0")}</span><h3 className="text-sm font-semibold">Участник {slot}</h3></div>
      {saved && <span className="flex items-center gap-1 text-xs text-primary"><Check className="size-3.5" />Загружено</span>}
    </div>
    {saved && !editing ? <>
      <p className="mb-4 text-lg font-medium">{saved.name}</p>
      <div className="mb-5 space-y-2">{saved.files.map(file => <div key={file.id} className="flex items-center gap-2 rounded-lg bg-muted/40 p-3 text-xs"><FileText className="size-4 shrink-0 text-primary" /><span className="truncate">{file.name}</span></div>)}</div>
      <Button variant="outline" className="mt-auto w-full" disabled={busy} onClick={() => { setName(saved.name); setEditing(true); }}>Заменить заявку</Button>
    </> : <>
      <label className="mb-2 text-xs font-medium" htmlFor={"bid-name-" + slot}>Название компании</label>
      <Input id={"bid-name-" + slot} aria-label={"Название участника " + slot} value={name} maxLength={120} onChange={e => setName(e.target.value)} disabled={busy} placeholder={["ООО «Альфа»", "ООО «Бета»", "ООО «Гамма»"][slot - 1]} />
      <div className="my-4"><FileDropzone files={files} onChange={setFiles} disabled={busy} label={"Заявка участника " + slot} /></div>
      <Button variant="outline" className="mt-auto w-full" disabled={busy || !name.trim() || !files.length} onClick={async () => { if (await onSave(slot, name, files)) { setFiles([]); setEditing(false); } }}>Сохранить заявку</Button>
    </>}
    <a className="mt-4 inline-flex items-center justify-center gap-1.5 text-xs text-muted-foreground hover:text-primary" href={"/demo/" + samples[slot - 1]} download><Download className="size-3" />Учебная заявка {slot}</a>
  </section>;
}
export function BidsStep({ bids, busy, onSave, onCompare }: { bids: Record<string, Bid>; busy: boolean; onSave: (slot: number, name: string, files: File[]) => Promise<boolean>; onCompare: () => Promise<void> }) {
  const count = Object.keys(bids).length;
  return <div>
    <div className="mb-6"><span className="eyebrow">ШАГ 03</span><h2 className="mt-2 text-xl font-semibold">Заявки поставщиков</h2><p className="mt-2 text-sm text-muted-foreground">Загрузите одну заявку для самопроверки или до трёх для сравнения участников.</p></div>
    <div className="grid gap-5 lg:grid-cols-3">{[1, 2, 3].map(slot => <BidSlot key={slot} slot={slot} saved={bids[String(slot)]} busy={busy} onSave={onSave} />)}</div>
    <div className="mt-6 flex flex-wrap items-center justify-between gap-4 rounded-xl border bg-white p-5">
      <span className="flex items-center gap-2 text-sm text-muted-foreground"><Users className="size-4" />{count} из 3 заявок загружено</span>
      <Button disabled={!count || busy} onClick={onCompare}>{busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}Сравнить заявки</Button>
    </div>
  </div>;
}
