"""Bounded leaf concurrency for independent teaching model operations.

This pool is separate from the chapter pool in ``book_parallel``. Chapter
workers may wait for independent reviews without occupying the same executor
that must run those reviews. Nothing here starts or changes a model client;
the client's existing process-wide transport limit still applies.

TEACHING_REVIEW_CONCURRENCY defaults to four and accepts 1..8. The first
nonempty outer call fixes the process-wide pool size; restart to change it.
Scope planning, point writing and independent reviews share this process-wide
leaf capacity.
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
_worker_state = threading.local()


def _configured_workers() -> int:
    try:
        workers = int(os.getenv("TEACHING_REVIEW_CONCURRENCY", "4"))
    except ValueError as error:
        raise ValueError("TEACHING_REVIEW_CONCURRENCY must be an integer between 1 and 8") from error
    if not 1 <= workers <= 8:
        raise ValueError("TEACHING_REVIEW_CONCURRENCY must be between 1 and 8")
    return workers


def _shared_pool() -> tuple[ThreadPoolExecutor, int]:
    global _pool
    with _pool_lock:
        if _pool is None:
            workers = _configured_workers()
            _pool = (
                ThreadPoolExecutor(max_workers=workers, thread_name_prefix="teaching-review"),
                workers,
            )
        return _pool


def _invoke(function: Callable[[_Input], _Output], value: _Input) -> _Output:
    previous = getattr(_worker_state, "inside_review", False)
    _worker_state.inside_review = True
    try:
        return function(value)
    finally:
        _worker_state.inside_review = previous


def ordered_review_map(
    function: Callable[[_Input], _Output], values: Iterable[_Input]
) -> list[_Output]:
    """Return all successful leaf results in input order, or raise an error.

    Every nonempty outer invocation uses the shared executor, even for one
    input or one configured worker. A caller-thread serial shortcut would let
    simultaneous chapters exceed the global limit. At most one worker-sized
    window is submitted by each invocation, rather than an unbounded chapter.

    Each leaf receives its own copied Context, preserving both the existing
    model deadline and private-audit context without leaking ContextVar
    rebindings between leaves (referenced mutable values are not deep-copied).
    A nested call from within a review leaf runs inline in fresh copied
    contexts; it cannot wait on its own saturated executor.

    Failure cancels work that has not started and returns no partial list.
    Already-running leaves are not forcibly interrupted: they keep their pool
    slots until they really exit, and their results are discarded. The helper
    cannot undo side effects of a callback or a completed inference.
    """
    inputs = list(values)
    if not inputs:
        return []
    if getattr(_worker_state, "inside_review", False):
        return [copy_context().run(function, value) for value in inputs]

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
            completed, _ = wait(pending, return_when=FIRST_COMPLETED)
            # Check the whole completed group before submitting its replacements.
            # A known failure must not schedule another window of paid work.
            for future in completed:
                index = pending.pop(future)
                results[index] = future.result()
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
