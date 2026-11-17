"use client";

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";
import { useUrlState } from "@/hooks/use-url-state";
import type { Filter } from "@/lib/types";
import { useApi } from "./providers";
import { Select } from "./ui/primitives";

export const RANGES = [
  { id: "24h", label: "Last 24 hours" }, { id: "7d", label: "Last 7 days" }, { id: "30d", label: "Last 30 days" }, { id: "90d", label: "Last 90 days" },
];

/** The filter state lives in the URL, so every view is a shareable link. */
export function useFilter(): { filter: Filter; set: (patch: Record<string, string | null>) => void } {
  const { get, set } = useUrlState();
  const filter = useMemo<Filter>(() => ({ range: get("range") || "30d", app: get("app") || undefined, environment: get("environment") || undefined, model: get("model") || undefined, prompt_id: get("prompt_id") || undefined }), [get]);
  return { filter, set };
}

export function FilterBar({ show = ["range", "app", "environment", "model"] }: { show?: ("range" | "app" | "environment" | "model")[] }) {
  const api = useApi();
  const { filter, set } = useFilter();
  const base = { range: filter.range };
  const apps = useQuery({ queryKey: ["dim", "app", filter.range], queryFn: () => api.dimension("app", base), enabled: show.includes("app") });
  const envs = useQuery({ queryKey: ["dim", "environment", filter.range], queryFn: () => api.dimension("environment", base), enabled: show.includes("environment") });
  const models = useQuery({ queryKey: ["dim", "model", filter.range], queryFn: () => api.dimension("model", base), enabled: show.includes("model") });
  const menu = (key: "app" | "environment" | "model", label: string, values?: string[]) => show.includes(key) && (
    <Select aria-label={label} value={filter[key] ?? ""} onChange={(e) => set({ [key]: e.target.value || null })} className="!w-40">
      <option value="">{`All ${label.toLowerCase()}s`}</option>
      {(values ?? []).map((v) => <option key={v} value={v}>{v}</option>)}
    </Select>
  );
  return (
    <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Filters">
      {show.includes("range") && (
        <Select aria-label="Time range" value={filter.range} onChange={(e) => set({ range: e.target.value })} className="!w-44">
          {RANGES.map((r) => <option key={r.id} value={r.id}>{r.label}</option>)}
        </Select>
      )}
      {menu("app", "App", apps.data?.values)}
      {menu("environment", "Environment", envs.data?.values)}
      {menu("model", "Model", models.data?.values)}
    </div>
  );
}
