"""An uncertain durable model submission must not hide another paid POST."""
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from threading import Barrier, BoundedSemaphore, Event, Lock

import httpx
import pytest

from adaptive_learning.llm import client as transport
from adaptive_learning.llm.client import (
    LLMConfig,
    LLMError,
    LLMTimeoutError,
    OpenAICompatibleClient,
    model_time_budget,
    no_ambiguous_model_retries,
)


def client(name, retries=2):
    return OpenAICompatibleClient(LLMConfig(
        "https://durable-submission.example.test/v1", "test-key", name, max_retries=retries,
    ))


def reply(url, *, text='{"ok":true}', status=200):
    return httpx.Response(status, request=httpx.Request("POST", url),
        headers={"Retry-After": "0"}, json={"choices": [{"message": {"content": text}}]})


@pytest.mark.parametrize("failure", ["timeout", "disconnect", "proxy", "invalid_json", "missing_fields"])
@pytest.mark.parametrize("deadline", [False, True])
def test_unknown_response_sends_only_once_even_with_retry_budget_and_deadline_worker(monkeypatch, failure, deadline):
    calls = []
    def post(self, url, **kwargs):
        calls.append(kwargs["json"])
        if failure == "timeout":
            raise httpx.ReadTimeout("response was lost", request=httpx.Request("POST", url))
        if failure == "disconnect":
            raise httpx.RemoteProtocolError("connection closed after submit")
        if failure == "proxy":
            raise httpx.ProxyError("upstream result unavailable")
        if failure == "missing_fields":
            return httpx.Response(200, request=httpx.Request("POST", url), json={})
        return reply(url, text='{"items":[')
    monkeypatch.setattr(httpx.Client, "post", post)
    model = client(f"fenced-{failure}-{deadline}")
    try:
        with no_ambiguous_model_retries(), model_time_budget(5) if deadline else nullcontext():
            with pytest.raises(LLMError):
                model.structured(system="Return JSON", user="Controlled transport test")
        assert len(calls) == 1
    finally:
        model.close()


def test_nested_context_restores_default_after_exception_without_changing_other_callers(monkeypatch):
    calls = Counter()
    def post(self, url, **kwargs):
        name = kwargs["json"]["model"]
        calls[name] += 1
        if calls[name] == 1:
            raise httpx.ReadError("reply lost")
        return reply(url)
    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr("adaptive_learning.llm.client.time.sleep", lambda _: None)
    fenced, default = client("nested-fenced"), client("after-context")
    try:
        with pytest.raises(LLMError), no_ambiguous_model_retries(), no_ambiguous_model_retries():
            fenced.structured(system="s", user="u")
        assert default.structured(system="s", user="u") == {"ok": True}
        assert calls == {"nested-fenced": 1, "after-context": 2}
    finally:
        fenced.close()
        default.close()


@pytest.mark.parametrize("status", [429, 500])
def test_explicit_retryable_http_response_still_uses_existing_policy(monkeypatch, status):
    calls = []
    def post(self, url, **kwargs):
        calls.append(1)
        return reply(url, status=status if len(calls) == 1 else 200)
    monkeypatch.setattr(httpx.Client, "post", post)
    model = client(f"known-http-{status}")
    try:
        with no_ambiguous_model_retries():
            assert model.structured(system="s", user="u") == {"ok": True}
        assert len(calls) == 2
    finally:
        model.close()


def test_concurrent_ordinary_calls_do_not_inherit_diagnostic_submission_fence(monkeypatch):
    first_posts = Barrier(2)
    lock, calls = Lock(), Counter()
    def post(self, url, **kwargs):
        name = kwargs["json"]["model"]
        with lock:
            calls[name] += 1
            count = calls[name]
        if count == 1:
            first_posts.wait(timeout=5)
            raise httpx.ReadError("reply lost")
        return reply(url)
    monkeypatch.setattr(httpx.Client, "post", post)
    monkeypatch.setattr("adaptive_learning.llm.client.time.sleep", lambda _: None)
    def run(fenced):
        model = client("concurrent-fenced" if fenced else "concurrent-default")
        try:
            with no_ambiguous_model_retries() if fenced else nullcontext():
                try:
                    return model.structured(system="s", user="u")
                except LLMError:
                    return "blocked"
        finally:
            model.close()
    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(run, True)
        assert pool.submit(run, False).result(timeout=10) == {"ok": True}
        assert future.result(timeout=10) == "blocked"
    assert calls == {"concurrent-fenced": 1, "concurrent-default": 2}


def test_draining_request_releases_the_slot_pool_that_admitted_it(monkeypatch):
    old, replacement = BoundedSemaphore(1), BoundedSemaphore(1)
    entered, release = Event(), Event()
    monkeypatch.setattr(transport, "_transport_slots", old)
    def pending():
        entered.set()
        assert release.wait(timeout=5)
        return {"late": True}
    try:
        with pytest.raises(LLMTimeoutError) as caught:
            transport._request_before_deadline(pending, time.monotonic() + .5)
        assert entered.is_set()
        assert caught.value._draining_future is not None
        assert replacement.acquire(blocking=False)
        monkeypatch.setattr(transport, "_transport_slots", replacement)
        release.set()
        assert old.acquire(timeout=2), "The original pool must recover its owned slot after draining"
        old.release()
        assert not replacement.acquire(blocking=False), "A late result must not release an unrelated pool"
        replacement.release()
    finally:
        release.set()
