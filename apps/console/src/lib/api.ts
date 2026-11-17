import type { Connection } from "./connection";
import type {
  Alert, Analysis, Anomaly, ApiKeyCreated, ApiKeyRecord, Budget, Comparison, EventRow, Filter, Overview, Price, PromptDetail, Recommendations, Report,
  Row, SeriesRow, Summary, SystemInfo,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public requestId?: string) {
    super(message);
    this.name = "ApiError";
  }
}

type Query = Record<string, string | number | boolean | undefined | null | (string | number)[]>;
interface Opts { query?: Query; body?: unknown }

export function qs(query?: Query): string {
  if (!query) return "";
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(query)) {
    if (v === undefined || v === null || v === "") continue;
    if (Array.isArray(v)) v.forEach((x) => p.append(k, String(x)));
    else p.set(k, String(v));
  }
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Turns the console's filter state into API query parameters. */
export function filterQuery(f: Filter): Query {
  return { range: f.range, app: f.app, environment: f.environment, model: f.model, prompt_id: f.prompt_id };
}

export class TokenProfilerApi {
  constructor(readonly conn: Connection) {}

  private async raw(method: string, path: string, opts: Opts = {}): Promise<Response> {
    try {
      return await fetch(`${this.conn.apiUrl}${path}${qs(opts.query)}`, {
        method,
        headers: { Authorization: `Bearer ${this.conn.apiKey}`, ...(opts.body !== undefined ? { "Content-Type": "application/json" } : {}) },
        body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
      });
    } catch {
      throw new ApiError(0, "network", `Cannot reach the API at ${this.conn.apiUrl}`);
    }
  }

  private async fail(res: Response): Promise<never> {
    let code = "error";
    let message = res.statusText;
    let requestId: string | undefined;
    try {
      const data = (await res.json()) as { error?: { code: string; message: string; request_id?: string } };
      if (data.error) ({ code, message, request_id: requestId } = data.error);
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, code, message, requestId);
  }

  private async request<T>(method: string, path: string, opts: Opts = {}): Promise<T> {
    const res = await this.raw(method, path, opts);
    if (!res.ok) return this.fail(res);
    if (res.status === 204) return undefined as T;
    return (await res.json()) as T;
  }

  overview = (f: Filter) => this.request<Overview>("GET", "/api/overview", { query: filterQuery(f) });
  summary = (f: Filter, groupBy: string[], metrics: string[], orderBy = "cost", limit = 100) =>
    this.request<Summary>("GET", "/api/usage/summary", { query: { ...filterQuery(f), group_by: groupBy, metrics, order_by: orderBy, limit } });
  timeseries = (f: Filter, opts: { bucket?: string; groupBy?: string; metrics?: string[]; top?: number } = {}) =>
    this.request<{ bucket: string; data: SeriesRow[] }>("GET", "/api/usage/timeseries", { query: { ...filterQuery(f), bucket: opts.bucket ?? "day", group_by: opts.groupBy, metrics: opts.metrics ?? ["requests", "cost", "total_tokens"], top: opts.top } });
  dimension = (name: string, f: Filter) => this.request<{ values: string[] }>("GET", `/api/dimensions/${encodeURIComponent(name)}`, { query: filterQuery(f) });
  events = (f: Filter, opts: { limit?: number; cursor?: string | null; status?: string } = {}) =>
    this.request<{ data: EventRow[]; next_cursor: string | null }>("GET", "/api/events", { query: { ...filterQuery(f), limit: opts.limit ?? 50, cursor: opts.cursor, status: opts.status } });
  prompts = (f: Filter) => this.request<{ data: Row[] }>("GET", "/api/prompts", { query: filterQuery(f) });
  prompt = (id: string, f: Filter) => this.request<PromptDetail>("GET", `/api/prompts/${id.split("/").map(encodeURIComponent).join("/")}`, { query: filterQuery(f) });
  compare = (f: Filter) => this.request<Comparison>("GET", "/api/models/compare", { query: filterQuery(f) });
  anomalies = (f: Filter) => this.request<{ data: Anomaly[] }>("GET", "/api/anomalies", { query: filterQuery(f) });
  recommendations = (f: Filter, includeDismissed = false) => this.request<Recommendations>("GET", "/api/recommendations", { query: { ...filterQuery(f), include_dismissed: includeDismissed } });
  dismiss = (id: string) => this.request<void>("POST", `/api/recommendations/${id}/dismiss`);
  restore = (id: string) => this.request<void>("DELETE", `/api/recommendations/${id}/dismiss`);
  analyze = (body: { text: string; model: string; expected_completion_tokens: number; requests_per_month: number }) => this.request<Analysis>("POST", "/api/prompts/analyze", { body });

  budgets = () => this.request<{ data: Budget[] }>("GET", "/api/budgets");
  createBudget = (b: { name: string; limit_usd: number; scope: Record<string, string>; warn_at: number; webhook_url?: string | null }) => this.request<Budget>("POST", "/api/budgets", { body: b });
  deleteBudget = (id: number) => this.request<void>("DELETE", `/api/budgets/${id}`);
  alerts = (includeAcked = false) => this.request<{ data: Alert[] }>("GET", "/api/alerts", { query: { include_acknowledged: includeAcked } });
  ackAlert = (id: number) => this.request<void>("POST", `/api/alerts/${id}/ack`);
  evaluateAlerts = () => this.request<{ raised: number }>("POST", "/api/alerts/evaluate");

  pricing = () => this.request<{ as_of: string; data: Price[] }>("GET", "/api/pricing");
  unpriced = (f: Filter) => this.request<{ data: Row[] }>("GET", "/api/pricing/unpriced", { query: filterQuery(f) });
  setPrice = (model: string, body: { input: number; output: number; cached_input?: number | null; provider?: string }) => this.request<Price>("PUT", `/api/pricing/${encodeURIComponent(model)}`, { body });
  deletePrice = (model: string) => this.request<void>("DELETE", `/api/pricing/${encodeURIComponent(model)}`);
  reprice = (model: string) => this.request<{ events_updated: number }>("POST", "/api/pricing/reprice", { body: { model } });

  keys = () => this.request<{ data: ApiKeyRecord[]; scopes: string[] }>("GET", "/api/keys");
  createKey = (name: string, scopes: string[], apps: string[]) => this.request<ApiKeyCreated>("POST", "/api/keys", { body: { name, scopes, apps } });
  revokeKey = (prefix: string) => this.request<void>("DELETE", `/api/keys/${prefix}`);
  system = () => this.request<SystemInfo>("GET", "/api/system");
  archive = (f: Filter, label: string) => this.request<{ bucket: string; key: string; bytes: number; rows: number }>("POST", "/api/reports/archive", { query: { ...filterQuery(f), label } });
  reports = () => this.request<{ data: Report[] }>("GET", "/api/reports");

  /** The CSV download URL for the current filters (the key is sent as a bearer header, so the console fetches it as a blob). */
  async exportCsv(f: Filter): Promise<Blob> {
    const res = await this.raw("GET", "/api/export/events", { query: { ...filterQuery(f), format: "csv", limit: 100000 } });
    if (!res.ok) return this.fail(res);
    return res.blob();
  }

  async ping(): Promise<void> {
    await this.system();
  }
}
