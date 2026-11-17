# ADR-0003: A calibrated approximate tokenizer, always flagged as estimated

Status: accepted

## Context

Providers report usage, but not always (some streams, some proxies), and the Prompt Lab needs counts before any call is
made. Shipping `tiktoken` and its vocabulary downloads as a hard dependency makes the package heavy and offline-hostile.

## Decision

Implement a regex approximation of BPE with parameters per tokenizer family, fitted against `tiktoken` on a corpus
fixture. Held-out mean absolute error is about 4.2% (o200k) and 5.8% (cl100k). Every estimated count is stored with
`estimated = 1` and shown as such. If `tiktoken` is installed, `exact_count` uses it, and a `network` test compares the two.

## Consequences

- Works offline with no model files; usage is trusted when the provider reports it.
- Estimates are good for budgeting and prompt analysis, not billing. The UI and API never present them as exact.
- Recalibration is a script plus a fixture, not a code change.
