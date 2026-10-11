"use client";
import { useState } from "react";
import { ArrowRight, Download, FileCheck2, Loader2 } from "lucide-react";
import { FileDropzone } from "./file-dropzone";
import { Button } from "./ui/button";

export function UploadStep({ onUpload, busy, demo }: { onUpload: (files: File[]) => Promise<void>; busy: boolean; demo: boolean }) {
  const [files, setFiles] = useState<File[]>([]);
  return <div className="grid gap-7 lg:grid-cols-[1fr_320px]">
    <section className="surface p-6 sm:p-8">
      <span className="eyebrow">ШАГ 01</span>
      <h2 className="mt-2 text-xl font-semibold">Документация заказчика</h2>
      <p className="mt-2 mb-7 max-w-lg text-sm leading-6 text-muted-foreground">Загрузите техническое задание и приложения. Мы соберём требования к товару и участнику в один чек-лист.</p>
      <FileDropzone files={files} onChange={setFiles} disabled={busy} label="Документация заказчика" />
      <div className="mt-7 flex flex-wrap items-center justify-between gap-4 border-t pt-6">
        <span className="text-xs text-muted-foreground">{files.length ? files.length + " из 5 файлов выбрано" : "Сканы и изображения не поддерживаются"}</span>
        <Button disabled={!files.length || busy} onClick={() => onUpload(files)}>{busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}Извлечь требования</Button>
      </div>
    </section>
    <aside className="space-y-4">
      <section className="rounded-xl border bg-[#edf2ee] p-6">
        <FileCheck2 className="mb-4 size-6 text-primary" />
        <h3 className="font-semibold">Каждый вывод — с источником</h3>
        <p className="mt-3 text-sm leading-6 text-muted-foreground">У каждого требования будет точная цитата и ссылка на страницу, абзац или строку таблицы. Проверьте чек-лист перед сравнением.</p>
      </section>
      <section className="surface p-6">
        <span className="eyebrow">УЧЕБНЫЙ КОМПЛЕКТ</span>
        <h3 className="mt-2 font-medium">Попробуйте на примере</h3>
        <p className="mt-2 text-xs leading-5 text-muted-foreground">Один тендер на ноутбуки и три заявки с разной степенью соответствия.</p>
        <Button asChild variant="outline" size="sm" className="mt-4 w-full"><a href="/demo/tender.docx" download><Download />Скачать учебное ТЗ</a></Button>
        {demo && <p className="mt-3 text-xs leading-5 text-muted-foreground">В учебном режиме используйте этот комплект. Для своих документов подключите OpenAI.</p>}
      </section>
    </aside>
  </div>;
}
