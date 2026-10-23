"""Restart-safe post-OCR queue; only one worker holds the process lock at a time."""
from __future__ import annotations

import fcntl
import logging
import sqlite3
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)


class PreparationQueue:
    def __init__(self, path: Path, steps: list[tuple[str, Callable[[str], object]]]):
        self.path, self.steps = path, steps
        path.parent.mkdir(parents=True, exist_ok=True)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        with self.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS preparation (book_id TEXT PRIMARY KEY, status TEXT NOT NULL, stage TEXT NOT NULL, error TEXT NOT NULL DEFAULT '', updated REAL NOT NULL)")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, book_id: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT * FROM preparation WHERE book_id=?", (book_id,)).fetchone()
        return dict(row) if row else None

    def enqueue(self, book_id: str, *, retry: bool = False, refresh: bool = False) -> dict:
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO preparation(book_id,status,stage,updated) VALUES(?,'queued','queued',?)", (book_id, time.time()))
            if retry:
                db.execute("UPDATE preparation SET status='queued',stage='queued',error='',updated=? WHERE book_id=? AND status='failed'", (time.time(), book_id))
            if refresh:
                db.execute("UPDATE preparation SET status='queued',stage='queued',error='',updated=? WHERE book_id=? AND status='succeeded'", (time.time(), book_id))
        return self.get(book_id)  # type: ignore[return-value]

    def update(self, book_id: str, status: str, stage: str, error: str = "") -> None:
        with self.connect() as db:
            db.execute("UPDATE preparation SET status=?,stage=?,error=?,updated=? WHERE book_id=?", (status, stage, error, time.time(), book_id))

    def process_one(self) -> bool:
        with self.connect() as db:
            row = db.execute("SELECT book_id FROM preparation WHERE status='queued' ORDER BY updated LIMIT 1").fetchone()
        if row is None:
            return False
        book_id = row[0]
        try:
            for stage, action in self.steps:
                self.update(book_id, "running", stage)
                action(book_id)
            self.update(book_id, "succeeded", "ready")
        except Exception as error:
            logger.warning("book preparation failed", extra={"error_type": type(error).__name__})
            self.update(book_id, "failed", stage, "教材整理暂未完成，已保存处理进度，请重试。")
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, daemon=True, name="book-preparation")
        self.thread.start()

    def loop(self) -> None:
        with self.path.with_suffix(".lock").open("a") as lock:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.stop_event.wait(1)
            else:
                return
            with self.connect() as db:
                db.execute("UPDATE preparation SET status='queued' WHERE status='running'")
            while not self.stop_event.is_set():
                try:
                    if self.process_one():
                        continue
                except sqlite3.Error:
                    logger.warning("preparation storage temporarily unavailable")
                self.stop_event.wait(.5)

    def stop(self) -> None:
        self.stop_event.set()
        if self.thread:
            self.thread.join(timeout=2)
