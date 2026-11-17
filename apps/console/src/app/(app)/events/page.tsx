"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { Calculator } from "lucide-react";
import { useState } from "react";
import { FilterBar, useFilter } from "@/components/filter-bar";
import { useApi } from "@/components/providers";
import { Chip } from "@/components/ui/badges";
import { Drawer } from "@/components/ui/overlays";
import { Button, EmptyState, ErrorBanner, PageHeader, Select, Skeleton } from "@/components/ui/primitives";
import { useUrlState } from "@/hooks/use-url-state";
import { cn } from "@/lib/cn";
import { formatDuration, formatTime, formatUsd, prettyJson } from "@/lib/format";
import type { EventRow } from "@/lib/types";

export default function EventsPage() {
  const api = useApi();
  const { filter } = useFilter();
  const { get, set } = useUrlState();
  const status = get("status") || undefined;
  const [open, setOpen] = useState<EventRow | null>(null);
  const q = useInfiniteQuery({
    queryKey: ["events", filter, status],
    queryFn: ({ pageParam }) => api.events(filter, { cursor: pageParam, limit: 50, status }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.next_cursor,
    refetchInterval: 10_000,
  });
  const rows = q.data?.pages.flatMap((p) => p.data) ?? [];

  return (
    <>
      <PageHeader title="Events" description="Every recorded call, newest first. Click one for the full picture." actions={<>
        <Select aria-label="Status" value={status ?? ""} onChange={(e) => set({ status: e.target.value || null })} className="!w-36"><option value="">All statuses</option><option value="ok">Succeeded</option><option value="error">Failed</option></Select>
        <FilterBar /></>} />
      {q.isError && <ErrorBanner error={q.error} onRetry={() => void q.refetch()} />}
      <section className="card overflow-hidden" aria-label="Events">
        {q.isLoading ? <div className="space-y-2 p-5"><Skeleton className="h-8" /><Skeleton className="h-8" /></div> : rows.length === 0 ? <EmptyState icon={<Calculator className="size-5" />} title="No events" hint="Nothing matches these filters yet." /> : (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-[13px]">
              <thead className="border-b border-line text-subtle"><tr>{["Time", "App", "Model", "Prompt", "In", "Out", "Cost", "Latency", "Status"].map((h) => <th key={h} className="label px-4 py-3 font-medium">{h}</th>)}</tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((e) => (
                  <tr key={e.id} data-testid="event-row" className="cursor-pointer hover:bg-raised/50" onClick={() => setOpen(e)} tabIndex={0} onKeyDown={(ev) => ev.key === "Enter" && setOpen(e)}>
                    <td className="px-4 py-2 font-mono text-xs text-muted">{formatTime(e.ts)}</td><td className="px-4 py-2">{e.app}</td><td className="px-4 py-2 text-muted">{e.model}</td>
                    <td className="max-w-40 truncate px-4 py-2 text-muted" title={e.prompt_id ?? ""}>{e.prompt_id ?? "-"}</td>
                    <td className="px-4 py-2 tabular-nums">{e.prompt_tokens.toLocaleString("en-US")}{e.estimated && <span title="estimated" className="text-subtle">~</span>}</td><td className="px-4 py-2 tabular-nums">{e.completion_tokens.toLocaleString("en-US")}</td>
                    <td className="px-4 py-2 tabular-nums">{e.cache_hit ? <span className="text-completed">saved {formatUsd(e.saved_micro)}</span> : formatUsd(e.cost_total)}{!e.priced && <span className="ml-1 text-waiting" title="no price">?</span>}</td>
                    <td className="px-4 py-2 tabular-nums text-muted">{e.latency_ms == null ? "-" : formatDuration(e.latency_ms)}</td>
                    <td className={cn("px-4 py-2", e.status === "error" ? "text-failed" : "text-completed")}>{e.status === "error" ? (e.error_type ?? "error") : "ok"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {q.hasNextPage && <div className="border-t border-line p-3 text-center"><Button onClick={() => void q.fetchNextPage()} loading={q.isFetchingNextPage}>Load more</Button></div>}
      </section>

      <Drawer open={open !== null} onClose={() => setOpen(null)} title={<div><div className="text-xs text-subtle">Event</div><div className="font-mono text-[13px]">{open?.id}</div></div>} width="max-w-xl">
        {open && (
          <div className="space-y-5 p-5" data-testid="event-detail">
            <div className="flex flex-wrap gap-1.5"><Chip>{open.provider}</Chip><Chip>{open.model_raw}</Chip>{open.stream && <Chip>stream</Chip>}{open.batch && <Chip>batch</Chip>}{open.cache_hit && <Chip tone="text-completed">cache hit</Chip>}{open.estimated && <Chip tone="text-waiting">tokens estimated</Chip>}{!open.priced && <Chip tone="text-waiting">unpriced model</Chip>}</div>
            <dl className="grid grid-cols-2 gap-4 text-[13px]">
              <div><dt className="label">Input cost</dt><dd className="mt-1 tabular-nums">{formatUsd(open.cost_input + open.cost_cached)}</dd></div>
              <div><dt className="label">Output cost</dt><dd className="mt-1 tabular-nums">{formatUsd(open.cost_output)}</dd></div>
              <div><dt className="label">Cached tokens</dt><dd className="mt-1 tabular-nums">{open.cached_tokens.toLocaleString("en-US")} of {open.prompt_tokens.toLocaleString("en-US")}</dd></div>
              <div><dt className="label">Time to first token</dt><dd className="mt-1 tabular-nums">{open.ttft_ms == null ? "-" : formatDuration(open.ttft_ms)}</dd></div>
              <div><dt className="label">Finish reason</dt><dd className="mt-1">{open.finish_reason ?? "-"}</dd></div>
              <div><dt className="label">User</dt><dd className="mt-1 break-all">{open.user_id ?? "-"}</dd></div>
            </dl>
            <pre className="overflow-x-auto rounded-lg border border-line bg-bg p-3 font-mono text-[11px] text-muted">{prettyJson(open)}</pre>
          </div>
        )}
      </Drawer>
    </>
  );
}
