# Using the optimizer

Open **Optimizer**. Each recommendation shows its title, estimated monthly saving, confidence and effort, the **evidence**
(the numbers behind it) and concrete steps.

| Rule | It looks for | Typical action |
| --- | --- | --- |
| Cheaper model | A model with a cheaper sibling and short, low-error traffic | Replay a sample on the cheaper model, then route a share |
| Prompt caching | Large shared prefixes, with a modelled hit rate | Put stable content first; enable provider caching |
| Response cache | Exact repeats of the same prompt | Cache the answer for a TTL |
| Truncation | Many responses cut at the output limit | Raise the limit or shorten the answer |
| Error waste | Spend on calls that failed | Fix retries, add backoff, validate before calling |
| Batch API | Non-interactive traffic | Move it to the provider's batch endpoint |
| Context growth | Sessions whose prompts keep growing | Summarise or trim history |
| Unpriced | Models with no price | Add a price so they are counted |

The headline **potential** is capped at 70% of projected spend because rules overlap. Treat savings as estimates:
verify quality before switching models. **Dismiss** hides a recommendation (it persists); **Restore** brings it back.

## Prompt Lab

Paste a prompt to see its estimated tokens, where they go (system, user, tool), findings (pretty-printed JSON, repeated
passages, filler, whitespace), a cleaned-up version for the safe fixes, and the cost on every model at your volume.
Nothing is stored.

## Budgets and alerts

A budget is a monthly limit, optionally scoped (for example to one app). Status: `ok`, `warning` (past the warning
fraction), `projected_overrun` (on track to exceed by month end) and `exceeded`. Alerts can post to a webhook (HTTPS to a
public address). Anomaly alerts fire on spend **spikes** against the trailing 14-day baseline.
