from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from adaptive_learning.llm import book_parallel
from adaptive_learning.llm.client import _deadline, model_time_budget


def test_shared_pool_limits_multiple_books_and_carries_deadlines(monkeypatch: pytest.MonkeyPatch) -> None:
    request = ContextVar("request_name", default="missing")
    gate = threading.Event()
    saturated = threading.Event()
    lock = threading.Lock()
    active = 0
    peak = 0
    seen: list[tuple[str, float | None]] = []

    def work(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            seen.append((request.get(), _deadline.get()))
            if active == 2:
                saturated.set()
        try:
            assert gate.wait(5), "test never released workers"
            return value * 2
        finally:
            with lock:
                active -= 1

    def book(name: str) -> list[int]:
        token = request.set(name)
        try:
            with model_time_budget(30):
                return book_parallel.ordered_book_map(work, [3, 1, 2])
        finally:
            request.reset(token)

    with ThreadPoolExecutor(max_workers=2) as shared, ThreadPoolExecutor(max_workers=2) as callers:
        monkeypatch.setattr(book_parallel, "_pool", (shared, 2))
        first = callers.submit(book, "first")
        second = callers.submit(book, "second")
        try:
            assert saturated.wait(5), "chapters ran serially"
            assert peak == 2
            assert len(seen) == 2
        finally:
            gate.set()
        assert first.result(timeout=5) == [6, 2, 4]
        assert second.result(timeout=5) == [6, 2, 4]
    assert peak == 2
    assert len(seen) == 6
    assert {name for name, _ in seen} == {"first", "second"}
    assert all(deadline is not None for _, deadline in seen)


def test_out_of_order_completion_does_not_reorder_book(monkeypatch: pytest.MonkeyPatch) -> None:
    second_finished = threading.Event()

    def work(value: int) -> int:
        if value == 0:
            assert second_finished.wait(5)
        elif value == 1:
            second_finished.set()
        return value

    with ThreadPoolExecutor(max_workers=2) as shared:
        monkeypatch.setattr(book_parallel, "_pool", (shared, 2))
        assert book_parallel.ordered_book_map(work, range(5)) == list(range(5))


def test_failed_chapter_does_not_return_partial_results(monkeypatch: pytest.MonkeyPatch) -> None:
    def work(value: int) -> int:
        if value == 1:
            raise ValueError("failed review")
        return value

    with ThreadPoolExecutor(max_workers=2) as shared:
        monkeypatch.setattr(book_parallel, "_pool", (shared, 2))
        with pytest.raises(ValueError, match="failed review"):
            book_parallel.ordered_book_map(work, [0, 1, 2])


@pytest.mark.parametrize("value", ["0", "9", "bad", "1.5"])
def test_invalid_concurrency_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", value)
    with pytest.raises(ValueError, match="BOOK_LLM_CONCURRENCY"):
        book_parallel._configured_workers()


@pytest.mark.parametrize("value", [1, 6, 8])
def test_concurrency_is_configurable(monkeypatch: pytest.MonkeyPatch, value: int) -> None:
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", str(value))
    assert book_parallel._configured_workers() == value
