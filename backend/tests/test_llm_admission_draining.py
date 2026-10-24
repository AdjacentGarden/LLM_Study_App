"""Recovery ownership follows real blocked transports, not caller deadlines."""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from adaptive_learning.llm import client as module
from adaptive_learning.llm.model_admission import AdmissionDenied, ModelAdmission, route_key
from test_llm_admission import Clock, client, success


def wait_for_probe_release(gate, key):
    until = time.monotonic() + 2
    while time.monotonic() < until:
        with gate._changed:
            state = gate._states.get(key)
            if state is None or not state.probe_active:
                return
        time.sleep(.005)
    pytest.fail('physical transport failed to release its recovery ownership')


@pytest.mark.parametrize('late_status', [200, 503])
def test_timed_out_half_open_retains_owner_until_actual_sdk_transport_drains(
    monkeypatch, late_status
):
    clock = Clock()
    gate = ModelAdmission(clock)
    monkeypatch.setattr(module, 'admission', gate)
    monkeypatch.setattr(module, '_transport_slots', threading.BoundedSemaphore(8))
    key = route_key('https://pucoding.com/v1', 'gpt-6-sol', 'fake-test-key')
    gate.finish(gate.begin(key), overload=30)
    clock.now += 30
    entered, release = threading.Event(), threading.Event()
    sends = []

    def handler(request):
        sends.append(request)
        entered.set()
        assert release.wait(timeout=3)
        return success('pucoding') if late_status == 200 else httpx.Response(
            503, json={'error': {'retry_after_seconds': 1}})

    obj = client(handler, provider='pucoding', model='gpt-6-sol')

    def timed_request(seconds):
        with module.model_time_budget(seconds):
            return obj.structured(system='audit', user='fixed target')

    with ThreadPoolExecutor(max_workers=1) as executor:
        initial = executor.submit(timed_request, .06)
        try:
            assert entered.wait(timeout=2)
            with pytest.raises(module.LLMTimeoutError):
                initial.result(timeout=2)
            # This is a fresh arrival, not a sibling already in begin(). The
            # timed-out first request is still blocked inside actual SDK send.
            with pytest.raises(module.LLMCooldownError):
                timed_request(.03)
            assert len(sends) == 1
            with pytest.raises(AdmissionDenied):
                gate.check(key)
        finally:
            release.set()
    wait_for_probe_release(gate, key)
    assert len(sends) == 1  # No automatic resend or retrieval of the late body.
    if late_status == 503:
        with pytest.raises(AdmissionDenied):
            gate.check(key)
    else:
        # Valid late JSON is still unknown to the caller, never proof of health.
        lease = gate.begin(key)
        assert lease.recovery is True
        gate.finish(lease)
    obj.close()


def test_older_normal_503_cannot_replace_active_recovery_owner(monkeypatch):
    clock = Clock()
    gate = ModelAdmission(clock)
    monkeypatch.setattr(module, 'admission', gate)
    monkeypatch.setattr(module, '_transport_slots', threading.BoundedSemaphore(8))
    key = route_key('https://pucoding.com/v1', 'gpt-6-sol', 'fake-test-key')
    old_entered, old_release = threading.Event(), threading.Event()
    probe_entered, probe_release = threading.Event(), threading.Event()
    sends, lock = [], threading.Lock()

    def handler(request):
        with lock:
            sends.append(request)
            index = len(sends)
        if index == 1:
            old_entered.set()
            assert old_release.wait(timeout=3)
            return httpx.Response(503, json={'error': {'retry_after_seconds': 1}})
        if index == 2:
            probe_entered.set()
            assert probe_release.wait(timeout=3)
        return success('pucoding')

    obj = client(handler, provider='pucoding', model='gpt-6-sol')
    with ThreadPoolExecutor(max_workers=2) as executor:
        old = executor.submit(obj.structured, system='audit', user='old normal call')
        probe = None
        try:
            assert old_entered.wait(timeout=2)
            gate.finish(gate.begin(key), overload=30)  # Separate known HTTP failure.
            clock.now += 30
            probe = executor.submit(obj.structured, system='audit', user='recovery call')
            assert probe_entered.wait(timeout=2)
            old_release.set()
            with pytest.raises(module.LLMError):
                old.result(timeout=2)
            clock.now += 31  # Newer overload cooldown expires; P still runs.
            with module.model_time_budget(.03), pytest.raises(module.LLMCooldownError):
                obj.structured(system='audit', user='must not become a second probe')
            assert len(sends) == 2
        finally:
            old_release.set()
            probe_release.set()
        assert probe is not None and probe.result(timeout=2) == {'ok': True}
    # P was accepted before the newer overload, so its success cannot clear it.
    next_lease = gate.begin(key)
    assert next_lease.recovery is True
    gate.finish(next_lease)
    assert len(sends) == 2
    obj.close()
