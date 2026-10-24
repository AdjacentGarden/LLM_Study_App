from __future__ import annotations

import hashlib
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ..database import Connection, connect
from ..personalization.models import ChapterLearningBundle
from .diagnostic_cache import DiagnosticChapterCacheError, chapter_identity, validate_chapter_items
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

    @contextmanager
    def _connect(self) -> Iterator[Connection]:
        with connect(self.database_path) as connection:
            yield connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            if not connection.postgres:
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
                CREATE TABLE IF NOT EXISTS diagnostic_chapter_cache (
                    cache_key TEXT PRIMARY KEY,
                    scope TEXT NOT NULL,
                    identity_json TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('running','approved','rejected','not_admitted','unknown')),
                    claim_token TEXT NOT NULL,
                    items_json TEXT,
                    items_sha256 TEXT,
                    recovery_epoch INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diagnostic_chapter_submissions (
                    token TEXT PRIMARY KEY,
                    cache_key TEXT NOT NULL REFERENCES diagnostic_chapter_cache(cache_key),
                    state TEXT NOT NULL CHECK(state IN ('running','approved','rejected','not_admitted','unknown')),
                    recovery_epoch INTEGER NOT NULL DEFAULT 0,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_diagnostic_chapter_submission
                    ON diagnostic_chapter_submissions(cache_key,created_at);
                CREATE INDEX IF NOT EXISTS idx_diagnostic_chapter_scope
                    ON diagnostic_chapter_cache(scope,state);
                CREATE TABLE IF NOT EXISTS diagnostic_recovery_epochs (
                    scope TEXT PRIMARY KEY,
                    epoch INTEGER NOT NULL,
                    failed_marker TEXT NOT NULL,
                    updated_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS diagnostic_recovery_grants (
                    scope TEXT NOT NULL,
                    marker TEXT NOT NULL,
                    epoch INTEGER NOT NULL,
                    created_at REAL NOT NULL,
                    PRIMARY KEY(scope,marker)
                );
                """
            )
            if not connection.postgres:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(course_bundles)")}
                if "content_digest" not in columns:
                    connection.execute("ALTER TABLE course_bundles ADD COLUMN content_digest TEXT NOT NULL DEFAULT ''")

    def claim_diagnostic_chapter(self, identity: dict[str, Any], *, now: float | None = None) -> dict[str, Any]:
        """Short claim. Only an explicit recovery grant can supersede unknown work."""
        key, payload = chapter_identity(identity)
        timestamp = time.time() if now is None else now
        token = uuid.uuid4().hex
        with self._connect() as connection:
            connection.begin_write()
            grant = connection.execute("SELECT epoch FROM diagnostic_recovery_epochs WHERE scope=?",
                                       (identity["scope"],)).fetchone()
            epoch = int(grant[0]) if grant else 0
            row = connection.execute("SELECT * FROM diagnostic_chapter_cache WHERE cache_key=?", (key,)).fetchone()
            if row is not None:
                if row["identity_json"] != payload or row["scope"] != identity["scope"]:
                    raise DiagnosticChapterCacheError("chapter approval identity mismatch")
                state = str(row["state"])
                if state == "approved":
                    encoded = str(row["items_json"])
                    if hashlib.sha256(encoded.encode()).hexdigest() != row["items_sha256"]:
                        raise DiagnosticChapterCacheError("chapter approval content digest mismatch")
                    try:
                        items = _ITEMS.validate_json(encoded)
                    except (ValueError, TypeError) as error:
                        raise DiagnosticChapterCacheError("chapter approval content is invalid") from error
                    validate_chapter_items(identity, items)
                    return {"status": "approved", "token": None, "items": items}
                if state in {"running", "unknown"} and epoch <= int(row["recovery_epoch"]):
                    return {"status": state, "token": None, "items": None}
                if state == "running":
                    connection.execute(
                        "UPDATE diagnostic_chapter_submissions SET state='unknown',updated_at=? "
                        "WHERE token=? AND state='running'", (timestamp, row["claim_token"]),
                    )
                updated = connection.execute(
                    "UPDATE diagnostic_chapter_cache SET state='running',claim_token=?,recovery_epoch=?,updated_at=? "
                    "WHERE cache_key=? AND claim_token=? AND state=?",
                    (token, epoch, timestamp, key, row["claim_token"], state),
                )
                if updated.rowcount != 1:
                    raise DiagnosticChapterCacheError("chapter claim was concurrently changed")
            else:
                connection.execute(
                    "INSERT INTO diagnostic_chapter_cache(cache_key,scope,identity_json,state,claim_token,recovery_epoch,created_at,updated_at) "
                    "VALUES (?,?,?,'running',?,?,?,?)", (key, identity["scope"], payload, token, epoch, timestamp, timestamp),
                )
            connection.execute(
                "INSERT INTO diagnostic_chapter_submissions(token,cache_key,state,recovery_epoch,created_at,updated_at) "
                "VALUES (?,?,'running',?,?,?)", (token, key, epoch, timestamp, timestamp),
            )
        return {"status": "claimed", "token": token, "items": None}

    def has_pending_diagnostic_submission(self, scope: str) -> bool:
        """Read-only admission check; never derive a retry nonce from live tokens."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM diagnostic_chapter_cache "
                "WHERE scope=? AND state IN ('running','unknown') LIMIT 1", (scope,),
            ).fetchone()
        return row is not None

    def grant_diagnostic_recovery(self, scope: str, failed_marker: str, *, now: float | None = None) -> int:
        """One permission per failed preparation revision, never from startup/lease."""
        if not scope.strip() or len(scope) > 512 or not failed_marker or len(failed_marker) > 512:
            raise ValueError("invalid explicit diagnostic recovery permission")
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.begin_write()
            receipt = connection.execute("SELECT epoch FROM diagnostic_recovery_grants WHERE scope=? AND marker=?",
                                         (scope, failed_marker)).fetchone()
            if receipt is not None:
                return int(receipt[0])
            row = connection.execute("SELECT epoch,failed_marker FROM diagnostic_recovery_epochs WHERE scope=?", (scope,)).fetchone()
            if row is None:
                connection.execute("INSERT INTO diagnostic_recovery_epochs(scope,epoch,failed_marker,updated_at) VALUES (?,1,?,?)",
                                   (scope, failed_marker, timestamp))
                epoch = 1
            else:
                epoch = int(row["epoch"]) + 1
                connection.execute("UPDATE diagnostic_recovery_epochs SET epoch=?,failed_marker=?,updated_at=? WHERE scope=?",
                                   (epoch, failed_marker, timestamp, scope))
            connection.execute("INSERT INTO diagnostic_recovery_grants(scope,marker,epoch,created_at) VALUES (?,?,?,?)",
                               (scope, failed_marker, epoch, timestamp))
            return epoch

    def approve_diagnostic_chapter(self, identity: dict[str, Any], token: str, items: list[DiagnosticItem],
                                   *, now: float | None = None) -> None:
        validate_chapter_items(identity, items)
        key, payload = chapter_identity(identity)
        encoded = _ITEMS.dump_json(items).decode()
        digest = hashlib.sha256(encoded.encode()).hexdigest()
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.begin_write()
            updated = connection.execute(
                "UPDATE diagnostic_chapter_cache SET state='approved',items_json=?,items_sha256=?,updated_at=? "
                "WHERE cache_key=? AND identity_json=? AND claim_token=? AND state='running'",
                (encoded, digest, timestamp, key, payload, token),
            )
            submitted = connection.execute(
                "UPDATE diagnostic_chapter_submissions SET state='approved',updated_at=? "
                "WHERE token=? AND cache_key=? AND state='running'", (timestamp, token, key),
            )
            if updated.rowcount != 1 or submitted.rowcount != 1:
                raise DiagnosticChapterCacheError("stale chapter approval token")

    def fail_diagnostic_chapter(self, identity: dict[str, Any], token: str, outcome: str,
                                *, now: float | None = None) -> None:
        if outcome not in {"rejected", "not_admitted", "unknown"}:
            raise ValueError("invalid chapter submission outcome")
        key, payload = chapter_identity(identity)
        timestamp = time.time() if now is None else now
        with self._connect() as connection:
            connection.begin_write()
            updated = connection.execute(
                "UPDATE diagnostic_chapter_cache SET state=?,updated_at=? "
                "WHERE cache_key=? AND identity_json=? AND claim_token=? AND state='running'",
                (outcome, timestamp, key, payload, token),
            )
            submitted = connection.execute(
                "UPDATE diagnostic_chapter_submissions SET state=?,updated_at=? "
                "WHERE token=? AND cache_key=? AND state='running'", (outcome, timestamp, token, key),
            )
            if updated.rowcount != 1 or submitted.rowcount != 1:
                raise DiagnosticChapterCacheError("stale chapter submission token")

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
                '\n                INSERT INTO course_bundles(\n                    course_id, session_id, chapter_id, version, profile_fingerprint,\n                    source_fingerprint, bundle_json, created_at, content_digest\n                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
                (
                    bundle.course_id,
                    session_id,
                    bundle.chapter_id,
                    bundle.version,
                    bundle.profile_fingerprint,
                    bundle.source_fingerprint,
                    bundle.model_dump_json(),
                    bundle.created_at.timestamp(),
                    hashlib.sha256(bundle.model_dump_json().encode()).hexdigest(),
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
                "UPDATE course_bundles SET bundle_json=?,content_digest=? WHERE course_id=?",
                (bundle.model_dump_json(), hashlib.sha256(bundle.model_dump_json().encode()).hexdigest(), bundle.course_id),
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
            connection.begin_write()
            inserted = connection.execute(
                '\n                INSERT INTO learning_events(\n                    event_id, session_id, course_id, item_id, event_type,\n                    payload_json, created_at\n                ) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT DO NOTHING',
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
