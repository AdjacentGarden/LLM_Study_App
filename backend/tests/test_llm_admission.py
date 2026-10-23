"""Actual production client admission, transport limits and outcome boundaries."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from adaptive_learning.llm import client as module
from adaptive_learning.llm.client import (
    LLMConfig,
    LLMCooldownError,
    LLMError,
    OpenAICompatibleClient,
)
from adaptive_learning.llm.model_admission import (
    AdmissionDenied,
    ModelAdmission,
    overload_wait,
    route_key,
)
from openai import OpenAI


class Clock:
    now = 100.0

    def __call__(self):
        return self.now


@pytest.fixture
def registry(monkeypatch):
    clock = Clock()
    gate = ModelAdmission(clock)
    monkeypatch.setattr(module, "admission", gate)
    return gate, clock


def client(handler, *, provider="deepseek", model="test-model", key="fake-test-key", retries=0):
    base = "https://pucoding.com/v1" if provider == "pucoding" else "https://api.deepseek.com/v1"
    obj = OpenAICompatibleClient(LLMConfig(base, key, model, max_retries=retries))
    obj._http.close()
    obj._http = httpx.Client(transport=httpx.MockTransport(handler))
    if provider == "pucoding":
        obj._responses_sdk = OpenAI(base_url=base, api_key=key, http_client=obj._http, max_retries=0)
    return obj


def success(provider="deepseek"):
    if provider == "pucoding":
        return httpx.Response(200, json={
            "id": "resp_fake", "object": "response", "status": "completed", "created_at": 1,
            "model": "gpt-6-sol", "output": [{"id": "msg_fake", "type": "message",
                "role": "assistant", "status": "completed", "content": [
                    {"type": "output_text", "text": '{"ok":true}', "annotations": []}]}],
        })
    return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok":true}'}}]})


@pytest.mark.parametrize("provider", ["deepseek", "pucoding"])
@pytest.mark.parametrize("status", [429, 503])
def test_final_known_overload_blocks_next_logical_call_and_fresh_client(registry, provider, status):
    sends = []

    def handler(request):
        sends.append(request)
        return httpx.Response(status, json={"error": {"retry_after_seconds": 1}})

    model = "gpt-6-sol" if provider == "pucoding" else "deepseek-flash"
    obj = client(handler, provider=provider, model=model)
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    with pytest.raises(LLMCooldownError) as rejected:
        obj.structured(system="test", user="test")
    assert rejected.value.retry_after_seconds == 30
    other = client(handler, provider=provider, model=model)
    with pytest.raises(LLMCooldownError):
        other.check_admission()
    assert len(sends) == 1
    obj.close()
    other.close()


@pytest.mark.parametrize("status", [400, 401, 402, 403, 422, 500, 502])
def test_other_known_http_status_does_not_create_overload_state(registry, status):
    obj = client(lambda request: httpx.Response(status, json={"error": {"message": "test"}}))
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    obj.check_admission()
    obj.close()


def test_retry_budget_unchanged_before_final_overload(registry, monkeypatch):
    sends, sleeps = [], []
    monkeypatch.setattr(module.time, "sleep", sleeps.append)

    def handler(request):
        sends.append(request)
        return httpx.Response(503, json={"error": {"retry_after_seconds": 1}})

    obj = client(handler, retries=1)
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    assert len(sends) == 2
    assert sleeps == [1]
    with pytest.raises(LLMCooldownError):
        obj.structured(system="test", user="test")
    assert len(sends) == 2
    obj.close()


def test_successful_existing_retry_does_not_trip(registry, monkeypatch):
    statuses = iter([503, 200])
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)
    obj = client(lambda request: httpx.Response(503) if next(statuses) == 503 else success(), retries=1)
    assert obj.structured(system="test", user="test") == {"ok": True}
    obj.check_admission()
    obj.close()


@pytest.mark.parametrize("error_type", [httpx.ReadError, httpx.ReadTimeout, httpx.WriteError])
def test_unknown_after_send_keeps_existing_zero_retry_and_does_not_claim_overload(registry, error_type):
    sends = []

    def handler(request):
        sends.append(request)
        raise error_type("simulated lost response", request=request)

    obj = client(handler, retries=0)
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    assert len(sends) == 1
    obj.check_admission()
    obj.close()


def test_unknown_automatic_retry_is_explicitly_a_remaining_baseline_risk(registry, monkeypatch):
    sends = []
    monkeypatch.setattr(module.time, "sleep", lambda delay: None)

    def handler(request):
        sends.append(request)
        raise httpx.ReadError("simulated post-send failure", request=request)

    obj = client(handler, retries=1)
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    assert len(sends) == 2  # Documented, not silently described as fixed by cooldown.
    obj.close()


def test_completed_malformed_json_is_not_an_overload(registry):
    obj = client(lambda request: httpx.Response(200, json={"choices": [
        {"message": {"content": "invalid JSON"}}]}))
    with pytest.raises(LLMError):
        obj.structured(system="test", user="test")
    obj.check_admission()
    obj.close()


def test_route_model_and_account_are_isolated(registry):
    gate, _ = registry
    key = route_key("https://pucoding.com/v1", "gpt-6-sol", "fake-a")
    gate.finish(gate.begin(key), overload=30)
    gate.check(route_key("https://api.deepseek.com/v1", "gpt-6-sol", "fake-a"))
    gate.check(route_key("https://pucoding.com/v1", "other-model", "fake-a"))
    gate.check(route_key("https://pucoding.com/v1", "gpt-6-sol", "fake-b"))


def test_half_open_real_call_single_flight_and_stale_success_cannot_clear_failure(registry):
    gate, clock = registry
    key = route_key("https://pucoding.com/v1", "gpt-6-sol", "fake")
    stale = gate.begin(key)
    gate.finish(gate.begin(key), overload=30)
    gate.finish(stale, completed=True)
    with pytest.raises(AdmissionDenied):
        gate.check(key)
    clock.now += 30
    gate.check(key)  # Pure preflight never reserves the recovery lease.
    barrier = threading.Barrier(12)

    def begin(_):
        barrier.wait(timeout=3)
        try:
            return gate.begin(key)
        except AdmissionDenied:
            return None

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(begin, range(12)))
    leases = [lease for lease in results if lease is not None]
    assert len(leases) == 1
    gate.finish(leases[0], completed=True)
    gate.check(key)


def test_unknown_recovery_does_not_declare_transport_healthy(registry):
    gate, clock = registry
    key = route_key("https://pucoding.com/v1", "gpt-6-sol", "fake")
    gate.finish(gate.begin(key), overload=30)
    clock.now += 30
    gate.finish(gate.begin(key))  # No new overload observation, no success.
    assert gate.begin(key).recovery is True


@pytest.mark.parametrize("recovery_status,expected_sends", [(200, 4), (503, 1)])
def test_real_four_review_siblings_wait_for_half_open_without_transport_slots(
    registry, monkeypatch, recovery_status, expected_sends
):
    gate, clock = registry
    monkeypatch.setattr(module, "_transport_slots", threading.BoundedSemaphore(8))
    key = route_key("https://pucoding.com/v1", "gpt-6-sol", "fake-test-key")
    gate.finish(gate.begin(key), overload=30)
    clock.now += 30
    first_entered, release_first = threading.Event(), threading.Event()
    lock = threading.Lock()
    sends = []

    def handler(request):
        with lock:
            sends.append(request)
            first = len(sends) == 1
        if first:
            first_entered.set()
            assert release_first.wait(timeout=3)
        if recovery_status == 503:
            return httpx.Response(503, json={"error": {"retry_after_seconds": 1}})
        return success("pucoding")

    obj = client(handler, provider="pucoding", model="gpt-6-sol")
    obj.check_admission()  # The course preflight does not consume the lease.
    original_begin = gate.begin
    barrier = threading.Barrier(4)

    def synchronized_begin(key, wait_seconds):
        barrier.wait(timeout=3)
        return original_begin(key, wait_seconds)

    monkeypatch.setattr(gate, "begin", synchronized_begin)
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(obj.structured, system="audit", user=str(i)) for i in range(4)]
        assert first_entered.wait(timeout=2)
        # The probe occupies exactly one real network slot; the waiting three
        # have not started transport and all seven other slots remain available.
        held = []
        try:
            for _ in range(7):
                assert module._transport_slots.acquire(blocking=False)
                held.append(True)
            assert not module._transport_slots.acquire(blocking=False)
        finally:
            for _ in held:
                module._transport_slots.release()
        release_first.set()
        results = []
        for future in futures:
            try:
                results.append(future.result(timeout=3))
            except LLMError as error:
                results.append(error)
    assert len(sends) == expected_sends
    if recovery_status == 200:
        assert results == [{"ok": True}] * 4
    else:
        assert all(isinstance(result, LLMError) for result in results)
        assert sum(isinstance(result, LLMCooldownError) for result in results) == 3
    obj.close()


def test_half_open_wait_has_own_timeout_without_sending(registry):
    gate, clock = registry
    key = route_key("https://pucoding.com/v1", "gpt-6-sol", "fake")
    gate.finish(gate.begin(key), overload=30)
    clock.now += 30
    lease = gate.begin(key)
    started = time.monotonic()
    with pytest.raises(AdmissionDenied):
        gate.begin(key, wait_seconds=.03)
    assert .02 <= time.monotonic() - started < 1
    gate.finish(lease, completed=True)
    gate.check(key)


@pytest.mark.parametrize("header,body,expected", [
    ({}, {}, 30), ({"Retry-After": "1"}, {}, 30), ({"Retry-After": "120"}, {}, 120),
    ({"Retry-After": "bad"}, {"error": {"retry_after_seconds": 40}}, 40),
    ({"Retry-After": "nan"}, {}, 30), ({"Retry-After": "-1"}, {}, 30),
    ({}, {"error": {"retry_after_seconds": True}}, 30),
    ({"Retry-After": "Thu, 01 Jan 1970 00:03:20 GMT"}, {}, 100),
])
def test_cooldown_preserves_valid_upstream_wait(header, body, expected):
    assert overload_wait(header, body, wall_time=100) == expected


def test_actual_twelve_unbudgeted_transports_never_exceed_eight(monkeypatch):
    monkeypatch.setattr(module, "_transport_slots", threading.BoundedSemaphore(8))
    release, eight_entered = threading.Event(), threading.Event()
    lock = threading.Lock()
    active = total = peak = 0

    def transport():
        nonlocal active, total, peak
        with lock:
            active += 1
            total += 1
            peak = max(peak, active)
            if active == 8:
                eight_entered.set()
        try:
            assert release.wait(timeout=3)
            return {"ok": True}
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = [executor.submit(module._request_before_deadline, transport, None) for _ in range(12)]
        assert eight_entered.wait(timeout=2)
        with lock:
            assert total == 8
            assert peak == 8
        release.set()
        assert [future.result(timeout=3) for future in futures] == [{"ok": True}] * 12
    assert peak == 8


def test_mixed_deadline_timeouts_hold_slots_until_real_transport_exits(monkeypatch):
    monkeypatch.setattr(module, "_transport_slots", threading.BoundedSemaphore(8))
    release, all_entered, ninth_entered = threading.Event(), threading.Event(), threading.Event()
    lock = threading.Lock()
    active = 0

    def transport():
        nonlocal active
        with lock:
            active += 1
            if active == 8:
                all_entered.set()
        assert release.wait(timeout=3)
        with lock:
            active -= 1
        return {"ok": True}

    with ThreadPoolExecutor(max_workers=9) as executor:
        ordinary = [executor.submit(module._request_before_deadline, transport, None) for _ in range(4)]
        budgeted = [executor.submit(module._request_before_deadline, transport, time.monotonic() + .1)
                    for _ in range(4)]
        assert all_entered.wait(timeout=2)
        for future in budgeted:
            with pytest.raises(module.LLMTimeoutError):
                future.result(timeout=2)
        ninth = executor.submit(module._request_before_deadline,
                                lambda: (ninth_entered.set() or {"ok": True}), None)
        assert not ninth_entered.wait(timeout=.05)
        release.set()
        assert ninth.result(timeout=2) == {"ok": True}
        assert all(future.result(timeout=2) == {"ok": True} for future in ordinary)


@pytest.mark.parametrize("budgeted", [True, False])
def test_exception_always_releases_real_transport_slot(monkeypatch, budgeted):
    monkeypatch.setattr(module, "_transport_slots", threading.BoundedSemaphore(1))

    def broken():
        raise httpx.ReadError("simulated lost response")

    until = time.monotonic() + 2 if budgeted else None
    with pytest.raises(httpx.ReadError):
        module._request_before_deadline(broken, until)
    assert module._request_before_deadline(lambda: {"ok": True}, until) == {"ok": True}
