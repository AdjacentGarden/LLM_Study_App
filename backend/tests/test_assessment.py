from datetime import UTC, datetime

from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine, score_choice
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    DiagnosticObservation,
    LearnerProfile,
    MasteryPosterior,
    ResponseType,
)


def item(
    item_id: str, point_id: str, *, chapter: str = "ch", difficulty: float = 0
) -> DiagnosticItem:
    return DiagnosticItem(
        item_id=item_id,
        chapter_id=chapter,
        knowledge_point_ids=[point_id],
        prompt="问题",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["A", "B"],
        correct_option_ids=["1"],
        difficulty=difficulty,
    )


def test_selector_prefers_uncovered_knowledge_point() -> None:
    profile = LearnerProfile(
        user_id="u",
        book_id="b",
        knowledge_mastery={
            "covered": MasteryPosterior(alpha=8, beta=2, evidence_count=4),
            "unknown": MasteryPosterior(),
        },
    )
    engine = AdaptiveAssessmentEngine()
    selected = engine.select_next(
        profile,
        [item("covered_item", "covered", difficulty=1.2), item("unknown_item", "unknown")],
        set(),
    )

    assert selected is not None
    assert selected.item_id == "unknown_item"


def test_choice_evidence_updates_mastery_and_calibration() -> None:
    diagnostic = item("i", "point")
    response = AssessmentResponse(
        item_id="i",
        answer="B",
        selected_option_ids=["1"],
        confidence=0.9,
        response_seconds=12,
    )
    profile = LearnerProfile(
        user_id="u", book_id="b", knowledge_mastery={"point": MasteryPosterior()}
    )
    evidence = score_choice(diagnostic, response)

    AdaptiveAssessmentEngine().update_profile(profile, diagnostic, response, evidence)

    assert profile.knowledge_mastery["point"].mean > 0.5
    assert profile.knowledge_mastery["point"].tracked_mastery is not None
    assert profile.confidence_observations == 1
    assert profile.calibration_error is not None
    assert len(profile.diagnostic_observations) == 1
    assert profile.diagnostic_observations[0].score == 1


def test_selector_uses_focus_only_after_information_and_coverage_are_considered() -> None:
    profile = LearnerProfile(
        user_id="u",
        book_id="b",
        focus_chapter_ids=["focus"],
        knowledge_mastery={"left": MasteryPosterior(), "right": MasteryPosterior()},
    )
    candidates = [
        item("other", "left", chapter="other"),
        item("focused", "right", chapter="focus"),
    ]

    selected = AdaptiveAssessmentEngine().select_next(profile, candidates, set())

    assert selected is not None
    assert selected.item_id == "focused"


def test_hints_and_implausibly_fast_response_reduce_evidence_weight() -> None:
    diagnostic = item("i", "point")
    engine = AdaptiveAssessmentEngine()
    careful = LearnerProfile(
        user_id="careful", book_id="b", knowledge_mastery={"point": MasteryPosterior()}
    )
    rushed = LearnerProfile(
        user_id="rushed", book_id="b", knowledge_mastery={"point": MasteryPosterior()}
    )
    evidence = score_choice(
        diagnostic,
        AssessmentResponse(
            item_id="i",
            answer="B",
            selected_option_ids=["1"],
            confidence=0.8,
            response_seconds=30,
        ),
    )
    engine.update_profile(
        careful,
        diagnostic,
        AssessmentResponse(
            item_id="i",
            answer="B",
            selected_option_ids=["1"],
            confidence=0.8,
            response_seconds=30,
        ),
        evidence,
    )
    engine.update_profile(
        rushed,
        diagnostic,
        AssessmentResponse(
            item_id="i",
            answer="B",
            selected_option_ids=["1"],
            confidence=0.8,
            response_seconds=2,
            hints_used=2,
        ),
        evidence,
    )

    assert (
        careful.diagnostic_observations[0].evidence_weight
        > rushed.diagnostic_observations[0].evidence_weight
    )
    assert (
        careful.knowledge_mastery["point"].mean
        > rushed.knowledge_mastery["point"].mean
    )


def test_wrong_answer_lowers_sequential_mastery_without_erasing_audit_counts() -> None:
    diagnostic = item("i", "point")
    profile = LearnerProfile(
        user_id="u",
        book_id="b",
        knowledge_mastery={"point": MasteryPosterior(alpha=3, beta=2, evidence_count=2)},
    )
    response = AssessmentResponse(
        item_id="i",
        answer="A",
        selected_option_ids=["0"],
        confidence=0.95,
        response_seconds=20,
    )

    AdaptiveAssessmentEngine().update_profile(
        profile,
        diagnostic,
        response,
        score_choice(diagnostic, response),
    )

    state = profile.knowledge_mastery["point"]
    assert state.tracked_mastery is not None
    assert state.tracked_mastery < 0.5
    assert state.evidence_count == 3
    assert state.alpha == 3
    assert state.beta > 2


def test_selector_avoids_abrupt_difficulty_jump_when_information_is_equal() -> None:
    profile = LearnerProfile(
        user_id="u",
        book_id="b",
        knowledge_mastery={"near": MasteryPosterior(), "jump": MasteryPosterior()},
        diagnostic_observations=[
            DiagnosticObservation(
                item_id="previous",
                chapter_id="old",
                knowledge_point_ids=["old"],
                score=0.5,
                evidence_weight=1,
                scoring_confidence=1,
                self_confidence=0.5,
                response_seconds=30,
                hints_used=0,
                item_difficulty=0,
                observed_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        ],
    )

    selected = AdaptiveAssessmentEngine().select_next(
        profile,
        [item("jump", "jump", chapter="new", difficulty=2.5), item("near", "near", chapter="new")],
        set(),
    )

    assert selected is not None
    assert selected.item_id == "near"
