"use client";

import { useEffect, useState } from "react";
import { ArrowLeft, ArrowRight, CheckCheck, Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import type { WorkspaceApi } from "@/lib/workspace-api";
import type { WorkspaceSession } from "@/lib/workspace-types";

export function WorkspaceLogin({ api, onLogin, initialError = "" }: { api: WorkspaceApi; onLogin: (session: WorkspaceSession) => void; initialError?: string }) {
  const [phone, setPhone] = useState("+998");
  const [code, setCode] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [resendAt, setResendAt] = useState(0);
  const [expiresAt, setExpiresAt] = useState(0);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError);
  useEffect(() => {
    if (!challenge) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [challenge]);
  const seconds = Math.max(0, Math.ceil((resendAt - now) / 1000));
  const expired = !!challenge && now >= expiresAt;

  async function sendCode() {
    setBusy(true); setError("");
    try {
      const result = await api.requestCode(phone);
      const current = Date.now();
      setChallenge(result.challenge_id); setCode(""); setNow(current);
      setResendAt(current + 60_000); setExpiresAt(current + result.expires_in * 1000);
    } catch (error) { setError(error instanceof Error ? error.message : "Не удалось отправить код."); }
    finally { setBusy(false); }
  }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!challenge) { await sendCode(); return; }
    setBusy(true); setError("");
    try { onLogin(await api.verifyCode(challenge, code)); }
    catch (error) { setError(error instanceof Error ? error.message : "Не удалось подтвердить код."); }
    finally { setBusy(false); }
  }

  return <main className="mx-auto grid min-h-[100dvh] max-w-6xl items-center gap-12 px-6 py-12 lg:grid-cols-[1.05fr_0.95fr] lg:gap-24">
    <div>
      <a href="/" className="inline-flex items-center gap-3 text-lg font-semibold tracking-tight"><span className="flex size-10 items-center justify-center rounded-xl bg-primary text-white"><CheckCheck className="size-6" /></span>Тендер-Сверка</a>
      <h1 className="mt-12 max-w-lg text-4xl font-semibold leading-[1.15] tracking-tight sm:text-5xl">Уверенность в каждом пункте заявки</h1>
      <p className="mt-6 max-w-md text-lg leading-relaxed text-[#56645b]">Рабочая область для требований, документов и решений вашей команды.</p>
      <div className="mt-12 inline-flex items-center gap-3 rounded-lg border bg-white/70 px-4 py-3 text-sm text-[#56645b]"><ShieldCheck className="size-5 text-primary" />Доступ к проектам по ролям организации</div>
    </div>
    <section aria-labelledby="login-title" className="surface p-7 sm:p-10">
      <h2 id="login-title" className="text-2xl font-semibold tracking-tight">{challenge ? "Подтвердите номер" : "Вход в рабочую область"}</h2>
      <p className="mt-3 text-sm leading-6 text-[#56645b]">{challenge ? `Код отправлен на ${phone}. Он действует 5 минут.` : "Введите номер телефона. Мы отправим одноразовый код для входа."}</p>
      <form onSubmit={submit} className="mt-8 space-y-5">
        {challenge ? <div><label htmlFor="login-code" className="mb-2 block text-sm font-medium">Код из SMS</label><Input id="login-code" type="text" inputMode="numeric" autoComplete="one-time-code" autoFocus pattern="[0-9]{6}" maxLength={6} value={code} onChange={event => setCode(event.target.value.replace(/[^0-9]/g, ""))} disabled={busy || expired} required className="h-12 text-center text-xl tracking-[0.35em]" placeholder="000000" /></div>
          : <div><label htmlFor="login-phone" className="mb-2 block text-sm font-medium">Номер телефона</label><Input id="login-phone" type="tel" autoComplete="tel" autoFocus maxLength={32} value={phone} onChange={event => setPhone(event.target.value)} required disabled={busy} className="h-12 text-base" placeholder="+998 90 123 45 67" /><p className="mt-2 text-xs text-[#56645b]">Международный формат с кодом страны</p></div>}
        {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm leading-6 text-red-800">{error}</div>}
        {expired && <p role="status" className="text-sm text-amber-800">Срок действия кода истёк. Запросите новый код.</p>}
        <Button className="h-12 w-full" disabled={busy || (challenge !== null && (code.length !== 6 || expired))}>{busy ? <Loader2 className="animate-spin" /> : null}{challenge ? "Войти" : "Получить код"}{!busy && <ArrowRight />}</Button>
      </form>
      {challenge && <div className="mt-5 flex flex-wrap items-center justify-between gap-2"><Button variant="ghost" size="sm" disabled={busy} onClick={() => { setChallenge(null); setError(""); setCode(""); }}><ArrowLeft />Другой номер</Button><Button variant="ghost" size="sm" disabled={busy || seconds > 0} onClick={sendCode}>{seconds > 0 ? `Повторить через ${seconds} с` : "Отправить код снова"}</Button></div>}
      <p className="mt-8 text-xs leading-5 text-[#56645b]">Никому не сообщайте код. Вход открывает только организации, к которым у вас есть доступ.</p>
    </section>
  </main>;
}
