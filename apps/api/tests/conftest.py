from __future__ import annotations

import io
import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from mock_llm.main import create_app as create_mock
from tokenprofiler_api.main import create_app
from tokenprofiler_api.settings import ApiSettings
from tokenprofiler_core.demo import generate
from tokenprofiler_core.events import now_ms
from tokenprofiler_core.ingest import resolve
from tokenprofiler_core.pricing import PriceBook
from tokenprofiler_core.store import DAY_MS

ROOT_KEY = "tp_test0000_root-key-for-tests-only"
NOW = 1_763_000_000_000  # a fixed "now" for seeded data


class FakeS3:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}

    def put_object(self, Bucket: str, Key: str, Body: bytes, ContentType: str = "") -> dict[str, Any]:
        self.objects[(Bucket, Key)] = Body
        return {}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}


@dataclass
class Stack:
    api: httpx.AsyncClient
    app: Any
    settings: ApiSettings
    mock: httpx.AsyncClient
    s3: FakeS3
    tmp: Path

    @property
    def state(self) -> Any:
        return self.app.state.tp

    def client(self, key: str | None) -> httpx.AsyncClient:
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        return httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.app), base_url="http://test", headers=headers
        )

    async def new_key(self, scopes: list[str], apps: list[str] | None = None) -> str:
        r = await self.api.post("/api/keys", json={"name": "t", "scopes": scopes, "apps": apps or []})
        assert r.status_code == 201, r.text
        return str(r.json()["key"])

    async def post_events(
        self, events: list[dict[str, Any]], client: httpx.AsyncClient | None = None
    ) -> httpx.Response:
        return await (client or self.api).post("/v1/events", json={"events": events})

    async def seed_demo(self, days: int = 30, scale: float = 0.08, seed: int = 5) -> int:
        """Loads synthetic traffic straight into the store, ending now (the API judges "recent" by the real clock)."""
        book = PriceBook()
        evs = [resolve(e, book) for e in generate(now_ms(), days=days, scale=scale, seed=seed)]
        self.state.usage.record(evs)
        return len(evs)


def ev(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {"model": "gpt-4o", "prompt_tokens": 1_000, "completion_tokens": 500}
    base.update(kw)
    return base


@pytest.fixture
async def stack(tmp_path: Path) -> AsyncIterator[Stack]:
    settings = ApiSettings(
        _env_file=None, data_dir=tmp_path / "data", api_key_pepper="test-pepper-123", bootstrap_api_key=ROOT_KEY, cors_origins=["http://localhost:3300"],
        upstream_base_url="http://mock/v1", reports_bucket="test-reports", alert_interval_s=3600,
    )  # fmt: skip
    mock = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_mock(latency_ms=0)), base_url="http://mock"
    )
    s3 = FakeS3()
    app = create_app(settings, http_client=mock, s3_client=s3)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            headers={"Authorization": f"Bearer {ROOT_KEY}"},
        ) as api,
    ):
        yield Stack(api, app, settings, mock, s3, tmp_path)
    await mock.aclose()


__all__ = ["DAY_MS", "NOW", "ROOT_KEY", "FakeS3", "Stack", "ev", "json"]
