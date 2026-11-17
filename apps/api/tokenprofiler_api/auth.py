"""API keys: ``tp_<prefix>_<secret>``, stored as a peppered HMAC, with scopes and an optional per-app restriction.

An ingest key limited to ``["support-bot"]`` can only write events for that app, and any read it is allowed is forced
to that app: application teams can see their own usage and nobody else's.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, Field

from .db import Database

ALL_SCOPES = ("ingest", "read", "budgets:write", "pricing:write", "admin")


class ApiKeyRecord(BaseModel):
    prefix: str
    name: str
    scopes: list[str]
    apps: list[str] = Field(default_factory=list, description="Empty means every app")
    created_at: float
    last_used_at: float | None = None
    revoked: bool = False


class ApiKeyCreated(ApiKeyRecord):
    key: str = Field(description="Shown once. Store it safely.")


class Principal(BaseModel):
    prefix: str
    name: str
    scopes: list[str]
    apps: list[str] = Field(default_factory=list)

    def can(self, scope: str) -> bool:
        return "*" in self.scopes or "admin" in self.scopes or scope in self.scopes

    def may_use_app(self, app: str) -> bool:
        return not self.apps or app in self.apps


class ApiKeyStore:
    def __init__(self, db: Database, pepper: str) -> None:
        self._db = db
        self._pepper = pepper.encode()

    def _digest(self, key: str) -> str:
        return hmac.new(self._pepper, key.encode(), hashlib.sha256).hexdigest()

    @staticmethod
    def prefix_of(key: str) -> str:
        parts = key.split("_", 2)
        return (
            parts[1] if len(parts) == 3 and parts[0] == "tp" else hashlib.sha256(key.encode()).hexdigest()[:8]
        )

    async def create(
        self, name: str, scopes: list[str], *, apps: list[str] | None = None, key: str | None = None
    ) -> ApiKeyCreated:
        for s in scopes:
            if s != "*" and s not in ALL_SCOPES:
                raise ValueError(f"unknown scope {s!r}")
        full = key or f"tp_{secrets.token_hex(4)}_{secrets.token_urlsafe(24)}"
        rec = ApiKeyRecord(
            prefix=self.prefix_of(full), name=name, scopes=scopes, apps=apps or [], created_at=time.time()
        )
        await self._db.execute(
            "INSERT INTO api_keys (prefix, name, digest, scopes, apps, created_at) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(prefix) DO UPDATE SET name=excluded.name, digest=excluded.digest, scopes=excluded.scopes, apps=excluded.apps, revoked=0",
            rec.prefix, name, self._digest(full), json.dumps(scopes), json.dumps(rec.apps), rec.created_at,
        )  # fmt: skip
        return ApiKeyCreated(**rec.model_dump(), key=full)

    async def verify(self, key: str) -> Principal | None:
        rows = await self._db.query(
            "SELECT name, digest, scopes, apps, revoked FROM api_keys WHERE prefix=?", self.prefix_of(key)
        )
        if not rows or not hmac.compare_digest(rows[0]["digest"], self._digest(key)) or rows[0]["revoked"]:
            return None
        r = rows[0]
        return Principal(
            prefix=self.prefix_of(key),
            name=r["name"],
            scopes=json.loads(r["scopes"]),
            apps=json.loads(r["apps"]),
        )

    async def touch(self, prefix: str) -> None:
        await self._db.execute("UPDATE api_keys SET last_used_at=? WHERE prefix=?", time.time(), prefix)

    async def list(self) -> list[ApiKeyRecord]:
        rows = await self._db.query(
            "SELECT prefix, name, scopes, apps, created_at, last_used_at, revoked FROM api_keys ORDER BY created_at"
        )
        return [ApiKeyRecord(prefix=r["prefix"], name=r["name"], scopes=json.loads(r["scopes"]), apps=json.loads(r["apps"]), created_at=r["created_at"], last_used_at=r["last_used_at"], revoked=bool(r["revoked"])) for r in rows]  # fmt: skip

    async def revoke(self, prefix: str) -> bool:
        return await self._db.execute("UPDATE api_keys SET revoked=1 WHERE prefix=?", prefix) > 0


class RateLimiter:
    """A fixed one-minute window per key, in-process."""

    def __init__(self, rpm: int) -> None:
        self.rpm = rpm
        self._windows: dict[str, tuple[int, int]] = {}

    def allow(self, key: str, now: float | None = None) -> bool:
        if self.rpm <= 0:
            return True
        minute = int((now if now is not None else time.time()) // 60)
        window, count = self._windows.get(key, (minute, 0))
        if window != minute:
            window, count = minute, 0
        if count >= self.rpm:
            self._windows[key] = (window, count)
            return False
        self._windows[key] = (window, count + 1)
        return True


def _extract_key(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


async def authenticate(
    request: Request,
    authorization: Annotated[str | None, Header()] = None,
    x_api_key: Annotated[str | None, Header()] = None,
) -> Principal:
    key = _extract_key(authorization, x_api_key)
    if not key:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, "missing API key (Authorization: Bearer <key> or X-API-Key)"
        )
    store: ApiKeyStore = request.app.state.keys
    principal = await store.verify(key)
    if principal is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid or revoked API key")
    request.state.principal = principal
    await store.touch(principal.prefix)
    return principal


def require(scope: str):  # type: ignore[no-untyped-def]
    async def dep(principal: Annotated[Principal, Depends(authenticate)]) -> Principal:
        if not principal.can(scope):
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"missing scope {scope!r}")
        return principal

    return dep
