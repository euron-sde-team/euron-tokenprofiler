"use client";

import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { HBars } from "@/components/charts/hbars";
import { StackedBars } from "@/components/charts/stacked-bars";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi, useToast } from "@/components/providers";
import { Button, EmptyState, ErrorBanner, Field, PageHeader, Select, Skeleton } from "@/components/ui/primitives";
import { useUrlState } from "@/hooks/use-url-state";
import { compact, formatDuration, formatPct, formatUsd } from "@/lib/format";

const DIMS = ["app", "model", "provider", "prompt_id", "environment", "endpoint", "user_id", "status", "error_type", "finish_reason", "tag:team", "tag:feature"];
const METRICS = [
  { id: "cost", label: "Cost", fmt: formatUsd }, { id: "requests", label: "Requests", fmt: compact }, { id: "total_tokens", label: "Tokens", fmt: compact },
  { id: "prompt_tokens", label: "Input tokens", fmt: compact }, { id: "completion_tokens", label: "Output tokens", fmt: compact },
];

export default function ExplorerPage() {
  const api = useApi();
  const toast = useToast();
  const { filter } = useFilter();
  const { get, set } = useUrlState();
  const by = get("by") || "model";
  const metric = METRICS.find((m) => m.id === get("metric")) ?? METRICS[0]!;
  const [busy, setBusy] = useState(false);
  const rows = useQuery({
    queryKey: ["explorer", filter, by],
    queryFn: () => api.summary(filter, [by], ["requests", "prompt_tokens", "completion_tokens", "cached_tokens", "total_tokens", "cost", "errors", "avg_latency_ms"], "cost", 50),
  });
  const series = useQuery({ queryKey: ["explorer-series", filter, by], queryFn: () => api.timeseries(filter, { groupBy: by, metrics: ["cost"] }) });
  const data = rows.data?.data ?? [];

  async function download() {
    setBusy(true);
    try {
      const blob = await api.exportCsv(filter);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = "tokenprofiler-events.csv";
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      toast("error", e instanceof Error ? e.message : "Export failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <>
      <PageHeader title="Explorer" description="Slice usage by any dimension: app, model, prompt, user, error type, or your own tags."
        actions={<><FilterBar /><Button onClick={download} loading={busy} aria-label="Export CSV"><Download className="size-3.5" />Export CSV</Button></>} />
      <div className="mb-4 flex flex-wrap gap-3">
        <Field label="Group by"><Select value={by} onChange={(e) => set({ by: e.target.value })} aria-label="Group by" className="w-44">{DIMS.map((d) => <option key={d} value={d}>{d}</option>)}</Select></Field>
        <Field label="Chart"><Select value={metric.id} onChange={(e) => set({ metric: e.target.value })} aria-label="Metric" className="w-44">{METRICS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}</Select></Field>
      </div>
      {rows.isError && <ErrorBanner error={rows.error} onRetry={() => void rows.refetch()} />}
      <div className="grid gap-5 lg:grid-cols-3">
        <section className="card p-5 lg:col-span-2" aria-label="Over time"><h2 className="mb-4 font-semibold">Cost over time by {by}</h2>{series.data ? <StackedBars rows={series.data.data} by={by} /> : <Skeleton className="h-52" />}</section>
        <section className="card p-5" aria-label="Ranking"><h2 className="mb-4 font-semibold">{metric.label} by {by}</h2>
          {rows.data ? <HBars rows={[...data].sort((a, b) => Number(b[metric.id]) - Number(a[metric.id])).slice(0, 8).map((r) => ({ label: String(r[by]), value: Number(r[metric.id]), display: metric.fmt(Number(r[metric.id])) }))} /> : <Skeleton className="h-40" />}
        </section>
      </div>
      <section className="card mt-5 overflow-hidden" aria-label="Table">
        {rows.isLoading ? <div className="space-y-2 p-5"><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : data.length === 0 ? <EmptyState icon={<Download className="size-5" />} title="No data for these filters" hint="Widen the time range or clear a filter." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-line text-subtle"><tr>{[by, "Requests", "Input", "Output", "Cached", "Cost", "Errors", "Avg latency"].map((h) => <th key={h} className="label px-5 py-3 font-medium">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">
                {data.map((r) => (
                  <tr key={String(r[by])} data-testid="explorer-row">
                    <td className="px-5 py-2.5 font-medium">{String(r[by])}</td>
                    <td className="px-5 py-2.5 tabular-nums">{compact(Number(r.requests))}</td>
                    <td className="px-5 py-2.5 tabular-nums">{compact(Number(r.prompt_tokens))}</td>
                    <td className="px-5 py-2.5 tabular-nums">{compact(Number(r.completion_tokens))}</td>
                    <td className="px-5 py-2.5 tabular-nums text-muted">{formatPct(Number(r.prompt_tokens) ? Number(r.cached_tokens) / Number(r.prompt_tokens) : 0)}</td>
                    <td className="px-5 py-2.5 tabular-nums font-medium">{formatUsd(Number(r.cost))}</td>
                    <td className={`px-5 py-2.5 tabular-nums ${Number(r.errors) ? "text-failed" : "text-subtle"}`}>{compact(Number(r.errors))}</td>
                    <td className="px-5 py-2.5 tabular-nums text-muted">{formatDuration(Number(r.avg_latency_ms))}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}
