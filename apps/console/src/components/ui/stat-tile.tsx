import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

export function StatTile({ label, value, hint, tone, icon }: {
  label: string; value: ReactNode; hint?: ReactNode; tone?: string; icon?: ReactNode;
}) {
  return (
    <div className="p-4" data-testid={`stat-${label.toLowerCase().replace(/\s+/g, "-")}`}>
      <div className="flex items-center justify-between">
        <span className="label">{label}</span>
        {icon && <span className="text-subtle">{icon}</span>}
      </div>
      <div className={cn("mt-2 text-[26px] font-semibold leading-none tracking-tight tabular-nums", tone)}>{value}</div>
      {hint && <div className="mt-2 truncate text-xs text-subtle">{hint}</div>}
    </div>
  );
}
