from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
from email.message import Message
from hashlib import sha256
import json
import random
import threading
import time
from typing import Any
import urllib.error
import urllib.request

from app.core.config import get_settings
from app.core.errors import AppError


_TRANSIENT_HTTP_STATUSES = {408, 409, 425, 429, 500, 502, 503, 504}


class _RejectRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Never forward provider credentials to a redirect destination."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_NO_REDIRECT_OPENER = urllib.request.build_opener(_RejectRedirectHandler())


@dataclass(frozen=True)
class AIRuntimePolicy:
    max_concurrent: int = 4
    queue_timeout_seconds: float = 5.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    retry_max_backoff_seconds: float = 8.0
    retry_transport_errors: bool = False
    circuit_failure_threshold: int = 5
    circuit_reset_seconds: float = 30.0
    max_response_bytes: int = 2 * 1024 * 1024
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300
    cache_max_items: int = 256


@dataclass
class _CacheEntry:
    expires_at: float
    value: dict[str, Any]


class AIProviderRuntime:
    """Bounded, observable execution policy for OpenAI-compatible providers.

    The runtime deliberately retries only explicit transient HTTP responses by
    default.  Retrying an ambiguous POST transport failure can duplicate a
    billable generation, so that behaviour requires an explicit operator flag.
    """

    def __init__(self, provider: str, policy: AIRuntimePolicy) -> None:
        self.provider = provider
        self.policy = policy
        self._semaphore = threading.BoundedSemaphore(policy.max_concurrent)
        self._lock = threading.Lock()
        self._cache: OrderedDict[str, _CacheEntry] = OrderedDict()
        self._failure_count = 0
        self._circuit_opened_at: float | None = None
        self._half_open_in_flight = False
        self._metrics: dict[str, int | float] = {
            "requests": 0,
            "successes": 0,
            "failures": 0,
            "retries": 0,
            "cache_hits": 0,
            "rejected": 0,
            "in_flight": 0,
            "total_latency_ms": 0.0,
        }

    @staticmethod
    def fingerprint(*parts: Any) -> str:
        encoded = json.dumps(parts, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        return sha256(encoded.encode("utf-8")).hexdigest()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            metrics = dict(self._metrics)
            successes = int(metrics["successes"])
            metrics["average_latency_ms"] = round(
                float(metrics["total_latency_ms"]) / successes if successes else 0.0,
                2,
            )
            metrics["circuit_state"] = self._circuit_state_locked(time.monotonic())
            metrics["consecutive_failures"] = self._failure_count
            metrics["cache_items"] = len(self._cache)
            metrics["provider"] = self.provider
            return metrics

    def post_json(
        self,
        *,
        api_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        error_prefix: str,
        cache_key: str | None = None,
    ) -> dict[str, Any]:
        started = time.monotonic()
        if cache_key and (cached := self._cache_get(cache_key)) is not None:
            return cached

        half_open_probe = self._enter_circuit(error_prefix)
        acquired = self._semaphore.acquire(timeout=min(timeout_seconds, self.policy.queue_timeout_seconds))
        if not acquired:
            with self._lock:
                self._metrics["rejected"] += 1
                if half_open_probe:
                    self._half_open_in_flight = False
            raise AppError(
                f"{error_prefix}_busy",
                "AI provider concurrency limit reached",
                details={"provider": self.provider, "retryable": True},
                status_code=503,
            )

        with self._lock:
            self._metrics["requests"] += 1
            self._metrics["in_flight"] += 1
        try:
            body = self._request_with_retries(
                api_url=api_url,
                api_key=api_key,
                payload=payload,
                timeout_seconds=timeout_seconds,
                error_prefix=error_prefix,
            )
            if cache_key:
                self._cache_put(cache_key, body)
            with self._lock:
                self._failure_count = 0
                self._circuit_opened_at = None
                self._half_open_in_flight = False
                self._metrics["successes"] += 1
            return body
        except AppError:
            self._record_failure()
            raise
        finally:
            elapsed_ms = (time.monotonic() - started) * 1000
            with self._lock:
                self._metrics["in_flight"] -= 1
                self._metrics["total_latency_ms"] += elapsed_ms
            self._semaphore.release()

    def _request_with_retries(
        self,
        *,
        api_url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: float,
        error_prefix: str,
    ) -> dict[str, Any]:
        encoded = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request_id = sha256(encoded).hexdigest()
        last_error: Exception | None = None
        for attempt in range(self.policy.max_retries + 1):
            request = urllib.request.Request(
                api_url,
                data=encoded,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "X-Request-Id": request_id,
                },
                method="POST",
            )
            try:
                with _NO_REDIRECT_OPENER.open(request, timeout=timeout_seconds) as response:
                    raw = self._read_bounded(response)
                parsed = json.loads(raw.decode("utf-8"))
                if not isinstance(parsed, dict):
                    raise ValueError("provider response root must be an object")
                return parsed
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in _TRANSIENT_HTTP_STATUSES or attempt >= self.policy.max_retries:
                    raise AppError(
                        f"{error_prefix}_http_error",
                        "AI provider returned an error",
                        details={"provider": self.provider, "status": exc.code, "attempts": attempt + 1},
                        status_code=502,
                    ) from exc
                self._retry_wait(attempt, exc.headers)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if not self.policy.retry_transport_errors or attempt >= self.policy.max_retries:
                    raise AppError(
                        f"{error_prefix}_request_failed",
                        "AI provider request failed",
                        details={"provider": self.provider, "attempts": attempt + 1},
                        status_code=502,
                    ) from exc
                self._retry_wait(attempt)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
                raise AppError(
                    f"{error_prefix}_invalid_response",
                    "AI provider returned an invalid JSON response",
                    details={"provider": self.provider},
                    status_code=502,
                ) from exc
        raise AppError(
            f"{error_prefix}_request_failed",
            "AI provider request failed",
            details={"provider": self.provider},
            status_code=502,
        ) from last_error

    def _read_bounded(self, response: Any) -> bytes:
        content_length = getattr(response, "headers", {}).get("Content-Length") if getattr(response, "headers", None) else None
        if content_length:
            try:
                if int(content_length) > self.policy.max_response_bytes:
                    raise AppError("ai_response_too_large", "AI provider response exceeded the configured limit", status_code=502)
            except ValueError:
                pass
        try:
            raw = response.read(self.policy.max_response_bytes + 1)
        except TypeError:  # Small test doubles and a few compatible clients expose read() only.
            raw = response.read()
        if len(raw) > self.policy.max_response_bytes:
            raise AppError("ai_response_too_large", "AI provider response exceeded the configured limit", status_code=502)
        return raw

    def _retry_wait(self, attempt: int, headers: Message | None = None) -> None:
        retry_after = None
        if headers is not None:
            raw = headers.get("Retry-After")
            if raw:
                try:
                    retry_after = max(0.0, float(raw))
                except ValueError:
                    retry_after = None
        backoff = min(
            self.policy.retry_max_backoff_seconds,
            self.policy.retry_backoff_seconds * (2**attempt),
        )
        delay = min(retry_after, self.policy.retry_max_backoff_seconds) if retry_after is not None else backoff * random.uniform(0.8, 1.2)
        with self._lock:
            self._metrics["retries"] += 1
        if delay > 0:
            time.sleep(delay)

    def _record_failure(self) -> None:
        with self._lock:
            self._metrics["failures"] += 1
            self._failure_count += 1
            self._half_open_in_flight = False
            if self._failure_count >= self.policy.circuit_failure_threshold:
                self._circuit_opened_at = time.monotonic()

    def _enter_circuit(self, error_prefix: str) -> bool:
        with self._lock:
            now = time.monotonic()
            state = self._circuit_state_locked(now)
            if state == "open" or (state == "half_open" and self._half_open_in_flight):
                self._metrics["rejected"] += 1
                raise AppError(
                    f"{error_prefix}_circuit_open",
                    "AI provider is temporarily unavailable",
                    details={"provider": self.provider, "retryable": True},
                    status_code=503,
                )
            if state == "half_open":
                self._half_open_in_flight = True
                return True
            return False

    def _circuit_state_locked(self, now: float) -> str:
        if self._circuit_opened_at is None:
            return "closed"
        if now - self._circuit_opened_at >= self.policy.circuit_reset_seconds:
            return "half_open"
        return "open"

    def _cache_get(self, key: str) -> dict[str, Any] | None:
        if not self.policy.cache_enabled or self.policy.cache_ttl_seconds <= 0:
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            if entry.expires_at <= now:
                self._cache.pop(key, None)
                return None
            self._cache.move_to_end(key)
            self._metrics["cache_hits"] += 1
            return json.loads(json.dumps(entry.value))

    def _cache_put(self, key: str, value: dict[str, Any]) -> None:
        if not self.policy.cache_enabled or self.policy.cache_ttl_seconds <= 0:
            return
        with self._lock:
            self._cache[key] = _CacheEntry(
                expires_at=time.monotonic() + self.policy.cache_ttl_seconds,
                value=json.loads(json.dumps(value)),
            )
            self._cache.move_to_end(key)
            while len(self._cache) > self.policy.cache_max_items:
                self._cache.popitem(last=False)


def policy_from_settings() -> AIRuntimePolicy:
    settings = get_settings()
    return AIRuntimePolicy(
        max_concurrent=settings.ai_max_concurrent,
        queue_timeout_seconds=settings.ai_queue_timeout_seconds,
        max_retries=settings.ai_max_retries,
        retry_backoff_seconds=settings.ai_retry_backoff_seconds,
        retry_max_backoff_seconds=settings.ai_retry_max_backoff_seconds,
        retry_transport_errors=settings.ai_retry_transport_errors,
        circuit_failure_threshold=settings.ai_circuit_failure_threshold,
        circuit_reset_seconds=settings.ai_circuit_reset_seconds,
        max_response_bytes=settings.ai_max_response_bytes,
        cache_enabled=settings.ai_cache_enabled,
        cache_ttl_seconds=settings.ai_cache_ttl_seconds,
        cache_max_items=settings.ai_cache_max_items,
    )


_runtime_lock = threading.Lock()
_runtimes: dict[tuple[str, str], AIProviderRuntime] = {}


def get_ai_runtime(provider: str) -> AIProviderRuntime:
    policy = policy_from_settings()
    signature = sha256(json.dumps(asdict(policy), sort_keys=True).encode("utf-8")).hexdigest()
    key = (provider, signature)
    with _runtime_lock:
        runtime = _runtimes.get(key)
        if runtime is None:
            runtime = AIProviderRuntime(provider, policy)
            for stale_key in [item for item in _runtimes if item[0] == provider and item != key]:
                _runtimes.pop(stale_key, None)
            _runtimes[key] = runtime
        return runtime


def ai_runtime_snapshots() -> list[dict[str, Any]]:
    with _runtime_lock:
        runtimes = list(_runtimes.values())
    return [runtime.snapshot() for runtime in runtimes]


__all__ = [
    "AIProviderRuntime",
    "AIRuntimePolicy",
    "ai_runtime_snapshots",
    "get_ai_runtime",
    "policy_from_settings",
]
