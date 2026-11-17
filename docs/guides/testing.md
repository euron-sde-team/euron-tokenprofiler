# Testing

| Layer | Tool | Command | Count |
| --- | --- | --- | --- |
| Core (estimator, pricing, store, analytics, optimizer) | pytest | `make test` | 154 |
| SDK | pytest | `make test` | 19 |
| API (ingest, proxy, security, analytics, admin) | pytest | `make test` | 85 |
| Mock provider | pytest | `make test` | 8 |
| Console (format, API client, connection, components) | Vitest | `make test-console` | 18 |
| Full-stack end to end | Python against Compose + Floci | `make e2e` | 49 checks |
| Chaos | Python against Compose + Floci | `make e2e-chaos` | 8 scenarios |
| Browser | Playwright (Chromium) | `make e2e-browser` | 23 |
| Infrastructure | `terraform validate` | `make tf-validate` | 3 environments |

`make up && make demo` first for the stack-based suites.

## What the stack suites prove

- **e2e**: SDK and proxy to console numbers, exact costs, idempotent replays, budgets and alerts, isolation, CSV, re-pricing.
- **chaos**: 20 parallel writers without loss, restart under write load, dead provider, dead S3 with re-provisioning,
  hostile input (SQL, paths, NULs, oversize), 300 wrong keys, tenant isolation, the SDK under failure.
- **browser**: every screen, filters in the URL, drawers, optimizer dismiss/restore, budgets lifecycle, key creation and
  revocation, an app-limited key, XSS strings rendered as text, API outage and recovery, no console errors, mobile with no
  horizontal scroll, keyboard.

## Bugs these found (each now has a regression test)

Half-up rounding differences between SQL and Python; `INSERT OR IGNORE` hiding NOT NULL failures as duplicates; a
+133% trend from 10 days of data; the optimizer dropping a key's app allow-list; budgets and alerts visible across apps;
a startup crash loop when Secrets Manager restarted; a cache race that re-cached a stale budget list; a console formatter
treating micro-dollars as dollars.
