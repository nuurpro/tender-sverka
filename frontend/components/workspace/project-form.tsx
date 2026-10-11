"use client";

import { useState } from "react";
import { ArrowRight, Loader2, Scale, ScanLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { NewProject, Project } from "@/lib/workspace-types";

export function ProjectForm({ onSave, onCancel }: { onSave: (input: NewProject) => Promise<void>; onCancel: () => void }) {
  const [mode, setMode] = useState<Project["mode"]>("supplier");
  const [name, setName] = useState("");
  const [customer, setCustomer] = useState("");
  const [deadline, setDeadline] = useState("");
  const [language, setLanguage] = useState<Project["report_language"]>("ru");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("");
    try {
      await onSave({ mode, lot_name: name.trim(), customer: customer.trim() || null,
        deadline: deadline ? new Date(deadline + "+05:00").toISOString() : null,
        timezone: "Asia/Tashkent", report_language: language });
    } catch (error) { setError(error instanceof Error ? error.message : "Не удалось создать проект."); }
    finally { setBusy(false); }
  }
  return <form onSubmit={submit} className="mt-7 space-y-6">
    <fieldset disabled={busy}><legend className="mb-3 text-sm font-medium">Сценарий проверки</legend><div className="grid gap-3 sm:grid-cols-2">
      {([{ value: "supplier", title: "Я поставщик", text: "Проверить одну заявку", icon: ScanLine }, { value: "buyer", title: "Я заказчик", text: "Сравнить до 5 участников", icon: Scale }] as const).map(option => <label key={option.value} className={`relative flex cursor-pointer items-start gap-3 rounded-lg border p-4 transition-colors ${mode === option.value ? "border-primary bg-primary/5" : "bg-white hover:bg-muted"}`}><input type="radio" name="project-mode" value={option.value} checked={mode === option.value} onChange={() => setMode(option.value)} className="mt-1 accent-primary" /><div><div className="flex items-center gap-2 text-sm font-semibold"><option.icon className="size-4 text-primary" />{option.title}</div><div className="mt-1 text-xs text-[#56645b]">{option.text}</div></div></label>)}
    </div></fieldset>
    <div><label htmlFor="project-name" className="mb-2 block text-sm font-medium">Название лота</label><Input id="project-name" maxLength={500} value={name} onChange={event => setName(event.target.value)} placeholder="Например, поставка офисных ноутбуков" disabled={busy} required /></div>
    <div><label htmlFor="project-customer" className="mb-2 block text-sm font-medium">Заказчик <span className="font-normal text-[#56645b]">(необязательно)</span></label><Input id="project-customer" maxLength={500} value={customer} onChange={event => setCustomer(event.target.value)} disabled={busy} /></div>
    <div className="grid gap-5 sm:grid-cols-2"><div><label htmlFor="project-deadline" className="mb-2 block text-sm font-medium">Окончание приёма заявок</label><Input id="project-deadline" type="datetime-local" value={deadline} onChange={event => setDeadline(event.target.value)} disabled={busy} /><p className="mt-2 text-xs text-[#56645b]">Ташкент, UTC+5. Можно указать позже.</p></div><div><label htmlFor="report-language" className="mb-2 block text-sm font-medium">Язык отчёта</label><select id="report-language" className="h-10 w-full rounded-md border bg-white px-3 text-sm" value={language} onChange={event => setLanguage(event.target.value as Project["report_language"])} disabled={busy}><option value="ru">Русский</option><option value="uz">O‘zbekcha</option><option value="en">English</option></select></div></div>
    {error && <div role="alert" className="rounded-lg bg-red-50 px-4 py-3 text-sm text-red-800">{error}</div>}
    <div className="flex flex-wrap justify-end gap-3 border-t pt-5"><Button type="button" variant="outline" onClick={onCancel} disabled={busy}>Отмена</Button><Button disabled={busy || !name.trim()}>{busy ? <Loader2 className="animate-spin" /> : <ArrowRight />}Создать проект</Button></div>
  </form>;
}
