from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar

import pytest

from adaptive_learning.llm import book_parallel
from adaptive_learning.llm.client import (
    LLMTimeoutError,
    model_time_budget,
    remaining_model_time,
)


@pytest.fixture(autouse=True)
def isolated_pool(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(book_parallel, "_pool", None)
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", "2")
    yield
    if book_parallel._pool is not None:
        book_parallel._pool[0].shutdown(wait=True, cancel_futures=True)


def test_completion_order_does_not_change_chapter_order() -> None:
    second_finished = threading.Event()
    completions: list[int] = []

    def chapter(value: int) -> int:
        if value == 0:
            assert second_finished.wait(1)
        completions.append(value)
        if value == 1:
            second_finished.set()
        return value * 2

    assert book_parallel.ordered_book_map(chapter, range(8)) == list(range(0, 16, 2))
    assert completions[0] == 1


@pytest.mark.parametrize("workers", [1, 2])
def test_limit_is_shared_across_books_including_single_chapter_books(
    monkeypatch: pytest.MonkeyPatch, workers: int
) -> None:
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", str(workers))
    active = peak = 0
    lock = threading.Lock()
    admitted = threading.Event()
    release = threading.Event()

    def chapter(value: int) -> int:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            if active == workers:
                admitted.set()
        try:
            assert release.wait(2)
            return value
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=4) as books:
        futures = [books.submit(book_parallel.ordered_book_map, chapter, [i]) for i in range(4)]
        try:
            assert admitted.wait(1)
            assert peak == workers
        finally:
            release.set()
        assert [future.result(timeout=1) for future in futures] == [[i] for i in range(4)]
    assert peak == workers


def test_failure_returns_no_partial_bank_and_does_not_start_later_chapters() -> None:
    first_running = threading.Event()
    release = threading.Event()
    started: list[int] = []

    def chapter(value: int) -> int:
        started.append(value)
        if value == 0:
            first_running.set()
            assert release.wait(2)
        elif value == 1:
            assert first_running.wait(1)
            raise ValueError("chapter rejected")
        return value

    try:
        with pytest.raises(ValueError, match="chapter rejected"):
            book_parallel.ordered_book_map(chapter, range(5))
        assert sorted(started) == [0, 1]
    finally:
        release.set()


def test_workers_inherit_separate_contexts_and_shared_deadline() -> None:
    marker: ContextVar[str] = ContextVar("chapter_marker", default="unset")
    marker.set("request")
    both_running = threading.Barrier(2)

    def chapter(value: int) -> tuple[str, float | None]:
        inherited = marker.get()
        marker.set(str(value))
        both_running.wait(timeout=1)
        assert marker.get() == str(value)
        return inherited, remaining_model_time()

    with model_time_budget(1):
        result = book_parallel.ordered_book_map(chapter, [0, 1])
    assert [entry[0] for entry in result] == ["request", "request"]
    assert all(entry[1] is not None and 0 < entry[1] <= 1 for entry in result)
    assert marker.get() == "request"
    assert remaining_model_time() is None


def test_queue_wait_expires_without_starting_an_abandoned_chapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", "1")
    release = threading.Event()
    called = threading.Event()
    executor, _ = book_parallel._shared_pool()
    occupied = executor.submit(release.wait, 2)
    started = time.monotonic()
    try:
        with model_time_budget(0.04), pytest.raises(LLMTimeoutError, match="time budget"):
            book_parallel.ordered_book_map(lambda _: called.set(), [0])
        assert time.monotonic() - started < 0.4
    finally:
        release.set()
        occupied.result(timeout=1)
    assert not called.is_set()


def test_nested_parallel_work_cannot_starve_the_shared_pool() -> None:
    both_running = threading.Barrier(2)

    def chapter(value: int) -> list[int]:
        both_running.wait(timeout=1)
        return book_parallel.ordered_book_map(lambda item: value + item, [10, 20])

    with model_time_budget(1):
        assert book_parallel.ordered_book_map(chapter, [1, 2]) == [[11, 21], [12, 22]]


@pytest.mark.parametrize("value", ["0", "9", "many"])
def test_invalid_concurrency_is_rejected(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", value)
    with pytest.raises(ValueError, match="BOOK_LLM_CONCURRENCY"):
        book_parallel.ordered_book_map(lambda item: item, [1])


def test_default_concurrency_is_six_and_maximum_is_eight(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOOK_LLM_CONCURRENCY", raising=False)
    assert book_parallel._configured_workers() == 6
    monkeypatch.setenv("BOOK_LLM_CONCURRENCY", "8")
    assert book_parallel._configured_workers() == 8
