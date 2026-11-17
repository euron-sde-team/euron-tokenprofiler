# ADR-0001: A single API process with SQLite, no external database

Status: accepted

## Context

An analytics tool people try on a laptop should not require Postgres or ClickHouse. Event volume for one team is
modest: the demo holds 200k+ events and dashboards answer in well under a second.

## Decision

One API process owns two SQLite files in WAL mode (`usage.db`, `control.db`), one connection each behind a lock, called
through worker threads. Heavy dashboard queries are cached for a few seconds.

## Consequences

- Nothing else to install, secure or back up beyond two files (backed up daily to S3 on AWS).
- One writer process; the AWS shape is an Auto Scaling group of size one that restores from S3 on boot.
- Ingest throughput is bounded by one SQLite writer. Batches of up to 1,000 events keep it high; the chaos suite writes
  from 20 parallel clients without loss.
- Very large histories would want a columnar store. The query layer is isolated in `query.py`/`store.py` so it can be swapped.
