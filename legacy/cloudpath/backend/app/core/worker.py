from __future__ import annotations

import queue
import threading
from typing import Any, Callable

from app.core.logging import get_logger


_logger = get_logger("app.worker")


class TaskQueue:
    def __init__(self) -> None:
        self._queue: "queue.Queue[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]]" = queue.Queue()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._run, name="bookcourse-worker", daemon=True)
            self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        self._queue.put_nowait(None)
        thread = self._thread
        if thread is not None:
            thread.join(timeout=timeout)
        with self._lock:
            self._thread = None

    def enqueue(self, fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        self._queue.put_nowait((fn, args, kwargs))

    def _run(self) -> None:
        while not self._stop.is_set():
            item = self._queue.get()
            if item is None:
                break
            fn, args, kwargs = item
            try:
                fn(*args, **kwargs)
            except Exception as exc:
                _logger.exception("worker_task_failed", extra={"event": "worker_task_failed", "error": repr(exc)})


task_queue = TaskQueue()