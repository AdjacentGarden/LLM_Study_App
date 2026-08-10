from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock
from time import monotonic

from app.core.errors import AppError


@dataclass
class _TaskBucket:
    active: int = 0
    starts: list[float] = field(default_factory=list)


class InMemoryTaskLimiter:
    def __init__(self) -> None:
        self._buckets: dict[str, _TaskBucket] = {}
        self._lock = Lock()

    def start(self, task_name: str, *, max_concurrent: int, max_per_minute: int) -> None:
        now = monotonic()
        with self._lock:
            bucket = self._buckets.setdefault(task_name, _TaskBucket())
            bucket.starts = [started_at for started_at in bucket.starts if now - started_at < 60]
            if max_concurrent > 0 and bucket.active >= max_concurrent:
                raise AppError(
                    "heavy_task_concurrency_limited",
                    "当前重任务正在处理，请稍后再试",
                    status_code=429,
                    details={"task": task_name, "max_concurrent": max_concurrent},
                )
            if max_per_minute > 0 and len(bucket.starts) >= max_per_minute:
                raise AppError(
                    "heavy_task_rate_limited",
                    "请求过于频繁，请稍后再试",
                    status_code=429,
                    details={"task": task_name, "max_per_minute": max_per_minute},
                )
            bucket.active += 1
            bucket.starts.append(now)

    def finish(self, task_name: str) -> None:
        with self._lock:
            bucket = self._buckets.setdefault(task_name, _TaskBucket())
            bucket.active = max(0, bucket.active - 1)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


heavy_task_limiter = InMemoryTaskLimiter()
