# Security model

TokenProfiler stores usage metadata, not prompts, but costs and traffic shape are sensitive. The goals: nobody reads or
writes outside what their key allows, a hostile client cannot hurt the server, and a failure never leaks a secret.

## Keys

- Format `tp_<8 hex prefix>_<secret>`. The secret is shown once; only `HMAC-SHA256(pepper, secret)` is stored.
- Scopes: `ingest`, `read`, `budgets:write`, `pricing:write`, `admin`. Optional **app allow-list** per key.
- Revocation is immediate. 300 wrong keys in parallel are all `401` and do not slow a good key (chaos suite).
- The pepper and bootstrap key come from Secrets Manager in AWS and are read at startup with retries; the API **fails
  closed** if it cannot read them. `DEV_PEPPER` is for local use only.

## Isolation

An app-restricted key cannot read, count, export, optimise, budget or alert on other apps, and cannot write into them.
Regression tests cover the optimizer rebuilding filters (it once dropped the allow-list) and budgets/alerts (an unscoped
company-total budget revealed every app's spend). Foreign resources return `404`, indistinguishable from missing ones.

## Input

- Body size and batch size are bounded. Every field is validated; hostile text is stored as data and rendered as text
  (the browser suite asserts no HTML/JS executes from app, prompt or key names).
- SQL: only whitelisted identifiers are interpolated; everything else is bound (injection attempts in the chaos suite
  leave data intact).
- Webhooks: HTTPS only, no embedded credentials, and every resolved address must be public, checked at save and again at delivery; private ranges are refused unless
  `TP_ALLOW_PRIVATE_WEBHOOKS` is set.
- CSV export neutralises formula injection.

## Proxy

The upstream provider key is sent per request (`X-Upstream-Api-Key`) or configured server-side, and is never logged or
stored. The proxy always records, even when the provider fails, and never fails the caller because recording failed.

## Infrastructure

IMDSv2 required, SSM instead of SSH, private subnets for the app host, encrypted buckets with public access blocked,
secrets in Secrets Manager. The AWS environments are validated with `terraform validate` and have not been applied.

## Reporting

See [SECURITY.md](../../SECURITY.md).
