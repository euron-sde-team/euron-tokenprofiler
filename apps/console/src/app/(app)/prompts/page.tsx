"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { useState } from "react";
import { StackedBars } from "@/components/charts/stacked-bars";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi } from "@/components/providers";
import { Drawer } from "@/components/ui/overlays";
import { EmptyState, ErrorBanner, PageHeader, Skeleton } from "@/components/ui/primitives";
import { compact, formatDuration, formatPct, formatUsd } from "@/lib/format";

export default function PromptsPage() {
  const api = useApi();
  const { filter } = useFilter();
  const [open, setOpen] = useState<string | null>(null);
  const prompts = useQuery({ queryKey: ["prompts", filter], queryFn: () => api.prompts(filter) });
  const detail = useQuery({ queryKey: ["prompt", open, filter], queryFn: () => api.prompt(open!, filter), enabled: open !== null });
  const rows = prompts.data?.data ?? [];

  return (
    <>
      <PageHeader title="Prompts" description="Every prompt template, ranked by what it costs. Name prompts with prompt_id; otherwise calls are grouped by the shape of their text." actions={<FilterBar show={["range", "app", "environment"]} />} />
      {prompts.isError && <ErrorBanner error={prompts.error} onRetry={() => void prompts.refetch()} />}
      <section className="card overflow-hidden" aria-label="Prompt templates">
        {prompts.isLoading ? <div className="space-y-2 p-5"><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : rows.length === 0 ? <EmptyState icon={<FlaskConical className="size-5" />} title="No prompts in this range" hint="Send prompt_id with your events (or prompt_text so templates can be detected)." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-line text-subtle"><tr>{["Prompt", "App", "Model", "Calls", "Avg in", "Avg out", "Cached", "Per call", "Total"].map((h) => <th key={h} className="label px-5 py-3 font-medium">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((r, i) => (
                  <tr key={i} data-testid="prompt-row" className="cursor-pointer hover:bg-raised/50" onClick={() => setOpen(String(r.prompt_id))} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setOpen(String(r.prompt_id))}>
                    <td className="px-5 py-2.5 font-medium">{String(r.prompt_id)}</td><td className="px-5 py-2.5 text-muted">{String(r.app)}</td><td className="px-5 py-2.5 text-muted">{String(r.model)}</td>
                    <td className="px-5 py-2.5 tabular-nums">{compact(Number(r.requests))}</td><td className="px-5 py-2.5 tabular-nums">{compact(Number(r.avg_prompt_tokens))}</td><td className="px-5 py-2.5 tabular-nums">{compact(Number(r.avg_completion_tokens))}</td>
                    <td className="px-5 py-2.5 tabular-nums text-muted">{formatPct(Number(r.cached_share))}</td><td className="px-5 py-2.5 tabular-nums">{formatUsd(Number(r.cost_per_request_micro))}</td><td className="px-5 py-2.5 font-medium tabular-nums">{formatUsd(Number(r.cost))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <Drawer open={open !== null} onClose={() => setOpen(null)} title={<div><div className="text-xs text-subtle">Prompt</div><div className="font-semibold" data-testid="prompt-title">{open}</div></div>} width="max-w-2xl">
        {detail.isLoading && <div className="p-5"><Skeleton className="h-40" /></div>}
        {detail.isError && <div className="p-5"><ErrorBanner error={detail.error} /></div>}
        {detail.data && (
          <div className="space-y-6 p-5" data-testid="prompt-detail">
            <dl className="grid grid-cols-3 gap-4 text-[13px]">
              <div><dt className="label">Calls</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{compact(detail.data.totals.requests)}</dd></div>
              <div><dt className="label">Cost</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{formatUsd(detail.data.totals.cost)}</dd></div>
              <div><dt className="label">Largest prompt</dt><dd className="mt-1 text-lg font-semibold tabular-nums">{compact(detail.data.totals.max_prompt_tokens)}</dd></div>
              <div><dt className="label">Latency p50 / p95</dt><dd className="mt-1 tabular-nums">{formatDuration(detail.data.latency.p50)} / {formatDuration(detail.data.latency.p95)}</dd></div>
              <div><dt className="label">Errors</dt><dd className="mt-1 tabular-nums">{compact(detail.data.totals.errors)}</dd></div>
              <div><dt className="label">Cached input</dt><dd className="mt-1 tabular-nums">{formatPct(detail.data.totals.prompt_tokens ? detail.data.totals.cached_tokens / detail.data.totals.prompt_tokens : 0)}</dd></div>
            </dl>
            <div><h3 className="mb-3 text-[13px] font-semibold">Cost per day</h3><StackedBars rows={detail.data.trend.map((r) => ({ ...r, series: "cost" }))} by="series" height={140} /></div>
            <div><h3 className="mb-2 text-[13px] font-semibold">Models</h3><ul className="space-y-1 text-[13px]">{detail.data.models.map((m) => <li key={String(m.model)} className="flex justify-between"><span>{String(m.model)}</span><span className="tabular-nums text-muted">{compact(Number(m.requests))} calls · {formatUsd(Number(m.cost))}</span></li>)}</ul></div>
            {detail.data.errors.length > 0 && <div><h3 className="mb-2 text-[13px] font-semibold">Errors</h3><ul className="space-y-1 text-[13px]">{detail.data.errors.map((e) => <li key={e.error_type} className="flex justify-between"><span className="text-failed">{e.error_type}</span><span className="tabular-nums text-muted">{e.requests} calls · {formatUsd(e.cost)} wasted</span></li>)}</ul></div>}
          </div>
        )}
      </Drawer>
    </>
  );
}
