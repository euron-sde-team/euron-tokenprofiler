"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, AlertTriangle, Coins, Gauge, Layers, TrendingUp, Zap } from "lucide-react";
import Link from "next/link";
import { HBars } from "@/components/charts/hbars";
import { StackedBars } from "@/components/charts/stacked-bars";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi } from "@/components/providers";
import { BudgetBadge, budgetTone } from "@/components/ui/badges";
import { EmptyState, ErrorBanner, PageHeader, ProgressBar, Skeleton } from "@/components/ui/primitives";
import { StatTile } from "@/components/ui/stat-tile";
import { cn } from "@/lib/cn";
import { compact, dayLabel, formatDelta, formatDuration, formatPct, formatUsd } from "@/lib/format";
import type { Row } from "@/lib/types";

function Delta({ value, goodWhenDown = true }: { value: number | null | undefined; goodWhenDown?: boolean }) {
  if (value === undefined) return null;
  const up = (value ?? 0) > 0;
  const good = value === null ? true : goodWhenDown ? !up : up;
  return <span className={cn("ml-1.5 text-xs font-medium", value === null ? "text-subtle" : good ? "text-completed" : "text-failed")} data-testid="delta">{formatDelta(value)}</span>;
}

const bars = (rows: Row[] | undefined, key: string, hint?: (r: Row) => string) =>
  (rows ?? []).map((r) => ({ label: String(r[key]), value: Number(r.cost), display: formatUsd(Number(r.cost)), hint: hint?.(r) }));

export default function OverviewPage() {
  const api = useApi();
  const { filter } = useFilter();
  const overview = useQuery({ queryKey: ["overview", filter], queryFn: () => api.overview(filter), refetchInterval: 15_000 });
  const series = useQuery({ queryKey: ["series", filter], queryFn: () => api.timeseries(filter, { groupBy: "model", metrics: ["cost"], bucket: filter.range === "24h" ? "hour" : "day" }), refetchInterval: 30_000 });
  const budgets = useQuery({ queryKey: ["budgets"], queryFn: () => api.budgets(), refetchInterval: 30_000 });
  const recs = useQuery({ queryKey: ["recs", { range: "30d" }], queryFn: () => api.recommendations({ range: "30d" }), refetchInterval: 60_000 });
  const o = overview.data;
  const empty = o && o.totals.requests === 0;

  return (
    <>
      <PageHeader title="Overview" description="What your AI usage costs, where it goes, and where it is heading." actions={<FilterBar />} />
      {overview.isError && <div className="mb-4"><ErrorBanner error={overview.error} onRetry={() => void overview.refetch()} /></div>}

      {empty ? (
        <div className="card"><EmptyState icon={<Activity className="size-5" />} title="No usage in this range" hint="Send events with the SDK or POST /v1/events, route calls through the proxy, or run `make demo` to load sample traffic." action={<Link href="/settings" className="text-[13px] text-accent hover:underline">See how to connect an app</Link>} /></div>
      ) : (
        <>
          <section className="card mb-5 grid grid-cols-2 divide-x divide-y divide-line md:grid-cols-3 lg:grid-cols-6 lg:divide-y-0" aria-label="Key numbers">
            <StatTile label="Spend" value={o ? formatUsd(o.totals.cost) : <Skeleton className="h-7 w-20" />} hint={o ? <>vs previous period<Delta value={o.change.cost} /></> : undefined} icon={<Coins className="size-4" />} />
            <StatTile label="Projected month" value={o ? formatUsd(o.forecast.projected_month_micro) : <Skeleton className="h-7 w-20" />} hint={o ? `${formatUsd(o.forecast.month_to_date_micro)} so far · ${o.forecast.confidence} confidence` : undefined} icon={<TrendingUp className="size-4" />} />
            <StatTile label="Tokens" value={o ? compact(o.totals.total_tokens) : <Skeleton className="h-7 w-16" />} hint={o ? <>{compact(o.totals.prompt_tokens)} in · {compact(o.totals.completion_tokens)} out<Delta value={o.change.total_tokens} goodWhenDown={false} /></> : undefined} icon={<Layers className="size-4" />} />
            <StatTile label="Requests" value={o ? compact(o.totals.requests) : <Skeleton className="h-7 w-14" />} hint={o ? <>{formatPct(o.totals.requests ? o.totals.cached_tokens / Math.max(1, o.totals.prompt_tokens) : 0)} of input cached<Delta value={o.change.requests} goodWhenDown={false} /></> : undefined} icon={<Activity className="size-4" />} />
            <StatTile label="Cost per 1k calls" value={o ? formatUsd(o.cost_per_1k_requests_micro) : <Skeleton className="h-7 w-16" />} hint="average request cost" icon={<Gauge className="size-4" />} />
            <StatTile label="Error rate" value={o ? formatPct(o.error_rate, 1) : <Skeleton className="h-7 w-12" />} tone={o && o.error_rate > 0.05 ? "text-failed" : undefined} hint={o ? `p95 latency ${formatDuration(o.latency.p95)}` : undefined} icon={<Zap className="size-4" />} />
          </section>

          <div className="grid gap-5 lg:grid-cols-3">
            <section className="card p-5 lg:col-span-2" aria-label="Spend over time">
              <h2 className="mb-4 font-semibold">Spend over time, by model</h2>
              {series.data ? <StackedBars rows={series.data.data} by="model" bucket={filter.range === "24h" ? "hour" : "day"} /> : <Skeleton className="h-52" />}
            </section>
            <section className="card p-5" aria-label="Top apps">
              <h2 className="mb-4 font-semibold">Top apps</h2>
              {o ? <HBars rows={bars(o.top_apps, "app")} /> : <Skeleton className="h-40" />}
            </section>
          </div>

          <div className="mt-5 grid gap-5 lg:grid-cols-3">
            <section className="card p-5" aria-label="Top models"><h2 className="mb-4 font-semibold">Top models</h2>{o ? <HBars rows={bars(o.top_models, "model", (r) => `${compact(Number(r.total_tokens))} tokens`)} /> : <Skeleton className="h-40" />}</section>
            <section className="card p-5" aria-label="Top prompts"><h2 className="mb-4 font-semibold">Top prompts</h2>{o ? <HBars rows={bars(o.top_prompts, "prompt_id")} /> : <Skeleton className="h-40" />}</section>
            <section className="card space-y-4 p-5" aria-label="Insights">
              <h2 className="font-semibold">Worth a look</h2>
              {recs.data && recs.data.recommendations.length > 0 && (
                <Link href="/optimizer" className="block rounded-lg border border-completed/25 bg-completed/10 p-3 hover:bg-completed/15" data-testid="optimizer-teaser">
                  <div className="text-xs text-completed">Optimizer</div>
                  <div className="mt-0.5 text-[13px]">{recs.data.recommendations.length} ways to save up to <b>{formatUsd(recs.data.potential_monthly_savings_micro)}</b> a month</div>
                </Link>
              )}
              {o?.recent_anomalies.length ? o.recent_anomalies.map((a) => (
                <div key={a.day} className="flex items-start gap-2 rounded-lg border border-waiting/30 bg-waiting/10 p-3 text-[13px]" data-testid="anomaly">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0 text-waiting" />
                  <span>{dayLabel(a.day)}: spend {a.direction === "spike" ? "spiked to" : "fell to"} {formatUsd(a.value_micro)}, about {a.ratio.toFixed(1)}x the usual {formatUsd(a.expected_micro)}.</span>
                </div>
              )) : <p className="text-[13px] text-subtle">No unusual days recently.</p>}
              {o && o.totals.unpriced > 0 && <Link href="/settings" className="block rounded-lg border border-line p-3 text-[13px] text-muted hover:text-fg">{o.totals.unpriced.toLocaleString("en-US")} calls have no price, so totals are understated. Set prices</Link>}
            </section>
          </div>

          {budgets.data && budgets.data.data.length > 0 && (
            <section className="card mt-5 p-5" aria-label="Budgets">
              <div className="mb-4 flex items-center justify-between"><h2 className="font-semibold">Budgets</h2><Link href="/budgets" className="text-xs text-accent hover:underline">Manage</Link></div>
              <div className="grid gap-4 md:grid-cols-3">
                {budgets.data.data.slice(0, 3).map((b) => (
                  <div key={b.id} data-testid="budget-mini">
                    <div className="mb-1.5 flex items-baseline justify-between text-[13px]"><span className="font-medium">{b.name}</span><BudgetBadge status={b.status.status} /></div>
                    <ProgressBar value={b.status.used_pct} tone={budgetTone(b.status.status)} />
                    <div className="mt-1 text-xs tabular-nums text-subtle">{formatUsd(b.status.spent_micro)} of {formatUsd(b.status.limit_micro)}</div>
                  </div>
                ))}
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
