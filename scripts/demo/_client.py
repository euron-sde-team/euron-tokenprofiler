"""Tiny stdlib HTTP client shared by the demo scripts (no third-party dependencies)."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    f = ROOT / "deploy/compose/.env"
    if f.exists():
        for line in f.read_text().splitlines():
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                env[k] = v
    return env


class Reply:
    def __init__(self, status: int, body: Any, headers: dict[str, str], ms: float) -> None:
        self.status, self.body, self.headers, self.ms = status, body, headers, ms

    @property
    def error_code(self) -> str:
        try:
            return str(self.body["error"]["code"])
        except (KeyError, TypeError):
            return ""


class Api:
    def __init__(self, base: str | None = None) -> None:
        env = load_env()
        self.base = base or os.environ.get("TP_API_URL", f"http://localhost:{env.get('TP_API_PORT', '8380')}")
        self.key = os.environ.get("TP_API_KEY", env.get("TP_BOOTSTRAP_API_KEY", ""))
        self.mock = os.environ.get("TP_MOCK_URL", f"http://localhost:{env.get('TP_MOCK_PORT', '8390')}")
        self.floci = os.environ.get("TP_FLOCI_URL", f"http://localhost:{env.get('TP_FLOCI_PORT', '4866')}")

    def call(
        self, method: str, path: str, body: Any = None, key: str | None = None, headers: dict[str, str] | None = None, base: str | None = None,
        raw_body: bytes | None = None, timeout: float = 120,
    ) -> Reply:  # fmt: skip
        data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
        req = urllib.request.Request((base or self.base) + path, data=data, method=method)
        k = self.key if key is None else key
        if k:
            req.add_header("Authorization", f"Bearer {k}")
        if data:
            req.add_header("Content-Type", "application/json")
        for hk, hv in (headers or {}).items():
            req.add_header(hk, hv)
        start = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                payload = r.read()
                hdrs = {a.lower(): b for a, b in r.headers.items()}
                ms = (time.perf_counter() - start) * 1000
                ctype = hdrs.get("content-type", "")
                if "json" not in ctype or not payload:
                    return Reply(r.status, payload.decode(), hdrs, ms)
                return Reply(r.status, json.loads(payload), hdrs, ms)
        except urllib.error.HTTPError as e:
            payload = e.read()
            hdrs = {a.lower(): b for a, b in e.headers.items()}
            ms = (time.perf_counter() - start) * 1000
            try:
                return Reply(e.code, json.loads(payload), hdrs, ms)
            except ValueError:
                return Reply(e.code, payload.decode(), hdrs, ms)

    def events(self, events: list[dict[str, Any]], key: str | None = None) -> Reply:
        return self.call("POST", "/v1/events", {"events": events}, key=key)

    def summary(self, qs: str = "") -> list[dict[str, Any]]:
        r = self.call("GET", f"/api/usage/summary?range=365d{qs}")
        assert r.status == 200, r.body
        return list(r.body["data"])

    def wait_ready(self, timeout: float = 120) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            try:
                if self.call("GET", "/readyz", key="").status == 200:
                    return True
            except Exception:
                pass
            time.sleep(1)
        return False
