"""Study workspace uses persisted book evidence and account-scoped events."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaptive_learning.accounts import Accounts
from adaptive_learning.api.study_workspace_routes import study_workspace_router
from adaptive_learning.assessment.models import (
    DiagnosticObservation,
    InterviewPhase,
    InterviewSession,
    LearnerProfile,
)
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import SQLiteOCRJobRepository
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.study_workspace import StudyWorkspace


def _chapter(
    chapter_id: str,
    title: str,
    start: int,
    end: int,
    *,
    parent_id: str | None = None,
    point: str | None = None,
) -> ChapterDraft:
    return ChapterDraft(
        chapter_id=chapter_id,
        order=start,
        title=title,
        start_page=start,
        end_page=end,
        summary=title,
        knowledge_points=[point] if point else [],
        source_block_ids=[f"block-{chapter_id}"] if point else [],
        evidence=[SourceQuote(page_number=start, quote=f"教材原文证据：{point}。")]
        if point else [],
        knowledge_point_evidence={
            point: [SourceQuote(page_number=start, quote=f"教材原文证据：{point}。")]
        } if point else {},
        parent_id=parent_id,
        level=2 if parent_id else 1,
    )


@pytest.fixture
def workspace_api(tmp_path: Path):
    jobs = SQLiteOCRJobRepository(tmp_path / "ocr.sqlite3")
    assessments = SQLiteAssessmentRepository(tmp_path / "assessment.sqlite3")
    community = CommunityRepository(tmp_path / "community.sqlite3")
    source = tmp_path / "book.pdf"
    source.write_bytes(b"only a job repository fixture; no PDF is read")
    jobs.register_book(book_id="book-a", original_name="Book A", file_path=source, source_sha256="book-a-source")
    structure = BookStructure(
        title="Book A",
        summary="Real structure fixture",
        source_page_count=7,
        chapters=[
            _chapter("chapter-1", "第一章", 1, 4, point="父章概念"),
            _chapter("section-1", "第一节", 1, 2, parent_id="chapter-1", point="能验证的叶子概念"),
            _chapter("section-2", "第二节", 3, 4, parent_id="chapter-1"),
            _chapter("chapter-2", "第二章", 5, 7, point="另一个独立概念"),
        ],
    )
    jobs.save_structure("book-a", structure)
    community.register_asset({"book_id": "book-a", "title": "Book A"}, "book-a-source", None)
    alice, alice_token, _ = community.visitor(None, [])
    bob, bob_token, _ = community.visitor(None, [])
    community.add_book(alice, "book-a")
    community.add_book(bob, "book-a")
    now = datetime.now(UTC)
    session = InterviewSession(
        session_id="session-alice",
        profile=LearnerProfile(user_id=alice, book_id="book-a"),
        phase=InterviewPhase.COMPLETE,
        chapter_options=[{"id": "chapter-1", "label": "第一章"}, {"id": "chapter-2", "label": "第二章"}],
        created_at=now,
        updated_at=now,
    )
    session.profile.constraints.minutes_per_day = 20
    assessments.create_session(session)
    community.claim_session(alice, session.session_id)
    path = tmp_path / "workspace.sqlite3"
    workspace = StudyWorkspace(path, assessments, jobs, community)
    app = FastAPI()
    app.include_router(study_workspace_router(workspace, Accounts(community)))
    with TestClient(app) as alice_client, TestClient(app) as bob_client, TestClient(app) as stranger:
        alice_client.cookies.set("zhiwo_visitor", alice_token)
        bob_client.cookies.set("zhiwo_visitor", bob_token)
        yield {
            "alice": alice_client,
            "bob": bob_client,
            "stranger": stranger,
            "session": session,
            "assessments": assessments,
            "jobs": jobs,
            "community": community,
            "workspace_path": path,
            "structure": structure,
            "tmp_path": tmp_path,
        }


def _tasks(payload: dict) -> list[dict]:
    return [task for day in payload["plan"]["days"] for task in day["tasks"]]


def test_real_leaf_plan_pacing_self_report_persistence_and_permissions(workspace_api) -> None:
    alice = workspace_api["alice"]
    url = "/api/interviews/session-alice/study-workspace"
    response = alice.get(url)
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    payload = response.json()
    assert payload["book_id"] == "book-a"
    assert payload["plan"]["minutes_per_day"] == 20
    assert payload["plan"]["minutes_source"] == "profile"
    assert all(day["estimated_minutes"] <= 20 for day in payload["plan"]["days"])
    tasks = _tasks(payload)
    assert [(task["section_id"], task["kind"]) for task in tasks] == [
        ("section-1", "reading"),
        ("section-1", "practice"),
        ("section-1", "flashcards"),
        ("section-2", "reading"),
        (None, "reading"),
        (None, "practice"),
        (None, "flashcards"),
    ]
    assert tasks[0]["chapter_id"] == "chapter-1"
    assert tasks[0]["source_start_page"] == 1
    assert tasks[0]["source_end_page"] == 2
    assert payload["plan"]["progress"] == {"done": 0, "total": 7, "percent": 0}

    reading = tasks[0]
    patched = alice.patch(f"{url.rsplit('/', 1)[0]}/study-tasks/{reading['task_id']}", json={"status": "done"})
    assert patched.status_code == 200
    assert patched.json()["completion_source"] == "self_report"
    assert patched.json()["status"] == "done"
    assert alice.get(url).json()["plan"]["progress"] == {"done": 1, "total": 7, "percent": 14}
    assert StudyWorkspace(
        workspace_api["workspace_path"], workspace_api["assessments"],
        workspace_api["jobs"], workspace_api["community"],
    ).get(workspace_api["session"].profile.user_id, "session-alice")["plan"]["progress"]["done"] == 1
    assert alice.patch(f"{url.rsplit('/', 1)[0]}/study-tasks/{reading['task_id']}", json={"status": "todo"}).json()["status"] == "todo"
    assert alice.get(url).json()["plan"]["progress"]["done"] == 0
    original_ids = [task["task_id"] for task in tasks]
    revised = workspace_api["structure"].model_copy(deep=True)
    revised.chapters[1].title = "第一节（校订标题）"
    workspace_api["jobs"].save_structure("book-a", revised)
    assert [task["task_id"] for task in _tasks(alice.get(url).json())] == original_ids
    assert alice.patch(f"{url.rsplit('/', 1)[0]}/study-tasks/unknown", json={"status": "done"}).status_code == 404
    assert alice.patch(f"{url.rsplit('/', 1)[0]}/study-tasks/{reading['task_id']}", json={"status": "mastered"}).status_code == 422
    assert alice.patch(f"{url.rsplit('/', 1)[0]}/study-tasks/{reading['task_id']}", json={"status": "done"}, headers={"Origin": "https://evil.example"}).status_code == 403
    assert workspace_api["bob"].get(url).status_code == 403
    assert workspace_api["stranger"].get(url).status_code == 401
    assert alice.get("/api/interviews/missing/study-workspace").status_code == 404


def test_mistakes_come_only_from_graded_attempts_and_old_course_remains_retryable(workspace_api) -> None:
    assessments = workspace_api["assessments"]
    session = workspace_api["session"]
    structure = workspace_api["structure"]
    leaf = next(chapter for chapter in structure.chapters if chapter.chapter_id == "section-1")
    # Build a real saved course; the unused answer is deliberately secret.
    bundle = ChapterCourseCompiler().compile(
        chapter=leaf,
        profile=session.profile,
        decision=PersonalizationPolicy().decide(session.profile, leaf.chapter_id),
        instance_key=session.session_id,
    )
    bundle.practice_items[0].expected_answer = "ATTEMPTED_REVIEW_ONLY"
    unused = bundle.practice_items[0].model_copy(deep=True)
    unused.item_id = "practice_not_yet_attempted"
    unused.expected_answer = "SECRET_UNATTEMPTED"
    bundle.practice_items.append(unused)
    assert assessments.save_course(session.session_id, bundle)
    event_id = "event_wrong_once"
    practice = bundle.practice_items[0]
    session.profile.diagnostic_observations.append(
        DiagnosticObservation(
            item_id=practice.item_id,
            chapter_id=leaf.chapter_id,
            knowledge_point_ids=[practice.point_id],
            score=0.2,
            evidence_weight=1,
            scoring_confidence=0.99,
            self_confidence=0.8,
            response_seconds=18,
            hints_used=0,
            observed_at=datetime.now(UTC),
            question_snapshot=practice.prompt,
            answer_snapshot="我的错误解释",
            source_pages=[1],
        )
    )
    first_payload = {
        "evidence": {"score": 0.2, "scoring_confidence": 0.99},
        "profile": session.profile.model_dump(mode="json"),
    }
    assert assessments.save_session_with_event(
        session, event_id=event_id, course_id=bundle.course_id,
        item_id=practice.item_id, event_type="practice_answer",
        payload_json=json.dumps(first_payload),
    )
    url = "/api/interviews/session-alice/study-workspace"
    first_response = workspace_api["alice"].get(url)
    first = first_response.json()
    assert "SECRET_UNATTEMPTED" not in first_response.text
    mistake = first["mistakes"][0]
    assert len(first["mistakes"]) == 1
    assert mistake["chapter_id"] == "chapter-1"
    assert mistake["section_id"] == "section-1"
    assert mistake["course_id"] == bundle.course_id
    assert mistake["course_version"] == 1
    assert mistake["item_id"] == practice.item_id
    assert mistake["practice_item"]["prompt"] == practice.prompt
    assert "expected_answer" not in mistake["practice_item"]
    assert mistake["attempt"]["answer"] == "我的错误解释"
    assert mistake["attempt"]["score"] == 0.2
    assert mistake["review_material"]["expected_answer"] == "ATTEMPTED_REVIEW_ONLY"
    assert mistake["review_material"]["citations"][0]["page_number"] == 1
    assert mistake["can_retry"] is True
    practice_task = next(task for task in _tasks(first) if task["section_id"] == "section-1" and task["kind"] == "practice")
    assert practice_task["activity_count"] == 1
    assert practice_task["status"] == "todo"  # One of two actual items remains unattempted.
    assert workspace_api["alice"].get(f"{url}?chapter_id=chapter-2").json()["mistakes"] == []
    assert len(workspace_api["alice"].get(f"{url}?chapter_id=section-1").json()["mistakes"]) == 1

    review_url = f"/api/interviews/session-alice/mistakes/{mistake['mistake_id']}"
    confirmed = workspace_api["alice"].patch(
        review_url, json={"status": "self_reported_mastered", "reason": "我重新核对了第 1 页"}
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["review"]["status"] == "self_reported_mastered"
    assert confirmed.json()["attempt"]["score"] == 0.2  # A checkmark cannot change a grade.
    repeated = workspace_api["alice"].patch(review_url, json={"status": "self_reported_mastered"})
    assert repeated.json()["review"] == confirmed.json()["review"]
    assert workspace_api["bob"].patch(review_url, json={"status": "reviewing"}).status_code == 403
    assert workspace_api["alice"].patch("/api/interviews/session-alice/mistakes/nope", json={"status": "reviewing"}).status_code == 404

    # A later actual grade updates retry evidence without automatically
    # converting the user's review status into a measured mastery score.
    second_payload = {"evidence": {"score": 1.0, "scoring_confidence": 0.99}, "profile": session.profile.model_dump(mode="json")}
    assert assessments.save_session_with_event(
        session, event_id="event_correct_later", course_id=bundle.course_id,
        item_id=practice.item_id, event_type="practice_answer",
        payload_json=json.dumps(second_payload), now=datetime.now(UTC).timestamp() + 1,
    )
    refreshed = workspace_api["alice"].get(url).json()["mistakes"][0]
    assert refreshed["attempt_count"] == 2
    assert refreshed["latest_attempt"]["score"] == 1.0
    assert refreshed["review"]["status"] == "self_reported_mastered"
    # This version is still addressable by the existing public practice route.
    assert assessments.get_course_for_session(session.session_id, mistake["course_id"]) is not None
    assert assessments.save_session_with_event(
        session, event_id="event_other_item", course_id=bundle.course_id,
        item_id=unused.item_id, event_type="practice_answer",
        payload_json=json.dumps(second_payload), now=datetime.now(UTC).timestamp() + 2,
    )
    completed = workspace_api["alice"].get(url).json()
    completed_task = next(task for task in _tasks(completed) if task["task_id"] == practice_task["task_id"])
    assert completed_task["status"] == "done"
    assert completed_task["completion_source"] == "activity"
    assert completed_task["activity_count"] == 2
    undone = workspace_api["alice"].patch(
        f"/api/interviews/session-alice/study-tasks/{practice_task['task_id']}",
        json={"status": "todo"},
    )
    assert undone.json()["status"] == "done"  # Verified activity cannot be undone by a checkmark.
    assert undone.json()["completion_source"] == "activity"

    # A new version with unattempted items is a new current task. Historical
    # scores and the original retriable wrong item remain in their own course.
    newer = bundle.model_copy(deep=True)
    newer.course_id = "course_newer_version"
    newer.version = 2
    newer.practice_items[0].item_id = "practice_new_version"
    assert assessments.save_course(session.session_id, newer)
    current = workspace_api["alice"].get(url).json()
    current_task = next(task for task in _tasks(current) if task["task_id"] == practice_task["task_id"])
    assert current_task["status"] == "todo"
    assert current_task["completion_source"] is None
    assert current_task["activity_count"] == 0
    assert current["mistakes"][0]["course_id"] == bundle.course_id
    assert current["mistakes"][0]["can_retry"] is True
    # Missing historical course content keeps the scored record but cannot
    # offer a fake or unscorable retry.
    with assessments._connect() as db:
        db.execute("DELETE FROM course_bundles WHERE course_id=?", (bundle.course_id,))
    historical = workspace_api["alice"].get(url).json()["mistakes"][0]
    assert historical["can_retry"] is False
    assert historical["course_version"] is None
    assert historical["practice_item"]["prompt"] == practice.prompt
    assert historical["review_material"]["expected_answer"] is None
    assert historical["attempt"]["answer"] == "我的错误解释"


def test_structure_upgrade_preserves_only_real_historical_parent_tasks(workspace_api) -> None:
    jobs = workspace_api["jobs"]
    assessments = workspace_api["assessments"]
    session = workspace_api["session"]
    layered = workspace_api["structure"]
    flat = layered.model_copy(deep=True)
    flat.chapters = [chapter for chapter in flat.chapters if chapter.parent_id is None]
    jobs.save_structure("book-a", flat)
    parent = flat.chapters[0]
    course = ChapterCourseCompiler().compile(
        chapter=parent,
        profile=session.profile,
        decision=PersonalizationPolicy().decide(session.profile, parent.chapter_id),
        instance_key=session.session_id,
    )
    assert assessments.save_course(session.session_id, course)
    original = workspace_api["alice"].get("/api/interviews/session-alice/study-workspace").json()
    old_parent_practice = next(
        task for task in _tasks(original)
        if task["chapter_id"] == "chapter-1" and task["kind"] == "practice"
    )
    assert old_parent_practice["historical"] is False
    assert assessments.save_session_with_event(
        session, event_id="event_parent_course", course_id=course.course_id,
        item_id=course.practice_items[0].item_id, event_type="practice_answer",
        payload_json=json.dumps({
            "evidence": {"score": 1.0, "scoring_confidence": 0.99},
            "profile": session.profile.model_dump(mode="json"),
        }),
    )
    assert next(
        task for task in _tasks(workspace_api["alice"].get("/api/interviews/session-alice/study-workspace").json())
        if task["task_id"] == old_parent_practice["task_id"]
    )["status"] == "done"

    jobs.save_structure("book-a", layered)
    migrated = workspace_api["alice"].get("/api/interviews/session-alice/study-workspace").json()
    tasks = _tasks(migrated)
    old = next(task for task in tasks if task["task_id"] == old_parent_practice["task_id"])
    assert old["historical"] is True
    assert old["title"].startswith("历史整章 · ")
    assert old["status"] == "done"
    assert old["completion_source"] == "activity"
    assert migrated["plan"]["progress"]["done"] >= 1
    assert len([task for task in tasks if task["chapter_id"] == "chapter-1" and task["section_id"] is None]) == 3
    assert len([task for task in tasks if task["chapter_id"] == "chapter-1" and task["section_id"] is not None]) == 4


def test_old_session_uses_saved_chapter_snapshot_if_structure_is_missing(workspace_api) -> None:
    jobs = workspace_api["jobs"]
    with jobs._connect() as db:
        db.execute("DELETE FROM book_structures WHERE book_id=?", ("book-a",))
    response = workspace_api["alice"].get("/api/interviews/session-alice/study-workspace")
    assert response.status_code == 200
    tasks = _tasks(response.json())
    assert [(task["chapter_id"], task["kind"]) for task in tasks] == [
        ("chapter-1", "reading"),
        ("chapter-2", "reading"),
    ]
    assert all(task["source_start_page"] is None for task in tasks)
