from __future__ import annotations

import json
import logging
import math
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .models import BookStructure

logger = logging.getLogger(__name__)


class JobStateError(RuntimeError):
    """Raised when a worker tries to mutate a job it no longer owns."""


@dataclass(frozen=True, slots=True)
class StoredBook:
    book_id: str
    original_name: str
    file_path: Path
    status: str
    progress: float
    current_step: str


@dataclass(frozen=True, slots=True)
class OCRJob:
    job_id: int
    book_id: str
    source_path: Path
    output_dir: Path
    status: str
    progress: float
    current_step: str
    attempts: int
    max_attempts: int
    lease_owner: str | None
    lease_expires_at: float | None
    last_error: str | None
    quality_score: float | None
    needs_human_review: bool
    page_count: int | None
    created_at: float
    updated_at: float
    finished_at: float | None


@dataclass(frozen=True, slots=True)
class OCRRunResult:
    quality_score: float
    needs_human_review: bool
    page_count: int


class OCRRunner(Protocol):
    def run(self, job: OCRJob, heartbeat: Callable[[float, str], bool]) -> OCRRunResult: ...


class SQLiteOCRJobRepository:
    """Durable single-queue repository with atomic leases and restart recovery."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def source_fingerprint(self, book_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute("SELECT source_sha256 FROM books WHERE book_id=?", (book_id,)).fetchone()
        return str(row[0]) if row else None

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS books (
                    book_id TEXT PRIMARY KEY,
                    original_name TEXT NOT NULL,
                    file_path TEXT NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL CHECK(progress >= 0 AND progress <= 1),
                    current_step TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ocr_jobs (
                    job_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    book_id TEXT NOT NULL UNIQUE REFERENCES books(book_id) ON DELETE CASCADE,
                    output_dir TEXT NOT NULL,
                    status TEXT NOT NULL,
                    progress REAL NOT NULL CHECK(progress >= 0 AND progress <= 1),
                    current_step TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL CHECK(max_attempts >= 1),
                    available_at REAL NOT NULL,
                    lease_owner TEXT,
                    lease_expires_at REAL,
                    last_error TEXT,
                    result_json TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    finished_at REAL
                );
                CREATE INDEX IF NOT EXISTS idx_ocr_jobs_claim
                    ON ocr_jobs(status, available_at, created_at);
                CREATE TABLE IF NOT EXISTS book_structures (
                    book_id TEXT PRIMARY KEY REFERENCES books(book_id) ON DELETE CASCADE,
                    structure_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    def register_book(
        self,
        *,
        book_id: str,
        original_name: str,
        file_path: Path,
        source_sha256: str,
        now: float | None = None,
    ) -> StoredBook:
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO books(
                    book_id, original_name, file_path, source_sha256, status, progress,
                    current_step, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'uploaded', 0, '等待文档重建', ?, ?)
                """,
                (book_id, original_name, str(file_path), source_sha256, timestamp, timestamp),
            )
        book = self.get_book(book_id)
        assert book is not None
        return book

    def get_book(self, book_id: str) -> StoredBook | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT book_id, original_name, file_path, status, progress, current_step
                FROM books WHERE book_id = ?""",
                (book_id,),
            ).fetchone()
        if row is None:
            return None
        return StoredBook(
            book_id=str(row["book_id"]),
            original_name=str(row["original_name"]),
            file_path=Path(str(row["file_path"])),
            status=str(row["status"]),
            progress=float(row["progress"]),
            current_step=str(row["current_step"]),
        )

    def enqueue(
        self,
        *,
        book_id: str,
        output_dir: Path,
        max_attempts: int,
        now: float | None = None,
        force_retry: bool = False,
    ) -> OCRJob:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            book = connection.execute(
                "SELECT book_id, status FROM books WHERE book_id = ?", (book_id,)
            ).fetchone()
            if book is None:
                connection.rollback()
                raise KeyError(book_id)
            existing = connection.execute(
                "SELECT * FROM ocr_jobs WHERE book_id = ?", (book_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """
                    INSERT INTO ocr_jobs(
                        book_id, output_dir, status, progress, current_step, attempts,
                        max_attempts, available_at, created_at, updated_at
                    ) VALUES (?, ?, 'queued', 0.02, '已进入 GPU 文档重建队列', 0, ?, ?, ?, ?)
                    """,
                    (book_id, str(output_dir), max_attempts, timestamp, timestamp, timestamp),
                )
            elif force_retry and str(existing["status"]) in {"failed", "ocr_review_required"}:
                connection.execute(
                    """
                    UPDATE ocr_jobs SET status='queued', progress=0.02,
                        current_step='已重新进入 GPU 文档重建队列', attempts=0,
                        max_attempts=?, available_at=?, lease_owner=NULL,
                        lease_expires_at=NULL, last_error=NULL, result_json=NULL,
                        updated_at=?, finished_at=NULL WHERE book_id=?
                    """,
                    (max_attempts, timestamp, timestamp, book_id),
                )
            preserve_structure = (
                existing is not None and str(book["status"]) == "structured" and not force_retry
            )
            if not preserve_structure:
                connection.execute(
                    """
                    UPDATE books SET status=(SELECT status FROM ocr_jobs WHERE book_id=?),
                        progress=(SELECT progress FROM ocr_jobs WHERE book_id=?),
                        current_step=(SELECT current_step FROM ocr_jobs WHERE book_id=?),
                        updated_at=? WHERE book_id=?
                    """,
                    (book_id, book_id, book_id, timestamp, book_id),
                )
            connection.commit()
        job = self.get_job(book_id)
        assert job is not None
        return job

    def recover_expired(self, *, now: float | None = None) -> int:
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            expired = connection.execute(
                """SELECT book_id, attempts, max_attempts FROM ocr_jobs
                WHERE status='running' AND lease_expires_at < ?""",
                (timestamp,),
            ).fetchall()
            for row in expired:
                exhausted = int(row["attempts"]) >= int(row["max_attempts"])
                status = "failed" if exhausted else "queued"
                step = "OCR 工作进程中断，等待手动重试" if exhausted else "检测到中断，正在自动恢复"
                connection.execute(
                    """
                    UPDATE ocr_jobs SET status=?, current_step=?, available_at=?,
                        lease_owner=NULL, lease_expires_at=NULL, updated_at=?,
                        finished_at=CASE WHEN ?='failed' THEN ? ELSE NULL END
                    WHERE book_id=?
                    """,
                    (status, step, timestamp, timestamp, status, timestamp, row["book_id"]),
                )
                connection.execute(
                    """UPDATE books SET status=?, current_step=?, updated_at=? WHERE book_id=?""",
                    (status, step, timestamp, row["book_id"]),
                )
            connection.commit()
        return len(expired)

    def claim_next(
        self,
        *,
        owner: str,
        lease_seconds: float,
        now: float | None = None,
    ) -> OCRJob | None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        timestamp = time.time() if now is None else now
        self.recover_expired(now=timestamp)
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """
                SELECT job_id, book_id FROM ocr_jobs
                WHERE status IN ('queued', 'retry_wait') AND available_at <= ?
                ORDER BY created_at, job_id LIMIT 1
                """,
                (timestamp,),
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            step = "正在进行页面识别与版面还原"
            connection.execute(
                """
                UPDATE ocr_jobs SET status='running', progress=MAX(progress, 0.05),
                    current_step=?, attempts=attempts+1, lease_owner=?,
                    lease_expires_at=?, updated_at=? WHERE job_id=?
                """,
                (step, owner, timestamp + lease_seconds, timestamp, row["job_id"]),
            )
            connection.execute(
                """UPDATE books SET status='running', progress=MAX(progress, 0.05),
                current_step=?, updated_at=? WHERE book_id=?""",
                (step, timestamp, row["book_id"]),
            )
            connection.commit()
        return self.get_job(str(row["book_id"]))

    def heartbeat(
        self,
        *,
        job_id: int,
        owner: str,
        lease_seconds: float,
        progress: float,
        current_step: str,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        bounded_progress = min(0.98, max(0.05, progress))
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE ocr_jobs SET progress=MAX(progress, ?), current_step=?,
                    lease_expires_at=?, updated_at=?
                WHERE job_id=? AND status='running' AND lease_owner=?
                """,
                (
                    bounded_progress,
                    current_step,
                    timestamp + lease_seconds,
                    timestamp,
                    job_id,
                    owner,
                ),
            )
            if cursor.rowcount:
                connection.execute(
                    """UPDATE books SET progress=MAX(progress, ?), current_step=?, updated_at=?
                    WHERE book_id=(SELECT book_id FROM ocr_jobs WHERE job_id=?)""",
                    (bounded_progress, current_step, timestamp, job_id),
                )
        return bool(cursor.rowcount)

    def succeed(
        self,
        *,
        job_id: int,
        owner: str,
        result: OCRRunResult,
        now: float | None = None,
    ) -> None:
        timestamp = time.time() if now is None else now
        status = "ocr_review_required" if result.needs_human_review else "ocr_ready"
        step = "OCR 完成，存在需复核页面" if result.needs_human_review else "OCR 文本已通过质量门控"
        payload = json.dumps(
            {
                "quality_score": result.quality_score,
                "needs_human_review": result.needs_human_review,
                "page_count": result.page_count,
            },
            ensure_ascii=False,
        )
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE ocr_jobs SET status=?, progress=1, current_step=?, result_json=?,
                    lease_owner=NULL, lease_expires_at=NULL, last_error=NULL,
                    updated_at=?, finished_at=?
                WHERE job_id=? AND status='running' AND lease_owner=?
                """,
                (status, step, payload, timestamp, timestamp, job_id, owner),
            )
            if not cursor.rowcount:
                connection.rollback()
                raise JobStateError("OCR job lease is no longer owned by this worker")
            connection.execute(
                """UPDATE books SET status=?, progress=1, current_step=?, updated_at=?
                WHERE book_id=(SELECT book_id FROM ocr_jobs WHERE job_id=?)""",
                (status, step, timestamp, job_id),
            )
            connection.commit()

    def fail(
        self,
        *,
        job_id: int,
        owner: str,
        error: str,
        retry_delay_seconds: float,
        now: float | None = None,
    ) -> str:
        timestamp = time.time() if now is None else now
        safe_error = " ".join(error.split())[:1000]
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT attempts, max_attempts FROM ocr_jobs
                WHERE job_id=? AND status='running' AND lease_owner=?""",
                (job_id, owner),
            ).fetchone()
            if row is None:
                connection.rollback()
                raise JobStateError("OCR job lease is no longer owned by this worker")
            exhausted = int(row["attempts"]) >= int(row["max_attempts"])
            status = "failed" if exhausted else "retry_wait"
            step = "OCR 处理失败，可重新尝试" if exhausted else "OCR 暂时失败，等待自动重试"
            available_at = timestamp if exhausted else timestamp + max(0, retry_delay_seconds)
            connection.execute(
                """
                UPDATE ocr_jobs SET status=?, current_step=?, available_at=?,
                    lease_owner=NULL, lease_expires_at=NULL, last_error=?, updated_at=?,
                    finished_at=CASE WHEN ?='failed' THEN ? ELSE NULL END WHERE job_id=?
                """,
                (status, step, available_at, safe_error, timestamp, status, timestamp, job_id),
            )
            connection.execute(
                """UPDATE books SET status=?, current_step=?, updated_at=?
                WHERE book_id=(SELECT book_id FROM ocr_jobs WHERE job_id=?)""",
                (status, step, timestamp, job_id),
            )
            connection.commit()
        return status

    def release(self, *, job_id: int, owner: str, now: float | None = None) -> bool:
        """Return a cooperatively cancelled job to the queue without spending an attempt."""
        timestamp = time.time() if now is None else now
        step = "服务更新完成后将自动继续 OCR"
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                """
                UPDATE ocr_jobs SET status='queued', attempts=MAX(0, attempts-1),
                    current_step=?, available_at=?, lease_owner=NULL,
                    lease_expires_at=NULL, updated_at=?
                WHERE job_id=? AND status='running' AND lease_owner=?
                """,
                (step, timestamp, timestamp, job_id, owner),
            )
            if cursor.rowcount:
                connection.execute(
                    """UPDATE books SET status='queued', current_step=?, updated_at=?
                    WHERE book_id=(SELECT book_id FROM ocr_jobs WHERE job_id=?)""",
                    (step, timestamp, job_id),
                )
            connection.commit()
        return bool(cursor.rowcount)

    def get_job(self, book_id: str) -> OCRJob | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT j.*, b.file_path FROM ocr_jobs j JOIN books b USING(book_id)
                WHERE j.book_id=?
                """,
                (book_id,),
            ).fetchone()
        return None if row is None else self._job(row)

    def save_structure(
        self, book_id: str, structure: BookStructure, *, now: float | None = None
    ) -> None:
        timestamp = time.time() if now is None else now
        payload = structure.model_dump_json()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if (
                connection.execute("SELECT 1 FROM books WHERE book_id=?", (book_id,)).fetchone()
                is None
            ):
                connection.rollback()
                raise KeyError(book_id)
            connection.execute(
                """
                INSERT INTO book_structures(book_id, structure_json, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    structure_json=excluded.structure_json,
                    updated_at=excluded.updated_at
                """,
                (book_id, payload, timestamp, timestamp),
            )
            connection.execute(
                """UPDATE books SET status='structured', progress=1,
                current_step='章节结构与摘要已生成', updated_at=? WHERE book_id=?""",
                (timestamp, book_id),
            )
            connection.commit()

    def get_structure(self, book_id: str) -> BookStructure | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT structure_json FROM book_structures WHERE book_id=?", (book_id,)
            ).fetchone()
        if row is None:
            return None
        return BookStructure.model_validate_json(str(row["structure_json"]))

    @staticmethod
    def _job(row: sqlite3.Row) -> OCRJob:
        result = json.loads(str(row["result_json"])) if row["result_json"] else {}
        return OCRJob(
            job_id=int(row["job_id"]),
            book_id=str(row["book_id"]),
            source_path=Path(str(row["file_path"])),
            output_dir=Path(str(row["output_dir"])),
            status=str(row["status"]),
            progress=float(row["progress"]),
            current_step=str(row["current_step"]),
            attempts=int(row["attempts"]),
            max_attempts=int(row["max_attempts"]),
            lease_owner=None if row["lease_owner"] is None else str(row["lease_owner"]),
            lease_expires_at=(
                None if row["lease_expires_at"] is None else float(row["lease_expires_at"])
            ),
            last_error=None if row["last_error"] is None else str(row["last_error"]),
            quality_score=(
                None if result.get("quality_score") is None else float(result["quality_score"])
            ),
            needs_human_review=bool(result.get("needs_human_review", False)),
            page_count=None if result.get("page_count") is None else int(result["page_count"]),
            created_at=float(row["created_at"]),
            updated_at=float(row["updated_at"]),
            finished_at=None if row["finished_at"] is None else float(row["finished_at"]),
        )


class SubprocessOCRRunner:
    def __init__(
        self,
        *,
        backend: str,
        language: str = "ch",
        heartbeat_interval: float = 5,
        timeout_seconds: float = 4 * 60 * 60,
    ) -> None:
        self.backend = backend
        self.language = language
        self.heartbeat_interval = heartbeat_interval
        self.timeout_seconds = timeout_seconds

    def run(self, job: OCRJob, heartbeat: Callable[[float, str], bool]) -> OCRRunResult:
        job.output_dir.mkdir(parents=True, exist_ok=True)
        attempt_dir = job.output_dir / (
            f"attempt-{job.attempts:02d}-{int(time.time())}-{uuid.uuid4().hex[:8]}"
        )
        attempt_dir.mkdir(parents=True, exist_ok=False)
        log_path = attempt_dir / "worker.log"
        command = [
            sys.executable,
            "-m",
            "adaptive_learning.ingestion.ocr_job",
            "--source",
            str(job.source_path),
            "--output",
            str(attempt_dir),
            "--backend",
            self.backend,
            "--language",
            self.language,
        ]
        started = time.monotonic()
        from .ocr_job import pdf_page_count

        page_count = pdf_page_count(job.source_path)
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                stdout=log,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
            )
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if elapsed > self.timeout_seconds:
                    _terminate_process_group(process)
                    raise TimeoutError("OCR subprocess exceeded its execution limit")
                estimated = estimate_ocr_progress(elapsed, page_count)
                if not heartbeat(estimated, "正在识别页面并重建阅读顺序"):
                    _terminate_process_group(process)
                    raise JobStateError("OCR worker lost its lease")
                time.sleep(self.heartbeat_interval)
            if process.returncode != 0:
                diagnostic = _log_tail(attempt_dir / "mineru.log")
                raise RuntimeError(
                    f"OCR subprocess exited with code {process.returncode}; {diagnostic}"
                )
        report_path = attempt_dir / "normalized" / "quality_report.json"
        if not report_path.is_file():
            raise RuntimeError("OCR subprocess completed without a quality report")
        report = json.loads(report_path.read_text(encoding="utf-8"))
        canonical = job.output_dir / "normalized"
        shutil.copytree(attempt_dir / "normalized", canonical, dirs_exist_ok=True)
        _write_canonical_hashes(canonical)
        bands = report.get("quality_band_counts", {})
        needs_review = any(int(bands.get(name, 0)) for name in ("missing", "rescue"))
        return OCRRunResult(
            quality_score=float(report["mean_heuristic_score"]),
            needs_human_review=needs_review,
            page_count=int(report["page_count_normalized"]),
        )


def estimate_ocr_progress(elapsed_seconds: float, page_count: int | None) -> float:
    """Smooth progress estimate that accounts for model startup and document length."""
    pages = page_count or 20
    expected_seconds = 120 + max(1, pages) * 1.5
    progress = 0.08 + 0.86 * (1 - math.exp(-max(0, elapsed_seconds) / expected_seconds))
    return min(0.94, progress)


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    descendants = _descendant_pids(process.pid)
    try:
        for pid in reversed(descendants):
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=10)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    for pid in reversed(descendants):
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OCR subprocess tree could not be terminated") from error


def _descendant_pids(root_pid: int) -> list[int]:
    """Snapshot descendants before a parent exits and they are re-parented by the OS."""
    try:
        completed = subprocess.run(
            ["ps", "-eo", "pid=,ppid="], capture_output=True, text=True, check=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError):
        return []
    children: dict[int, list[int]] = {}
    for line in completed.stdout.splitlines():
        fields = line.split()
        if len(fields) != 2:
            continue
        try:
            pid, parent = (int(field) for field in fields)
        except ValueError:
            continue
        children.setdefault(parent, []).append(pid)
    descendants: list[int] = []
    pending = list(children.get(root_pid, []))
    while pending:
        pid = pending.pop()
        descendants.append(pid)
        pending.extend(children.get(pid, []))
    return descendants


def _log_tail(path: Path, lines: int = 24) -> str:
    if not path.is_file():
        return "MinerU diagnostic log is unavailable"
    content = path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
    return " | ".join(line.strip() for line in content if line.strip())[-4000:]


def _write_canonical_hashes(normalized_dir: Path) -> None:
    from .ocr_job import sha256_file

    names = ("quality_report.json", "quality_report.md", "pages.jsonl", "full_text.md")
    paths = [normalized_dir / name for name in names]
    missing = [path.name for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError(f"normalized OCR output is incomplete: {', '.join(missing)}")
    manifest = "\n".join(f"{sha256_file(path)}  {path.name}" for path in paths)
    (normalized_dir / "SHA256SUMS").write_text(manifest + "\n", encoding="utf-8")


class OCRWorker:
    def __init__(
        self,
        *,
        repository: SQLiteOCRJobRepository,
        runner: OCRRunner,
        lease_seconds: float = 90,
        retry_delay_seconds: float = 30,
        poll_interval: float = 1,
        owner: str | None = None,
    ) -> None:
        self.repository = repository
        self.runner = runner
        self.lease_seconds = lease_seconds
        self.retry_delay_seconds = retry_delay_seconds
        self.poll_interval = poll_interval
        self.owner = owner or f"worker-{uuid.uuid4().hex}"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def process_once(self) -> bool:
        job = self.repository.claim_next(owner=self.owner, lease_seconds=self.lease_seconds)
        if job is None:
            return False

        def heartbeat(progress: float, step: str) -> bool:
            if self._stop.is_set():
                return False
            return self.repository.heartbeat(
                job_id=job.job_id,
                owner=self.owner,
                lease_seconds=self.lease_seconds,
                progress=progress,
                current_step=step,
            )

        try:
            result = self.runner.run(job, heartbeat)
            self.repository.succeed(job_id=job.job_id, owner=self.owner, result=result)
        except JobStateError:
            if self._stop.is_set():
                self.repository.release(job_id=job.job_id, owner=self.owner)
            logger.warning("OCR job lease was lost", extra={"book_id": job.book_id})
        except Exception as error:
            logger.exception("OCR job failed", extra={"book_id": job.book_id})
            try:
                self.repository.fail(
                    job_id=job.job_id,
                    owner=self.owner,
                    error=f"{type(error).__name__}: {error}",
                    retry_delay_seconds=self.retry_delay_seconds,
                )
            except JobStateError:
                logger.warning(
                    "OCR failure arrived after lease loss", extra={"book_id": job.book_id}
                )
        return True

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name=self.owner, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 15) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.is_set():
            if not self.process_once():
                self._stop.wait(self.poll_interval)
