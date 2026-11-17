export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || Number.isNaN(ms)) return "-";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${Math.round(s % 60)}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  return `${Math.floor(h / 24)}d ${h % 24}h`;
}

/** "5s ago", "in 2m". `now` is injectable for tests. */
export function relativeTime(ts: number | null | undefined, now: number = Date.now()): string {
  if (!ts) return "-";
  const diff = ts - now;
  const abs = Math.abs(diff);
  if (abs < 1000) return "just now";
  const body = formatDuration(abs);
  return diff < 0 ? `${body} ago` : `in ${body}`;
}

export function formatNumber(n: number | null | undefined): string {
  if (n == null) return "-";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 10_000) return `${(n / 1000).toFixed(1)}k`;
  return n.toLocaleString("en-US");
}

export function shortId(id: string): string {
  return id.length > 12 ? `${id.slice(0, 4)}…${id.slice(-6)}` : id;
}

export function formatTime(ts: number | null | undefined): string {
  if (!ts) return "-";
  return new Date(ts).toLocaleString("en-US", {
    month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  });
}

export function prettyJson(value: unknown): string {
  try {
    return JSON.stringify(value, null, 2) ?? "null";
  } catch {
    return String(value);
  }
}

/** Parses user JSON, returning an error message instead of throwing. */
export function parseJsonObject(text: string): { value: Record<string, unknown> } | { error: string } {
  if (!text.trim()) return { value: {} };
  try {
    const v: unknown = JSON.parse(text);
    if (v === null || typeof v !== "object" || Array.isArray(v)) return { error: "Payload must be a JSON object" };
    return { value: v as Record<string, unknown> };
  } catch (e) {
    return { error: e instanceof Error ? e.message : "Invalid JSON" };
  }
}

/** Money arrives from the API as integer micro-dollars (1e-6 USD): "$0.0042", "$12.40", "$1.2k". */
export function formatUsd(micro: number | null | undefined): string {
  if (micro == null) return "-";
  const n = micro / 1_000_000;
  if (n === 0) return "$0.00";
  if (Math.abs(n) < 0.0001) return n > 0 ? "<$0.0001" : "-<$0.0001";
  if (Math.abs(n) < 0.01) return `$${n.toFixed(4)}`;
  if (Math.abs(n) < 1000) return `$${n.toFixed(2)}`;
  if (Math.abs(n) < 1_000_000) return `$${(n / 1000).toFixed(1)}k`;
  return `$${(n / 1_000_000).toFixed(1)}M`;
}

export function formatPct(ratio: number | null | undefined, digits = 0): string {
  if (ratio == null || Number.isNaN(ratio)) return "-";
  return `${(ratio * 100).toFixed(digits)}%`;
}

export function formatTtl(seconds: number): string {
  if (seconds % 86_400 === 0) return `${seconds / 86_400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

/** "512 MiB", "4.3 GiB": memory and file sizes. */
export function formatMb(mb: number | null | undefined): string {
  if (mb == null) return "-";
  if (mb < 1024) return `${Math.round(mb)} MiB`;
  const gb = mb / 1024;
  return `${gb >= 100 ? Math.round(gb) : gb.toFixed(1)} GiB`;
}

export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null) return "-";
  return formatMb(bytes / 2 ** 20);
}

export function formatRate(bps: number): string {
  return `${formatBytes(bps)}/s`;
}

export function formatTps(tps: number | null | undefined): string {
  if (tps == null) return "-";
  return tps >= 100 ? `${Math.round(tps)} tok/s` : `${tps.toFixed(1)} tok/s`;
}

/** Parameters in billions as "0.5B", "3.2B", "70B"; tiny models in millions or thousands. */
export function formatParams(b: number | null | undefined): string {
  if (b == null) return "-";
  if (b < 0.001) return `${Math.round(b * 1e6)}K`;
  if (b < 0.1) return `${Math.round(b * 1000)}M`;
  return b >= 10 ? `${Math.round(b)}B` : `${Number(b.toFixed(1))}B`;
}

/** "12.3k", "4.5M" tokens (shorter than formatNumber for chart labels). */
export function compact(n: number | null | undefined): string {
  if (n == null) return "-";
  const a = Math.abs(n);
  if (a >= 1e9) return `${(n / 1e9).toFixed(1)}B`;
  if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (a >= 1e3) return `${(n / 1e3).toFixed(1)}k`;
  return String(Math.round(n));
}

/** A signed percentage change: "+12%", "-4.5%", "new" when there is no previous value. */
export function formatDelta(ratio: number | null | undefined): string {
  if (ratio == null) return "new";
  const pct = ratio * 100;
  return `${pct >= 0 ? "+" : ""}${Math.abs(pct) < 10 ? pct.toFixed(1) : Math.round(pct)}%`;
}

/** Day number (days since the epoch, UTC) to a short date. */
export function dayLabel(day: number): string {
  return new Date(day * 86_400_000).toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}
