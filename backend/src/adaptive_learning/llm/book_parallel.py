"""Bounded, process-wide concurrency for independent book analysis calls.

BOOK_LLM_CONCURRENCY defaults to six, and accepts 1..8. It is read when the
first parallel book operation starts; restart the process to change it. The
shared pool caps aggregate work across books and across both analysis stages.
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import copy_context
from typing import TypeVar

_Input = TypeVar("_Input")
_Output = TypeVar("_Output")
_pool_lock = threading.Lock()
_pool: tuple[ThreadPoolExecutor, int] | None = None


def _configured_workers() -> int:
    try:
        workers = int(os.getenv("BOOK_LLM_CONCURRENCY", "6"))
    except ValueError as error:
        raise ValueError("BOOK_LLM_CONCURRENCY must be an integer between 1 and 8") from error
    if not 1 <= workers <= 8:
        raise ValueError("BOOK_LLM_CONCURRENCY must be between 1 and 8")
    return workers


def _shared_pool() -> tuple[ThreadPoolExecutor, int]:
    global _pool
    with _pool_lock:
        if _pool is None:
            workers = _configured_workers()
            _pool = (
                ThreadPoolExecutor(max_workers=workers, thread_name_prefix="book-llm"),
                workers,
            )
        return _pool


def ordered_book_map(
    function: Callable[[_Input], _Output], values: Iterable[_Input]
) -> list[_Output]:
    """Run independent chapters concurrently and return their original order.

    Keep at most one window of submitted work per book; do not queue every
    chapter of an arbitrarily large book. Each task gets its own copied context
    so the client's existing generation deadline follows calls into the pool.
    No partially successful result is returned if any chapter fails validation.
    """
    inputs = list(values)
    if len(inputs) < 2:
        return [function(value) for value in inputs]
    executor, workers = _shared_pool()
    if workers == 1:
        return [function(value) for value in inputs]
    source = iter(enumerate(inputs))
    pending: dict[Future[_Output], int] = {}
    results: dict[int, _Output] = {}

    def submit_next() -> None:
        index, value = next(source)
        pending[executor.submit(copy_context().run, function, value)] = index

    try:
        for _ in range(min(workers, len(inputs))):
            submit_next()
        while pending:
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            for future in completed:
                index = pending.pop(future)
                results[index] = future.result()
                try:
                    submit_next()
                except StopIteration:
                    pass
    except BaseException:
        for future in pending:
            future.cancel()
        raise
    return [results[index] for index in range(len(inputs))]
