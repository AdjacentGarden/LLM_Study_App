from __future__ import annotations

from datetime import UTC, datetime

from fsrs import Card, Rating, Scheduler, State

from ..assessment.models import FlashcardReviewState

RATINGS = {"again", "hard", "good", "easy"}
FSRS_ALGORITHM = "fsrs-6"
DESIRED_RETENTION = 0.9

_RATING_MAP = {
    "again": Rating.Again,
    "hard": Rating.Hard,
    "good": Rating.Good,
    "easy": Rating.Easy,
}


def _scheduler() -> Scheduler:
    # Daily book learning does not need minute-level learning steps. Disabling
    # fuzzing keeps API tests and user-visible dates deterministic.
    return Scheduler(
        desired_retention=DESIRED_RETENTION,
        learning_steps=(),
        relearning_steps=(),
        enable_fuzzing=False,
    )


def _restore_card(state: FlashcardReviewState, observed_at: datetime) -> Card:
    if (
        state.algorithm != FSRS_ALGORITHM
        or state.card_id is None
        or state.memory_state is None
    ):
        # Legacy SM-2-like records migrate on their next review. Historical
        # evidence remains in the learner profile; only future timing switches.
        return Card(card_id=state.card_id, due=observed_at)
    return Card(
        card_id=state.card_id,
        state=State(state.memory_state),
        step=state.learning_step,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due_at or observed_at,
        last_review=state.last_review_at,
    )


def schedule_review(
    previous: FlashcardReviewState | None,
    rating: str,
    *,
    now: datetime | None = None,
    response_seconds: float | None = None,
) -> FlashcardReviewState:
    if rating not in RATINGS:
        raise ValueError("unsupported flashcard rating")
    current = previous or FlashcardReviewState()
    observed_at = now or datetime.now(UTC)
    card = _restore_card(current, observed_at)
    duration_ms = None if response_seconds is None else max(1, round(response_seconds * 1000))
    reviewed, _ = _scheduler().review_card(
        card,
        _RATING_MAP[rating],
        review_datetime=observed_at,
        review_duration=duration_ms,
    )
    interval = max(0.0, (reviewed.due - observed_at).total_seconds() / 86400)
    difficulty = reviewed.difficulty
    legacy_ease = 2.5 if difficulty is None else min(3.5, max(1.3, 3.5 - 0.275 * difficulty))
    return FlashcardReviewState(
        algorithm=FSRS_ALGORITHM,
        repetitions=current.repetitions + 1,
        ease_factor=legacy_ease,
        interval_days=round(interval, 4),
        due_at=reviewed.due,
        last_rating=rating,
        updated_at=observed_at,
        card_id=reviewed.card_id,
        memory_state=reviewed.state.value,
        learning_step=reviewed.step,
        stability=reviewed.stability,
        difficulty=reviewed.difficulty,
        last_review_at=reviewed.last_review,
        desired_retention=DESIRED_RETENTION,
    )


def rating_score(rating: str) -> float:
    if rating not in RATINGS:
        raise ValueError("unsupported flashcard rating")
    return {"again": 0.0, "hard": 0.4, "good": 0.75, "easy": 1.0}[rating]
