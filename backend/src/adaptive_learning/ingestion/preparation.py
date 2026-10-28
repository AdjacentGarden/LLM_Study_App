"""Persistent, restartable post-OCR preparation, with one active worker per queue."""

from __future__ import annotations

import json
import logging
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ..database import DATABASE_ERRORS, Connection, connect, database_url

logger = logging.getLogger(__name__)


class PreparationDeferred(RuntimeError):
    def __init__(self, details: dict[str, Any]) -> None:
        super().__init__("preparation will continue automatically")
        self.details = details


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
        self._owner = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            if db.postgres:
                return  # PostgreSQL DDL belongs to explicit migrations 006/008.
            db.begin_write()
            db.execute(
                "CREATE TABLE IF NOT EXISTS preparation (book_id TEXT PRIMARY KEY, "
                "status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', "
                "updated REAL NOT NULL, next_step INTEGER NOT NULL DEFAULT 0)"
            )
            # Upgrade the first deployed queue without losing pending books.
            columns = {row[1] for row in db.execute("PRAGMA table_info(preparation)")}
            if "next_step" not in columns:
                db.execute("ALTER TABLE preparation ADD COLUMN next_step INTEGER NOT NULL DEFAULT 0")
            if "retry_at" not in columns:
                db.execute("ALTER TABLE preparation ADD COLUMN retry_at REAL NOT NULL DEFAULT 0")
            if "details" not in columns:
                db.execute("ALTER TABLE preparation ADD COLUMN details TEXT NOT NULL DEFAULT '{}'")

    @contextmanager
    def connect(self) -> Iterator[Connection]:
        with connect(self.path) as db:
            yield db

    def get(self, book_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM preparation WHERE book_id=?", (book_id,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        result.update(json.loads(result.pop("details")))
        return result

    def enqueue(
        self, book_id: str, *, retry: bool = False, refresh: bool = False
    ) -> dict[str, Any]:
        with self.connect() as db:
            db.begin_write()
            db.execute(
                "INSERT INTO preparation(book_id,status,stage,updated) "
                "VALUES(?,'queued','queued',?) ON CONFLICT(book_id) DO NOTHING",
                (book_id, time.time()),
            )
            if retry:
                db.execute(
                    "UPDATE preparation SET status='queued',error='',retry_at=0,updated=? "
                    "WHERE book_id=? AND status IN ('failed','cancelled')",
                    (time.time(), book_id),
                )
            if refresh:
                db.execute(
                    "UPDATE preparation SET status='queued',stage='queued',next_step=0,"
                    "error='',retry_at=0,details='{}',updated=? WHERE book_id=? AND status='succeeded'",
                    (time.time(), book_id),
                )
        result = self.get(book_id)
        assert result is not None
        return result

    def cancel(self, book_id: str, *, create: bool = False) -> dict[str, Any] | None:
        with self.connect() as db:
            db.begin_write()
            if create:
                db.execute("INSERT INTO preparation(book_id,status,stage,updated) VALUES (?,'cancelled','queued',?) "
                           "ON CONFLICT(book_id) DO NOTHING", (book_id, time.time()))
            db.execute("UPDATE preparation SET status='cancelled',updated=? "
                       "WHERE book_id=? AND status IN ('queued','running','failed')", (time.time(), book_id))
        return self.get(book_id)

    def update(self, book_id: str, status: str, stage: str, error: str = "") -> None:
        """Keep the deployed compatibility API without discarding stage checkpoints."""
        self._check_owner()
        with self.connect() as db:
            db.execute(
                "UPDATE preparation SET status=?,stage=?,error=?,updated=? WHERE book_id=?",
                (status, stage, error, time.time(), book_id),
            )

    def _check_owner(self) -> None:
        owner = getattr(self._owner, "connection", None)
        if isinstance(owner, Connection):
            # A lost PostgreSQL lock session must stop this worker, not use a
            # fresh connection to reset or complete another live worker's task.
            owner.execute("SELECT 1")
            owner.commit()

    @contextmanager
    def _consumer_lock(self) -> Iterator[Connection | bool]:
        if database_url() is None:
            with _worker_lock(self.path.with_suffix(".lock")) as acquired:
                yield acquired
            return
        with self.connect() as db:
            # Scope by schema so isolated validation schemas do not block live work.
            acquired = bool(db.execute(
                "SELECT pg_try_advisory_lock(hashtext(current_schema()), ?)",
                (2026102302,),
            ).fetchone()[0])
            db.commit()
            if not acquired:
                yield False
                return
            try:
                yield db
            finally:
                try:
                    db.rollback()
                    db.execute(
                        "SELECT pg_advisory_unlock(hashtext(current_schema()), ?)",
                        (2026102302,),
                    )
                    db.commit()
                except DATABASE_ERRORS:
                    # Never return an uncertain session lock to the shared pool.
                    db.raw.close()
                    raise

    def _update(
        self, book_id: str, status: str, stage: str, next_step: int, error: str = ""
    ) -> None:
        self._check_owner()
        with self.connect() as db:
            db.execute(
                "UPDATE preparation SET status=?,stage=?,next_step=?,error=?,retry_at=0,details=CASE WHEN ?='diagnostics' THEN details ELSE '{}' END,updated=? "
                "WHERE book_id=? AND status<>'cancelled'",
                (status, stage, next_step, error, stage, time.time(), book_id),
            )

    def process_one(self) -> bool:
        if getattr(self._owner, "connection", None) is None:
            with self._consumer_lock() as owner:
                if not owner:
                    return False
                self._owner.connection = owner
                try:
                    return self._process_one()
                finally:
                    del self._owner.connection
        return self._process_one()

    def _process_one(self) -> bool:
        self._check_owner()
        with self.connect() as db:
            db.begin_write(serialize=False)
            row = db.execute(
                "SELECT book_id,next_step FROM preparation WHERE status='queued' AND retry_at<=? "
                "ORDER BY updated LIMIT 1", (time.time(),)
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
                if self.stop_event.is_set() or (self.get(book_id) or {}).get("status") == "cancelled":
                    return True
                stage, action = self.steps[index]
                self._update(book_id, "running", stage, index)
                action(book_id)
                next_step = index + 1
                self._update(book_id, "running", stage, next_step)
            self._update(book_id, "succeeded", "ready", len(self.steps))
        except PreparationDeferred as pending:
            self._check_owner()
            with self.connect() as db:
                db.execute(
                    "UPDATE preparation SET status='queued',stage=?,next_step=?,error='',retry_at=?,details=?,updated=? "
                    "WHERE book_id=? AND status<>'cancelled'",
                    (stage, next_step, time.time() + max(1, pending.details.get("retry_after_seconds", 5)),
                     json.dumps(pending.details, ensure_ascii=False), time.time(), book_id),
                )
        except DATABASE_ERRORS:
            raise
        except Exception as error:
            logger.warning("book preparation failed at %s", stage,
                           extra={"error_type": type(error).__name__})
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
            try:
                with self._consumer_lock() as owner:
                    if owner:
                        self._owner.connection = owner
                        try:
                            self._run_owned()
                        finally:
                            del self._owner.connection
                        return
            except DATABASE_ERRORS:
                # Losing a PostgreSQL ownership connection ends this worker;
                # another process may now own the advisory lock. No blind reset.
                logger.warning("preparation worker lost database ownership")
                if database_url() is not None:
                    return
            self.stop_event.wait(self.poll_interval)

    def _run_owned(self) -> None:
        recover_pending = True
        while not self.stop_event.is_set():
            try:
                self._check_owner()
                if recover_pending:
                    with self.connect() as db:
                        db.begin_write()
                        db.execute("UPDATE preparation SET status='queued' WHERE status='running'")
                    recover_pending = False
                if self.process_one():
                    continue
            except DATABASE_ERRORS:
                if database_url() is not None:
                    raise
                recover_pending = True
                logger.warning("preparation storage temporarily unavailable")
            self.stop_event.wait(self.poll_interval)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
