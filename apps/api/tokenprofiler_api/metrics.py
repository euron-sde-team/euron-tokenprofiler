"""Prometheus metrics for the API itself (events ingested, rejected, proxied) plus spend gauges computed at scrape time."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest


class ApiMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "tokenprofiler_http_requests_total",
            "HTTP requests",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.latency = Histogram(
            "tokenprofiler_http_request_seconds",
            "HTTP request latency",
            ["method", "route"],
            registry=self.registry,
        )
        self.ingested = Counter(
            "tokenprofiler_events_ingested_total", "Events stored", ["source"], registry=self.registry
        )
        self.duplicates = Counter(
            "tokenprofiler_events_duplicate_total", "Events skipped as duplicates", registry=self.registry
        )
        self.rejected = Counter(
            "tokenprofiler_events_rejected_total", "Events rejected as invalid", registry=self.registry
        )
        self.proxied = Counter(
            "tokenprofiler_proxy_requests_total", "Proxied LLM requests", ["status"], registry=self.registry
        )
        self.record_failures = Counter(
            "tokenprofiler_record_failures_total",
            "Usage records that could not be stored",
            registry=self.registry,
        )
        self.alerts = Counter("tokenprofiler_alerts_total", "Alerts raised", ["kind"], registry=self.registry)

    def render(self) -> bytes:
        return generate_latest(self.registry)
