"""In-memory sliding-window rate limiting (per verified-user bucket or per client IP) as pure ASGI middleware.

The middleware never verifies a token: an unverified bearer string is only HASHED to make a bucket key, so a forged token gets
its own (small) bucket and cannot spend anyone else's. Single-process only; behind several workers each has its own counters.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Mapping
from typing import Any

# route class -> (method set, path regex)
ROUTE_CLASSES: dict[str, tuple[frozenset[str], re.Pattern[str]]] = {
    "upload": (frozenset({"POST"}), re.compile(r"/(uploads|base-model|clash/models)/?$")),
    "run": (frozenset({"POST"}), re.compile(r"/(run-rules|skills/[^/]+/run)/?$")),
    "share": (frozenset({"GET", "POST", "HEAD"}), re.compile(r"^/share(/|$)")),
    "auth": (frozenset({"GET", "POST", "PUT", "DELETE"}), re.compile(r"^/(invitations(/|$)|me(/|$))")),
}

# (max requests, window seconds); generous for normal use
DEFAULT_LIMITS: dict[str, tuple[int, float]] = {
    "upload": (30, 60.0),
    "run": (60, 60.0),
    "share": (120, 60.0),
    "auth": (120, 60.0),
}


class SlidingWindowLimiter:
    """Thread-safe sliding-window counter with bounded memory (least-recently-used keys are evicted past max_keys)."""

    def __init__(self, max_keys: int = 10_000, clock: Callable[[], float] = time.monotonic) -> None:
        self._hits: OrderedDict[str, deque[float]] = OrderedDict()
        self._max_keys = max_keys
        self._clock = clock
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: float) -> float:
        """Record a hit and return 0.0 if allowed, else the seconds to wait (the hit is not recorded when refused)."""
        now = self._clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                hits = self._hits[key] = deque()
            else:
                self._hits.move_to_end(key)
            while hits and now - hits[0] >= window:
                hits.popleft()
            if len(hits) >= limit:
                return max(hits[0] + window - now, 0.001)
            hits.append(now)
            while len(self._hits) > self._max_keys:
                self._hits.popitem(last=False)
            return 0.0

    def __len__(self) -> int:
        return len(self._hits)


def classify(method: str, path: str) -> str | None:
    for name, (methods, pattern) in ROUTE_CLASSES.items():
        if method in methods and pattern.search(path):
            return name
    return None


def client_key(headers: Mapping[str, str], peer: str | None) -> str:
    """Bearer token (hashed, unverified) when present, else the rightmost X-Forwarded-For hop, else the socket peer."""
    auth = headers.get("authorization", "")
    if auth[:7].lower() == "bearer " and auth[7:].strip():
        return "u:" + hashlib.sha256(auth[7:].strip().encode()).hexdigest()[:32]
    hops = [h.strip() for h in headers.get("x-forwarded-for", "").split(",") if h.strip()]
    return "ip:" + (hops[-1] if hops else (peer or "?"))


def limits_from_env(env: Mapping[str, str] | None = None) -> dict[str, tuple[int, float]]:
    """MEP_RATELIMIT_<CLASS>=<max>[/<window seconds>], e.g. MEP_RATELIMIT_UPLOAD=10/60. Bad values keep the default."""
    env = os.environ if env is None else env
    out = dict(DEFAULT_LIMITS)
    for name, (count, window) in DEFAULT_LIMITS.items():
        raw = env.get(f"MEP_RATELIMIT_{name.upper()}")
        if not raw:
            continue
        try:
            first, _, second = raw.partition("/")
            out[name] = (int(first), float(second) if second else window)
            if out[name][0] < 1 or out[name][1] <= 0:
                out[name] = (count, window)
        except ValueError:
            out[name] = (count, window)
    return out


class RateLimitMiddleware:
    def __init__(self, app: Any, limits: Mapping[str, tuple[int, float]] | None = None, limiter: SlidingWindowLimiter | None = None) -> None:
        self.app = app
        self.limits = dict(DEFAULT_LIMITS if limits is None else limits)
        self.limiter = limiter or SlidingWindowLimiter()

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        name = classify(scope["method"], scope["path"])
        if name is None or name not in self.limits:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        client = scope.get("client")
        key = f"{name}:{client_key(headers, client[0] if client else None)}"
        limit, window = self.limits[name]
        wait = self.limiter.check(key, limit, window)
        if wait == 0.0:
            await self.app(scope, receive, send)
            return
        body = json.dumps({"detail": {"code": "rate_limited", "message": "too many requests; slow down and retry shortly"}}).encode()
        await send({"type": "http.response.start", "status": 429, "headers": [
            (b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
            (b"retry-after", str(max(1, math.ceil(wait))).encode()), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})


def install(app: Any, limits: Mapping[str, tuple[int, float]] | None = None) -> bool:
    """Add the middleware unless MEP_RATELIMIT=off. Returns whether it was installed."""
    if os.environ.get("MEP_RATELIMIT", "").lower() in {"off", "0", "false"}:
        return False
    app.add_middleware(RateLimitMiddleware, limits=limits if limits is not None else limits_from_env())
    return True
