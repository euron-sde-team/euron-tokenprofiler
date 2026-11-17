# Low-level design

## Money and tokens

- **Money is integer micro-dollars** (1e-6 USD) from the price book to the chart. Prices are USD per 1M tokens, so
  `tokens x price` is micro-dollars with no scaling. Rounding is half-up (`pricing.half_up`), the same rule SQLite's
  `ROUND()` applies, so re-pricing history in SQL equals Python to the micro-dollar. See [ADR-0002](adr/0002-integer-micro-dollars.md).
- `prompt_tokens` is the **total** input, including the cached and cache-write subsets. `completion_tokens` includes
  reasoning tokens. Cost splits input into fresh, cached and cache-write portions.
- The **estimator** (`tokens.py`) is a regex approximation of byte-pair encoding with per-family parameters (o200k,
  cl100k, generic). It is calibrated on `fixtures/token_corpus.json`; held-out mean error is about 4.2% (o200k) and 5.8%
  (cl100k). Anything it produces is flagged `estimated`. `exact_count` uses `tiktoken` when installed. See [ADR-0003](adr/0003-estimator-calibration.md).

## Price book

`prices.json` holds 42 approximate list prices (as of 2025-06). Lookup normalises the name (provider prefixes, dates,
`-latest`, `-preview` removed) then takes the longest matching prefix. Overrides live in `control.db` and are applied on
load; `POST /api/pricing/reprice` recomputes stored costs for a model.

## Storage

`UsageStore` keeps one SQLite connection behind an `RLock`, called through worker threads. Ingest is
`INSERT ... ON CONFLICT(id) DO NOTHING` inside a transaction (a NOT NULL violation rolls back instead of being counted as
a duplicate). Dimensions and metrics are **whitelisted** in `query.py`; user text is only ever a bound value, and tag
names are validated before use in a JSON path.

## Analytics

- **Anomalies** (`anomaly.py`): each day against the median and MAD of the trailing 14 days (never including itself).
  Flagged only if beyond a robust z-score threshold **and** 25% away **and** above an absolute floor. Alerts raise spikes only.
- **Forecast** (`forecast.py`): month-end projection from the median daily run-rate; a trend term only with at least 14
  days of data.
- **Budgets** (`budgets.py`): monthly, optionally scoped to an app/model/etc. Status is `ok`, `warning`,
  `projected_overrun` or `exceeded`.

## Optimizer

`recommend.analyze` runs rules over the real traffic: cheaper model, prompt caching (with a hit-rate model), response
cache from exact repeats, output truncation, error waste, batch API, context growth and unpriced models. Rules overlap, so
the total potential is **capped at 70%** of spend instead of summing. Recommendation ids are stable 12-character hashes so
dismissals survive. See [ADR-0004](adr/0004-optimizer-overlap-cap.md).

## API

One error shape: `{"error": {"message", "type", "code", "request_id"}}`. `/v1/events` returns 422 `all_events_invalid`
when no event in a batch is valid. The proxy records in a detached task and closes the stream generator so a client that
disconnects is still recorded. Webhook URLs are checked against an SSRF guard at save time and again at delivery.
CSV export neutralises cells that start with `=`, `+`, `-`, `@`.

## Auth and isolation

Keys are `tp_<prefix>_<secret>`; only an HMAC-SHA256 of the secret (with a server pepper) is stored. Scopes: `ingest`,
`read`, `budgets:write`, `pricing:write`, `admin`. A key may carry an **app allow-list**; it is applied to every read via
`Filters.within`, to budgets and alerts, to the optimizer, and to writes. The response cache key includes what the caller
may see. See [security.md](security.md) and [ADR-0005](adr/0005-per-app-isolation.md).

## SDK

A bounded queue (drop oldest), one background thread, batches, retries with backoff except on 400/401/403/413/422, and
idempotent event ids so a retried batch is safe. Wrappers hide the usage-only stream chunk they request.

## Cache

`TTLCache.get_or_compute` serves heavy queries for `TP_CACHE_TTL_S`. A generation counter makes `clear()` race-safe: a value
computed before a clear is returned to its caller but never stored.
