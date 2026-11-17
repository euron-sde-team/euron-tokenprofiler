# ADR-0005: Per-key app allow-lists enforced in the query layer

Status: accepted

## Context

Teams share one deployment, each with its own apps. A key for one team must not see another team's spend.

## Decision

A key may carry an allow-list of apps. It becomes `Filters.within`, ANDed into **every** SQL where-clause, and
`Filters.replace`/`narrowed` always carry it over. Budgets and alerts are filtered by scope. Writes outside the list are
rejected. The response cache is keyed by what the caller can see.

## Consequences

- Isolation does not depend on each endpoint remembering to filter; the filter object carries it.
- Two real leaks were found by tests (the optimizer rebuilding filters; budgets and alerts) and fixed, each with a regression test.
- This is isolation by app name, not full multi-tenancy; there is one operator and one set of price overrides.
