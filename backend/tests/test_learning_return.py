import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from test_community import community as community
from test_course_compiler import chapter

from adaptive_learning.api.retention_routes import memory_snapshot
from adaptive_learning.assessment.models import InterviewSession, LearnerProfile
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy
from adaptive_learning.personalization.review import schedule_review


def save(a, **extra):
    return a.post(
        "/api/learning/doubts",
        json={
            "book_id": "book-0",
            "question": "为什么会这样？",
            "chapter_id": "ch-1",
            "chapter_title": "第一章",
            "excerpt": "原文线索",
            "pages": [2, 3, 2],
            **extra,
        },
    )


def test_doubt_is_private_durable_idempotent_and_recoverable(community):
    a, b, _, _ = community
    first = save(a)
    assert first.status_code == 200, first.text
    d = first.json()
    assert d["status"] == "open" and d["pages"] == [2, 3]
    assert d["due_at"] - d["created"] == 3 * 86400
    assert "owner" not in d and "fingerprint" not in d
    assert save(a).json()["id"] == d["id"]
    assert b.get("/api/learning/doubts?book_id=book-0").json()["items"] == []
    assert b.post("/api/learning/doubts/" + d["id"], json={"action": "resolve"}).status_code == 404
    with TestClient(a.app) as again:
        again.cookies.update(a.cookies)
        assert again.get("/api/learning/doubts?book_id=book-0").json()["items"][0] == d
    snoozed = a.post("/api/learning/doubts/" + d["id"], json={"action": "snooze"}).json()
    assert snoozed["due_at"] - snoozed["updated"] == 7 * 86400
    solved = a.post(
        "/api/learning/doubts/" + d["id"], json={"action": "resolve", "note": "我理解了适用条件"}
    ).json()
    assert solved["status"] == "resolved" and solved["note"] == "我理解了适用条件"
    reopened = a.post("/api/learning/doubts/" + d["id"], json={"action": "reopen"}).json()
    assert reopened["status"] == "open" and reopened["note"] == solved["note"]
    assert reopened["due_at"] == reopened["updated"]
    assert (
        a.get("/api/learning/doubts?book_id=book-0").headers["cache-control"] == "private, no-store"
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"question": " "},
        {"question": "x" * 2001},
        {"pages": [0]},
        {"pages": [-1]},
        {"pages": [100001]},
        {"owner": "x"},
        {"excerpt": "x" * 3001},
    ],
)
def test_doubt_input_validation(community, changes):
    a, *_ = community
    assert save(a, **changes).status_code == 422


def test_doubt_book_permissions_origin_and_remove_restore(community):
    a, _, _, _ = community
    assert save(a, book_id="book-9").status_code == 403
    assert (
        a.post(
            "/api/learning/doubts",
            json={"book_id": "book-0", "question": "a"},
            headers={"origin": "https://evil.test"},
        ).status_code
        == 403
    )
    d = save(a).json()
    a.post("/api/library/books/book-0/remove")
    assert a.get("/api/learning/doubts?book_id=book-0").status_code == 403
    assert a.post("/api/learning/doubts/" + d["id"], json={"action": "resolve"}).status_code == 403
    a.post("/api/library/books/book-0/restore")
    assert len(a.get("/api/learning/doubts?book_id=book-0").json()["items"]) == 1


def test_duplicate_save_race(community):
    a, *_ = community
    a.get("/api/library")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: save(a).json()["id"], range(8)))
    assert len(set(ids)) == 1


def memory_fixture(community):
    a, b, repo, assessments = community
    a.get("/api/library")
    owner, _, _ = repo.visitor(a.cookies.get("zhiwo_visitor"), [])
    profile = LearnerProfile(user_id="return-test", book_id="book-0")
    session = InterviewSession(
        session_id="return-session",
        profile=profile,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )
    assessments.create_session(session)
    assert repo.claim_session(owner, session.session_id)
    source = chapter()
    bundle = ChapterCourseCompiler().compile(
        chapter=source,
        profile=profile,
        decision=PersonalizationPolicy().decide(profile, source.chapter_id),
        version=1,
    )
    assessments.save_course(session.session_id, bundle)
    return a, b, assessments, session, bundle


def test_memory_real_schedule_due_boundary_and_week_evidence(community):
    a, b, repo, session, bundle = memory_fixture(community)
    card = bundle.flashcards[0]
    t = datetime(2026, 1, 1, tzinfo=UTC)
    state = None
    for i, days in enumerate([0, 0.1, 1.1, 8.1]):
        at = t + timedelta(days=days)
        state = schedule_review(state, "good", now=at)
        session.profile.flashcard_reviews[card.card_id] = state
        repo.save_session_with_event(
            session,
            event_id=f"event-{i}",
            course_id=bundle.course_id,
            item_id=card.card_id,
            event_type="flashcard_review",
            payload_json=json.dumps({"review_state": state.model_dump(mode="json")}),
            now=at.timestamp(),
        )
    before = memory_snapshot(repo, session, now=state.due_at - timedelta(seconds=1))
    after = memory_snapshot(repo, session, now=state.due_at)
    assert before["due_count"] == 0 and after["due_count"] == 1
    assert after["delayed_count"] == 2 and after["week_checks"] == 1 and after["week_recalled"] == 1
    assert after["reviewed_count"] == 1 and after["items"][0]["card"]["front"] == card.front
    assert a.get("/api/learning/memory/return-session").status_code == 200
    assert b.get("/api/learning/memory/return-session").status_code == 403
    assert a.get("/api/learning/memory/no-such-session").status_code == 404


def test_memory_empty_does_not_invent_mastery(community):
    _, _, repo, session, _ = memory_fixture(community)
    data = memory_snapshot(repo, session)
    assert data["items"] == [] and data["week_checks"] == 0 and data["due_count"] == 0
