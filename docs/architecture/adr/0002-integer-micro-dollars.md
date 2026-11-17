# ADR-0002: Money is integer micro-dollars end to end

Status: accepted

## Context

Floating point sums drift, and a cost tool that is off by a cent in a total loses trust. Per-token prices are tiny
(for example $0.15 per million tokens).

## Decision

Store and compute cost as integers in micro-dollars (1e-6 USD). Prices are USD per 1M tokens, so `tokens x price` is
already micro-dollars. Rounding is half-up in Python (`half_up`) and SQLite `ROUND()` is half-away-from-zero, which is the
same for non-negative values, so re-pricing in SQL equals Python exactly. The API returns micro-dollars; only the
console formats dollars.

## Consequences

- Totals are exact and re-pricing is reproducible. A test pins the SQL and Python results to the micro-dollar.
- Clients must divide by 1e6 for display. Field names carry the unit (`*_micro`) to prevent mistakes.
- A copied dollar formatter once displayed micro-dollars as dollars; the console now has unit tests for formatting.
