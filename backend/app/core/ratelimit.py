from __future__ import annotations

from collections import OrderedDict
import secrets
from threading import Lock
from time import monotonic
from typing import Callable

from app.core.config import get_settings
from app.core.errors import AppError
from app.core.logging import get_logger
from fastapi import Request, Response
from fastapi.responses import JSONResponse


class _Bucket:
    __slots__ = ("capacity", "refill_per_sec", "tokens", "last_refill", "last_seen")

    def __init__(self, capacity: float, refill_per_sec: float) -> None:
        self.capacity = float(capacity)
        self.refill_per_sec = float(refill_per_sec)
        self.tokens = float(capacity)
        self.last_refill = monotonic()
        self.last_seen = self.last_refill

    def take(self, now: float) -> bool:
        elapsed = max(0.0, now - self.last_refill)
        self.last_seen = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_per_sec)
        self.last_refill = now
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False

    def seconds_until_token(self) -> float:
        if self.tokens >= 1.0:
            return 0.0
        return min(1.0, (1.0 - self.tokens) / self.refill_per_sec)


class RateLimiter:
    def __init__(self) -> None:
        self._lock = Lock()
        self._buckets: OrderedDict[tuple[str, str, str], _Bucket] = OrderedDict()
        self._capacity_for_global = 0.0
        self._refill_for_global = 0.0
        self._capacity_for_write = 0.0
        self._refill_for_write = 0.0

    def configure(self, global_per_minute: int, write_per_minute: int) -> None:
        self._capacity_for_global = max(1.0, float(global_per_minute))
        self._refill_for_global = self._capacity_for_global / 60.0
        self._capacity_for_write = max(1.0, float(write_per_minute))
        self._refill_for_write = self._capacity_for_write / 60.0

    def _key(self, request: Request, scope: str) -> tuple[str, str, str]:
        client = request.client.host if request.client else "unknown"
        # This middleware runs before authentication. Never partition the
        # budget by a caller-controlled identity header: rotating that value
        # would mint a fresh bucket for every request.
        settings = get_settings()
        proxy_token = request.headers.get("X-BookCourse-Proxy-Token", "")
        trusted_client = request.headers.get("X-BookCourse-Client-Id", "").strip()
        if (
            settings.environment == "production"
            and trusted_client
            and settings.trusted_proxy_token
            and proxy_token
            and secrets.compare_digest(proxy_token, settings.trusted_proxy_token)
        ):
            # The production edge overwrites this header from its authenticated
            # session/IP. It is accepted only alongside the unforgeable proxy
            # token, so users behind one reverse proxy do not share one bucket.
            return (trusted_client[:256], "trusted_proxy", scope)
        return (client, "peer", scope)

    def _bucket(
        self,
        key: tuple[str, str, str],
        capacity: float,
        refill: float,
        *,
        now: float,
        max_buckets: int,
        ttl_seconds: int,
    ) -> _Bucket:
        bucket = self._buckets.get(key)
        if bucket is not None:
            self._buckets.move_to_end(key)
            return bucket
        # Remove expired identities before enforcing the hard LRU ceiling.
        while self._buckets:
            oldest_key, oldest = next(iter(self._buckets.items()))
            if now - oldest.last_seen <= ttl_seconds:
                break
            self._buckets.pop(oldest_key, None)
        while len(self._buckets) >= max_buckets:
            self._buckets.popitem(last=False)
        bucket = _Bucket(capacity, refill)
        self._buckets[key] = bucket
        return bucket

    def check(self, request: Request) -> tuple[bool, int]:
        settings = get_settings()
        self.configure(settings.global_rate_per_minute, settings.write_rate_per_minute)
        method = request.method.upper()
        now = monotonic()
        with self._lock:
            if method in {"POST", "PATCH", "PUT", "DELETE"}:
                key = self._key(request, "write")
                bucket = self._bucket(
                    key,
                    self._capacity_for_write,
                    self._refill_for_write,
                    now=now,
                    max_buckets=settings.rate_limit_max_buckets,
                    ttl_seconds=settings.rate_limit_bucket_ttl_seconds,
                )
            else:
                key = self._key(request, "read")
                bucket = self._bucket(
                    key,
                    self._capacity_for_global,
                    self._refill_for_global,
                    now=now,
                    max_buckets=settings.rate_limit_max_buckets,
                    ttl_seconds=settings.rate_limit_bucket_ttl_seconds,
                )
            allowed = bucket.take(now)
            if not allowed:
                retry_after = max(1, int(bucket.seconds_until_token()) + 1)
                return False, retry_after
            return True, 0

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {"bucket_count": len(self._buckets)}


rate_limiter = RateLimiter()

WRITE_METHODS = {"POST", "PATCH", "PUT", "DELETE"}
_logger = get_logger("app.ratelimit")


async def rate_limit_middleware(request: Request, call_next: Callable[[Request], Any]) -> Response:
    path = request.url.path
    if path == "/api/health" or not path.startswith("/api"):
        return await call_next(request)
    allowed, retry_after = rate_limiter.check(request)
    if not allowed:
        rid = request.headers.get("X-Request-Id", "")
        _logger.warning(
            "rate_limited",
            extra={
                "event": "rate_limited",
                "code": "rate_limited",
                "path": path,
                "method": request.method,
                "retry_after": retry_after,
            },
        )
        response = JSONResponse(
            status_code=429,
            content={
                "code": "rate_limited",
                "message": "请求过于频繁，请稍后再试",
                "details": {"retry_after": retry_after},
            },
        )
        response.headers["Retry-After"] = str(retry_after)
        response.headers["X-Request-Id"] = rid or ""
        return response
    return await call_next(request)
