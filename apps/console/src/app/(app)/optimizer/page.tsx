"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, EyeOff, Sparkles, Undo2 } from "lucide-react";
import { useState } from "react";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi, useToast } from "@/components/providers";
import { Confidence, Effort } from "@/components/ui/badges";
import { Button, EmptyState, ErrorBanner, PageHeader, Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { formatUsd, prettyJson } from "@/lib/format";
import type { Recommendation } from "@/lib/types";

const KIND: Record<string, string> = {
  model_downgrade: "Cheaper model", prompt_caching: "Prompt caching", response_cache: "Response cache", quality: "Quality", error_waste: "Failed calls",
  batch_api: "Batch API", context_growth: "Context growth", pricing: "Pricing",
};

function Card({ r, onDismiss, onRestore, busy }: { r: Recommendation; onDismiss: () => void; onRestore: () => void; busy: boolean }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={cn("card p-5", r.dismissed && "opacity-60")} data-testid="recommendation" data-kind={r.kind} data-app={r.app ?? ""}>
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs"><span className="rounded-md bg-accent/15 px-2 py-0.5 font-medium text-accent">{KIND[r.kind] ?? r.kind}</span><Confidence level={r.confidence} /><Effort level={r.effort} />{r.dismissed && <span className="text-subtle">dismissed</span>}</div>
          <h3 className="text-[15px] font-semibold">{r.title}</h3>
          <p className="mt-1.5 max-w-3xl text-[13px] text-muted">{r.summary}</p>
        </div>
        <div className="text-right">
          {r.monthly_savings_micro > 0 ? <><div className="label">Could save</div><div className="mt-0.5 text-2xl font-semibold tabular-nums text-completed" data-testid="rec-savings">{formatUsd(r.monthly_savings_micro)}<span className="text-xs font-normal text-subtle"> /mo</span></div></> : <div className="text-xs text-subtle">no direct saving</div>}
        </div>
      </div>
      <div className="mt-3 flex items-center gap-2">
        <Button size="sm" variant="ghost" onClick={() => setOpen(!open)} aria-expanded={open}><ChevronDown className={cn("size-3.5 transition", open && "rotate-180")} />{open ? "Hide" : "Show"} evidence and steps</Button>
        {r.dismissed ? <Button size="sm" variant="ghost" onClick={onRestore} loading={busy}><Undo2 className="size-3.5" />Restore</Button> : <Button size="sm" variant="ghost" onClick={onDismiss} loading={busy} aria-label={`Dismiss ${r.title}`}><EyeOff className="size-3.5" />Dismiss</Button>}
      </div>
      {open && (
        <div className="mt-4 grid gap-5 border-t border-line pt-4 md:grid-cols-2" data-testid="rec-details">
          <div><h4 className="label mb-2">What the data shows</h4><pre className="overflow-x-auto rounded-lg border border-line bg-bg p-3 font-mono text-[11px] text-muted">{prettyJson(r.evidence)}</pre></div>
          <div><h4 className="label mb-2">What to do</h4><ol className="list-decimal space-y-1.5 pl-4 text-[13px] text-muted">{r.steps.map((s) => <li key={s}>{s}</li>)}</ol></div>
        </div>
      )}
    </article>
  );
}

export default function OptimizerPage() {
  const api = useApi();
  const qc = useQueryClient();
  const toast = useToast();
  const { filter } = useFilter();
  const [showDismissed, setShowDismissed] = useState(false);
  const recs = useQuery({ queryKey: ["recs", filter, showDismissed], queryFn: () => api.recommendations(filter, showDismissed) });
  const refresh = () => { void qc.invalidateQueries({ queryKey: ["recs"] }); void qc.invalidateQueries({ queryKey: ["recs-badge"] }); };
  const dismiss = useMutation({ mutationFn: (id: string) => api.dismiss(id), onSuccess: () => { toast("info", "Dismissed. You can restore it from 'Show dismissed'."); refresh(); }, onError: (e) => toast("error", e.message) });
  const restore = useMutation({ mutationFn: (id: string) => api.restore(id), onSuccess: refresh, onError: (e) => toast("error", e.message) });
  const d = recs.data;

  return (
    <>
      <PageHeader title="Optimizer" description="Specific ways to spend less, worked out from what your applications actually did. Each one shows its evidence and how sure it is." actions={<FilterBar show={["app", "environment"]} />} />
      {recs.isError && <ErrorBanner error={recs.error} onRetry={() => void recs.refetch()} />}
      {d && (
        <section className="card mb-5 flex flex-wrap items-center gap-x-10 gap-y-3 p-5" aria-label="Summary">
          <div><div className="label">Spend, projected monthly</div><div className="mt-1 text-2xl font-semibold tabular-nums">{formatUsd(d.monthly_cost_micro)}</div></div>
          <div><div className="label">Potential savings</div><div className="mt-1 text-2xl font-semibold tabular-nums text-completed" data-testid="potential">{formatUsd(d.potential_monthly_savings_micro)}<span className="text-xs font-normal text-subtle"> /mo</span></div></div>
          <p className="max-w-md text-xs text-subtle">{d.overlap_note} Based on {d.observed_days} days of data, projected to 30.</p>
          <label className="ml-auto flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={showDismissed} onChange={(e) => setShowDismissed(e.target.checked)} className="accent-[rgb(var(--accent))]" />Show dismissed ({d.dismissed_count})</label>
        </section>
      )}
      {recs.isLoading && <div className="space-y-4"><Skeleton className="h-32" /><Skeleton className="h-32" /></div>}
      {d && d.recommendations.length === 0 && <div className="card"><EmptyState icon={<Sparkles className="size-5" />} title="Nothing to optimize yet" hint="The optimizer needs a few days of traffic (at least 30 calls per prompt). Come back after more usage, or load sample data with `make demo`." /></div>}
      <div className="space-y-4">
        {d?.recommendations.map((r) => <Card key={r.id} r={r} busy={(dismiss.isPending && dismiss.variables === r.id) || (restore.isPending && restore.variables === r.id)} onDismiss={() => dismiss.mutate(r.id)} onRestore={() => restore.mutate(r.id)} />)}
      </div>
    </>
  );
}
