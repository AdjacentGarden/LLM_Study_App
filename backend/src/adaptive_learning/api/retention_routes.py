"""Private, durable reading questions and read-only FSRS return queue."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..assessment.models import InterviewSession
from ..assessment.repository import SQLiteAssessmentRepository
from ..community import CommunityRepository


class DoubtInput(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    book_id: str = Field(min_length=1, max_length=100)
    question: str = Field(min_length=1, max_length=2000)
    chapter_id: str = Field(default="", max_length=150)
    chapter_title: str = Field(default="", max_length=250)
    excerpt: str = Field(default="", max_length=3000)
    pages: list[int] = Field(default_factory=list, max_length=30)


class DoubtUpdate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")
    action: Literal["resolve", "reopen", "snooze"]
    note: str = Field(default="", max_length=2000)


def retention_router(
    repo: CommunityRepository,
    visitor: Callable[..., str],
    owned_book: Callable[[str, str], None],
    owned_session: Callable[[str, str], InterviewSession],
    assessments: Callable[[], SQLiteAssessmentRepository],
) -> APIRouter:
    router = APIRouter(tags=["learning-return"])
    with repo.connect() as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS reading_doubts (
                id TEXT PRIMARY KEY, owner TEXT NOT NULL, book_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, question TEXT NOT NULL,
                chapter_id TEXT NOT NULL, chapter_title TEXT NOT NULL,
                excerpt TEXT NOT NULL, pages TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'open', note TEXT NOT NULL DEFAULT '',
                due_at REAL NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                UNIQUE(owner,book_id,fingerprint)
            );
            CREATE INDEX IF NOT EXISTS idx_doubts_owner_book
                ON reading_doubts(owner,book_id,status,due_at);
        """)

    def public(row: sqlite3.Row) -> dict[str, Any]:
        value = dict(row)
        value.pop("owner", None)
        value.pop("fingerprint", None)
        value["pages"] = json.loads(value["pages"])
        return value

    @router.get("/learning/doubts")
    def doubts(book_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, book_id)
        with repo.connect() as db:
            rows = db.execute(
                "SELECT * FROM reading_doubts WHERE owner=? AND book_id=? ORDER BY updated DESC LIMIT 200",
                (owner, book_id),
            ).fetchall()
        return {"items": [public(row) for row in rows], "now": time.time()}

    @router.post("/learning/doubts")
    def save(data: DoubtInput, owner: str = Depends(visitor)) -> dict[str, Any]:
        owned_book(owner, data.book_id)
        if any(p < 1 or p > 100000 for p in data.pages):
            raise HTTPException(422, "页码不正确")
        fingerprint = hashlib.sha256(
            " ".join(data.question.split()).casefold().encode()
        ).hexdigest()
        now = time.time()
        with repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute(
                "SELECT * FROM reading_doubts WHERE owner=? AND book_id=? AND fingerprint=?",
                (owner, data.book_id, fingerprint),
            ).fetchone()
            if old:
                return public(old)
            count = db.execute(
                "SELECT COUNT(*) FROM reading_doubts WHERE owner=? AND book_id=?",
                (owner, data.book_id),
            ).fetchone()[0]
            if count >= 200:
                raise HTTPException(409, "这本书已保存 200 个疑问，请先整理已有记录")
            key = uuid.uuid4().hex
            db.execute(
                "INSERT INTO reading_doubts(id,owner,book_id,fingerprint,question,chapter_id,chapter_title,excerpt,pages,due_at,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    key,
                    owner,
                    data.book_id,
                    fingerprint,
                    data.question,
                    data.chapter_id,
                    data.chapter_title,
                    data.excerpt,
                    json.dumps(sorted(set(data.pages))),
                    now + 3 * 86400,
                    now,
                    now,
                ),
            )
            return public(db.execute("SELECT * FROM reading_doubts WHERE id=?", (key,)).fetchone())

    @router.post("/learning/doubts/{key}")
    def update(key: str, data: DoubtUpdate, owner: str = Depends(visitor)) -> dict[str, Any]:
        now = time.time()
        with repo.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT * FROM reading_doubts WHERE id=? AND owner=?", (key, owner)
            ).fetchone()
            if not row:
                raise HTTPException(404, "疑问不存在")
            owned_book(owner, row["book_id"])
            status = "resolved" if data.action == "resolve" else "open"
            due = now + (7 if data.action == "snooze" else 0) * 86400
            note = data.note if data.action == "resolve" else row["note"]
            db.execute(
                "UPDATE reading_doubts SET status=?,note=?,due_at=?,updated=? WHERE id=? AND owner=?",
                (status, note, due, now, key, owner),
            )
            return public(db.execute("SELECT * FROM reading_doubts WHERE id=?", (key,)).fetchone())

    @router.get("/learning/memory/{session_id}")
    def memory(session_id: str, owner: str = Depends(visitor)) -> dict[str, Any]:
        session = owned_session(owner, session_id)
        return memory_snapshot(assessments(), session)

    return router


def memory_snapshot(
    repository: SQLiteAssessmentRepository,
    session: InterviewSession,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    # Reuse the exact reviewed card/version: never regenerate a course to review it.
    cards: dict[str, dict[str, Any]] = {}
    with repository._connect() as db:
        for row in db.execute(
            "SELECT course_id,bundle_json FROM course_bundles WHERE session_id=? ORDER BY created_at DESC",
            (session.session_id,),
        ):
            bundle = json.loads(row["bundle_json"])
            for card in bundle.get("flashcards", []):
                cards.setdefault(
                    card["card_id"],
                    {
                        "card": card,
                        "course_id": row["course_id"],
                        "chapter_title": bundle["chapter_title"],
                        "chapter_id": bundle["chapter_id"],
                    },
                )
        events = db.execute(
            "SELECT item_id,json_extract(payload_json,'$.review_state.last_rating') AS rating,created_at FROM learning_events WHERE session_id=? AND event_type='flashcard_review' ORDER BY created_at",
            (session.session_id,),
        ).fetchall()
    history = []
    previous: dict[str, float] = {}
    for row in events:
        key = row["item_id"]
        at = row["created_at"]
        gap = (at - previous[key]) / 86400 if key in previous else 0
        previous[key] = at
        history.append(
            {
                "card_id": key,
                "at": at,
                "rating": row["rating"],
                "gap_days": round(gap, 2),
                "delayed": gap >= 1,
                "week_later": gap >= 7,
            }
        )
    items = []
    for key, state in session.profile.flashcard_reviews.items():
        if key not in cards or not state.due_at:
            continue
        items.append(
            {
                **cards[key],
                "due_at": state.due_at.isoformat(),
                "due": state.due_at <= now,
                "last_rating": state.last_rating,
                "repetitions": state.repetitions,
            }
        )
    items.sort(key=lambda item: item["due_at"])
    week = [h for h in history if h["week_later"]]
    return {
        "items": items,
        "due_count": sum(i["due"] for i in items),
        "reviewed_count": len(items),
        "delayed_count": sum(h["delayed"] for h in history),
        "week_recalled": sum(h["rating"] in {"good", "easy"} for h in week),
        "week_checks": len(week),
        "history": history[-50:],
        "now": now.isoformat(),
    }
