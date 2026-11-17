"use client";

import { X } from "lucide-react";
import { useEffect, useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";

function useEscape(open: boolean, onClose: () => void) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
}

export function Drawer({ open, onClose, title, children, width = "max-w-xl" }: {
  open: boolean; onClose: () => void; title: ReactNode; children: ReactNode; width?: string;
}) {
  useEscape(open, onClose);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (open) ref.current?.focus(); }, [open]);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div className="absolute inset-0 animate-fadeIn bg-black/50 backdrop-blur-[1px]" onClick={onClose} aria-hidden />
      <aside
        ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="Details"
        className={cn("relative flex h-full w-full animate-slideIn flex-col border-l border-line bg-surface shadow-pop", width)}
      >
        <header className="flex items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div className="min-w-0 flex-1">{title}</div>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-raised hover:text-fg">
            <X className="size-4" />
          </button>
        </header>
        <div className="flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
  );
}

export function Modal({ open, onClose, title, children, footer }: {
  open: boolean; onClose: () => void; title: string; children: ReactNode; footer?: ReactNode;
}) {
  useEscape(open, onClose);
  if (!open) return null;
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <div className="absolute inset-0 animate-fadeIn bg-black/55" onClick={onClose} aria-hidden />
      <div role="dialog" aria-modal="true" aria-label={title} className="card relative w-full max-w-lg animate-fadeIn shadow-pop">
        <header className="flex items-center justify-between border-b border-line px-5 py-3.5">
          <h2 className="font-semibold">{title}</h2>
          <button onClick={onClose} aria-label="Close" className="rounded-md p-1.5 text-muted hover:bg-raised hover:text-fg">
            <X className="size-4" />
          </button>
        </header>
        <div className="space-y-4 px-5 py-4">{children}</div>
        {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3.5">{footer}</footer>}
      </div>
    </div>
  );
}
