"""Persisted study-plan checkmarks and reviews of genuinely graded mistakes.

The plan is a relative sequence of days, not a calendar reservation. A task
checkmark is never used as a mastery score. Practice grades continue to come
only from the existing course activity endpoint.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, cast

from .assessment.models import InterviewSession
from .assessment.repository import SQLiteAssessmentRepository
from .community import CommunityRepository
from .ingestion.jobs import SQLiteOCRJobRepository
from .ingestion.models import BookStructure, ChapterDraft
from .personalization.models import ChapterLearningBundle, PublicPracticeItem

TaskStatus = Literal["todo", "done"]
ReviewStatus = Literal["unreviewed", "reviewing", "self_reported_mastered"]
TaskKind = Literal["reading", "practice", "flashcards"]


class WorkspaceError(Exception):
    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def _stable_id(prefix: str, *parts: str) -> str:
    digest = hashlib.sha256("\x1f".join(parts).encode()).hexdigest()[:24]
    return f"{prefix}_{digest}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StudyWorkspace:
    def __init__(
        self,
        database_path: Path,
        assessments: SQLiteAssessmentRepository,
        jobs: SQLiteOCRJobRepository,
        community: CommunityRepository,
    ) -> None:
        self.database_path = database_path
        self.assessments = assessments
        self.jobs = jobs
        self.community = community
        database_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS study_task_states (
                    session_id TEXT NOT NULL,
                    task_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('todo','done')),
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id,task_id)
                );
                CREATE TABLE IF NOT EXISTS study_mistake_reviews (
                    session_id TEXT NOT NULL,
                    mistake_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN
                        ('unreviewed','reviewing','self_reported_mastered')),
                    reason TEXT,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(session_id,mistake_id)
                );
                """
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _session(self, owner: str | None, session_id: str) -> InterviewSession:
        if not owner:
            raise WorkspaceError(401, "请先登录或建立访客身份")
        session = self.assessments.get_session(session_id)
        if session is None:
            raise WorkspaceError(404, "学习档案不存在")
        if not self.community.owns(owner, session.profile.book_id):
            raise WorkspaceError(403, "请先将这本书加入自己的书架")
        # Legacy sessions lacked an owner row. An owner of the same book can
        # bind that opaque session once, matching the existing community API.
        if not self.community.claim_session(owner, session_id):
            raise WorkspaceError(403, "该学习档案不属于当前账号")
        return session

    def _events(self, session_id: str) -> list[dict[str, Any]]:
        db = sqlite3.connect(self.assessments.database_path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                """SELECT event_id,course_id,item_id,event_type,payload_json,created_at
                   FROM learning_events WHERE session_id=?
                   AND event_type IN ('practice_answer','flashcard_review')
                   ORDER BY created_at,event_id""",
                (session_id,),
            ).fetchall()
        finally:
            db.close()
        events: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(str(row["payload_json"]))
            except (ValueError, TypeError):
                continue
            if not isinstance(payload, dict):
                continue
            events.append({**dict(row), "payload": payload})
        return events

    @staticmethod
    def _units(
        session: InterviewSession, structure: BookStructure | None
    ) -> tuple[list[tuple[str, str, str, str | None, int | None, int | None, bool]], dict[str, str]]:
        """Return real leaf units and an ID to top-level chapter mapping."""
        if structure is None:
            # Historical sessions keep their saved real chapter labels. Course
            # existence decides whether their non-reading tasks are available.
            return (
                [
                    (str(option["id"]), str(option["label"]), str(option["label"]), None, None, None, False)
                    for option in session.chapter_options
                ],
                {str(option["id"]): str(option["id"]) for option in session.chapter_options},
            )
        chapters = structure.chapters
        by_id = {chapter.chapter_id: chapter for chapter in chapters}
        children: dict[str, list[ChapterDraft]] = defaultdict(list)
        for chapter in chapters:
            parent_id = getattr(chapter, "parent_id", None)
            if parent_id in by_id and parent_id != chapter.chapter_id:
                children[parent_id].append(chapter)

        def root_id(chapter: ChapterDraft) -> str:
            current = chapter
            seen: set[str] = set()
            while (parent_id := getattr(current, "parent_id", None)) in by_id:
                if parent_id in seen or parent_id == current.chapter_id:
                    break
                seen.add(parent_id)
                current = by_id[parent_id]
            return current.chapter_id

        roots = [chapter for chapter in chapters if root_id(chapter) == chapter.chapter_id]
        mapping = {chapter.chapter_id: root_id(chapter) for chapter in chapters}
        units: list[tuple[str, str, str, str | None, int | None, int | None, bool]] = []

        def visit(chapter: ChapterDraft, root: ChapterDraft, seen: set[str]) -> None:
            if chapter.chapter_id in seen:
                return
            seen.add(chapter.chapter_id)
            descendants = children.get(chapter.chapter_id, [])
            if descendants:
                for child in descendants:
                    visit(child, root, seen)
                return
            has_evidence = any(
                chapter.knowledge_point_evidence.get(label)
                for label in chapter.knowledge_points
            )
            units.append(
                (
                    chapter.chapter_id,
                    root.title,
                    chapter.title,
                    chapter.chapter_id if chapter.chapter_id != root.chapter_id else None,
                    chapter.start_page,
                    chapter.end_page,
                    bool(has_evidence),
                )
            )

        seen: set[str] = set()
        for root in roots:
            visit(root, root, seen)
        return units, mapping

    def _plan(
        self,
        session: InterviewSession,
        structure: BookStructure | None,
        courses: list[ChapterLearningBundle],
        events: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        units, roots = self._units(session, structure)
        if not units:
            raise WorkspaceError(409, "该学习档案尚无可核验章节")
        course_by_unit: dict[str, list[ChapterLearningBundle]] = defaultdict(list)
        for course in courses:
            course_by_unit[course.chapter_id].append(course)

        def activity_ids(course: ChapterLearningBundle, kind: TaskKind) -> list[str]:
            if kind == "practice":
                return [item.item_id for item in course.practice_items]
            if kind == "flashcards":
                return [card.card_id for card in course.flashcards]
            return []

        graded = {
            (str(event["course_id"]), str(event["item_id"]))
            for event in events
            if event["event_type"] == "practice_answer"
            and float(event["payload"].get("evidence", {}).get("scoring_confidence", 0)) > 0
        }
        reviewed = {
            (str(event["course_id"]), str(event["item_id"]))
            for event in events
            if event["event_type"] == "flashcard_review"
        }
        with self._connect() as db:
            saved = {
                str(row["task_id"]): str(row["status"])
                for row in db.execute(
                    "SELECT task_id,status FROM study_task_states WHERE session_id=?",
                    (session.session_id,),
                )
            }
        historical_roots: set[str] = set()
        if structure is not None:
            for chapter in structure.chapters:
                if roots.get(chapter.chapter_id) != chapter.chapter_id:
                    continue
                if not any(root == chapter.chapter_id and unit_id != chapter.chapter_id for unit_id, root in roots.items()):
                    continue
                previous_task = any(
                    _stable_id("task", session.session_id, chapter.chapter_id, kind) in saved
                    for kind in ("reading", "practice", "flashcards")
                )
                if not course_by_unit.get(chapter.chapter_id) and not previous_task:
                    continue
                historical_roots.add(chapter.chapter_id)
                position = next(
                    (index for index, unit in enumerate(units) if roots[unit[0]] == chapter.chapter_id),
                    len(units),
                )
                units.insert(
                    position,
                    (
                        chapter.chapter_id, chapter.title, chapter.title, None,
                        chapter.start_page, chapter.end_page, bool(chapter.knowledge_points),
                    ),
                )
        minutes = session.profile.constraints.minutes_per_day
        # Pydantic's default is persisted as 30 even for older profiles; do not
        # claim that the learner explicitly chose it before confirmation.
        source = "profile" if session.phase.value == "complete" else "default"
        days: list[dict[str, Any]] = []
        day: dict[str, Any] = {"day_index": 1, "estimated_minutes": 0, "tasks": []}
        completed = 0
        total = 0
        for unit_id, root_title, unit_title, section_id, start, end, has_evidence in units:
            chapter_id = roots[unit_id]
            bundles = course_by_unit.get(unit_id, [])
            current_course = max(bundles, key=lambda bundle: bundle.version) if bundles else None
            historical = unit_id in historical_roots
            task_kinds: tuple[TaskKind, ...] = ("reading", "practice", "flashcards")
            # A saved historical course is evidence that the unit was
            # compilable even when a later structure revision lost its quotes.
            if historical:
                kinds: list[TaskKind] = [
                    kind for kind in task_kinds
                    if bundles or _stable_id("task", session.session_id, unit_id, kind) in saved
                ]
            else:
                kinds = ["reading"]
                if has_evidence or bundles:
                    kinds.extend(("practice", "flashcards"))
            page_span = max(1, (end or 1) - (start or 1) + 1)
            estimates = {"reading": max(8, min(30, 3 * page_span)), "practice": 15, "flashcards": 8}
            for kind in kinds:
                task_id = _stable_id("task", session.session_id, unit_id, kind)
                event_keys = graded if kind == "practice" else reviewed
                activity_count = (
                    sum(
                        (current_course.course_id, item_id) in event_keys
                        for item_id in activity_ids(current_course, kind)
                    )
                    if kind != "reading" and current_course is not None
                    else 0
                )
                activity_done = bool(
                    kind != "reading" and current_course is not None
                    and (item_ids := activity_ids(current_course, kind))
                    and all((current_course.course_id, item_id) in event_keys for item_id in item_ids)
                )
                marked = saved.get(task_id)
                # An actual completed set of graded/reviewed activities wins
                # over an older manual todo checkmark. A manual status never
                # creates a score or suppresses later learning evidence.
                status = "done" if activity_done else (marked or "todo")
                completion_source = (
                    "activity" if activity_done else "self_report" if marked == "done" else None
                )
                estimate = min(minutes, estimates[kind])
                if day["tasks"] and day["estimated_minutes"] + estimate > minutes:
                    days.append(day)
                    day = {"day_index": len(days) + 1, "estimated_minutes": 0, "tasks": []}
                task = {
                    "task_id": task_id,
                    "kind": kind,
                    "chapter_id": chapter_id,
                    "chapter_title": root_title,
                    "section_id": section_id,
                    "section_title": unit_title if section_id else None,
                    "title": f"{'历史整章 · ' if historical else ''}{'阅读' if kind == 'reading' else '完成练习' if kind == 'practice' else '复习闪卡'}《{unit_title}》",
                    "historical": historical,
                    "estimated_minutes": estimate,
                    "status": status,
                    "completion_source": completion_source,
                    "activity_count": activity_count,
                    "source_start_page": start if kind == "reading" else None,
                    "source_end_page": end if kind == "reading" else None,
                }
                day["tasks"].append(task)
                day["estimated_minutes"] += estimate
                total += 1
                completed += status == "done"
        if day["tasks"]:
            days.append(day)
        return (
            {
                "minutes_per_day": minutes,
                "minutes_source": source,
                "days": days,
                "progress": {
                    "done": completed,
                    "total": total,
                    "percent": round(100 * completed / total) if total else 0,
                },
            },
            roots,
        )

    def _mistakes(
        self,
        session_id: str,
        courses: list[ChapterLearningBundle],
        events: list[dict[str, Any]],
        roots: dict[str, str],
        titles: dict[str, str],
        chapter_id: str | None,
    ) -> list[dict[str, Any]]:
        by_course = {course.course_id: course for course in courses}
        attempts: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
        for event in events:
            if event["event_type"] != "practice_answer":
                continue
            evidence = event["payload"].get("evidence", {})
            if not isinstance(evidence, dict) or float(evidence.get("scoring_confidence", 0)) <= 0:
                continue
            attempts[(str(event["course_id"]), str(event["item_id"]))].append(event)
        with self._connect() as db:
            saved = {
                str(row["mistake_id"]): dict(row)
                for row in db.execute(
                    "SELECT mistake_id,status,reason,updated_at FROM study_mistake_reviews WHERE session_id=?",
                    (session_id,),
                )
            }
        mistakes: list[dict[str, Any]] = []
        for (course_id, item_id), item_events in attempts.items():
            course = by_course.get(course_id)
            practice = (
                next((item for item in course.practice_items if item.item_id == item_id), None)
                if course else None
            )
            if course is None:
                # A deleted historical bundle cannot be scored again. Keep its
                # graded observation below, with an explicit non-retry state.
                historical_profile = item_events[0]["payload"].get("profile", {})
                historical_observations = (
                    historical_profile.get("diagnostic_observations", [])
                    if isinstance(historical_profile, dict) else []
                )
                unit_id = str(historical_observations[-1].get("chapter_id", "")) if historical_observations else ""
            else:
                unit_id = course.chapter_id
            root_id = roots.get(unit_id, unit_id)
            if chapter_id and chapter_id not in {root_id, unit_id}:
                continue
            wrong = [event for event in item_events if float(event["payload"]["evidence"]["score"]) < 0.65]
            if not wrong:
                continue
            # One stable review record per original course item, even after
            # multiple retries. The most recent wrong attempt is the review
            # context; latest_attempt separately reports the last real grade.
            event = wrong[-1]
            latest = item_events[-1]
            evidence = event["payload"]["evidence"]
            score = float(evidence["score"])
            profile = event["payload"].get("profile", {})
            observations = profile.get("diagnostic_observations", []) if isinstance(profile, dict) else []
            observation = observations[-1] if observations and isinstance(observations[-1], dict) else {}
            if observation.get("item_id") != item_id:
                observation = {}
            mistake_id = _stable_id("mistake", session_id, course_id, item_id)
            state = saved.get(mistake_id)
            snapshot = observation.get("question_snapshot")
            public_item = (
                PublicPracticeItem.from_private(practice).model_dump()
                if practice else {
                    "item_id": item_id,
                    "point_id": "",
                    "prompt": snapshot or "历史题目（原内容暂不可用）",
                    "response_type": "unavailable",
                    "options": [],
                    "estimated_seconds": 0,
                }
            )
            mistakes.append(
                {
                    "mistake_id": mistake_id,
                    "chapter_id": root_id,
                    "chapter_title": titles.get(root_id, course.chapter_title if course else "历史章节"),
                    "section_id": unit_id if unit_id and unit_id != root_id else None,
                    "course_id": course_id,
                    "course_version": course.version if course else None,
                    "item_id": item_id,
                    "practice_item": public_item,
                    "attempt": {
                        "answer": observation.get("answer_snapshot"),
                        "score": score,
                        "scoring_confidence": float(evidence["scoring_confidence"]),
                        "at": datetime.fromtimestamp(float(event["created_at"]), UTC).isoformat(),
                    },
                    "review": {
                        "status": state["status"] if state else "unreviewed",
                        "reason": state["reason"] if state else None,
                        "updated_at": state["updated_at"] if state else None,
                    },
                    # Only a previously attempted item may reveal this private
                    # review material. Unattempted keys stay in the bundle.
                    "review_material": {
                        "expected_answer": practice.expected_answer if practice else None,
                        "correct_option_ids": practice.correct_option_ids if practice else [],
                        "citations": [citation.model_dump() for citation in practice.citations]
                        if practice else [],
                    },
                    "can_retry": practice is not None,
                    "attempt_count": len(item_events),
                    "latest_attempt": {
                        "score": float(latest["payload"]["evidence"]["score"]),
                        "at": datetime.fromtimestamp(float(latest["created_at"]), UTC).isoformat(),
                    },
                }
            )
        mistakes.sort(key=lambda item: item["attempt"]["at"], reverse=True)
        return mistakes

    def get(self, owner: str | None, session_id: str, chapter_id: str | None = None) -> dict[str, Any]:
        session = self._session(owner, session_id)
        structure = self.jobs.get_structure(session.profile.book_id)
        courses = self.assessments.courses_for_session(session_id)
        events = self._events(session_id)
        plan, roots = self._plan(session, structure, courses, events)
        titles = {str(option["id"]): str(option["label"]) for option in session.chapter_options}
        if structure is not None:
            titles.update({chapter.chapter_id: chapter.title for chapter in structure.chapters})
        return {
            "session_id": session_id,
            "book_id": session.profile.book_id,
            "plan": plan,
            "mistakes": self._mistakes(session_id, courses, events, roots, titles, chapter_id),
        }

    def set_task(
        self, owner: str | None, session_id: str, task_id: str, status: TaskStatus
    ) -> dict[str, Any]:
        workspace = self.get(owner, session_id)
        task = next(
            (item for day in workspace["plan"]["days"] for item in day["tasks"] if item["task_id"] == task_id),
            None,
        )
        if task is None:
            raise WorkspaceError(404, "学习任务不存在")
        with self._connect() as db:
            old = db.execute(
                "SELECT status FROM study_task_states WHERE session_id=? AND task_id=?",
                (session_id, task_id),
            ).fetchone()
            if old is None or old["status"] != status:
                db.execute(
                    """INSERT INTO study_task_states(session_id,task_id,status,updated_at)
                       VALUES(?,?,?,?) ON CONFLICT(session_id,task_id)
                       DO UPDATE SET status=excluded.status,updated_at=excluded.updated_at""",
                    (session_id, task_id, status, _now()),
                )
        refreshed = self.get(owner, session_id)
        effective = next(
            item for day in refreshed["plan"]["days"] for item in day["tasks"]
            if item["task_id"] == task_id
        )
        return cast(dict[str, Any], effective)

    def set_mistake(
        self,
        owner: str | None,
        session_id: str,
        mistake_id: str,
        status: ReviewStatus,
        reason: str | None,
        reason_given: bool,
    ) -> dict[str, Any]:
        workspace = self.get(owner, session_id)
        mistake = next(
            (item for item in workspace["mistakes"] if item["mistake_id"] == mistake_id),
            None,
        )
        if mistake is None:
            raise WorkspaceError(404, "错题记录不存在")
        with self._connect() as db:
            old = db.execute(
                "SELECT status,reason,updated_at FROM study_mistake_reviews WHERE session_id=? AND mistake_id=?",
                (session_id, mistake_id),
            ).fetchone()
            next_reason = reason if reason_given else (str(old["reason"]) if old and old["reason"] is not None else None)
            if old is not None and old["status"] == status and old["reason"] == next_reason:
                updated_at = str(old["updated_at"])
            else:
                updated_at = _now()
                db.execute(
                    """INSERT INTO study_mistake_reviews(session_id,mistake_id,status,reason,updated_at)
                       VALUES(?,?,?,?,?) ON CONFLICT(session_id,mistake_id)
                       DO UPDATE SET status=excluded.status,reason=excluded.reason,
                                     updated_at=excluded.updated_at""",
                    (session_id, mistake_id, status, next_reason, updated_at),
                )
        mistake["review"] = {"status": status, "reason": next_reason, "updated_at": updated_at}
        return cast(dict[str, Any], mistake)
