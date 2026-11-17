import { cn } from "@/lib/cn";

/** A ranked list with a proportional bar behind each row. */
export function HBars({ rows, empty = "Nothing yet" }: { rows: { label: string; value: number; display: string; hint?: string; href?: string }[]; empty?: string }) {
  const max = Math.max(1, ...rows.map((r) => r.value));
  if (rows.length === 0) return <p className="px-1 py-4 text-[13px] text-subtle">{empty}</p>;
  return (
    <ul className="space-y-2" data-testid="hbars">
      {rows.map((r) => (
        <li key={r.label} className="group relative overflow-hidden rounded-md">
          <div className={cn("absolute inset-y-0 left-0 rounded-md bg-accent/15 transition-all group-hover:bg-accent/25")} style={{ width: `${(r.value / max) * 100}%` }} aria-hidden />
          <div className="relative flex items-baseline justify-between gap-3 px-2.5 py-1.5 text-[13px]">
            <span className="min-w-0 truncate font-medium" title={r.label}>{r.label}{r.hint && <span className="ml-2 text-xs font-normal text-subtle">{r.hint}</span>}</span>
            <span className="shrink-0 tabular-nums text-muted">{r.display}</span>
          </div>
        </li>
      ))}
    </ul>
  );
}
