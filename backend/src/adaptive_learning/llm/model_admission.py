"""Paid-call admission after an observed HTTP 429/503.

No network probes, retries, provider substitution, or semantic approval occur
here. Only a subsequent, already-authorized real request can recover a route.
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC
from email.utils import parsedate_to_datetime


class AdmissionDenied(RuntimeError):
    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__("model route temporarily cooling down after HTTP overload")
        self.retry_after_seconds = max(0.0, retry_after_seconds)


@dataclass(frozen=True, slots=True)
class RouteKey:
    endpoint: str
    model: str
    account_digest: bytes


def route_key(base_url: str, model: str, api_key: str) -> RouteKey:
    # Never put this private key, endpoint, or digest in public readiness data.
    return RouteKey(base_url.rstrip("/"), model, hashlib.sha256(api_key.encode()).digest())


def overload_wait(headers: Mapping[str, str], body: object, wall_time: float) -> float:
    """Preserve a valid upstream wait; never shorten it to the local 30s floor."""
    def number(value: object) -> float | None:
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            seconds = float(value)
        except (ValueError, OverflowError):
            return None
        return seconds if math.isfinite(seconds) and seconds >= 0 else None

    header = headers.get("Retry-After", headers.get("retry-after"))
    delay = number(header)
    if delay is None and isinstance(header, str):
        try:
            date = parsedate_to_datetime(header)
            if date.tzinfo is None:
                date = date.replace(tzinfo=UTC)
            delay = max(0.0, date.timestamp() - wall_time)
        except (ValueError, TypeError, OverflowError, OSError):
            pass
    if delay is None and isinstance(body, dict):
        error = body.get("error")
        values = [error.get("retry_after_seconds")] if isinstance(error, dict) else []
        values.append(body.get("retry_after_seconds"))
        delay = next((value for item in values if (value := number(item)) is not None), None)
    return max(30.0, delay or 0.0)


@dataclass(slots=True)
class _State:
    epoch: int
    until: float
    probe_active: bool = False
    probe_owner_epoch: int | None = None


@dataclass(frozen=True, slots=True)
class Lease:
    key: RouteKey
    epoch: int
    recovery: bool


class ModelAdmission:
    """Process-local route breaker; deploy one backend process initially.

    Only exhausted logical calls with a final HTTP 429/503 open the circuit.
    Existing in-flight calls keep running; an older success cannot erase a
    newer failure. At expiry exactly one real call is admitted for recovery.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._changed = threading.Condition()
        self._states: dict[RouteKey, _State] = {}

    def check(self, key: RouteKey) -> None:
        """No-cost preflight; it cannot reserve, probe, or establish health."""
        with self._changed:
            state = self._states.get(key)
            if state is not None and (state.probe_active or state.until > self._clock()):
                raise AdmissionDenied(max(1.0, state.until - self._clock()))

    def begin(self, key: RouteKey, wait_seconds: float = 0.0) -> Lease:
        wait_until = time.monotonic() + max(0.0, wait_seconds)
        waited_epoch = None
        with self._changed:
            while (state := self._states.get(key)) is not None and state.probe_active:
                waited_epoch = state.epoch
                remaining = wait_until - time.monotonic()
                if remaining <= 0:
                    raise AdmissionDenied(1.0)
                self._changed.wait(timeout=remaining)
            state = self._states.get(key)
            if state is None:
                return Lease(key, 0, False)
            if waited_epoch is not None and state.epoch != waited_epoch:
                # A failed/unknown recovery must not let queued siblings turn
                # themselves into a second probe automatically.
                raise AdmissionDenied(max(1.0, state.until - self._clock()))
            if state.probe_active or state.until > self._clock():
                raise AdmissionDenied(max(1.0, state.until - self._clock()))
            state.probe_active = True
            state.probe_owner_epoch = state.epoch
            return Lease(key, state.epoch, True)

    def finish(self, lease: Lease, *, overload: float | None = None, completed: bool = False) -> None:
        with self._changed:
            state = self._states.get(lease.key)
            if overload is not None:
                if not math.isfinite(overload) or overload < 0:
                    raise ValueError("overload wait must be finite and non-negative")
                epoch = (state.epoch if state else 0) + 1
                until = max(self._clock() + overload, state.until if state else 0.0)
                owns_probe = (lease.recovery and state is not None
                              and state.probe_owner_epoch == lease.epoch)
                active = bool(state and state.probe_active and not owns_probe)
                owner = state.probe_owner_epoch if state and active else None
                # An older normal request may report overload while the current
                # recovery request is still physically draining. Keep its owner.
                self._states[lease.key] = _State(epoch, until, active, owner)
            elif (lease.recovery and state is not None
                  and state.probe_owner_epoch == lease.epoch):
                if completed and state.epoch == lease.epoch:
                    # Successful valid JSON is transport recovery, not course approval.
                    del self._states[lease.key]
                else:
                    # Only the actual owner releases occupancy. Unknown output
                    # and older successes cannot clear a newer overload epoch.
                    state.probe_active = False
                    state.probe_owner_epoch = None
                    state.epoch += 1
            self._changed.notify_all()


admission = ModelAdmission()
