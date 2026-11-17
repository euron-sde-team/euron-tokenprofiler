// Where the console talks to, and with which key. Kept in localStorage: it is a per-browser setting.

const STORAGE_KEY = "tokenprofiler.connection";

export interface Connection {
  apiUrl: string;
  apiKey: string;
}

export const DEFAULT_API_URL = process.env.NEXT_PUBLIC_TP_API_URL ?? "http://localhost:8380";
export const DEFAULT_API_KEY = process.env.NEXT_PUBLIC_TP_DEFAULT_API_KEY ?? "";

export function loadConnection(): Connection | null {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as Partial<Connection>;
    if (typeof parsed.apiUrl === "string" && typeof parsed.apiKey === "string" && parsed.apiKey) {
      return { apiUrl: parsed.apiUrl, apiKey: parsed.apiKey };
    }
  } catch {
    /* storage unavailable or corrupt: treat as disconnected */
  }
  return null;
}

export function saveConnection(c: Connection): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(c));
  } catch {
    /* ignore */
  }
}

export function clearConnection(): void {
  try {
    window.localStorage.removeItem(STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

export function normalizeApiUrl(url: string): string {
  return url.trim().replace(/\/+$/, "");
}
