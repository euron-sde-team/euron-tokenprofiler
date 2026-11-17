"use client";

import { Loader2 } from "lucide-react";
import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes, TextareaHTMLAttributes } from "react";
import { cn } from "@/lib/cn";

type Variant = "primary" | "secondary" | "ghost" | "danger";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:brightness-110 border-transparent",
  secondary: "bg-raised text-fg border-line hover:border-subtle/60",
  ghost: "bg-transparent text-muted border-transparent hover:bg-raised hover:text-fg",
  danger: "bg-failed/10 text-failed border-failed/30 hover:bg-failed/20",
};

export function Button({
  variant = "secondary", size = "md", loading, className, children, disabled, ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean }) {
  return (
    <button
      {...rest}
      disabled={disabled || loading}
      className={cn(
        "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-lg border font-medium transition disabled:cursor-not-allowed disabled:opacity-50",
        size === "sm" ? "h-7 px-2.5 text-xs" : "h-9 px-3.5 text-[13px]",
        variants[variant], className,
      )}
    >
      {loading && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

const field = "w-full rounded-lg border border-line bg-bg px-3 text-[13px] text-fg placeholder:text-subtle transition hover:border-subtle/60 focus:border-accent/70";

export function Input({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input {...rest} className={cn(field, "h-9", className)} />;
}
export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select {...rest} className={cn(field, "h-9 pr-8", className)}>{children}</select>;
}
export function Textarea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea {...rest} className={cn(field, "py-2 font-mono text-xs leading-relaxed", className)} />;
}

export function Field({ label, hint, error, children }: { label: string; hint?: string; error?: string | null; children: ReactNode }) {
  return (
    <label className="block space-y-1.5">
      <span className="label block">{label}</span>
      {children}
      {error ? <span className="block text-xs text-failed">{error}</span> : hint ? <span className="block text-xs text-subtle">{hint}</span> : null}
    </label>
  );
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn("size-4 animate-spin text-subtle", className)} aria-label="Loading" />;
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("animate-pulse rounded-md bg-raised", className)} />;
}

export function EmptyState({ icon, title, hint, action }: { icon: ReactNode; title: string; hint?: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <div className="mb-1 flex size-11 items-center justify-center rounded-xl border border-line bg-raised text-subtle">{icon}</div>
      <p className="font-medium">{title}</p>
      {hint && <p className="max-w-sm text-[13px] text-muted">{hint}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorBanner({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong";
  return (
    <div role="alert" className="flex items-center justify-between gap-3 rounded-lg border border-failed/30 bg-failed/10 px-4 py-3 text-[13px] text-failed">
      <span>{message}</span>
      {onRetry && <Button size="sm" variant="secondary" onClick={onRetry}>Retry</Button>}
    </div>
  );
}

export function PageHeader({ title, description, actions }: { title: string; description?: string; actions?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-1 text-[13px] text-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  );
}

export function ProgressBar({ value, tone = "bg-accent", className }: { value: number; tone?: string; className?: string }) {
  const v = Math.max(0, Math.min(100, value));
  return (
    <div className={cn("h-1.5 overflow-hidden rounded-full bg-raised", className)} role="progressbar" aria-valuenow={v} aria-valuemin={0} aria-valuemax={100}>
      <div className={cn("h-full rounded-full transition-all", tone)} style={{ width: `${v}%` }} />
    </div>
  );
}
