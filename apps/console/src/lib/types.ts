// Shapes returned by the TokenProfiler API (see apps/api). Only the fields the console reads. Money is micro-dollars.

export interface Totals {
  requests: number; prompt_tokens: number; completion_tokens: number; cached_tokens: number; total_tokens: number; cost: number; errors: number;
  saved: number; estimated: number; unpriced: number; avg_latency_ms: number; cost_input: number; cost_output: number; cache_hits: number;
}
export interface Row { [key: string]: string | number }
export interface Overview {
  range: { since: number; until: number }; totals: Totals; previous: { requests: number; cost: number; total_tokens: number; errors: number };
  change: { requests: number | null; cost: number | null; total_tokens: number | null; errors: number | null };
  cost_per_1k_requests_micro: number; error_rate: number; latency: { p50?: number; p95?: number; p99?: number; count?: number };
  forecast: { month_to_date_micro: number; projected_month_micro: number; run_rate_per_day_micro: number; trend_pct: number | null; confidence: "low" | "medium" | "high"; days_in_month: number };
  today_micro: number; top_models: Row[]; top_apps: Row[]; top_prompts: Row[];
  recent_anomalies: { day: number; value_micro: number; expected_micro: number; ratio: number; direction: string }[];
  bounds: { first_ts: number | null; last_ts: number | null; events: number }; prices_as_of: string;
}
export interface SeriesRow { [key: string]: string | number }
export interface Summary { group_by: string[]; metrics: string[]; data: Row[] }
export interface Recommendation {
  id: string; kind: string; title: string; summary: string; monthly_savings_micro: number; confidence: "high" | "medium" | "low"; effort: "low" | "medium" | "high";
  app: string | null; model: string | null; prompt_id: string | null; evidence: Record<string, unknown>; steps: string[]; dismissed: boolean;
}
export interface Recommendations {
  window_days: number; observed_days: number; monthly_cost_micro: number; potential_monthly_savings_micro: number; sum_of_recommendations_micro: number;
  overlap_note: string; recommendations: Recommendation[]; dismissed_count: number;
}
export interface BudgetStatus { status: "ok" | "warning" | "projected_overrun" | "exceeded"; spent_micro: number; limit_micro: number; used_pct: number; projected_micro: number; projected_pct: number; remaining_micro: number; days_until_exhausted: number | null }
export interface Budget { id: number; name: string; limit_micro: number; scope: Record<string, string>; warn_at: number; webhook_url: string | null; status: BudgetStatus; forecast_confidence: string; created_at: number }
export interface Alert { id: number; ts: number; kind: string; severity: string; title: string; detail: Record<string, unknown>; acknowledged: boolean; delivered: boolean }
export interface EventRow {
  id: string; ts: number; provider: string; model: string; model_raw: string; app: string; environment: string; endpoint: string; prompt_tokens: number; completion_tokens: number;
  cached_tokens: number; cache_write_tokens: number; estimated: boolean; cost_total: number; cost_input: number; cost_cached: number; cost_output: number; priced: boolean; saved_micro: number;
  latency_ms: number | null; ttft_ms: number | null; status: string; error_type: string | null; http_status: number | null; finish_reason: string | null; stream: boolean; batch: boolean;
  cache_hit: boolean; max_tokens: number | null; temperature: number | null; prompt_id: string | null; prompt_hash: string | null; user_id: string | null; session_id: string | null; tags: Record<string, string>;
}
export interface Price { id: string; provider: string; input: number; output: number; cached_input: number | null; cache_write: number | null; context: number | null; tier: string; cheaper: string | null; batch_discount: number | null; overridden: boolean }
export interface Candidate { model: string; provider: string; tier: string; cost_micro: number; delta_micro: number; delta_pct: number | null; fits_largest_prompt: boolean }
export interface Comparison { actual_cost_micro: number; requests: number; tokens: { prompt: number; completion: number; cached: number }; current_models: { model: string; cost_micro: number; requests: number }[]; candidates: Candidate[]; note: string }
export interface Finding { kind: string; severity: string; title: string; detail: string; tokens_saved: number; auto_fixable: boolean; examples: string[] }
export interface Analysis {
  model: string; tokenizer_family: string; estimated: boolean; total_tokens: number; optimized_tokens: number; tokens_saved: number; saved_pct: number; optimized_text: string | null;
  segments: { role: string; tokens: number; share: number; chars: number }[]; findings: Finding[]; cost_per_request_micro: number; optimized_cost_per_request_micro: number;
  monthly_savings_micro: number; priced: boolean; price_table: { model: string; provider: string; tier: string; per_request_micro: number; total_micro: number; fits: boolean }[];
}
export interface ApiKeyRecord { prefix: string; name: string; scopes: string[]; apps: string[]; created_at: number; last_used_at: number | null; revoked: boolean }
export interface ApiKeyCreated extends ApiKeyRecord { key: string }
export interface SystemInfo {
  version: string; bounds: { first_ts: number | null; last_ts: number | null; events: number }; prices_as_of: string; price_overrides: string[]; retention_days: number;
  limits: { max_batch_events: number; max_request_bytes: number; ingest_rate_limit_rpm: number };
  features: { proxy: boolean; reports: boolean; reports_bucket: string | null; derive_prompt_ids: boolean }; proxy_upstream: string | null;
}
export interface PromptDetail {
  prompt_id: string; totals: Totals & { max_prompt_tokens: number }; models: Row[]; trend: SeriesRow[]; latency: { p50?: number; p95?: number; p99?: number };
  errors: { error_type: string; requests: number; cost: number }[];
}
export interface Anomaly { scope: string; day: number; value_micro: number; expected_micro: number; ratio: number; score: number; direction: string }
export interface Report { id: number; ts: number; period: string; bucket: string; key: string; bytes: number }

export interface Filter { range: string; app?: string; environment?: string; model?: string; prompt_id?: string }
