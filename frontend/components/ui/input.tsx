import * as React from "react";
import { cn } from "@/lib/utils";
export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return <input className={cn("flex h-10 w-full rounded-md border border-input bg-white px-3 py-2 text-sm outline-none transition focus:border-primary focus:ring-2 focus:ring-primary/10 disabled:opacity-50", className)} {...props} />;
}
