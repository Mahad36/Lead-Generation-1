"""DNS fallback for networks whose resolver fails on some domains.

Some home routers / ISPs answer "host not found" (Windows error 11002) for perfectly valid business domains, which made
most website checks come back empty. `install()` wraps `socket.getaddrinfo`: the system resolver is always tried first;
only when it fails is the name looked up over HTTPS at Cloudflare (1.1.1.1), then Google (8.8.8.8). Answers are cached
for a few minutes. Every library in the process (httpx, smtplib, ...) benefits, and nothing changes when DNS works.
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import threading
import time
import urllib.request

from app.logging_config import get_logger

logger = get_logger("dns_fallback")

RESOLVERS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/resolve")
CACHE_SECONDS = 600
FAILED_CACHE_SECONDS = 60

_original_getaddrinfo = socket.getaddrinfo
_cache: dict[str, tuple[float, list[str]]] = {}
_lock = threading.Lock()
_installed = False


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def resolve_over_https(host: str) -> list[str]:
    """IPv4 addresses for `host` from public DNS-over-HTTPS resolvers (cached)."""
    now = time.time()
    with _lock:
        cached = _cache.get(host)
        if cached and cached[0] > now:
            return cached[1]
    addresses: list[str] = []
    for resolver in RESOLVERS:
        try:
            request = urllib.request.Request(
                f"{resolver}?name={host}&type=A", headers={"accept": "application/dns-json"}
            )
            with urllib.request.urlopen(request, timeout=5, context=ssl.create_default_context()) as response:
                answer = json.loads(response.read().decode())
            addresses = [a["data"] for a in answer.get("Answer", []) if a.get("type") == 1]
            if addresses:
                break
        except Exception:
            continue
    with _lock:
        _cache[host] = (now + (CACHE_SECONDS if addresses else FAILED_CACHE_SECONDS), addresses)
    return addresses


def _getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
    try:
        return _original_getaddrinfo(host, port, family, type, proto, flags)
    except socket.gaierror:
        name = host.decode() if isinstance(host, bytes) else host
        if not name or _is_ip(name) or family == socket.AF_INET6 or "." not in name:
            raise
        addresses = resolve_over_https(name)
        if not addresses:
            raise
        results = []
        for address in addresses:
            results.extend(_original_getaddrinfo(address, port, socket.AF_INET, type, proto, flags))
        return results


def install() -> None:
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _getaddrinfo
    _installed = True
    logger.info("dns_fallback_installed")
