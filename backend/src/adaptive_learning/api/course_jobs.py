"""Durable single-consumer course generation, independent of HTTP request lifetime."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import sqlite3
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)

_EXHAUSTED_COURSE_MESSAGE = '此任务已停止，本版本的尝试次数已用完。任务和学习记录已保留；请先检查服务状态，待可用的新版本就绪后再生成。'


class CourseJobInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    chapter_id: str = Field(min_length=1, max_length=100)
    retry: bool = False
    request_id: str | None = Field(default=None, min_length=1, max_length=128)


class CourseJobFailure(RuntimeError):
    """A user-safe failure supplied by the publication boundary."""


class CourseAdmissionDeferred(CourseJobFailure):
    """A known local cooldown rejected work before a generation attempt."""

    def __init__(self, retry_after_seconds: float) -> None:
        super().__init__('模型服务正在冷却，本次未启动新的课程生成，请稍后重试。')
        self.retry_after_seconds = retry_after_seconds


class CourseRetryLimit(RuntimeError):
    pass


class CourseJobQueue:
    def __init__(self, path: Path, generate: Callable[[str, str], dict[str, Any]], *,
                 max_attempts: int = 3,
                 admission_check: Callable[[], None] | None = None) -> None:
        self.path, self.generate, self.max_attempts = path, generate, max_attempts
        self.admission_check = admission_check
        self.stop_event = threading.Event()
        self.wake = threading.Event()
        self.thread: threading.Thread | None = None
        path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('''CREATE TABLE IF NOT EXISTS course_jobs (
                job_id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL,
                session_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                status TEXT NOT NULL, stage TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT, result TEXT, created REAL NOT NULL, updated REAL NOT NULL)''')
            db.execute('''CREATE TABLE IF NOT EXISTS course_job_requests (
                session_id TEXT NOT NULL, request_id TEXT NOT NULL, job_id TEXT NOT NULL,
                PRIMARY KEY(session_id, request_id))''')

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def _public(self, row: sqlite3.Row) -> dict[str, Any]:
        retryable = row['status'] == 'failed' and row['attempts'] < self.max_attempts
        error = row['error']
        if row['status'] == 'failed' and not retryable:
            # Disclosure only: preserve the durable failure/cause and attempt limit.
            error = _EXHAUSTED_COURSE_MESSAGE
        return {
            'job_id': row['job_id'], 'session_id': row['session_id'],
            'chapter_id': row['chapter_id'], 'status': row['status'], 'stage': row['stage'],
            'attempts': row['attempts'], 'error': error,
            'retryable': retryable,
            'created_at': datetime.fromtimestamp(row['created'], UTC).isoformat(),
            'updated_at': datetime.fromtimestamp(row['updated'], UTC).isoformat(),
            'course': json.loads(row['result']) if row['status'] == 'succeeded' and row['result'] else None,
        }

    def enqueue(self, session_id: str, chapter_id: str, fingerprint: str, *, retry: bool = False,
                request_id: str | None = None) -> dict[str, Any]:
        key = hashlib.sha256(json.dumps([session_id, chapter_id, fingerprint]).encode()).hexdigest()
        now = time.time()
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = None
            if request_id:
                row = db.execute('''SELECT j.* FROM course_jobs j JOIN course_job_requests r
                    ON j.job_id=r.job_id WHERE r.session_id=? AND r.request_id=?''',
                    (session_id, request_id)).fetchone()
                if row is not None and row['chapter_id'] != chapter_id:
                    raise CourseJobFailure('该请求编号已用于其他章节，请保留原任务。')
            if row is None:
                row = db.execute('SELECT * FROM course_jobs WHERE request_key=?', (key,)).fetchone()
            if row is None:
                if self.admission_check is not None:
                    # Pure local check before any durable job/request mutation.
                    self.admission_check()
                job_id = 'course_job_' + uuid.uuid4().hex
                db.execute('''INSERT INTO course_jobs(job_id,request_key,session_id,chapter_id,
                    status,stage,created,updated) VALUES(?,?,?,?,'queued','queued',?,?)''',
                    (job_id, key, session_id, chapter_id, now, now))
            else:
                job_id = row['job_id']
                if row['status'] == 'failed' and retry:
                    if row['attempts'] >= self.max_attempts:
                        raise CourseRetryLimit(_EXHAUSTED_COURSE_MESSAGE)
                    if self.admission_check is not None:
                        self.admission_check()
                    db.execute("UPDATE course_jobs SET status='queued',stage='queued',error=NULL,result=NULL,updated=? WHERE job_id=?", (now, job_id))
            if request_id:
                db.execute('INSERT OR IGNORE INTO course_job_requests VALUES(?,?,?)',
                           (session_id, request_id, job_id))
            result = self._public(db.execute('SELECT * FROM course_jobs WHERE job_id=?', (job_id,)).fetchone())
        self.wake.set()
        return result

    def get(self, session_id: str, job_id: str) -> dict[str, Any] | None:
        with self.connect() as db:
            row = db.execute('SELECT * FROM course_jobs WHERE job_id=? AND session_id=?', (job_id, session_id)).fetchone()
        return self._public(row) if row is not None else None

    def recover(self) -> None:
        # Called only after obtaining the exclusive worker lock, so a live owner
        # cannot be mistaken for a crashed worker in another application process.
        with self.connect() as db:
            db.execute("UPDATE course_jobs SET status='queued',stage='queued',updated=? WHERE status='running' AND attempts<?", (time.time(), self.max_attempts))
            db.execute("UPDATE course_jobs SET status='failed',stage='failed',error=?,updated=? WHERE status='running'", ('任务多次中断，已有学习内容已保留，请检查服务状态。', time.time()))

    def process_one(self) -> bool:
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT * FROM course_jobs WHERE status='queued' ORDER BY created,job_id LIMIT 1").fetchone()
            if row is None:
                return False
            if self.admission_check is not None:
                try:
                    self.admission_check()
                except CourseAdmissionDeferred:
                    # Keep the queued row intact and use the existing loop wait.
                    # No processor, alias, or generation attempt starts here.
                    return False
            db.execute("UPDATE course_jobs SET status='running',stage='generating',attempts=attempts+1,updated=? WHERE job_id=?", (time.time(), row['job_id']))
        try:
            course = self.generate(row['session_id'], row['chapter_id'])
            encoded = json.dumps(course, ensure_ascii=False, allow_nan=False)
        except Exception as error:
            # Provider responses and private audit text must not enter task status.
            message = str(error)[:300] if isinstance(error, CourseJobFailure) else '课程暂未生成或未通过审核，任务已保存，可以重试。'
            logger.warning('course task failed', extra={'error_type': type(error).__name__})
            with self.connect() as db:
                db.execute("UPDATE course_jobs SET status='failed',stage='failed',error=?,updated=? WHERE job_id=?", (message, time.time(), row['job_id']))
        else:
            with self.connect() as db:
                db.execute("UPDATE course_jobs SET status='succeeded',stage='ready',error=NULL,result=?,updated=? WHERE job_id=?", (encoded, time.time(), row['job_id']))
        return True

    def start(self) -> None:
        if self.thread and self.thread.is_alive():
            return
        self.stop_event.clear()
        self.thread = threading.Thread(target=self.loop, name='course-generation', daemon=True)
        self.thread.start()

    def loop(self) -> None:
        with self.path.with_suffix('.lock').open('a') as lock:
            while not self.stop_event.is_set():
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    self.stop_event.wait(.2)
            else:
                return
            self.recover()
            while not self.stop_event.is_set():
                self.wake.clear()
                try:
                    if self.process_one():
                        continue
                except sqlite3.Error:
                    logger.warning('course task storage temporarily unavailable')
                self.wake.wait(.5)

    def stop(self) -> None:
        self.stop_event.set()
        self.wake.set()
        if self.thread:
            self.thread.join(timeout=2)

    def is_alive(self) -> bool:
        return bool(self.thread and self.thread.is_alive())
