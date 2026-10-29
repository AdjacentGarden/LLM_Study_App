"""Production client integration tests using offline HTTP and SDK transports.

This file is self-contained and can be placed in backend/tests. It imports the
production module directly, with no draft implementation or external fixture.
All waiting and transport are faked; no model endpoint is contacted.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from types import SimpleNamespace

import httpx
import pytest

from adaptive_learning.llm import client as llm
from adaptive_learning.llm.model_admission import ModelAdmission


class FakeClock:
    def __init__(self):
        self.now = 100.0
        self.wall = 1_800_000_000.0
        self.sleeps = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.wall

    def sleep(self, delay):
        assert 0 <= delay <= 30
        self.sleeps.append(delay)
        self.now += delay
        self.wall += delay


class FakeTransport:
    def __init__(self, protocol, monkeypatch):
        self.protocol = protocol
        self.monkeypatch = monkeypatch
        self.clock = FakeClock()
        self.client = None
        self.calls = []
        self.events = []
        # Do not replace global time functions used by threading or pytest.
        monkeypatch.setattr(llm, "time", self.clock)

    def install(self, events, *, max_retries=1):
        self.events = list(events)
        self.client = llm.OpenAICompatibleClient(llm.LLMConfig(
            base_url="https://pucoding.com/v1" if self.protocol == "sdk" else "https://offline.test/v1",
            api_key="offline-test-key",
            model="gpt-offline-test" if self.protocol == "sdk" else "offline-model",
            max_retries=max_retries,
        ))
        if self.protocol == "sdk":
            assert self.client._responses_sdk.max_retries == 0
            self.monkeypatch.setattr(self.client._responses_sdk.responses, "create", self.sdk)
        else:
            self.monkeypatch.setattr(self.client._http, "post", self.http)
        return self

    def next_event(self, kwargs):
        self.calls.append(kwargs)
        assert self.events, "unexpected extra attempt"
        event = self.events.pop(0)
        if isinstance(event, BaseException):
            raise event
        return event

    @staticmethod
    def response(event):
        return httpx.Response(
            event.get("status", 200), headers=event.get("headers", {}),
            json=event["body"], request=httpx.Request("POST", "https://offline.test/v1/model"),
        )

    def http(self, *args, **kwargs):
        return self.response(self.next_event(kwargs))

    def sdk(self, **kwargs):
        event = self.next_event(kwargs)
        response = self.response(event)
        if response.status_code >= 400:
            raise llm.APIStatusError("fake upstream status", response=response, body=event["body"])
        return SimpleNamespace(model_dump=lambda **_: event["body"])

    def success(self, text='{"ok":true}', **extra):
        if self.protocol == "sdk":
            body = {
                "status": "completed", "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": text}
                ]}],
            }
        else:
            body = {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}
        return {"body": {**body, **extra}}

    @staticmethod
    def error(status=503, *, headers=None, body=None):
        return {"status": status, "headers": headers or {},
                "body": body if body is not None else {"error": {"message": "busy"}}}

    def run(self):
        return self.client.structured(system="offline system", user="offline data")


@pytest.fixture(params=["http", "sdk"])
def transport(request, monkeypatch):
    value = FakeTransport(request.param, monkeypatch)
    # Each parameter is an independent process-local route history. A previous
    # final overload must not preempt this case's retry/deadline assertions.
    monkeypatch.setattr(llm, "admission", ModelAdmission(value.clock.monotonic))
    yield value
    if value.client is not None:
        value.client.close()


@pytest.mark.parametrize("status", [429, 503])
def test_retryable_status_respects_one_second_header(transport, status):
    transport.install([transport.error(status, headers={"Retry-After": "1"}), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [1]
    assert len(transport.calls) == 2
    assert transport.client.config.max_retries == 1


@pytest.mark.parametrize("nested", [True, False])
def test_actual_503_json_metadata_is_respected(transport, nested):
    body = {"error": {"message": "overloaded", "retry_after_seconds": 1}} if nested else {
        "error": {"message": "overloaded"}, "retry_after_seconds": 1
    }
    transport.install([transport.error(body=body), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [1]
    assert len(transport.calls) == 2


def test_http_date_uses_wall_clock(transport):
    date = format_datetime(datetime.fromtimestamp(transport.clock.wall + 7, UTC), usegmt=True)
    transport.install([transport.error(headers={"Retry-After": date}), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [7]


@pytest.mark.parametrize(
    "header,nested,top",
    [("NaN", None, None), ("-1", -1, -1),
     ("invalid date", True, False), ("Infinity", float("nan"), float("inf"))],
)
def test_invalid_negative_nonfinite_and_bool_hints_keep_fixed_backoff(transport, header, nested, top):
    transport.install([transport.error(headers={"Retry-After": header}, body={
        "error": {"retry_after_seconds": nested}, "retry_after_seconds": top
    }), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [0.25]


@pytest.mark.parametrize("location", ["header", "nested"])
def test_large_valid_hint_is_capped_at_thirty_seconds(transport, location):
    error = transport.error(headers={"Retry-After": "999999"}) if location == "header" else (
        transport.error(body={"error": {"retry_after_seconds": 999999}})
    )
    transport.install([error, transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [30]


@pytest.mark.parametrize("remaining", [0.5, 1])
def test_insufficient_or_equal_deadline_does_not_sleep_or_resend(transport, remaining):
    transport.install([transport.error(headers={"Retry-After": "1"}), transport.success()])
    with llm.model_time_budget(remaining), pytest.raises(llm.LLMTimeoutError, match="time budget"):
        transport.run()
    assert transport.clock.sleeps == []
    assert len(transport.calls) == 1


def test_missing_hint_keeps_original_delays_and_attempt_budget(transport):
    transport.install([transport.error(), transport.error(), transport.success()], max_retries=2)
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [0.25, 0.5]
    assert len(transport.calls) == 3
    assert transport.client.config.max_retries == 2


@pytest.mark.parametrize("max_retries", [0, 1])
def test_final_error_never_sleeps_or_exceeds_retry_budget(transport, max_retries):
    transport.install([
        transport.error(headers={"Retry-After": "1"}) for _ in range(max_retries + 1)
    ], max_retries=max_retries)
    with pytest.raises(llm.LLMError):
        transport.run()
    assert len(transport.calls) == max_retries + 1
    assert transport.clock.sleeps == ([] if max_retries == 0 else [1])


@pytest.mark.parametrize("status", [400, 403])
def test_hint_cannot_make_nonretryable_status_retryable(transport, status):
    transport.install([transport.error(status, headers={"Retry-After": "1"})], max_retries=2)
    with pytest.raises(llm.LLMError):
        transport.run()
    assert len(transport.calls) == 1
    assert transport.clock.sleeps == []


def test_provider_content_refusal_is_not_retried(transport):
    transport.install([transport.error(403, headers={"Retry-After": "1"}, body={
        "error": {"code": "content_policy_violation", "message": "content policy rejected"}
    })], max_retries=2)
    with pytest.raises(llm.LLMRefusalError):
        transport.run()
    assert len(transport.calls) == 1
    assert transport.clock.sleeps == []


def test_response_content_refusal_is_not_retried(transport):
    response = transport.success(retry_after_seconds=4)
    if transport.protocol == "sdk":
        response["body"]["output"][0]["content"] = [{"type": "refusal", "refusal": "no"}]
    else:
        response["body"]["choices"][0]["message"]["refusal"] = "no"
    transport.install([response], max_retries=2)
    with pytest.raises(llm.LLMRefusalError):
        transport.run()
    assert len(transport.calls) == 1
    assert transport.clock.sleeps == []


def test_parse_failure_keeps_original_retry_delay(transport):
    transport.install([transport.success("{", retry_after_seconds=6), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [0.25]
    assert len(transport.calls) == 2


def test_unknown_transport_timeout_never_replays_a_submission(transport):
    request = httpx.Request("POST", "https://offline.test/v1/model")
    error = (llm.APITimeoutError(request=request) if transport.protocol == "sdk"
             else httpx.ReadTimeout("fake timeout", request=request))
    transport.install([error, transport.success()])
    with pytest.raises(llm.LLMTimeoutError):
        transport.run()
    assert transport.clock.sleeps == []
    assert len(transport.calls) == 1


def test_previous_hint_does_not_leak_into_next_parse_failure(transport):
    transport.install([
        transport.error(headers={"Retry-After": "4"}),
        transport.success("{", retry_after_seconds=9), transport.success()
    ], max_retries=2)
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [4, 0.5]
    assert len(transport.calls) == 3


def test_unknown_exception_does_not_add_a_retry(transport):
    transport.install([RuntimeError("unexpected fake transport failure")], max_retries=2)
    with pytest.raises(RuntimeError, match="unexpected fake transport failure"):
        transport.run()
    assert len(transport.calls) == 1
    assert transport.clock.sleeps == []


def test_valid_header_has_priority_over_nested_and_root_hints(transport):
    transport.install([transport.error(headers={"Retry-After": "3"}, body={
        "error": {"retry_after_seconds": 4}, "retry_after_seconds": 5
    }), transport.success()])
    assert transport.run() == {"ok": True}
    assert transport.clock.sleeps == [3]
