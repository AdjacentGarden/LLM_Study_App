from datetime import UTC, datetime

import pytest

from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
from adaptive_learning.assessment.interview import InterviewOrchestrator
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    DiagnosticObservation,
    InterviewPhase,
    LearnerProfile,
    PublicDiagnosticItem,
    ResponseType,
)


def candidate(name: str, difficulty: float) -> DiagnosticItem:
    return DiagnosticItem(
        item_id=name, chapter_id=name, knowledge_point_ids=[name], prompt="选择正确解释",
        response_type=ResponseType.SINGLE_CHOICE, options=["解释 A", "解释 B"],
        correct_option_ids=["0"], difficulty=difficulty,
    )


def learner(score: float, confidence: float = .95) -> LearnerProfile:
    return LearnerProfile(user_id="journey", book_id="any-book", diagnostic_observations=[
        DiagnosticObservation(
            item_id="answered", chapter_id="old", knowledge_point_ids=["old"],
            score=score, self_confidence=confidence, scoring_confidence=1,
            evidence_weight=1, response_seconds=30, hints_used=0,
            item_difficulty=-.8, observed_at=datetime.now(UTC),
        )
    ])


def test_first_question_starts_in_easiest_available_band() -> None:
    profile = LearnerProfile(user_id="new", book_id="any-book", focus_chapter_ids=["hard"])
    result = AdaptiveAssessmentEngine().select_next(
        profile, [candidate("hard", 1.5), candidate("easy", -1), candidate("mid", 0)], set()
    )
    assert result is not None and result.item_id == "easy"


def test_secure_answer_moves_up_without_jumping_to_hardest_item() -> None:
    result = AdaptiveAssessmentEngine().select_next(
        learner(1), [candidate("hard", 2), candidate("step", -.3), candidate("easy", -1)], set()
    )
    assert result is not None and result.item_id == "step"


@pytest.mark.parametrize(("score", "confidence"), [(0, .95), (1, .25), (.5, .95)])
def test_wrong_or_uncertain_answer_checks_basics(score: float, confidence: float) -> None:
    result = AdaptiveAssessmentEngine().select_next(
        learner(score, confidence), [candidate("step", -.3), candidate("basic", -1)], set()
    )
    assert result is not None and result.item_id == "basic"


def test_sparse_bank_uses_nearest_remaining_question_without_repeating() -> None:
    bank = [candidate("answered", -.8), candidate("near", .2), candidate("far", 2)]
    result = AdaptiveAssessmentEngine().select_next(learner(0), bank, {"answered"})
    assert result is not None and result.item_id == "near"
    assert AdaptiveAssessmentEngine().select_next(learner(0), bank, {x.item_id for x in bank}) is None


def test_unreliable_fast_answer_does_not_unlock_harder_band() -> None:
    profile = learner(1)
    profile.diagnostic_observations[-1].evidence_weight = .1
    result = AdaptiveAssessmentEngine().select_next(
        profile, [candidate("step", -.3), candidate("basic", -1)], set()
    )
    assert result is not None and result.item_id == "basic"


def test_public_questions_never_expose_answer_bearing_labels_even_after_resume() -> None:
    private = candidate("basic", -1)
    private.knowledge_point_labels = ["正确答案的完整表述"]
    public = PublicDiagnosticItem.from_private(private)
    assert public.knowledge_point_labels == []
    cached = public.model_dump()
    cached["knowledge_point_labels"] = ["旧缓存包含答案"]
    assert PublicDiagnosticItem.model_validate(cached).model_dump()["knowledge_point_labels"] == []
    assert private.knowledge_point_labels == ["正确答案的完整表述"]


def test_option_order_is_stable_on_resume_and_preserves_answer_scoring() -> None:
    item = candidate("shuffle", -1)
    item.options = ["正确陈述", "干扰一", "干扰二", "干扰三"]
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine())
    orders = set()
    for index in range(12):
        session = orchestrator.start(
            user_id=f"test-{index}", book_id="any", book_title="测试书",
            chapter_titles=["第一章"], items=[item],
        )
        session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
        turn = orchestrator.next_diagnostic(session, [item])
        orders.add(tuple(option["id"] for option in turn.options))
        assert orchestrator.next_diagnostic(session, [item]).options == turn.options
        assert turn.item is not None
        assert turn.item.options == [option["label"] for option in turn.options]
        correct = next(option for option in turn.options if option["label"] == "正确陈述")
        evidence = orchestrator.record_diagnostic(
            session, AssessmentResponse(
                item_id=item.item_id, answer=correct["label"], selected_option_ids=[correct["id"]],
                confidence=.9, response_seconds=25,
            ), [item],
        )
        assert evidence.score == 1
    assert len(orders) > 1


def test_generated_negative_question_is_skipped() -> None:
    invalid = candidate("invalid", -2)
    invalid.prompt = "下列说法不正确的是？"
    invalid.knowledge_point_labels = [invalid.options[0]]
    valid = candidate("valid", -1)
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine())
    session = orchestrator.start(
        user_id="u", book_id="any", book_title="测试书",
        chapter_titles=["第一章"], items=[invalid, valid],
    )
    session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
    turn = orchestrator.next_diagnostic(session, [invalid, valid])
    assert turn.item is not None and turn.item.item_id == "valid"


def test_old_pending_invalid_question_advances_without_polluting_mastery() -> None:
    item = candidate("old-invalid", -1)
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine())
    session = orchestrator.start(
        user_id="u", book_id="any", book_title="测试书",
        chapter_titles=["第一章"], items=[item],
    )
    session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
    orchestrator.next_diagnostic(session, [item])
    item.prompt = "下列说法不正确的是？"
    item.knowledge_point_labels = [item.options[0]]
    evidence = orchestrator.record_diagnostic(
        session, AssessmentResponse(
            item_id=item.item_id, answer=item.options[0], selected_option_ids=["0"],
            confidence=.9, response_seconds=25,
        ), [item],
    )
    assert evidence.scoring_confidence == 0
    assert session.profile.diagnostic_observations == []
    assert orchestrator.next_diagnostic(session, [item]).phase == InterviewPhase.PROFILE_CONFIRMATION
