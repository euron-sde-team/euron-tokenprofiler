# Configuration

All settings are environment variables with the `TP_` prefix (or a `.env` file).

| Variable | Default | Meaning |
| --- | --- | --- |
| `TP_HOST` / `TP_PORT` | `127.0.0.1` / `8380` | Listen address |
| `TP_DATA_DIR` | `~/.tokenprofiler` | Where `usage.db` and `control.db` live |
| `TP_API_KEY_PEPPER` | dev value | Secret mixed into key hashes. **Change it** (`TP_DATA_DIR` keys depend on it) |
| `TP_BOOTSTRAP_API_KEY` | none | An all-scopes key installed on every start (automation) |
| `TP_CORS_ORIGINS` | `http://localhost:3300` | Comma-separated console origins |
| `TP_MAX_BATCH_EVENTS` | `1000` | Events per `/v1/events` request |
| `TP_MAX_REQUEST_BYTES` | `8388608` | Request body limit |
| `TP_INGEST_RATE_LIMIT_RPM` | `0` (off) | Per key, on ingest and the proxy |
| `TP_RETENTION_DAYS` | `400` | Older events are deleted daily |
| `TP_CACHE_TTL_S` | `15` | Cache for heavy dashboard queries; `0` disables |
| `TP_DERIVE_PROMPT_IDS` | `true` | Group calls by prompt shape when no `prompt_id` is sent |
| `TP_PRICING_FILE` | none | A JSON file of extra or replacement prices |
| `TP_UPSTREAM_BASE_URL` / `TP_UPSTREAM_API_KEY` | none | Where the proxy forwards, and an optional server-side key |
| `TP_UPSTREAM_TIMEOUT_S` | `120` | Proxy read timeout |
| `TP_ALERT_INTERVAL_S` | `60` | How often budgets and anomalies are evaluated |
| `TP_ALLOW_PRIVATE_WEBHOOKS` | `false` | Allow webhook targets on private addresses |
| `TP_AWS_REGION`, `TP_AWS_ENDPOINT_URL` | none | AWS region, and an emulator endpoint for local use |
| `TP_REPORTS_BUCKET` | none | S3 bucket for archived reports |
| `TP_SECRETS_ARN` | none | Secrets Manager secret (JSON: `api_key_pepper`, `bootstrap_api_key`) |

## Prices

The built-in book has 42 approximate list prices (as of 2025-06) and is a starting point, not an invoice. Override a
model in the console (Settings, Prices) or `PUT /api/pricing/{model}`; stored history is re-priced when you ask. Models
with no price are shown as **unpriced** instead of costing zero; fix them under `GET /api/pricing/unpriced`.

## The console

`NEXT_PUBLIC_TP_API_URL` and `NEXT_PUBLIC_TP_DEFAULT_API_KEY` are baked in at build time (the local Compose build pre-fills a
demo key; leave the key empty for any shared build). The console keeps the URL and key you enter in the browser's local
storage; use a `read` key with an app allow-list for shared screens.
