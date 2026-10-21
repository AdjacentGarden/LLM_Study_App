"""Persistent, restartable post-OCR preparation, with one active worker per queue."""

from __future__ import annotations

import logging
import sqlite3
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@contextmanager
def _worker_lock(path: Path) -> Iterator[bool]:
    """Keep a process lock until the current operation finishes, including shutdown."""
    with path.open("a+b") as lock:
        if sys.platform == "win32":
            import msvcrt

            lock.seek(0, 2)
            if lock.tell() == 0:
                lock.write(b"\0")
                lock.flush()
            lock.seek(0)
            try:
                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                yield False
                return
            try:
                yield True
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                yield False
                return
            try:
                yield True
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)


class PreparationQueue:
    """Checkpoint completed stages; interrupted stages must be safe to run again."""

    def __init__(
        self,
        path: Path,
        steps: Sequence[tuple[str, Callable[[str], object]]],
        *,
        poll_interval: float = 0.5,
    ) -> None:
        if not steps or len({name for name, _ in steps}) != len(steps):
            raise ValueError("preparation requires distinct, nonempty steps")
        self.path = path
        self.steps = tuple(steps)
        self.poll_interval = poll_interval
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._thread_lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS preparation (book_id TEXT PRIMARY KEY, "
                "status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', "
                "updated REAL NOT NULL, next_step INTEGER NOT NULL DEFAULT 0)"
            )
            # Upgrade the first deployed queue without losing pending books.
            columns = {row[1] for row in db.execute("PRAGMA table_info(preparation)")}
            if "next_step" not in columns:
                db.execute("ALTER TABLE preparation ADD COLUMN next_step INTEGER NOT NULL DEFAULT 0")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, book_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM preparation WHERE book_id=?", (book_id,)).fetchone()
        return dict(row) if row else None

    def enqueue(self, book_id: str, *, retry: bool = False) -> dict[str, Any]:
        with self.connect() as db:
            db.execute(
                "INSERT OR IGNORE INTO preparation(book_id,status,stage,updated) "
                "VALUES(?,'queued','queued',?)",
                (book_id, time.time()),
            )
            if retry:
                db.execute(
                    "UPDATE preparation SET status='queued',error='',updated=? "
                    "WHERE book_id=? AND status='failed'",
                    (time.time(), book_id),
                )
        result = self.get(book_id)
        assert result is not None
        return result

    def _update(
        self, book_id: str, status: str, stage: str, next_step: int, error: str = ""
    ) -> None:
        with self.connect() as db:
            db.execute(
                "UPDATE preparation SET status=?,stage=?,next_step=?,error=?,updated=? "
                "WHERE book_id=?",
                (status, stage, next_step, error, time.time(), book_id),
            )

    def process_one(self) -> bool:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT book_id,next_step FROM preparation WHERE status='queued' "
                "ORDER BY updated LIMIT 1"
            ).fetchone()
            if row is None:
                return False
            db.execute(
                "UPDATE preparation SET status='running',updated=? WHERE book_id=?",
                (time.time(), row["book_id"]),
            )
        book_id, next_step = row["book_id"], row["next_step"]
        stage = self.steps[min(next_step, len(self.steps) - 1)][0]
        try:
            for index in range(next_step, len(self.steps)):
                if self.stop_event.is_set():
                    return True
                stage, action = self.steps[index]
                self._update(book_id, "running", stage, index)
                action(book_id)
                next_step = index + 1
                self._update(book_id, "running", stage, next_step)
            self._update(book_id, "succeeded", "ready", len(self.steps))
        except Exception:
            logger.warning("book preparation failed at %s", stage, exc_info=True)
            self._update(
                book_id,
                "failed",
                stage,
                next_step,
                "教材整理暂未完成，已保存处理进度，请重试。",
            )
        return True

    def start(self) -> None:
        with self._thread_lock:
            if self.thread and self.thread.is_alive():
                return
            self.stop_event.clear()
            self.thread = threading.Thread(
                target=self._loop, daemon=True, name="book-preparation"
            )
            self.thread.start()

    def _loop(self) -> None:
        while not self.stop_event.is_set():
            with _worker_lock(self.path.with_suffix(".lock")) as acquired:
                if acquired:
                    # Only the lock holder can recover a worker lost on process exit.
                    recover_pending = True
                    while not self.stop_event.is_set():
                        try:
                            if recover_pending:
                                with self.connect() as db:
                                    db.execute("UPDATE preparation SET status='queued' WHERE status='running'")
                                recover_pending = False
                            if self.process_one():
                                continue
                        except sqlite3.Error:
                            recover_pending = True
                            logger.warning("preparation storage temporarily unavailable", exc_info=True)
                        self.stop_event.wait(self.poll_interval)
                    return
            self.stop_event.wait(self.poll_interval)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
