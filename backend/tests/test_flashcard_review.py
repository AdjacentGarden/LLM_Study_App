from datetime import UTC, datetime

import pytest

from adaptive_learning.personalization.review import rating_score, schedule_review


def test_spaced_repetition_schedule_expands_after_success() -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first = schedule_review(None, "good", now=now)
    second = schedule_review(first, "good", now=now)
    easy = schedule_review(second, "easy", now=now)

    assert first.algorithm == 'fsrs-6'
    assert first.interval_days >= 1
    assert second.interval_days >= first.interval_days
    assert easy.interval_days > second.interval_days
    assert easy.ease_factor > second.ease_factor
    assert easy.due_at is not None and easy.due_at > now


def test_again_shortens_interval_without_erasing_review_history() -> None:
    state = schedule_review(None, "easy")
    hard = schedule_review(state, "hard")
    again = schedule_review(hard, "again")

    assert hard.interval_days <= state.interval_days * 1.2
    assert again.repetitions == hard.repetitions + 1
    assert again.interval_days <= hard.interval_days
    assert again.stability < hard.stability
    assert rating_score("again") == 0
    assert rating_score("easy") == 1


def test_invalid_rating_is_rejected() -> None:
    with pytest.raises(ValueError):
        schedule_review(None, "perfect")
