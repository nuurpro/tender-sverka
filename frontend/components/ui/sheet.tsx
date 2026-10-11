"use client";
import * as React from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";

export const Sheet = Dialog.Root;
export const SheetTrigger = Dialog.Trigger;
export const SheetClose = Dialog.Close;
export const SheetTitle = Dialog.Title;
export const SheetDescription = Dialog.Description;
export function SheetContent({ className, children, ...props }: React.ComponentProps<typeof Dialog.Content>) {
  return <Dialog.Portal>
    <Dialog.Overlay className="fixed inset-0 z-50 bg-black/25 backdrop-blur-[2px] data-[state=open]:animate-in" />
    <Dialog.Content className={cn("fixed inset-y-0 right-0 z-50 w-full max-w-xl overflow-y-auto border-l bg-white p-7 shadow-xl outline-none", className)} {...props}>
      {children}
      <Dialog.Close className="absolute right-5 top-5 rounded-md p-2 text-muted-foreground hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring" aria-label="Закрыть источники"><X className="size-5" /></Dialog.Close>
    </Dialog.Content>
  </Dialog.Portal>;
}
