import { Check, CircleHelp, CircleDashed, Minus, X } from "lucide-react";
import type { Status } from "@/lib/types";
import { cn } from "@/lib/utils";

export const statusLabels: Record<Status, string> = {
  compliant: "Соответствует", partial: "Частично", unconfirmed: "Не подтверждено",
  non_compliant: "Не соответствует", review: "Требует проверки",
};
export const importanceLabels = { mandatory: "Обязательное", scored: "Оценочное", desirable: "Желательное" };
const styles: Record<Status, string> = {
  compliant: "bg-emerald-50 text-emerald-800 border-emerald-100",
  partial: "bg-amber-50 text-amber-800 border-amber-100",
  unconfirmed: "bg-orange-50 text-orange-800 border-orange-100",
  non_compliant: "bg-red-50 text-red-700 border-red-100",
  review: "bg-stone-100 text-stone-600 border-stone-200",
};
const icons = { compliant: Check, partial: Minus, unconfirmed: CircleDashed, non_compliant: X, review: CircleHelp };
export function StatusLabel({ status, className }: { status: Status; className?: string }) {
  const Icon = icons[status];
  return <span className={cn("inline-flex items-center gap-1.5 rounded-md border px-2.5 py-1.5 text-xs font-medium", styles[status], className)}><Icon className="size-3.5 shrink-0" />{statusLabels[status]}</span>;
}
