"""Webhook targets for alerts. A webhook is a server-side request to a user-supplied URL, so it gets the same guard as any
other SSRF-prone fetch: HTTPS only (HTTP only when private targets are allowed), no embedded credentials, and every resolved
address must be public, checked again at delivery time so DNS cannot be pointed at an internal host after validation."""

from __future__ import annotations

import ipaddress
import socket
from collections.abc import Callable
from urllib.parse import urlparse

Resolver = Callable[[str], list[str]]


class WebhookError(ValueError):
    pass


def _resolve(host: str) -> list[str]:
    try:
        return sorted({str(i[4][0]) for i in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)})
    except OSError:
        return []


def _private(ip: str) -> bool:
    a = ipaddress.ip_address(ip)
    return (
        a.is_private
        or a.is_loopback
        or a.is_link_local
        or a.is_reserved
        or a.is_multicast
        or a.is_unspecified
    )


def check_url(
    url: str, *, allow_private: bool = False, resolve: Resolver | None = None, lookup: bool = True
) -> str:
    """Returns the URL if it may be called. ``lookup=False`` checks only the shape (used when a budget is saved)."""
    u = urlparse(url)
    if u.scheme not in ("https", "http"):
        raise WebhookError("webhook URLs must start with https://")
    if u.scheme == "http" and not allow_private:
        raise WebhookError("plain http:// webhooks are not allowed")
    if not u.hostname:
        raise WebhookError("the webhook URL has no host")
    if u.username or u.password:
        raise WebhookError("credentials in webhook URLs are not allowed")
    if lookup and not allow_private:
        addrs = (resolve or _resolve)(u.hostname)
        if not addrs:
            raise WebhookError(f"{u.hostname} could not be resolved")
        bad = [a for a in addrs if _private(a)]
        if bad:
            raise WebhookError(
                f"{u.hostname} resolves to a non-public address ({bad[0]}): refusing to call it"
            )
    return url
