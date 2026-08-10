from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.core.ai_runtime import AIProviderRuntime, AIRuntimePolicy
from app.core.errors import AppError


class _Response:
    headers: dict[str, str] = {}

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, _limit: int | None = None) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _policy(**updates) -> AIRuntimePolicy:
    values = {
        "max_concurrent": 1,
        "queue_timeout_seconds": 0.1,
        "max_retries": 2,
        "retry_backoff_seconds": 0,
        "retry_max_backoff_seconds": 0,
        "retry_transport_errors": False,
        "circuit_failure_threshold": 2,
        "circuit_reset_seconds": 60,
        "max_response_bytes": 4096,
        "cache_enabled": True,
        "cache_ttl_seconds": 60,
        "cache_max_items": 4,
    }
    values.update(updates)
    return AIRuntimePolicy(**values)


def test_runtime_retries_transient_http_and_reports_metrics(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise urllib.error.HTTPError(request.full_url, 429, "busy", {}, None)
        return _Response({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    runtime = AIProviderRuntime("test", _policy())
    result = runtime.post_json(
        api_url="https://example.test/chat",
        api_key="secret",
        payload={"model": "test"},
        timeout_seconds=1,
        error_prefix="test_ai",
    )

    assert result == {"ok": True}
    assert calls == 2
    snapshot = runtime.snapshot()
    assert snapshot["successes"] == 1
    assert snapshot["retries"] == 1
    assert snapshot["circuit_state"] == "closed"


def test_runtime_cache_avoids_duplicate_provider_calls(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        return _Response({"answer": "cached"})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    runtime = AIProviderRuntime("test", _policy())
    kwargs = {
        "api_url": "https://example.test/chat",
        "api_key": "secret",
        "payload": {"model": "test"},
        "timeout_seconds": 1,
        "error_prefix": "test_ai",
        "cache_key": "same-request",
    }

    assert runtime.post_json(**kwargs) == {"answer": "cached"}
    assert runtime.post_json(**kwargs) == {"answer": "cached"}
    assert calls == 1
    assert runtime.snapshot()["cache_hits"] == 1


def test_runtime_opens_circuit_after_repeated_failures(monkeypatch) -> None:
    calls = 0

    def fake_urlopen(request, timeout):
        nonlocal calls
        calls += 1
        raise urllib.error.HTTPError(request.full_url, 400, "bad request", {}, None)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    runtime = AIProviderRuntime("test", _policy(max_retries=0, circuit_failure_threshold=2))
    kwargs = {
        "api_url": "https://example.test/chat",
        "api_key": "secret",
        "payload": {"model": "test"},
        "timeout_seconds": 1,
        "error_prefix": "test_ai",
    }

    with pytest.raises(AppError):
        runtime.post_json(**kwargs)
    with pytest.raises(AppError):
        runtime.post_json(**kwargs)
    with pytest.raises(AppError) as exc_info:
        runtime.post_json(**kwargs)

    assert exc_info.value.code == "test_ai_circuit_open"
    assert calls == 2
    assert runtime.snapshot()["circuit_state"] == "open"
