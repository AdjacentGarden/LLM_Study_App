from datetime import UTC, datetime

import pytest

from adaptive_learning.personalization.review import rating_score, schedule_review


def test_spaced_repetition_schedule_expands_after_success() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = schedule_review(None, "good", now=now)
    assert first.due_at is not None
    second = schedule_review(first, "good", now=first.due_at)
    assert second.due_at is not None
    easy = schedule_review(second, "easy", now=second.due_at)

    assert first.algorithm == "fsrs-6"
    assert first.interval_days >= 1
    assert second.interval_days > first.interval_days
    assert easy.interval_days > second.interval_days
    assert easy.stability is not None and second.stability is not None
    assert easy.stability > second.stability
    assert easy.due_at is not None and easy.due_at > now


def test_again_resets_repetitions_and_hard_is_conservative() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    state = schedule_review(None, "easy", now=now)
    assert state.due_at is not None
    hard = schedule_review(state, "hard", now=state.due_at)
    good = schedule_review(state, "good", now=state.due_at)
    again = schedule_review(state, "again", now=state.due_at)

    assert again.interval_days < hard.interval_days < good.interval_days
    assert again.repetitions == 2
    assert rating_score("again") == 0
    assert rating_score("easy") == 1


def test_legacy_state_migrates_to_fsrs_on_next_review() -> None:
    from adaptive_learning.assessment.models import FlashcardReviewState

    migrated = schedule_review(
        FlashcardReviewState(repetitions=4, interval_days=12, last_rating="good"),
        "hard",
        now=datetime(2026, 1, 1, tzinfo=UTC),
        response_seconds=8.5,
    )

    assert migrated.algorithm == "fsrs-6"
    assert migrated.card_id is not None
    assert migrated.memory_state is not None
    assert migrated.stability is not None


def test_invalid_rating_is_rejected() -> None:
    with pytest.raises(ValueError):
        schedule_review(None, "perfect")
