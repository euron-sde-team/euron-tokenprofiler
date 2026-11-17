"use client";

import { useQuery } from "@tanstack/react-query";
import { Table2 } from "lucide-react";
import { useState } from "react";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi } from "@/components/providers";
import { Chip } from "@/components/ui/badges";
import { EmptyState, ErrorBanner, PageHeader, Skeleton } from "@/components/ui/primitives";
import { cn } from "@/lib/cn";
import { compact, formatDelta, formatUsd } from "@/lib/format";

export default function ModelsPage() {
  const api = useApi();
  const { filter } = useFilter();
  const cmp = useQuery({ queryKey: ["compare", filter], queryFn: () => api.compare(filter) });
  const [onlyFits, setOnlyFits] = useState(true);
  const c = cmp.data;
  const maxCost = Math.max(1, ...(c?.candidates ?? []).map((x) => x.cost_micro), c?.actual_cost_micro ?? 0);
  const rows = (c?.candidates ?? []).filter((x) => !onlyFits || x.fits_largest_prompt);

  return (
    <>
      <PageHeader title="Models" description="What this exact traffic would have cost on every other model. Cost only: a cheaper model may answer differently, so test before switching."
        actions={<FilterBar show={["range", "app", "environment"]} />} />
      {cmp.isError && <ErrorBanner error={cmp.error} onRetry={() => void cmp.refetch()} />}
      {c && c.requests === 0 ? <div className="card"><EmptyState icon={<Table2 className="size-5" />} title="No chat traffic in this range" hint="Widen the time range, or clear a filter." /></div> : (
        <>
          <section className="card mb-5 p-5" aria-label="Current models">
            <h2 className="mb-3 font-semibold">What you use now</h2>
            {c ? (
              <div className="flex flex-wrap gap-x-8 gap-y-3">
                <div><div className="label">Actual spend</div><div className="mt-1 text-2xl font-semibold tabular-nums" data-testid="actual-cost">{formatUsd(c.actual_cost_micro)}</div><div className="text-xs text-subtle">{compact(c.requests)} requests · {compact(c.tokens.prompt)} in · {compact(c.tokens.completion)} out</div></div>
                {c.current_models.slice(0, 5).map((m) => <div key={m.model}><div className="label">{m.model}</div><div className="mt-1 text-lg font-medium tabular-nums">{formatUsd(m.cost_micro)}</div><div className="text-xs text-subtle">{compact(m.requests)} calls</div></div>)}
              </div>
            ) : <Skeleton className="h-16" />}
          </section>
          <section className="card overflow-hidden" aria-label="Alternatives">
            <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
              <h2 className="font-semibold">If all of it ran on…</h2>
              <label className="flex items-center gap-2 text-xs text-muted"><input type="checkbox" checked={onlyFits} onChange={(e) => setOnlyFits(e.target.checked)} className="accent-[rgb(var(--accent))]" />Only models whose context fits your largest prompt</label>
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-left text-[13px]">
                <thead className="text-subtle"><tr>{["Model", "Provider", "Tier", "Would cost", "vs now", ""].map((h) => <th key={h} className="label px-5 py-3 font-medium">{h}</th>)}</tr></thead>
                <tbody className="divide-y divide-line">
                  {rows.map((m) => (
                    <tr key={m.model} data-testid="candidate-row" data-model={m.model}>
                      <td className="px-5 py-2.5 font-medium">{m.model}{!m.fits_largest_prompt && <span className="ml-2"><Chip tone="text-failed">too small for your biggest prompt</Chip></span>}</td>
                      <td className="px-5 py-2.5 text-muted">{m.provider}</td>
                      <td className="px-5 py-2.5 text-muted">{m.tier}</td>
                      <td className="px-5 py-2.5 tabular-nums">{formatUsd(m.cost_micro)}</td>
                      <td className={cn("px-5 py-2.5 tabular-nums font-medium", (m.delta_pct ?? 0) < 0 ? "text-completed" : "text-failed")}>{formatDelta(m.delta_pct)}</td>
                      <td className="w-48 px-5 py-2.5"><div className="h-2 overflow-hidden rounded-full bg-raised"><div className={cn("h-full rounded-full", (m.delta_pct ?? 0) < 0 ? "bg-completed" : "bg-failed/70")} style={{ width: `${(m.cost_micro / maxCost) * 100}%` }} /></div></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </section>
        </>
      )}
    </>
  );
}
