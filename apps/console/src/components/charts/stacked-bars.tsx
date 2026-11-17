"use client";

import { useState } from "react";
import { cn } from "@/lib/cn";
import { dayLabel, formatUsd } from "@/lib/format";
import type { SeriesRow } from "@/lib/types";

export const PALETTE = ["bg-accent", "bg-active", "bg-completed", "bg-waiting", "bg-delayed", "bg-failed", "bg-cancelled"];

/** Daily spend as stacked bars, one colour per series (model, app, ...). Hover for the day's breakdown. */
export function StackedBars({ rows, by, bucket = "day", height = 200, value = "cost" }: { rows: SeriesRow[]; by: string; bucket?: string; height?: number; value?: string }) {
  const [hover, setHover] = useState<number | null>(null);
  const xs = Array.from(new Set(rows.map((r) => Number(r[bucket])))).sort((a, b) => a - b);
  const keys = Array.from(new Set(rows.map((r) => String(r[by])))).sort((a, b) => (a === "other" ? 1 : b === "other" ? -1 : a.localeCompare(b)));
  const cell = new Map(rows.map((r) => [`${r[bucket]}|${r[by]}`, Number(r[value] ?? 0)]));
  const totals = xs.map((x) => keys.reduce((n, k) => n + (cell.get(`${x}|${k}`) ?? 0), 0));
  const max = Math.max(1, ...totals);
  const ticks = [max, max / 2, 0];
  const fmt = value === "cost" ? formatUsd : (n: number) => String(Math.round(n));
  const label = (x: number) => (bucket === "day" ? dayLabel(x) : new Date(x * 3_600_000).toLocaleTimeString("en-US", { hour: "2-digit", minute: "2-digit", hour12: false }));
  return (
    <div data-testid="stacked-bars">
      <div className="relative flex" style={{ height }}>
        <div className="flex w-14 shrink-0 flex-col justify-between pb-0.5 pr-2 text-right font-mono text-[10px] text-subtle">{ticks.map((t, i) => <span key={i}>{fmt(t)}</span>)}</div>
        <div className="relative flex-1">
          <div className="pointer-events-none absolute inset-0 flex flex-col justify-between" aria-hidden>{ticks.map((_, i) => <div key={i} className="border-t border-dashed border-line/70" />)}</div>
          <div className="relative flex h-full items-end gap-[3px]" role="img" aria-label={`${value} by ${by} over ${xs.length} ${bucket}s`} onMouseLeave={() => setHover(null)}>
            {xs.map((x, i) => (
              <div key={x} className="flex h-full flex-1 flex-col justify-end" onMouseEnter={() => setHover(i)}>
                <div className={cn("flex flex-col-reverse overflow-hidden rounded-[3px] transition-opacity", hover !== null && hover !== i && "opacity-50")}>
                  {keys.map((k, ki) => {
                    const v = cell.get(`${x}|${k}`) ?? 0;
                    return v > 0 ? <div key={k} className={PALETTE[ki % PALETTE.length]} style={{ height: `${(v / max) * (height - 4)}px` }} /> : null;
                  })}
                </div>
              </div>
            ))}
          </div>
          {hover !== null && xs[hover] !== undefined && (
            <div className="pointer-events-none absolute top-0 z-10 -translate-x-1/2 rounded-lg border border-line bg-raised px-2.5 py-1.5 text-[11px] shadow-pop" style={{ left: `${((hover + 0.5) / xs.length) * 100}%` }}>
              <div className="mb-0.5 font-mono text-subtle">{label(xs[hover]!)}</div>
              {keys.map((k, ki) => { const v = cell.get(`${xs[hover]}|${k}`) ?? 0; return v > 0 ? <div key={k} className="whitespace-nowrap"><span className={cn("mr-1 inline-block size-2 rounded-sm align-middle", PALETTE[ki % PALETTE.length])} />{k}: {fmt(v)}</div> : null; })}
              <div className="mt-0.5 border-t border-line pt-0.5 font-medium">{fmt(totals[hover] ?? 0)} total</div>
            </div>
          )}
        </div>
      </div>
      <ul className="mt-3 flex flex-wrap gap-x-4 gap-y-1 pl-14 text-[11px] text-muted">
        {keys.map((k, ki) => <li key={k} className="flex items-center gap-1.5"><span className={cn("size-2 rounded-sm", PALETTE[ki % PALETTE.length])} aria-hidden />{k}</li>)}
      </ul>
    </div>
  );
}
