from datetime import UTC, datetime

import pytest

from adaptive_learning.assessment.models import (
    DiagnosticItem,
    InterviewSession,
    LearnerProfile,
    ResponseType,
)
from adaptive_learning.assessment.repository import (
    SessionConflictError,
    SQLiteAssessmentRepository,
)


def item() -> DiagnosticItem:
    return DiagnosticItem(
        item_id="secret_item",
        chapter_id="ch",
        knowledge_point_ids=["kp"],
        knowledge_point_labels=["知识点"],
        prompt="下面哪项正确？",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["A", "B", "C", "D"],
        correct_option_ids=["2"],
        expected_answer="C",
        source_pages=[7],
    )


def session() -> InterviewSession:
    now = datetime.now(UTC)
    return InterviewSession(
        session_id="session_1",
        profile=LearnerProfile(user_id="u", book_id="b"),
        created_at=now,
        updated_at=now,
    )


def test_item_bank_persists_private_answer_key(tmp_path) -> None:
    path = tmp_path / "assessment.sqlite3"
    repository = SQLiteAssessmentRepository(path)
    repository.save_bank(book_id="b", structure_fingerprint="fp", items=[item()])

    reloaded = SQLiteAssessmentRepository(path).get_bank("b", expected_fingerprint="fp")

    assert reloaded is not None
    assert reloaded[0].correct_option_ids == ["2"]
    assert reloaded[0].expected_answer == "C"
    assert SQLiteAssessmentRepository(path).get_bank("b", expected_fingerprint="old") is None


def test_session_survives_repository_restart(tmp_path) -> None:
    path = tmp_path / "assessment.sqlite3"
    repository = SQLiteAssessmentRepository(path)
    original = session()
    repository.create_session(original)
    original.profile.goal = "掌握全书"
    repository.save_session(original)

    reloaded = SQLiteAssessmentRepository(path).get_session(original.session_id)

    assert reloaded is not None
    assert reloaded.profile.goal == "掌握全书"
    assert reloaded.revision == 1


def test_optimistic_lock_rejects_lost_session_update(tmp_path) -> None:
    repository = SQLiteAssessmentRepository(tmp_path / "assessment.sqlite3")
    repository.create_session(session())
    left = repository.get_session("session_1")
    right = repository.get_session("session_1")
    assert left is not None and right is not None
    left.profile.goal = "左侧更新"
    repository.save_session(left)
    right.profile.goal = "右侧覆盖"

    with pytest.raises(SessionConflictError):
        repository.save_session(right)

    current = repository.get_session("session_1")
    assert current is not None
    assert current.profile.goal == "左侧更新"


def test_learning_event_and_profile_update_are_atomic_and_idempotent(tmp_path) -> None:
    repository = SQLiteAssessmentRepository(tmp_path / "assessment.sqlite3")
    original = session()
    repository.create_session(original)
    original.profile.goal = "第一次学习证据"

    inserted = repository.save_session_with_event(
        original,
        event_id="event_unique_001",
        course_id="course_1",
        item_id="practice_1",
        event_type="practice_answer",
        payload_json='{"ok":true}',
    )
    persisted = repository.get_session(original.session_id)
    assert persisted is not None
    persisted.profile.goal = "不应重复覆盖"
    duplicate = repository.save_session_with_event(
        persisted,
        event_id="event_unique_001",
        course_id="course_1",
        item_id="practice_1",
        event_type="practice_answer",
        payload_json='{"ok":false}',
    )

    current = repository.get_session(original.session_id)
    assert inserted is True
    assert duplicate is False
    assert current is not None and current.profile.goal == "第一次学习证据"
    assert repository.get_event_payload(original.session_id, "event_unique_001") == '{"ok":true}'
    assert repository.get_event_payload("another_session", "event_unique_001") is None
