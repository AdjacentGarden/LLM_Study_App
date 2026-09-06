from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
from adaptive_learning.assessment.interview import InterviewOrchestrator
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticItem,
    InterviewPhase,
    ResponseType,
)


def test_uncertain_open_answer_requests_one_clarification() -> None:
    diagnostic = DiagnosticItem(
        item_id="open",
        chapter_id="ch",
        knowledge_point_ids=["point"],
        prompt="请解释",
        response_type=ResponseType.EXPLANATION,
    )
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine(min_items=1, max_items=2))
    session = orchestrator.start(
        user_id="u",
        book_id="b",
        book_title="书",
        chapter_titles=["章"],
        items=[diagnostic],
    )
    session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
    orchestrator.next_diagnostic(session, [diagnostic])
    response = AssessmentResponse(
        item_id="open",
        answer="不太确定",
        confidence=0.2,
        response_seconds=10,
    )

    evidence = orchestrator.record_diagnostic(session, response, [diagnostic])

    assert evidence.needs_follow_up
    assert session.pending_turn is not None
    assert "不能可靠" in session.pending_turn.message
    assert session.profile.knowledge_mastery["point"].evidence_count == 0
