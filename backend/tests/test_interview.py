from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
from adaptive_learning.assessment.interview import InterviewOrchestrator
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    InterviewPhase,
    ResponseType,
)


def test_interview_moves_from_goal_to_adaptive_diagnosis() -> None:
    item = DiagnosticItem(
        item_id="i1",
        chapter_id="ch1",
        knowledge_point_ids=["p1"],
        prompt="问题",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["是", "否"],
        correct_option_ids=["0"],
    )
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine(min_items=1, max_items=2))
    session = orchestrator.start(
        user_id="u",
        book_id="b",
        book_title="任意书",
        chapter_titles=["第一章"],
        items=[item],
    )

    first_turn = session.pending_turn
    assert first_turn is not None
    assert first_turn.response_type == ResponseType.SINGLE_CHOICE
    assert len(first_turn.options) == 4

    orchestrator.answer_profile_question(
        session, "从基础开始系统掌握", selected_option_ids=["foundation"]
    )
    orchestrator.answer_profile_question(session, "ch1", selected_option_ids=["chapter_1"])
    orchestrator.answer_profile_question(session, "some", selected_option_ids=["some"])
    turn = orchestrator.answer_profile_question(session, "20", selected_option_ids=["20"])
    diagnostic = orchestrator.next_diagnostic(session, [item])

    assert turn.phase == InterviewPhase.ADAPTIVE_DIAGNOSIS
    assert diagnostic.item is not None
    assert diagnostic.item.item_id == "i1"
    assert diagnostic.why_asked
    assert session.profile.background_level == "some"
    assert session.profile.constraints.minutes_per_day == 20
    assert session.profile.goal == "从基础开始系统掌握"
    assert len(session.profile.evidence) == 4


def test_interview_rejects_free_text_goal_without_a_choice() -> None:
    item = DiagnosticItem(
        item_id="i1",
        chapter_id="ch1",
        knowledge_point_ids=["p1"],
        prompt="问题",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["是", "否"],
        correct_option_ids=["0"],
    )
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine())
    session = orchestrator.start(
        user_id="u", book_id="b", book_title="书", chapter_titles=["第一章"], items=[item]
    )

    try:
        orchestrator.answer_profile_question(session, "我想随便看看")
    except ValueError as error:
        assert "option" in str(error)
    else:
        raise AssertionError("free-text goal should be rejected")


def test_profile_confirmation_is_plain_language_and_keeps_unknowns_explicit() -> None:
    diagnostic = DiagnosticItem(
        item_id="i1",
        chapter_id="ch1",
        knowledge_point_ids=["p1"],
        knowledge_point_labels=["关键概念"],
        prompt="下面哪项正确？",
        response_type=ResponseType.SINGLE_CHOICE,
        options=["正确", "错误"],
        correct_option_ids=["0"],
    )
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine(min_items=1, max_items=1))
    session = orchestrator.start(
        user_id="u",
        book_id="b",
        book_title="书",
        chapter_titles=["第一章"],
        chapter_options=[{"id": "ch1", "label": "第一章"}],
        items=[diagnostic],
    )
    session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
    orchestrator.next_diagnostic(session, [diagnostic])
    orchestrator.record_diagnostic(
        session,
        AssessmentResponse(
            item_id="i1",
            answer="正确",
            selected_option_ids=["0"],
            confidence=0.8,
            response_seconds=25,
        ),
        [diagnostic],
    )

    confirmation = orchestrator.next_diagnostic(session, [diagnostic])

    assert confirmation.phase == InterviewPhase.PROFILE_CONFIRMATION
    assert "第一章" in confirmation.message
    assert "作答证据" in confirmation.message
    assert "置信度约" not in confirmation.message

    revision = orchestrator.revise_profile(session)

    assert revision.phase == InterviewPhase.BOOK_BRIEFING
    assert revision.response_type == ResponseType.SINGLE_CHOICE
    assert [option["id"] for option in revision.options] == [
        "exam",
        "foundation",
        "overview",
        "application",
    ]
    assert "小测证据会保留" in revision.message
