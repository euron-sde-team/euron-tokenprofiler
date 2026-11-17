# ADR-0004: Cap optimizer savings instead of summing overlapping rules

Status: accepted

## Context

Each rule is individually sensible: use a cheaper model, cache the prompt prefix, cache exact repeats, batch. But they act
on the same spend. Summing them can claim more than 100% of what is being spent.

## Decision

Report each recommendation's saving on its own, but cap the headline **potential** at 70% of projected spend. Each
recommendation carries its evidence (the numbers it used), a confidence and an effort, and a stable id so dismissals persist.

## Consequences

- The headline is conservative and defensible; individual cards remain specific.
- Confidence is explicit (for example a model downgrade is medium or low until quality is compared).
- The cap is a constant, easy to revisit when real usage shows how much rules actually overlap.
