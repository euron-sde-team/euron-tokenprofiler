"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

/** Reads and writes query-string state so filters and the open job are shareable links. */
export function useUrlState() {
  const params = useSearchParams();
  const router = useRouter();
  const path = usePathname();

  const get = useCallback((key: string) => params.get(key) ?? "", [params]);
  const set = useCallback(
    (patch: Record<string, string | null>) => {
      const next = new URLSearchParams(params.toString());
      for (const [k, v] of Object.entries(patch)) {
        if (v === null || v === "") next.delete(k);
        else next.set(k, v);
      }
      const qs = next.toString();
      router.replace(qs ? `${path}?${qs}` : path, { scroll: false });
    },
    [params, router, path],
  );
  return { get, set };
}
