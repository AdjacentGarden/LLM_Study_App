"""Process-wide chapter concurrency, configured once by BOOK_LLM_CONCURRENCY."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Iterable
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from contextvars import ContextVar, copy_context
from typing import TypeVar

from .client import LLMTimeoutError, remaining_model_time

_Input = TypeVar("_Input")
_Output = TypeVar("_Output")
_pool_lock = threading.Lock()
_pool: tuple[ThreadPoolExecutor, int] | None = None
_in_book_worker: ContextVar[bool] = ContextVar("in_book_worker", default=False)


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


def _invoke(function: Callable[[_Input], _Output], value: _Input) -> _Output:
    remaining = remaining_model_time()
    if remaining is not None and remaining <= 0:
        raise LLMTimeoutError("model time budget exceeded")
    token = _in_book_worker.set(True)
    try:
        return function(value)
    finally:
        _in_book_worker.reset(token)


def ordered_book_map(
    function: Callable[[_Input], _Output], values: Iterable[_Input]
) -> list[_Output]:
    """Keep chapter output ordered and discard the whole result on any failure.

    At most one window of work per book is submitted to the shared pool. Each
    task inherits a separate context, including its caller's model deadline.
    Queue wait counts toward that deadline. Nested calls run on the current
    worker, avoiding starvation when every worker is already processing a book.
    Running calls retain their slots after failure until they finish; pending
    calls are cancelled. Restart the process to change the configured limit.
    """
    inputs = list(values)
    if not inputs:
        return []
    if _in_book_worker.get():
        return [_invoke(function, value) for value in inputs]
    executor, workers = _shared_pool()
    source = iter(enumerate(inputs))
    pending: dict[Future[_Output], int] = {}
    results: dict[int, _Output] = {}

    def submit_next() -> None:
        index, value = next(source)
        pending[executor.submit(copy_context().run, _invoke, function, value)] = index

    try:
        for _ in range(min(workers, len(inputs))):
            submit_next()
        while pending:
            remaining = remaining_model_time()
            if remaining is not None and remaining <= 0:
                raise LLMTimeoutError("model time budget exceeded")
            completed, _ = wait(pending, timeout=remaining, return_when=FIRST_COMPLETED)
            if not completed:
                raise LLMTimeoutError("model time budget exceeded")
            # Inspect the entire completed batch before admitting more work so
            # a successful sibling cannot queue more chapters past a failure.
            for future in completed:
                results[pending.pop(future)] = future.result()
            for _ in completed:
                try:
                    submit_next()
                except StopIteration:
                    break
    except BaseException:
        for future in pending:
            future.cancel()
        raise
    return [results[index] for index in range(len(inputs))]
