import json
from datetime import UTC, datetime

from test_assessment_repository import item, session
from test_course_compiler import chapter

from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
from adaptive_learning.assessment.models import (
    AssessmentResponse,
    DiagnosticObservation,
    FlashcardReviewState,
    MasteryPosterior,
    ScoredEvidence,
)
from adaptive_learning.assessment.records import learning_records
from adaptive_learning.assessment.repository import SQLiteAssessmentRepository
from adaptive_learning.personalization.generator import ChapterCourseCompiler
from adaptive_learning.personalization.policy import PersonalizationPolicy


def test_history_contains_actual_content_and_never_unused_answer_keys():
    value = session()
    bank_item = item()
    value.chapter_options = [{"id": "ch", "label": "测试章节"}]
    value.profile.knowledge_mastery = {
        "kp": MasteryPosterior(alpha=3, beta=1, evidence_count=2),
        "unseen": MasteryPosterior(),
    }
    for text, score in [("第一次选择", 0), ("第二次选择", 1)]:
        value.responses.append(
            AssessmentResponse(
                item_id=bank_item.item_id, answer=text, confidence=0.7, response_seconds=10
            )
        )
        value.profile.diagnostic_observations.append(
            DiagnosticObservation(
                item_id=bank_item.item_id,
                chapter_id="ch",
                knowledge_point_ids=["kp"],
                score=score,
                evidence_weight=1,
                scoring_confidence=0.9,
                self_confidence=0.7,
                response_seconds=10,
                hints_used=0,
                observed_at=datetime.now(UTC),
            )
        )
    result = learning_records(value, [bank_item], [])
    assert len(result["knowledge"]) == 1
    assert result["knowledge"][0]["title"] == "知识点"
    assert result["knowledge"][0]["mastery"] == 0.75
    assert [row["answer"] for row in result["evidence"]] == ["第二次选择", "第一次选择"]
    assert result["evidence"][0]["title"] == bank_item.prompt
    assert "correct_option_ids" not in json.dumps(result)
    assert "expected_answer" not in json.dumps(result)


def test_reviewed_flashcards_have_saved_front_back_and_session_isolation(tmp_path):
    value = session()
    source = chapter(3)
    course = ChapterCourseCompiler().compile(
        chapter=source,
        profile=value.profile,
        decision=PersonalizationPolicy().decide(value.profile, source.chapter_id),
    )
    repository = SQLiteAssessmentRepository(tmp_path / "records.sqlite3")
    repository.create_session(value)
    repository.save_course(value.session_id, course)
    card = course.flashcards[0]
    value.profile.flashcard_reviews[card.card_id] = FlashcardReviewState(
        last_rating="good", due_at=datetime.now(UTC)
    )
    result = learning_records(value, [], repository.courses_for_session(value.session_id))
    assert result["flashcards"][0]["front"] == card.front
    assert result["flashcards"][0]["back"] == card.back
    assert result["flashcards"][0]["rating"] == "good"
    assert repository.courses_for_session("another-session") == []
    assert learning_records(session(), [], [])["flashcards"] == []


def test_missing_historical_card_preserves_count_without_inventing_text():
    value = session()
    value.profile.flashcard_reviews["missing"] = FlashcardReviewState(last_rating="hard")
    row = learning_records(value, [], [])["flashcards"][0]
    assert row["back"] is None
    assert "暂不可用" in row["front"]


def test_new_records_keep_question_and_answer_after_bank_replacement():
    value = session()
    question = item()
    response = AssessmentResponse(
        item_id=question.item_id, answer="当时的选择", confidence=0.7, response_seconds=20
    )
    AdaptiveAssessmentEngine().update_profile(
        value.profile, question, response, ScoredEvidence(score=1, scoring_confidence=1)
    )
    restored = type(value).model_validate_json(value.model_dump_json())
    result = learning_records(restored, [], [])
    assert result["evidence"][0]["title"] == question.prompt
    assert result["evidence"][0]["answer"] == response.answer
    assert result["evidence"][0]["pages"] == question.source_pages
    assert result["knowledge"][0]["title"] == question.knowledge_point_labels[0]
    assert "expected_answer" not in json.dumps(result)
