from __future__ import annotations

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from adaptive_learning.ingestion.preparation import PreparationQueue


def wait_for(queue: PreparationQueue, book_id: str, status: str) -> dict:
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        value = queue.get(book_id)
        if value and value["status"] == status:
            return value
        time.sleep(0.01)
    raise AssertionError(f"preparation did not reach {status}: {queue.get(book_id)}")


def test_retry_resumes_failed_stage_without_rebuilding_successful_work(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    fail = True

    def diagnostics(book_id: str) -> None:
        calls.append((book_id, "diagnostics"))
        if fail:
            raise ValueError("private source path must not be returned")

    steps = [
        ("indexing", lambda book: calls.append((book, "indexing"))),
        ("structuring", lambda book: calls.append((book, "structuring"))),
        ("diagnostics", diagnostics),
    ]
    path = tmp_path / "preparation.sqlite3"
    first = PreparationQueue(path, steps)
    first.enqueue("book")
    assert first.process_one()
    failed = first.get("book")
    assert failed["status"] == "failed" and failed["stage"] == "diagnostics"
    assert failed["next_step"] == 2 and "private source" not in failed["error"]
    assert first.enqueue("book")["status"] == "failed"
    assert not first.process_one()

    fail = False
    restarted = PreparationQueue(path, steps)
    restarted.enqueue("book", retry=True)
    assert restarted.process_one()
    assert restarted.get("book")["status"] == "succeeded"
    assert calls == [("book", "indexing"), ("book", "structuring"), ("book", "diagnostics"), ("book", "diagnostics")]
    assert restarted.enqueue("book", retry=True)["status"] == "succeeded"
    assert not restarted.process_one()


def test_restart_recovers_interrupted_stage_and_keeps_books_separate(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    queue = PreparationQueue(
        tmp_path / "jobs.sqlite3",
        [
            ("indexing", lambda book: calls.append((book, "indexing"))),
            ("diagnostics", lambda book: calls.append((book, "diagnostics"))),
        ],
        poll_interval=0.01,
    )
    queue.enqueue("interrupted")
    queue.enqueue("second")
    with queue.connect() as db:
        db.execute("UPDATE preparation SET status='running',stage='diagnostics',next_step=1 WHERE book_id='interrupted'")
    queue.start()
    try:
        assert wait_for(queue, "interrupted", "succeeded")["stage"] == "ready"
        wait_for(queue, "second", "succeeded")
    finally:
        queue.stop()
    assert calls.count(("interrupted", "indexing")) == 0
    assert calls.count(("interrupted", "diagnostics")) == 1
    assert calls.count(("second", "indexing")) == 1
    assert calls.count(("second", "diagnostics")) == 1


def test_concurrent_queue_claim_executes_one_book_once(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def work(book: str) -> None:
        calls.append(book)
        entered.set()
        assert release.wait(2)

    path = tmp_path / "queue.sqlite3"
    a = PreparationQueue(path, [("indexing", work)])
    b = PreparationQueue(path, [("indexing", work)])
    a.enqueue("book")
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(a.process_one)
        assert entered.wait(2)
        assert not b.process_one()
        release.set()
        assert first.result()
    assert calls == ["book"]


def test_only_lock_holder_recovers_running_jobs(tmp_path: Path) -> None:
    entered = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def work(book: str) -> None:
        calls.append(book)
        entered.set()
        assert release.wait(2)

    path = tmp_path / "queue.sqlite3"
    first = PreparationQueue(path, [("indexing", work)], poll_interval=0.01)
    second = PreparationQueue(path, [("indexing", work)], poll_interval=0.01)
    first.enqueue("book")
    first.start()
    assert entered.wait(2)
    second.start()
    try:
        assert second.get("book")["status"] == "running"
        release.set()
        wait_for(first, "book", "succeeded")
    finally:
        release.set()
        first.stop()
        second.stop()
    assert calls == ["book"]


def test_legacy_queue_migration_is_safe_during_concurrent_process_start(tmp_path: Path, monkeypatch) -> None:
    # This case exercises the SQLite-only ALTER migration, even in a PG suite.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)
    path = tmp_path / "queue.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE preparation (book_id TEXT PRIMARY KEY,status TEXT NOT NULL,stage TEXT NOT NULL,error TEXT NOT NULL DEFAULT '',updated REAL NOT NULL)")
        db.execute("INSERT INTO preparation VALUES ('book','failed','diagnostics','saved failure',0)")

    def create(_: int) -> PreparationQueue:
        return PreparationQueue(path, [("diagnostics", lambda book: None)])

    with ThreadPoolExecutor(max_workers=4) as pool:
        queues = list(pool.map(create, range(4)))
    assert queues[0].get("book")["status"] == "failed"
    assert queues[0].enqueue("book", retry=True)["next_step"] == 0
    assert queues[0].process_one()
    assert queues[1].get("book")["status"] == "succeeded"


def test_worker_recovers_claim_after_temporary_storage_failure(tmp_path: Path, monkeypatch) -> None:
    # SQLite errors recover under the same file lock; PG owner loss is a separate fence test.
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_HOST", raising=False)
    queue = PreparationQueue(tmp_path / "queue.sqlite3", [("indexing", lambda book: None)], poll_interval=0.01)
    queue.enqueue("book")
    real_process = queue.process_one
    failed_once = False

    def process() -> bool:
        nonlocal failed_once
        if not failed_once:
            failed_once = True
            with queue.connect() as db:
                db.execute("UPDATE preparation SET status='running' WHERE book_id='book'")
            raise sqlite3.OperationalError("database temporarily unavailable")
        return real_process()

    monkeypatch.setattr(queue, "process_one", process)
    queue.start()
    try:
        wait_for(queue, "book", "succeeded")
    finally:
        queue.stop()
    assert failed_once
