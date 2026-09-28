from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest

from adaptive_learning.llm.client import LLMConfig, LLMError, OpenAICompatibleClient, model_time_budget


class FakeResponse:
    def __init__(self, *, status: int = 200, content: str = "{}") -> None:
        self.status_code = status
        self._content = content
        self.request = httpx.Request("POST", "https://example.test/chat/completions")

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "failed",
                request=self.request,
                response=self,  # type: ignore[arg-type]
            )

    def json(self) -> dict[str, Any]:
        return {"choices": [{"message": {"content": self._content}}]}


def _client(*, max_retries: int = 1) -> OpenAICompatibleClient:
    return OpenAICompatibleClient(
        LLMConfig(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
            max_retries=max_retries,
        )
    )


def test_structured_call_retries_truncated_json(monkeypatch: pytest.MonkeyPatch) -> None:
    responses: Iterator[FakeResponse] = iter(
        [FakeResponse(content='{"items": ['), FakeResponse(content='{"items": []}')]
    )
    calls = 0

    def fake_post(*_: object, **__: object) -> FakeResponse:
        nonlocal calls
        calls += 1
        return next(responses)

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    assert _client().structured(system="s", user="u") == {"items": []}
    assert calls == 2


def test_structured_call_uses_explicit_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_post(*_: object, **kwargs: object) -> FakeResponse:
        captured.update(kwargs)
        return FakeResponse(content='{"ok": true}')

    monkeypatch.setattr(httpx.Client, "post", fake_post)
    original_init = httpx.Client.__init__
    def capture_init(self: httpx.Client, **kwargs: Any) -> None:
        captured.update(kwargs)
        original_init(self, **kwargs)
    monkeypatch.setattr(httpx.Client, "__init__", capture_init)
    client = OpenAICompatibleClient(
        LLMConfig(
            base_url="https://example.test/v1",
            api_key="test-key",
            model="test-model",
            proxy_url="http://127.0.0.1:17897",
        )
    )

    assert client.structured(system="s", user="u") == {"ok": True}
    assert captured["proxy"] == "http://127.0.0.1:17897"


def test_structured_call_does_not_retry_non_retryable_401(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def fake_post(*_: object, **__: object) -> FakeResponse:
        nonlocal calls
        calls += 1
        return FakeResponse(status=401)

    monkeypatch.setattr(httpx.Client, "post", fake_post)

    with pytest.raises(LLMError, match="HTTPStatusError"):
        _client(max_retries=3).structured(system="s", user="u")
    assert calls == 1


@pytest.mark.parametrize(
    ("timeout", "retries"),
    [(0, 1), (60, -1)],
)
def test_invalid_retry_configuration_is_rejected(timeout: int, retries: int) -> None:
    with pytest.raises(ValueError):
        LLMConfig(
            base_url="https://example.test/v1",
            api_key="test",
            model="test",
            timeout_seconds=timeout,
            max_retries=retries,
        )


@pytest.mark.parametrize("content", [None, "", "[]", "not json"])
def test_bad_model_output_becomes_controlled_error(monkeypatch, content):
    monkeypatch.setattr(httpx.Client,"post",lambda *args,**kwargs: FakeResponse(content=content))
    with pytest.raises(LLMError):
        _client(max_retries=0).structured(system="s",user="u")


def test_reasoning_prelude_is_not_exposed_as_json_content() -> None:
    assert OpenAICompatibleClient._parse_object(
        '<think>internal reasoning that must stay hidden</think>\n{"ok": true}'
    ) == {"ok": True}


def test_connection_client_is_reused_and_can_be_closed(monkeypatch):
    client=_client(max_retries=0)
    instances=[]
    def post(self,*args,**kwargs):
        instances.append(id(self));return FakeResponse(content='{"ok":true}')
    monkeypatch.setattr(httpx.Client,"post",post)
    client.structured(system="s",user="1")
    client.structured(system="s",user="2")
    assert len(set(instances))==1
    client.close()
    assert client._http.is_closed


def test_nested_time_budgets_do_not_extend_deadline(monkeypatch):
    import time
    client=_client(max_retries=0)
    with model_time_budget(.001):
        time.sleep(.003)
        with model_time_budget(100):
            with pytest.raises(LLMError,match="time budget"):
                client.structured(system="s",user="u")
    captured={}
    def post(*args,**kwargs):
        captured.update(kwargs);return FakeResponse(content="{}")
    monkeypatch.setattr(httpx.Client,"post",post)
    with model_time_budget(2):
        client.structured(system="s",user="u")
    assert 0 < captured["timeout"].read <= 2


def test_deadline_returns_before_blocked_transport_and_discards_late_result(monkeypatch):
    import threading
    import time
    from adaptive_learning.llm.client import LLMTimeoutError
    release = threading.Event()
    completed = threading.Event()
    calls = []
    def post(*args, **kwargs):
        calls.append(1)
        try:
            release.wait(2)
            return FakeResponse(content='{"late": true}')
        finally:
            completed.set()
    monkeypatch.setattr(httpx.Client, "post", post)
    client = _client(max_retries=3)
    start = time.monotonic()
    try:
        with model_time_budget(.04), pytest.raises(LLMTimeoutError):
            client.structured(system="s", user="u")
        assert time.monotonic() - start < .3
        assert len(calls) == 1  # Timeout does not replay a potentially billed request.
    finally:
        release.set()
        assert completed.wait(1)
        client.close()


@pytest.mark.parametrize("model,body", [
    ("claude-test", {"stop_reason": "refusal", "content": []}),
    ("grok-test", {"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal", "refusal": "no"}]}]}),
    ("test-model", {"choices": [{"finish_reason": "content_filter", "message": {"content": None}}]}),
    ("test-model", {"choices": [{"message": {"content": None, "refusal": "no"}}]}),
])
def test_refusals_are_never_retried(monkeypatch, model, body):
    from adaptive_learning.llm.client import LLMRefusalError
    calls = []
    class Response(FakeResponse):
        def json(self):
            return body
    monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: calls.append(1) or Response())
    client = OpenAICompatibleClient(LLMConfig("https://example.test/v1", "test-key", model, max_retries=3))
    try:
        with pytest.raises(LLMRefusalError):
            client.structured(system="s", user="u")
        assert len(calls) == 1
    finally:
        client.close()


@pytest.mark.parametrize("code,expected_refusal", [("content_policy_violation", True), ("invalid_api_key", False)])
def test_http_403_content_refusal_is_not_confused_with_auth(monkeypatch, code, expected_refusal):
    from adaptive_learning.llm.client import LLMRefusalError
    class Response(FakeResponse):
        def json(self):
            return {"error": {"code": code, "message": "request rejected"}}
    calls = []
    monkeypatch.setattr(httpx.Client, "post", lambda *a, **k: calls.append(1) or Response(status=403))
    client = _client(max_retries=3)
    try:
        with pytest.raises(LLMError) as caught:
            client.structured(system="s", user="u")
        assert isinstance(caught.value, LLMRefusalError) == expected_refusal
        assert len(calls) == 1
    finally:
        client.close()


def test_expired_slot_admission_does_not_send_or_leak(monkeypatch):
    import time
    import adaptive_learning.llm.client as module
    calls = []
    class DelayedSlot:
        def acquire(self, **kwargs):
            time.sleep(.02)
            return True
        def release(self):
            calls.append("released")
    monkeypatch.setattr(module, "_transport_slots", DelayedSlot())
    with pytest.raises(module.LLMTimeoutError):
        module._request_before_deadline(lambda: calls.append("sent") or {}, time.monotonic() + .005)
    assert calls == ["released"]


def test_abandoned_transports_are_bounded_and_slots_recover(monkeypatch):
    import threading
    import time
    from concurrent.futures import ThreadPoolExecutor
    import adaptive_learning.llm.client as module
    slot = threading.BoundedSemaphore(2)
    release = threading.Event()
    calls = []
    monkeypatch.setattr(module, "_transport_slots", slot)
    def send():
        calls.append(1)
        release.wait(2)
        return {}
    def request():
        with pytest.raises(module.LLMTimeoutError):
            module._request_before_deadline(send, time.monotonic() + .04)
    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            list(pool.map(lambda _: request(), range(5)))
        assert len(calls) == 2
    finally:
        release.set()
    assert slot.acquire(timeout=1)
    assert slot.acquire(timeout=1)
    slot.release(); slot.release()
    assert module._request_before_deadline(lambda: {"ok": True}, time.monotonic() + 1) == {"ok": True}
