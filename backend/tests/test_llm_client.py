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
