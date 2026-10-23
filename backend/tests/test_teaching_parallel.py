from __future__ import annotations

import os
import subprocess
import sys
import textwrap
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from contextvars import ContextVar, copy_context
from pathlib import Path

import pytest

from adaptive_learning.llm import teaching_parallel
from adaptive_learning.llm.client import _deadline, model_time_budget
from adaptive_learning.personalization.teaching_content import _AUDIT_CONTEXT


@pytest.fixture(autouse=True)
def isolated_review_pool(monkeypatch: pytest.MonkeyPatch):
    """Never shut down a process pool created by another test module."""
    original = teaching_parallel._pool
    monkeypatch.setattr(teaching_parallel, "_pool", None)
    yield
    current = teaching_parallel._pool
    if current is not None and current is not original:
        current[0].shutdown(wait=True, cancel_futures=True)


def run_bounded_child(source: str) -> None:
    # A broken nested implementation can deadlock its executor's atexit hook.
    # Isolate those checks so failure kills the child rather than hanging pytest.
    env = dict(os.environ)
    backend_src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = backend_src + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        env=env, capture_output=True, text=True, timeout=8, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_saturated_book_pool_can_wait_for_independent_leaf_reviews() -> None:
    run_bounded_child("""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from adaptive_learning.llm import book_parallel, teaching_parallel

        chapters_started = threading.Barrier(2)
        def chapter(value):
            chapters_started.wait(timeout=3)
            return teaching_parallel.ordered_review_map(lambda n: n * 10, [value, value + 1])

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix='full-book') as books, \
             ThreadPoolExecutor(max_workers=2, thread_name_prefix='separate-leaf') as reviews:
            book_parallel._pool = (books, 2)
            teaching_parallel._pool = (reviews, 2)
            assert book_parallel.ordered_book_map(chapter, [1, 3]) == [[10, 20], [30, 40]]
    """)


def test_nested_maps_finish_when_every_leaf_worker_is_occupied() -> None:
    run_bounded_child("""
        import threading
        from concurrent.futures import ThreadPoolExecutor
        from adaptive_learning.llm import teaching_parallel

        full = threading.Barrier(2)
        def outer(value):
            owner = threading.get_ident()
            full.wait(timeout=3)
            def inner(number):
                assert threading.get_ident() == owner, 'nested work must run inline'
                return number + 1
            return teaching_parallel.ordered_review_map(inner, [value * 10, value * 10 + 1])

        with ThreadPoolExecutor(max_workers=2) as reviews:
            teaching_parallel._pool = (reviews, 2)
            assert teaching_parallel.ordered_review_map(outer, [1, 2]) == [[11, 12], [21, 22]]
    """)


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_singleton_calls_share_one_global_limit_even_with_one_worker(
    monkeypatch: pytest.MonkeyPatch, workers: int,
) -> None:
    release = threading.Event()
    saturated = threading.Event()
    start = threading.Barrier(workers + 4)
    lock = threading.Lock()
    active = peak = 0
    seen: list[int] = []

    def leaf(value: int) -> int:
        nonlocal active, peak
        assert threading.current_thread().name.startswith("review-cap"), "singleton bypassed pool"
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append(value)
            if active == workers:
                saturated.set()
        try:
            assert release.wait(4)
            return value * 2
        finally:
            with lock:
                active -= 1

    def caller(value: int) -> list[int]:
        start.wait(timeout=4)
        return teaching_parallel.ordered_review_map(leaf, [value])

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-cap") as reviews, \
         ThreadPoolExecutor(max_workers=workers + 3, thread_name_prefix="outside-caller") as callers:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, workers))
        futures = [callers.submit(caller, value) for value in range(workers + 3)]
        try:
            start.wait(timeout=4)
            assert saturated.wait(4), "shared review workers did not become occupied"
            with lock:
                assert active == workers and peak == workers
        finally:
            release.set()
        assert [future.result(timeout=4) for future in futures] == [[n * 2] for n in range(workers + 3)]
    assert peak == workers
    assert sorted(seen) == list(range(workers + 3))


def test_deadline_and_real_audit_context_are_copied_per_leaf_and_caller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = ContextVar("review_test_request", default="absent")

    def leaf(value: int) -> tuple[str, str, float | None, int]:
        audit = _AUDIT_CONTEXT.get()
        assert audit is not None
        result = (request.get(), audit["run"], _deadline.get(), value)
        # Context values are replaced, not mutated in-place: copy_context is
        # deliberately shallow and does not promise to clone arbitrary dicts.
        request.set("leaf-only")
        _AUDIT_CONTEXT.set({"run": "leaf-only"})
        _deadline.set(-1.0)
        return result

    def caller(name: str):
        request_token = request.set(name)
        audit_token = _AUDIT_CONTEXT.set({"run": name})
        try:
            with model_time_budget(30):
                deadline = _deadline.get()
                rows = teaching_parallel.ordered_review_map(leaf, range(4))
                assert _deadline.get() == deadline
                assert request.get() == name
                assert _AUDIT_CONTEXT.get() == {"run": name}
                assert rows == [(name, name, deadline, value) for value in range(4)]
                return rows
        finally:
            _AUDIT_CONTEXT.reset(audit_token)
            request.reset(request_token)

    before_deadline, before_audit = _deadline.get(), _AUDIT_CONTEXT.get()
    with ThreadPoolExecutor(max_workers=2) as reviews, ThreadPoolExecutor(max_workers=2) as callers:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, 2))
        futures = [callers.submit(caller, name) for name in ("first", "second")]
        rows = [future.result(timeout=5) for future in futures]
    assert {row[0][0] for row in rows} == {"first", "second"}
    assert all(row[0][2] is not None for row in rows)
    assert (_deadline.get(), _AUDIT_CONTEXT.get()) == (before_deadline, before_audit)


def test_copying_leaf_context_to_a_different_thread_does_not_bypass_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def outer(value: int) -> list[int]:
        context = copy_context()

        def leaf(number: int) -> int:
            assert threading.current_thread().name.startswith("actual-review"), "copied context bypassed pool"
            return number + 1

        with ThreadPoolExecutor(max_workers=1, thread_name_prefix="foreign-context") as callers:
            return callers.submit(context.run, teaching_parallel.ordered_review_map, leaf, [value]).result(timeout=4)

    with ThreadPoolExecutor(max_workers=2, thread_name_prefix="actual-review") as reviews:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, 2))
        assert teaching_parallel.ordered_review_map(outer, [3]) == [[4]]


def test_out_of_order_completion_preserves_input_order(monkeypatch: pytest.MonkeyPatch) -> None:
    second_done = threading.Event()
    completion: list[int] = []

    def leaf(value: int) -> int:
        if value == 0:
            assert second_done.wait(4)
        completion.append(value)
        if value == 1:
            second_done.set()
        return value * 10

    with ThreadPoolExecutor(max_workers=2) as reviews:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, 2))
        assert teaching_parallel.ordered_review_map(leaf, range(5)) == [0, 10, 20, 30, 40]
    assert completion.index(1) < completion.index(0)


def test_failure_raises_without_partial_results_and_running_work_keeps_its_slot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow_started = threading.Event()
    release_slow = threading.Event()
    slow_exited = threading.Event()
    called: list[int] = []

    def leaf(value: int) -> int:
        called.append(value)
        if value == 0:
            assert slow_started.wait(4)
            raise ValueError("review rejected")
        if value == 1:
            slow_started.set()
            try:
                assert release_slow.wait(4)
            finally:
                slow_exited.set()
        return value

    with ThreadPoolExecutor(max_workers=2) as reviews, ThreadPoolExecutor(max_workers=1) as callers:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, 2))
        failed = callers.submit(teaching_parallel.ordered_review_map, leaf, range(8))
        try:
            with pytest.raises(ValueError, match="review rejected"):
                failed.result(timeout=3)
            assert not slow_exited.is_set(), "running callback was forcefully interrupted"
            # A failed map does not destroy the pool or leak its nested marker.
            assert teaching_parallel.ordered_review_map(lambda value: value + 1, [40]) == [41]
            assert sorted(called) == [0, 1], "failure scheduled another review window"
        finally:
            release_slow.set()
    assert slow_exited.is_set()


def test_failure_cancels_a_future_that_has_not_started(monkeypatch: pytest.MonkeyPatch) -> None:
    class ControlledExecutor(ThreadPoolExecutor):
        def __init__(self):
            super().__init__(max_workers=1)
            self.submitted: list[Future] = []

        def submit(self, function, /, *args, **kwargs):
            future = Future()
            self.submitted.append(future)
            if len(self.submitted) == 1:
                future.set_exception(ValueError("known rejection"))
            return future

    # Manual Futures make cancellation deterministic; real executor scheduling
    # could start a queued callback between failure and the caller's cancel().
    with ControlledExecutor() as reviews:
        monkeypatch.setattr(teaching_parallel, "_pool", (reviews, 2))
        with pytest.raises(ValueError, match="known rejection"):
            teaching_parallel.ordered_review_map(lambda value: value, range(5))
        assert len(reviews.submitted) == 2
        assert reviews.submitted[1].cancelled()


def test_empty_input_does_not_freeze_or_validate_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEACHING_REVIEW_CONCURRENCY", "invalid")
    assert teaching_parallel.ordered_review_map(lambda value: pytest.fail("empty input ran callback"), []) == []
    assert teaching_parallel._pool is None


def test_default_pool_is_independent_and_configuration_freezes_on_first_nonempty_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("TEACHING_REVIEW_CONCURRENCY", raising=False)
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", "1")
    assert teaching_parallel.ordered_review_map(lambda value: value, [7]) == [7]
    original = teaching_parallel._pool
    assert original is not None and original[1] == 4
    monkeypatch.setenv("TEACHING_REVIEW_CONCURRENCY", "invalid-after-freeze")
    assert teaching_parallel.ordered_review_map(lambda value: value + 1, [7]) == [8]
    assert teaching_parallel._pool is original


@pytest.mark.parametrize("value", ["", "0", "9", "bad", "1.5", "-1"])
def test_invalid_concurrency_rejects_nonempty_work_before_allocating_pool(
    monkeypatch: pytest.MonkeyPatch, value: str,
) -> None:
    monkeypatch.setenv("TEACHING_REVIEW_CONCURRENCY", value)
    with pytest.raises(ValueError, match="TEACHING_REVIEW_CONCURRENCY"):
        teaching_parallel.ordered_review_map(lambda item: item, [1])
    assert teaching_parallel._pool is None


@pytest.mark.parametrize("workers", [1, 8])
def test_configuration_accepts_both_endpoints(monkeypatch: pytest.MonkeyPatch, workers: int) -> None:
    monkeypatch.setenv("TEACHING_REVIEW_CONCURRENCY", str(workers))
    assert teaching_parallel.ordered_review_map(lambda item: item + 1, [1]) == [2]
    assert teaching_parallel._pool is not None and teaching_parallel._pool[1] == workers
