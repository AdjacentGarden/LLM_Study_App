from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from pydantic import TypeAdapter

from ..personalization.models import ChapterLearningBundle
from .models import DiagnosticItem, InterviewSession

_ITEMS = TypeAdapter(list[DiagnosticItem])


class SessionConflictError(RuntimeError):
    pass


class SQLiteAssessmentRepository:
    """Durable item banks and optimistic-lock interview sessions."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS diagnostic_banks (
                    book_id TEXT PRIMARY KEY,
                    structure_fingerprint TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS interview_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    book_id TEXT NOT NULL,
                    phase TEXT NOT NULL,
                    revision INTEGER NOT NULL CHECK(revision >= 0),
                    session_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_interview_user_book
                    ON interview_sessions(user_id, book_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS course_bundles (
                    course_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL,
                    version INTEGER NOT NULL CHECK(version >= 1),
                    profile_fingerprint TEXT NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    bundle_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    UNIQUE(session_id, chapter_id, version)
                );
                CREATE INDEX IF NOT EXISTS idx_course_latest
                    ON course_bundles(session_id, chapter_id, version DESC);
                CREATE TABLE IF NOT EXISTS learning_events (
                    event_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    course_id TEXT NOT NULL,
                    item_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    def save_bank(
        self,
        *,
        book_id: str,
        structure_fingerprint: str,
        items: list[DiagnosticItem],
        now: float | None = None,
    ) -> None:
        if not items:
            raise ValueError("diagnostic bank must not be empty")
        timestamp = time.time() if now is None else now
        payload = _ITEMS.dump_json(items).decode()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO diagnostic_banks(
                    book_id, structure_fingerprint, items_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(book_id) DO UPDATE SET
                    structure_fingerprint=excluded.structure_fingerprint,
                    items_json=excluded.items_json,
                    updated_at=excluded.updated_at
                """,
                (book_id, structure_fingerprint, payload, timestamp, timestamp),
            )

    def get_bank(
        self, book_id: str, *, expected_fingerprint: str | None = None
    ) -> list[DiagnosticItem] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT structure_fingerprint, items_json FROM diagnostic_banks WHERE book_id=?",
                (book_id,),
            ).fetchone()
        if row is None:
            return None
        if (
            expected_fingerprint is not None
            and str(row["structure_fingerprint"]) != expected_fingerprint
        ):
            return None
        return _ITEMS.validate_json(str(row["items_json"]))

    def create_session(self, session: InterviewSession, *, now: float | None = None) -> None:
        if session.revision != 0:
            raise ValueError("new session revision must be zero")
        timestamp = time.time() if now is None else now
        payload = session.model_dump_json()
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO interview_sessions(
                    session_id, user_id, book_id, phase, revision, session_json,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, 0, ?, ?, ?)
                """,
                (
                    session.session_id,
                    session.profile.user_id,
                    session.profile.book_id,
                    session.phase.value,
                    payload,
                    timestamp,
                    timestamp,
                ),
            )

    def get_session(self, session_id: str) -> InterviewSession | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT revision, session_json FROM interview_sessions WHERE session_id=?",
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        session = InterviewSession.model_validate_json(str(row["session_json"]))
        session.revision = int(row["revision"])
        return session

    def save_session(self, session: InterviewSession, *, now: float | None = None) -> None:
        timestamp = time.time() if now is None else now
        previous_revision = session.revision
        session.revision += 1
        payload = session.model_dump_json()
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE interview_sessions SET phase=?, revision=?, session_json=?, updated_at=?
                WHERE session_id=? AND revision=?
                """,
                (
                    session.phase.value,
                    session.revision,
                    payload,
                    timestamp,
                    session.session_id,
                    previous_revision,
                ),
            )
        if cursor.rowcount != 1:
            session.revision = previous_revision
            raise SessionConflictError("interview session was changed by another request")

    def next_course_version(self, session_id: str, chapter_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(version) AS latest FROM course_bundles WHERE session_id=? AND chapter_id=?",
                (session_id, chapter_id),
            ).fetchone()
        return 1 if row is None or row["latest"] is None else int(row["latest"]) + 1

    def save_course(self, session_id: str, bundle: ChapterLearningBundle) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO course_bundles(
                    course_id, session_id, chapter_id, version, profile_fingerprint,
                    source_fingerprint, bundle_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    bundle.course_id,
                    session_id,
                    bundle.chapter_id,
                    bundle.version,
                    bundle.profile_fingerprint,
                    bundle.source_fingerprint,
                    bundle.model_dump_json(),
                    bundle.created_at.timestamp(),
                ),
            )
        return cursor.rowcount == 1

    def get_latest_course(self, session_id: str, chapter_id: str) -> ChapterLearningBundle | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT bundle_json FROM course_bundles
                WHERE session_id=? AND chapter_id=? ORDER BY version DESC LIMIT 1
                """,
                (session_id, chapter_id),
            ).fetchone()
        return (
            None if row is None else ChapterLearningBundle.model_validate_json(row["bundle_json"])
        )

    def get_course(self, course_id: str) -> ChapterLearningBundle | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT bundle_json FROM course_bundles WHERE course_id=?", (course_id,)
            ).fetchone()
        return (
            None if row is None else ChapterLearningBundle.model_validate_json(row["bundle_json"])
        )

    def courses_for_session(self, session_id: str) -> list[ChapterLearningBundle]:
        """Read only this session's saved content, including previously reviewed versions."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT bundle_json FROM course_bundles WHERE session_id=? ORDER BY version",
                (session_id,),
            ).fetchall()
        return [ChapterLearningBundle.model_validate_json(row["bundle_json"]) for row in rows]

    def update_reviewed_course(self, bundle: ChapterLearningBundle) -> None:
        """Refresh wording without replacing IDs, versions or learning-event history."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE course_bundles SET bundle_json=? WHERE course_id=?",
                (bundle.model_dump_json(), bundle.course_id),
            )

    def get_course_for_session(
        self, session_id: str, course_id: str
    ) -> ChapterLearningBundle | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT bundle_json FROM course_bundles WHERE session_id=? AND course_id=?",
                (session_id, course_id),
            ).fetchone()
        return (
            None if row is None else ChapterLearningBundle.model_validate_json(row["bundle_json"])
        )

    def save_session_with_event(
        self,
        session: InterviewSession,
        *,
        event_id: str,
        course_id: str,
        item_id: str,
        event_type: str,
        payload_json: str,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        previous_revision = session.revision
        session.revision += 1
        session_payload = session.model_dump_json()
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO learning_events(
                    event_id, session_id, course_id, item_id, event_type,
                    payload_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    session.session_id,
                    course_id,
                    item_id,
                    event_type,
                    payload_json,
                    timestamp,
                ),
            )
            if inserted.rowcount == 0:
                connection.rollback()
                session.revision = previous_revision
                return False
            updated = connection.execute(
                """
                UPDATE interview_sessions SET phase=?, revision=?, session_json=?, updated_at=?
                WHERE session_id=? AND revision=?
                """,
                (
                    session.phase.value,
                    session.revision,
                    session_payload,
                    timestamp,
                    session.session_id,
                    previous_revision,
                ),
            )
            if updated.rowcount != 1:
                connection.rollback()
                session.revision = previous_revision
                raise SessionConflictError("interview session was changed by another request")
            connection.commit()
        return True

    def get_event_payload(self, session_id: str, event_id: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM learning_events WHERE session_id=? AND event_id=?",
                (session_id, event_id),
            ).fetchone()
        return None if row is None else str(row["payload_json"])
