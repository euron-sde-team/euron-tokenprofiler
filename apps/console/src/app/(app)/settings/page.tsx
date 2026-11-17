"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, KeyRound, Plus } from "lucide-react";
import { useState } from "react";
import { useFilter } from "@/components/filter-bar";
import { useApi, useConnection, useToast } from "@/components/providers";
import { Chip } from "@/components/ui/badges";
import { Modal } from "@/components/ui/overlays";
import { Button, ErrorBanner, Field, Input, PageHeader, Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { formatTime, relativeTime } from "@/lib/format";
import { snippets } from "@/lib/snippets";
import type { ApiKeyCreated } from "@/lib/types";

export default function SettingsPage() {
  const api = useApi();
  const { conn } = useConnection();
  const qc = useQueryClient();
  const toast = useToast();
  const { filter } = useFilter();
  const system = useQuery({ queryKey: ["system"], queryFn: () => api.system() });
  const keys = useQuery({ queryKey: ["keys"], queryFn: () => api.keys(), retry: false });
  const prices = useQuery({ queryKey: ["pricing"], queryFn: () => api.pricing() });
  const unpriced = useQuery({ queryKey: ["unpriced", filter], queryFn: () => api.unpriced({ range: "365d" }) });
  const reports = useQuery({ queryKey: ["reports"], queryFn: () => api.reports(), retry: false });
  const [tab, setTab] = useState("sdk");
  const [copied, setCopied] = useState(false);
  const [keyOpen, setKeyOpen] = useState(false);
  const [keyName, setKeyName] = useState("");
  const [scopes, setScopes] = useState<string[]>(["ingest"]);
  const [apps, setApps] = useState("");
  const [created, setCreated] = useState<ApiKeyCreated | null>(null);
  const [priceFor, setPriceFor] = useState<string | null>(null);
  const [pin, setPin] = useState(1);
  const [pout, setPout] = useState(2);
  const [search, setSearch] = useState("");

  const snippet = snippets(conn?.apiUrl ?? "").find((s) => s.id === tab)!;
  const forbidden = keys.error && "status" in keys.error && keys.error.status === 403;
  const toggle = (v: string) => setScopes((s) => (s.includes(v) ? s.filter((x) => x !== v) : [...s, v]));
  const createKey = useMutation({
    mutationFn: () => api.createKey(keyName.trim(), scopes, apps.split(",").map((a) => a.trim()).filter(Boolean)),
    onSuccess: (k) => { setCreated(k); setKeyOpen(false); setKeyName(""); setApps(""); setScopes(["ingest"]); void qc.invalidateQueries({ queryKey: ["keys"] }); }, onError: (e) => toast("error", e.message),
  });
  const revoke = useMutation({ mutationFn: (p: string) => api.revokeKey(p), onSuccess: () => { toast("success", "Key revoked"); void qc.invalidateQueries({ queryKey: ["keys"] }); }, onError: (e) => toast("error", e.message) });
  const setPrice = useMutation({
    mutationFn: async (model: string) => { await api.setPrice(model, { input: pin, output: pout }); return api.reprice(model); },
    onSuccess: (r) => { toast("success", `Price saved. ${r.events_updated.toLocaleString("en-US")} past events re-priced.`); setPriceFor(null); void qc.invalidateQueries(); }, onError: (e) => toast("error", e.message),
  });
  const removePrice = useMutation({ mutationFn: (m: string) => api.deletePrice(m), onSuccess: () => { toast("info", "Override removed"); void qc.invalidateQueries({ queryKey: ["pricing"] }); }, onError: (e) => toast("error", e.message) });
  const archive = useMutation({ mutationFn: () => api.archive({ range: "30d" }, "manual"), onSuccess: (r) => { toast("success", `Archived to s3://${r.bucket}/${r.key}`); void qc.invalidateQueries({ queryKey: ["reports"] }); }, onError: (e) => toast("error", e.message) });

  const shown = (prices.data?.data ?? []).filter((p) => !search || p.id.includes(search.toLowerCase()) || p.provider.includes(search.toLowerCase()));

  return (
    <>
      <PageHeader title="Settings" description="Connect applications, manage API keys, prices and reports." />

      <section className="card mb-5 p-5" aria-label="Connect an app">
        <h2 className="mb-1 font-semibold">Connect an application</h2>
        <p className="mb-4 text-[13px] text-muted">Use the SDK, or send your calls through the proxy: either way usage is recorded without changing what your app gets back.{system.data && !system.data.features.proxy && " (The proxy is off: set TP_UPSTREAM_BASE_URL.)"}</p>
        <div className="mb-3 flex gap-1" role="tablist">{snippets("").map((s) => <button key={s.id} role="tab" aria-selected={tab === s.id} onClick={() => setTab(s.id)} className={cn("rounded-lg border px-3 py-1.5 text-[13px]", tab === s.id ? "border-accent/40 bg-accent/10 text-accent" : "border-line text-muted hover:text-fg")}>{s.label}</button>)}</div>
        <div className="relative">
          <pre className="overflow-x-auto rounded-lg border border-line bg-bg p-4 font-mono text-xs leading-relaxed" data-testid="snippet">{snippet.code}</pre>
          <Button size="sm" className="absolute right-2 top-2" aria-label="Copy snippet" onClick={() => { void navigator.clipboard?.writeText(snippet.code); setCopied(true); setTimeout(() => setCopied(false), 1500); }}>{copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}{copied ? "Copied" : "Copy"}</Button>
        </div>
      </section>

      <section className="card mb-5 overflow-hidden" aria-label="API keys">
        <div className="flex items-center justify-between border-b border-line px-5 py-3.5"><h2 className="font-semibold">API keys</h2><Button variant="primary" size="sm" onClick={() => setKeyOpen(true)} disabled={Boolean(forbidden)}><Plus className="size-3.5" />New key</Button></div>
        {forbidden ? <p className="px-5 py-6 text-[13px] text-muted" data-testid="keys-forbidden">This key cannot manage API keys. Connect with a key that has the <span className="font-mono">admin</span> scope.</p>
          : keys.isError ? <div className="p-4"><ErrorBanner error={keys.error} /></div>
          : keys.isLoading ? <div className="p-5"><Skeleton className="h-16" /></div> : (
            <table className="w-full text-left text-[13px]"><thead className="text-subtle"><tr>{["Name", "Prefix", "Scopes", "Apps", "Last used", ""].map((h) => <th key={h} className="label px-5 py-2.5 font-medium">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">{keys.data?.data.map((k) => (
                <tr key={k.prefix} data-testid="key-row" className={k.revoked ? "opacity-50" : undefined}>
                  <td className="px-5 py-3 font-medium">{k.name}{k.revoked && <span className="ml-2 text-xs text-failed">revoked</span>}</td><td className="px-5 py-3 font-mono text-xs">{k.prefix}</td>
                  <td className="px-5 py-3"><div className="flex flex-wrap gap-1">{k.scopes.map((s) => <Chip key={s}>{s}</Chip>)}</div></td>
                  <td className="px-5 py-3 text-xs text-muted">{k.apps.length ? k.apps.join(", ") : "all"}</td><td className="px-5 py-3 text-xs text-muted">{k.last_used_at ? relativeTime(k.last_used_at * 1000) : "never"}</td>
                  <td className="px-5 py-3 text-right">{!k.revoked && <Button size="sm" variant="danger" onClick={() => revoke.mutate(k.prefix)} aria-label={`Revoke ${k.name}`}>Revoke</Button>}</td>
                </tr>))}</tbody></table>
          )}
      </section>

      <section className="card mb-5 overflow-hidden" aria-label="Prices">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-5 py-3.5">
          <div><h2 className="font-semibold">Prices</h2><p className="text-xs text-subtle">USD per 1M tokens. Built-in list prices as of {prices.data?.as_of ?? "…"}: approximate, so override anything that differs from your contract.</p></div>
          <Input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Filter models" aria-label="Filter prices" className="w-48" />
        </div>
        {unpriced.data && unpriced.data.data.length > 0 && (
          <div className="border-b border-line bg-waiting/10 px-5 py-3 text-[13px]" data-testid="unpriced">
            <b>No price for:</b> {unpriced.data.data.map((r) => <button key={String(r.model)} className="ml-2 rounded-md border border-waiting/40 px-2 py-0.5 text-xs hover:bg-waiting/20" onClick={() => { setPriceFor(String(r.model)); setPin(1); setPout(2); }} aria-label={`Set a price for ${r.model}`}>{String(r.model)} ({Number(r.unpriced).toLocaleString("en-US")} calls)</button>)}
          </div>
        )}
        <div className="max-h-96 overflow-auto">
          <table className="w-full text-left text-[13px]"><thead className="sticky top-0 bg-surface text-subtle"><tr>{["Model", "Provider", "Input", "Cached", "Output", ""].map((h) => <th key={h} className="label px-5 py-2.5 font-medium">{h}</th>)}</tr></thead>
            <tbody className="divide-y divide-line">{shown.map((p) => (
              <tr key={p.id} data-testid="price-row" data-model={p.id}><td className="px-5 py-2 font-medium">{p.id}{p.overridden && <span className="ml-2"><Chip tone="text-accent">override</Chip></span>}</td><td className="px-5 py-2 text-muted">{p.provider}</td>
                <td className="px-5 py-2 tabular-nums">${p.input}</td><td className="px-5 py-2 tabular-nums text-muted">{p.cached_input == null ? "-" : `$${p.cached_input}`}</td><td className="px-5 py-2 tabular-nums">${p.output}</td>
                <td className="px-5 py-2 text-right"><Button size="sm" variant="ghost" onClick={() => { setPriceFor(p.id); setPin(p.input); setPout(p.output); }} aria-label={`Override ${p.id}`}>Override</Button>{p.overridden && <Button size="sm" variant="ghost" onClick={() => removePrice.mutate(p.id)} aria-label={`Remove override for ${p.id}`}>Remove</Button>}</td></tr>))}</tbody></table>
        </div>
      </section>

      <section className="card p-5" aria-label="Reports and server">
        <div className="mb-3 flex items-center justify-between"><h2 className="font-semibold">Reports</h2><Button size="sm" onClick={() => archive.mutate()} loading={archive.isPending} disabled={!system.data?.features.reports} title={system.data?.features.reports ? "Write a 30-day report to S3" : "Set TP_REPORTS_BUCKET to enable"}>Archive a report to S3</Button></div>
        {reports.data && reports.data.data.length > 0 ? <ul className="mb-4 space-y-1 text-[13px]" data-testid="reports">{reports.data.data.slice(0, 5).map((r) => <li key={r.id} className="flex justify-between"><span className="font-mono text-xs">s3://{r.bucket}/{r.key}</span><span className="text-xs text-subtle">{formatTime(r.ts * 1000)}</span></li>)}</ul> : <p className="mb-4 text-[13px] text-subtle">No reports archived yet.</p>}
        {system.data && <dl className="grid grid-cols-2 gap-4 border-t border-line pt-4 text-[13px] md:grid-cols-4">
          <div><dt className="label">Version</dt><dd className="mt-1">{system.data.version}</dd></div><div><dt className="label">Events stored</dt><dd className="mt-1 tabular-nums">{system.data.bounds.events.toLocaleString("en-US")}</dd></div>
          <div><dt className="label">Retention</dt><dd className="mt-1">{system.data.retention_days} days</dd></div><div><dt className="label">Proxy</dt><dd className="mt-1">{system.data.features.proxy ? "on" : "off"}</dd></div></dl>}
      </section>

      <Modal open={keyOpen} onClose={() => setKeyOpen(false)} title="New API key" footer={<><Button onClick={() => setKeyOpen(false)}>Cancel</Button><Button variant="primary" onClick={() => createKey.mutate()} loading={createKey.isPending} disabled={!keyName.trim() || scopes.length === 0}>Create key</Button></>}>
        <Field label="Name"><Input value={keyName} onChange={(e) => setKeyName(e.target.value)} placeholder="support-bot ingest" aria-label="Key name" autoFocus /></Field>
        <fieldset className="space-y-1.5"><legend className="label mb-1">Scopes</legend>{(keys.data?.scopes ?? ["ingest", "read", "budgets:write", "pricing:write", "admin"]).filter((s) => s !== "*").map((s) => <label key={s} className="flex items-center gap-2 text-[13px]"><input type="checkbox" checked={scopes.includes(s)} onChange={() => toggle(s)} className="accent-[rgb(var(--accent))]" />{s}</label>)}</fieldset>
        <Field label="Limit to apps (optional)" hint="Comma-separated. The key can then only write and read these apps."><Input value={apps} onChange={(e) => setApps(e.target.value)} placeholder="support-bot, docs-assistant" aria-label="Limit to apps" /></Field>
      </Modal>
      <Modal open={created !== null} onClose={() => setCreated(null)} title="Copy your new key" footer={<Button variant="primary" onClick={() => setCreated(null)}>Done</Button>}>
        <p className="text-[13px] text-muted">This is the only time the key is shown. Store it somewhere safe.</p>
        <div className="flex items-center gap-2 rounded-lg border border-line bg-bg p-3"><KeyRound className="size-4 shrink-0 text-subtle" /><code className="min-w-0 flex-1 break-all font-mono text-xs" data-testid="new-key">{created?.key}</code><Button size="sm" aria-label="Copy key" onClick={() => { void navigator.clipboard?.writeText(created?.key ?? ""); toast("info", "Copied"); }}><Copy className="size-3.5" /></Button></div>
      </Modal>
      <Modal open={priceFor !== null} onClose={() => setPriceFor(null)} title={`Price for ${priceFor ?? ""}`} footer={<><Button onClick={() => setPriceFor(null)}>Cancel</Button><Button variant="primary" onClick={() => priceFor && setPrice.mutate(priceFor)} loading={setPrice.isPending}>Save and re-price history</Button></>}>
        <div className="grid grid-cols-2 gap-3"><Field label="Input, USD per 1M tokens"><Input type="number" step="0.01" min={0} value={pin} onChange={(e) => setPin(Number(e.target.value))} aria-label="Input price" /></Field><Field label="Output, USD per 1M tokens"><Input type="number" step="0.01" min={0} value={pout} onChange={(e) => setPout(Number(e.target.value))} aria-label="Output price" /></Field></div>
        <p className="text-xs text-subtle">Saving also recomputes the cost of every stored event for this model.</p>
      </Modal>
    </>
  );
}
