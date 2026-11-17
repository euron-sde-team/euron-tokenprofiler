# Operations

## State and backups

State is `usage.db` and `control.db` in `TP_DATA_DIR`. Locally, `make down` deletes them. On AWS the app host backs up
SQLite to S3 daily and restores the latest backup on boot. Archive a report with `POST /api/reports/archive`.

## Health

`/healthz` (process) and `/readyz` (storage reachable) are unauthenticated. `/metrics` exposes Prometheus metrics.
Logs are JSON with a `request_id` that also appears in error bodies.

## Retention

Events older than `TP_RETENTION_DAYS` are deleted daily. Aggregates are computed from events, so shortening retention
shortens history.

## Keys

Create keys in Settings (shown once) or with `tokenprofiler keys:create --name x --scopes ingest --apps support-bot`.
Revoke in Settings or `DELETE /api/keys/{prefix}`. Use `ingest` keys in applications and app-limited `read` keys for dashboards.

## Failure behaviour (tested)

| Failure | Behaviour |
| --- | --- |
| API restarts during writes | Retrying clients lose nothing (event ids make retries safe) |
| Provider down | Proxy returns `502 upstream_unreachable` and records the failure; ingest and analytics keep working |
| S3 down | Archiving returns `502 reports_unavailable`; everything else works |
| Secrets Manager slow or restarting | Startup retries with backoff, then fails closed |
| Console cannot reach the API | Shows Unreachable and recovers by itself |

## Upgrading

Stop the API, replace the image or package, start it. Tables are created on start if missing (`CREATE TABLE IF NOT EXISTS`);
there is no migration framework yet, so back up `TP_DATA_DIR` before upgrading across versions.
