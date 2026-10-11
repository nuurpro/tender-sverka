import type { Check, Health, Requirement } from "./types";

const BASE = process.env.NEXT_PUBLIC_API_URL || "http://127.0.0.1:8000";
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try { response = await fetch(BASE + path, { ...init, cache: "no-store" }); }
  catch { throw new Error("Нет связи с сервером. Запустите FastAPI на порту 8000."); }
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : "Не удалось выполнить запрос (" + response.status + ").");
  }
  return response.json() as Promise<T>;
}
function form(files: File[], name?: string) {
  const body = new FormData();
  files.forEach(file => body.append("files", file));
  if (name) body.append("name", name);
  return body;
}
export const api = {
  health: () => request<Health>("/health"),
  get: (id: string) => request<Check>("/checks/" + id),
  create: (files: File[]) => request<{ id: string }>("/checks", { method: "POST", body: form(files) }),
  approve: (id: string, rows: Requirement[]) => request("/checks/" + id + "/requirements", {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(rows),
  }),
  bid: (id: string, slot: number, name: string, files: File[]) =>
    request("/checks/" + id + "/bids/" + slot, { method: "PUT", body: form(files, name) }),
  compare: (id: string) => request("/checks/" + id + "/compare", { method: "POST" }),
  retry: (id: string) => request("/checks/" + id + "/retry", { method: "POST" }),
  export: async (id: string) => {
    const response = await fetch(BASE + "/checks/" + id + "/matrix.xlsx");
    if (!response.ok) throw new Error("Экспорт доступен после завершения сравнения всех заявок.");
    const url = URL.createObjectURL(await response.blob());
    const anchor = document.createElement("a");
    anchor.href = url; anchor.download = "Тендер-Сверка-" + id.slice(0, 8) + ".xlsx"; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  },
};
