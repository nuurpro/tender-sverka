"use client";
import { useId, useRef, useState } from "react";
import { FileText, Plus, Upload, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export function FileDropzone({ files, onChange, disabled, label = "Документы" }: {
  files: File[]; onChange: (files: File[]) => void; disabled?: boolean; label?: string;
}) {
  const input = useRef<HTMLInputElement>(null);
  const id = useId();
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState("");
  function add(incoming: File[]) {
    if (disabled) return;
    const next = [...files];
    for (const file of incoming) {
      if (!/\.(pdf|docx|xlsx)$/i.test(file.name)) { setError("Выберите текстовый PDF, DOCX или XLSX."); return; }
      if (file.size > 10 * 1024 * 1024) { setError(file.name + ": файл больше 10 МБ."); return; }
      if (!next.some(f => f.name === file.name && f.size === file.size)) next.push(file);
    }
    if (next.length > 5) { setError("Можно загрузить до пяти файлов в один пакет."); return; }
    setError(""); onChange(next);
  }
  return <div>
    <div className={cn("rounded-xl border border-dashed bg-muted/30 p-6 text-center transition-colors", dragging ? "border-primary bg-primary/5" : "border-stone-300", disabled && "opacity-60")}
      onDragOver={e => { e.preventDefault(); if (!disabled) setDragging(true); }}
      onDragLeave={() => setDragging(false)}
      onDrop={e => { e.preventDefault(); setDragging(false); add(Array.from(e.dataTransfer.files)); }}>
      <input ref={input} id={id} type="file" multiple accept=".pdf,.docx,.xlsx" className="sr-only" aria-label={label} disabled={disabled}
        onChange={e => { add(Array.from(e.target.files || [])); e.target.value = ""; }} />
      <div className="mx-auto mb-3 flex size-11 items-center justify-center rounded-xl border bg-white text-primary"><Upload className="size-5" /></div>
      <p className="text-sm font-medium">Перетащите файлы сюда</p>
      <p className="mt-1 text-xs text-muted-foreground">PDF, DOCX, XLSX · до 10 МБ на файл</p>
      <Button type="button" variant="outline" size="sm" className="mt-4" disabled={disabled} onClick={() => input.current?.click()}><Plus />Выбрать файлы</Button>
    </div>
    {error && <p role="alert" className="mt-2 text-xs text-red-700">{error}</p>}
    {files.length > 0 && <ul className="mt-3 space-y-2">
      {files.map((file, i) => <li key={file.name + i} className="flex items-center gap-3 rounded-lg border bg-white px-3 py-2.5">
        <FileText className="size-4 shrink-0 text-primary" /><span className="min-w-0 flex-1 truncate text-xs">{file.name}</span>
        <span className="text-[11px] text-muted-foreground">{(file.size / 1024).toFixed(0)} КБ</span>
        <button disabled={disabled} type="button" onClick={() => onChange(files.filter((_, index) => index !== i))} aria-label={"Удалить " + file.name} className="rounded p-1 hover:bg-muted"><X className="size-3.5" /></button>
      </li>)}
    </ul>}
  </div>;
}
