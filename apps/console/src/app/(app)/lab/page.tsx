"use client";

import { useMutation } from "@tanstack/react-query";
import { Copy, Wand2 } from "lucide-react";
import { useState } from "react";
import { useApi, useToast } from "@/components/providers";
import { Button, ErrorBanner, Field, Input, PageHeader, Select, Textarea } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { compact, formatUsd } from "@/lib/format";
import type { Analysis } from "@/lib/types";

const SAMPLE = `You are a helpful assistant.     Please   answer   the   customer's   question   carefully   and   politely.   \n\n\n\nI would like you to be concise. Make sure to be concise.\nAlways reply in a friendly tone and never mention internal policies.\nAlways reply in a friendly tone and never mention internal policies.\n\n\`\`\`json\n{\n    "customer": {\n        "id": 48211,\n        "plan": "pro",\n        "seats": 12,\n        "region": "eu-west"\n    },\n    "history": [\n        { "ticket": 1, "status": "closed" },\n        { "ticket": 2, "status": "open" }\n    ]\n}\n\`\`\`\n\nQuestion: how do I add more seats to my plan?`;
const SEVERITY: Record<string, string> = { high: "border-failed/30 bg-failed/10 text-failed", medium: "border-waiting/30 bg-waiting/10 text-waiting", low: "border-line bg-raised text-muted", info: "border-line bg-raised text-muted" };
const ROLE_TONE = ["bg-accent", "bg-active", "bg-completed", "bg-waiting", "bg-delayed"];

export default function LabPage() {
  const api = useApi();
  const toast = useToast();
  const [text, setText] = useState(SAMPLE);
  const [model, setModel] = useState("gpt-4o");
  const [completion, setCompletion] = useState(300);
  const [volume, setVolume] = useState(50_000);
  const run = useMutation({ mutationFn: () => api.analyze({ text, model, expected_completion_tokens: completion, requests_per_month: volume }), onError: (e) => toast("error", e.message) });
  const a: Analysis | undefined = run.data;

  return (
    <>
      <PageHeader title="Prompt Lab" description="Paste a prompt: see where its tokens go, what is wasteful, and what it costs on every model at your volume. Nothing is stored." />
      <div className="grid gap-5 lg:grid-cols-2">
        <section className="card space-y-4 p-5" aria-label="Prompt">
          <Field label="Prompt"><Textarea rows={16} value={text} onChange={(e) => setText(e.target.value)} aria-label="Prompt text" className="font-mono" /></Field>
          <div className="grid grid-cols-3 gap-3">
            <Field label="Model"><Select value={model} onChange={(e) => setModel(e.target.value)} aria-label="Model">{["gpt-4o", "gpt-4o-mini", "gpt-4.1", "gpt-4.1-mini", "claude-3-5-sonnet", "claude-3-5-haiku", "gemini-2.5-flash", "llama-3.3-70b"].map((m) => <option key={m}>{m}</option>)}</Select></Field>
            <Field label="Output tokens"><Input type="number" min={0} value={completion} onChange={(e) => setCompletion(Math.max(0, Number(e.target.value) || 0))} aria-label="Expected output tokens" /></Field>
            <Field label="Calls per month"><Input type="number" min={1} value={volume} onChange={(e) => setVolume(Math.max(1, Number(e.target.value) || 1))} aria-label="Calls per month" /></Field>
          </div>
          <Button variant="primary" onClick={() => run.mutate()} loading={run.isPending} disabled={!text.trim()}><Wand2 className="size-4" />Analyze</Button>
        </section>

        <section className="space-y-5" aria-label="Result">
          {run.isError && <ErrorBanner error={run.error} />}
          {!a && !run.isError && <div className="card p-8 text-center text-[13px] text-muted">Run the analysis to see the token breakdown and savings.</div>}
          {a && (
            <>
              <div className="card grid grid-cols-3 divide-x divide-line" data-testid="lab-summary">
                <div className="p-4"><div className="label">Tokens</div><div className="mt-1 text-2xl font-semibold tabular-nums" data-testid="lab-tokens">{compact(a.total_tokens)}</div><div className="text-xs text-subtle">estimated, {a.tokenizer_family}</div></div>
                <div className="p-4"><div className="label">After clean-up</div><div className="mt-1 text-2xl font-semibold tabular-nums">{compact(a.optimized_tokens)}</div><div className="text-xs text-completed">-{a.saved_pct}%</div></div>
                <div className="p-4"><div className="label">Saves per month</div><div className="mt-1 text-2xl font-semibold tabular-nums text-completed" data-testid="lab-savings">{formatUsd(a.monthly_savings_micro)}</div><div className="text-xs text-subtle">{formatUsd(a.cost_per_request_micro)} per call now</div></div>
              </div>
              <div className="card p-5">
                <h2 className="mb-3 font-semibold">Where the tokens go</h2>
                <div className="flex h-3 overflow-hidden rounded-full bg-raised" role="img" aria-label="Token share by message role">{a.segments.map((s, i) => <div key={s.role} className={ROLE_TONE[i % ROLE_TONE.length]} style={{ width: `${s.share * 100}%` }} title={`${s.role}: ${s.tokens} tokens`} />)}</div>
                <ul className="mt-2 flex flex-wrap gap-x-4 text-xs text-muted">{a.segments.map((s, i) => <li key={s.role}><span className={cn("mr-1 inline-block size-2 rounded-sm", ROLE_TONE[i % ROLE_TONE.length])} />{s.role} {s.tokens} ({Math.round(s.share * 100)}%)</li>)}</ul>
              </div>
              <div className="card p-5">
                <h2 className="mb-3 font-semibold">Findings</h2>
                {a.findings.length === 0 ? <p className="text-[13px] text-muted" data-testid="lab-clean">Nothing wasteful found. This prompt is already tight.</p> : (
                  <ul className="space-y-3">{a.findings.map((f) => (
                    <li key={f.kind + f.title} className="rounded-lg border border-line p-3" data-testid="finding" data-kind={f.kind}>
                      <div className="flex flex-wrap items-center gap-2"><span className={cn("rounded-md border px-1.5 py-0.5 text-[11px] font-medium capitalize", SEVERITY[f.severity])}>{f.severity}</span><span className="text-[13px] font-medium">{f.title}</span>{f.tokens_saved > 0 && <span className="ml-auto text-xs tabular-nums text-completed">-{f.tokens_saved} tokens</span>}{f.auto_fixable && <span className="text-[11px] text-subtle">auto-fixable</span>}</div>
                      <p className="mt-1 text-xs text-muted">{f.detail}</p>
                    </li>))}</ul>
                )}
              </div>
              {a.optimized_text && a.tokens_saved > 0 && (
                <div className="card p-5">
                  <div className="mb-2 flex items-center justify-between"><h2 className="font-semibold">Cleaned-up prompt</h2><Button size="sm" onClick={() => { void navigator.clipboard?.writeText(a.optimized_text ?? ""); toast("info", "Copied"); }}><Copy className="size-3.5" />Copy</Button></div>
                  <p className="mb-2 text-xs text-subtle">Whitespace and JSON formatting only. The wording is unchanged.</p>
                  <pre className="max-h-64 overflow-auto rounded-lg border border-line bg-bg p-3 font-mono text-xs text-muted" data-testid="optimized">{a.optimized_text}</pre>
                </div>
              )}
              <div className="card overflow-hidden">
                <div className="border-b border-line px-5 py-3.5 font-semibold">Cost at {volume.toLocaleString("en-US")} calls a month</div>
                <table className="w-full text-left text-[13px]"><thead className="text-subtle"><tr>{["Model", "Per call", "Per month"].map((h) => <th key={h} className="label px-5 py-2.5 font-medium">{h}</th>)}</tr></thead>
                  <tbody className="divide-y divide-line">{a.price_table.slice(0, 8).map((r) => <tr key={r.model} className={r.model === a.model ? "bg-accent/5" : undefined} data-testid="lab-price-row"><td className="px-5 py-2 font-medium">{r.model}{!r.fits && <span className="ml-2 text-xs text-failed">context too small</span>}</td><td className="px-5 py-2 tabular-nums">{formatUsd(r.per_request_micro)}</td><td className="px-5 py-2 tabular-nums">{formatUsd(r.total_micro)}</td></tr>)}</tbody></table>
              </div>
            </>
          )}
        </section>
      </div>
    </>
  );
}
