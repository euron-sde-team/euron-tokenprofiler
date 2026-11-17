"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Check, Plus, Trash2, Wallet } from "lucide-react";
import { useState } from "react";
import { useApi, useToast } from "@/components/providers";
import { BudgetBadge, budgetTone } from "@/components/ui/badges";
import { Modal } from "@/components/ui/overlays";
import { Button, EmptyState, ErrorBanner, Field, Input, PageHeader, ProgressBar, Select, Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import type { Budget } from "@/lib/types";
import { formatUsd, relativeTime } from "@/lib/format";

const SCOPES = [{ id: "", label: "Everything" }, { id: "app", label: "One app" }, { id: "environment", label: "One environment" }, { id: "model", label: "One model" }, { id: "prompt_id", label: "One prompt" }];

export default function BudgetsPage() {
  const api = useApi();
  const qc = useQueryClient();
  const toast = useToast();
  const budgets = useQuery({ queryKey: ["budgets"], queryFn: () => api.budgets(), refetchInterval: 20_000 });
  const alerts = useQuery({ queryKey: ["alerts"], queryFn: () => api.alerts(), refetchInterval: 15_000 });
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [limit, setLimit] = useState(500);
  const [scopeKey, setScopeKey] = useState("");
  const [scopeVal, setScopeVal] = useState("");
  const [warn, setWarn] = useState(80);
  const [hook, setHook] = useState("");

  const refresh = () => { void qc.invalidateQueries({ queryKey: ["budgets"] }); void qc.invalidateQueries({ queryKey: ["alerts"] }); void qc.invalidateQueries({ queryKey: ["alerts-badge"] }); };
  const create = useMutation({
    mutationFn: () => api.createBudget({ name: name.trim(), limit_usd: limit, scope: scopeKey && scopeVal ? { [scopeKey]: scopeVal.trim() } : {}, warn_at: warn / 100, webhook_url: hook.trim() || null }),
    onSuccess: async (created) => {
      toast("success", "Budget created");
      setOpen(false); setName(""); setScopeVal(""); setHook("");
      // A list request that started before this create may still be in flight and would show the old list. Drop it, show the
      // server's answer immediately, then revalidate.
      await qc.cancelQueries({ queryKey: ["budgets"] });
      qc.setQueryData<{ data: Budget[] }>(["budgets"], (old) => ({ data: [...(old?.data ?? []).filter((b) => b.id !== created.id), created] }));
      refresh();
    },
    onError: (e) => toast("error", e.message),
  });
  const del = useMutation({
    mutationFn: (id: number) => api.deleteBudget(id),
    onSuccess: async (_, id) => {
      await qc.cancelQueries({ queryKey: ["budgets"] });
      qc.setQueryData<{ data: Budget[] }>(["budgets"], (old) => ({ data: (old?.data ?? []).filter((b) => b.id !== id) }));
      refresh();
    },
    onError: (e) => toast("error", e.message),
  });
  const ack = useMutation({ mutationFn: (id: number) => api.ackAlert(id), onSuccess: refresh, onError: (e) => toast("error", e.message) });
  const evaluate = useMutation({ mutationFn: () => api.evaluateAlerts(), onSuccess: (r) => { toast("info", r.raised ? `${r.raised} new alert(s)` : "Nothing new"); refresh(); }, onError: (e) => toast("error", e.message) });

  return (
    <>
      <PageHeader title="Budgets & alerts" description="Set a monthly limit for everything or for one slice of traffic. You are alerted when it is nearly used, on track to overrun, or exceeded."
        actions={<><Button onClick={() => evaluate.mutate()} loading={evaluate.isPending}><BellRing className="size-3.5" />Check now</Button><Button variant="primary" onClick={() => setOpen(true)}><Plus className="size-3.5" />New budget</Button></>} />
      {budgets.isError && <ErrorBanner error={budgets.error} onRetry={() => void budgets.refetch()} />}

      <section aria-label="Budgets" className="mb-6 grid gap-4 md:grid-cols-2">
        {budgets.isLoading && <Skeleton className="h-36" />}
        {budgets.data?.data.length === 0 && <div className="card md:col-span-2"><EmptyState icon={<Wallet className="size-5" />} title="No budgets yet" hint="Create one to be warned before a bill surprises you." action={<Button variant="primary" onClick={() => setOpen(true)}>New budget</Button>} /></div>}
        {budgets.data?.data.map((b) => (
          <article key={b.id} className="card p-5" data-testid="budget" data-status={b.status.status}>
            <div className="flex items-start justify-between gap-3"><div><h3 className="font-semibold">{b.name}</h3><p className="text-xs text-subtle">{Object.keys(b.scope).length ? Object.entries(b.scope).map(([k, v]) => `${k}: ${v}`).join(", ") : "all traffic"} · monthly</p></div><div className="flex items-center gap-2"><BudgetBadge status={b.status.status} /><button onClick={() => del.mutate(b.id)} aria-label={`Delete ${b.name}`} className="rounded-md p-1 text-muted hover:bg-raised hover:text-failed"><Trash2 className="size-3.5" /></button></div></div>
            <div className="mt-4 flex items-baseline justify-between text-sm"><span className="text-2xl font-semibold tabular-nums">{formatUsd(b.status.spent_micro)}</span><span className="text-xs text-subtle">of {formatUsd(b.status.limit_micro)} ({b.status.used_pct}%)</span></div>
            <ProgressBar value={b.status.used_pct} tone={budgetTone(b.status.status)} className="mt-2" />
            <p className="mt-3 text-xs text-muted">Projected {formatUsd(b.status.projected_micro)} by month end ({b.status.projected_pct}%){b.status.days_until_exhausted !== null && <> · runs out in about <b>{b.status.days_until_exhausted} days</b></>}.</p>
          </article>
        ))}
      </section>

      <section className="card overflow-hidden" aria-label="Alerts">
        <div className="border-b border-line px-5 py-3.5 font-semibold">Alerts</div>
        {alerts.data && alerts.data.data.length === 0 ? <EmptyState icon={<Check className="size-5" />} title="All quiet" hint="Budget threshold crossings and unusual spend appear here." /> : (
          <ul className="divide-y divide-line">
            {alerts.data?.data.map((a) => (
              <li key={a.id} className="flex items-start justify-between gap-4 px-5 py-3" data-testid="alert" data-kind={a.kind}>
                <div className="min-w-0"><div className="flex items-center gap-2 text-[13px]"><span className={cn("size-2 rounded-full", a.severity === "critical" ? "bg-failed" : "bg-waiting")} aria-hidden />{a.title}</div><div className="mt-0.5 text-xs text-subtle">{relativeTime(a.ts * 1000)} · {a.kind}{a.delivered && " · webhook delivered"}</div></div>
                <Button size="sm" variant="ghost" onClick={() => ack.mutate(a.id)} aria-label={`Acknowledge alert ${a.id}`}>Acknowledge</Button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <Modal open={open} onClose={() => setOpen(false)} title="New budget" footer={<><Button onClick={() => setOpen(false)}>Cancel</Button><Button variant="primary" onClick={() => create.mutate()} loading={create.isPending} disabled={!name.trim() || limit <= 0 || (scopeKey !== "" && !scopeVal.trim())}>Create budget</Button></>}>
        <Field label="Name"><Input value={name} onChange={(e) => setName(e.target.value)} placeholder="Support bot" aria-label="Budget name" autoFocus /></Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Monthly limit (USD)"><Input type="number" min={1} value={limit} onChange={(e) => setLimit(Number(e.target.value))} aria-label="Monthly limit" /></Field>
          <Field label="Warn at (%)"><Input type="number" min={1} max={99} value={warn} onChange={(e) => setWarn(Number(e.target.value))} aria-label="Warn at" /></Field>
        </div>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Applies to"><Select value={scopeKey} onChange={(e) => setScopeKey(e.target.value)} aria-label="Applies to">{SCOPES.map((s) => <option key={s.id} value={s.id}>{s.label}</option>)}</Select></Field>
          <Field label="Value"><Input value={scopeVal} onChange={(e) => setScopeVal(e.target.value)} disabled={!scopeKey} placeholder={scopeKey ? "support-bot" : ""} aria-label="Scope value" /></Field>
        </div>
        <Field label="Webhook (optional)" hint="An https:// URL that receives a JSON POST when a threshold is crossed. Internal addresses are refused."><Input value={hook} onChange={(e) => setHook(e.target.value)} placeholder="https://hooks.example.com/tokenprofiler" aria-label="Webhook URL" /></Field>
      </Modal>
    </>
  );
}
