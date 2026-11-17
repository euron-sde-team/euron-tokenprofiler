"""One error shape for the whole API: ``{"error": {"message", "type", "code", "request_id"}}``."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse

TYPES = {
    400: "invalid_request_error", 401: "authentication_error", 403: "permission_error", 404: "not_found_error", 409: "conflict_error",
    413: "invalid_request_error", 422: "invalid_request_error", 429: "rate_limit_error", 502: "upstream_error", 503: "unavailable_error",
}  # fmt: skip


class ApiError(Exception):
    def __init__(self, status: int, code: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.extra = status, code, message, extra


def error_response(status: int, code: str, message: str, request: Request, **extra: Any) -> JSONResponse:
    body = {
        "message": message,
        "type": TYPES.get(status, "api_error"),
        "code": code,
        "request_id": getattr(request.state, "request_id", None),
        **extra,
    }
    return JSONResponse(
        status_code=status,
        content={"error": body},
        headers={"Retry-After": "5"} if status in (429, 503) else None,
    )
