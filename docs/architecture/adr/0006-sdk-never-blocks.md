# ADR-0006: The SDK and proxy must never block or fail the caller

Status: accepted

## Context

Telemetry is useful, but an outage of the profiler must never become an outage of the application.

## Decision

The SDK enqueues to a bounded queue (dropping the oldest when full) and a background thread sends batches with retries
and backoff, except for errors that retrying cannot fix (400, 401, 403, 413, 422). Event ids make retries idempotent. The
proxy returns the provider's answer first and records from a detached task, and closes the stream generator so a
disconnected client is still recorded.

## Consequences

- Under a profiler outage the application runs normally; some telemetry is dropped and counted in `stats`.
- Data can be lost on a hard crash before the queue flushes; `flush()` and `close()` exist for shutdown.
- The chaos suite proves the proxy and SDK under a dead profiler, dead provider and dead S3.
