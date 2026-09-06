from __future__ import annotations

from app.core.config import get_settings
from app.schemas.books import Flashcard, Lesson, LessonBlock, LessonCitation, QuizQuestion
from app.services.artifact_store import (
    read_flashcards,
    read_lessons,
    read_quizzes,
    write_flashcards,
    write_lessons,
    write_quizzes,
)


def test_lesson_artifacts_roundtrip(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_lesson_artifacts"

    lesson = Lesson(
        book_id=book_id,
        lesson_id="lesson_c1",
        chapter_id="c1",
        title="Lesson 1",
        source_title="Chapter 1",
        page_start=1,
        page_end=8,
        confidence=88,
        objectives=["Understand the core idea"],
        key_concepts=["concept A"],
        summary="A structured lesson summary.",
        blocks=[
            LessonBlock(
                block_id="lesson_c1_b001",
                block_type="explanation",
                title="Core explanation",
                content="Generated explanation based on source chunks.",
                citations=[
                    LessonCitation(
                        chunk_id="chunk_001",
                        page_start=2,
                        page_end=2,
                        quote="Short source evidence.",
                    )
                ],
                source_chunk_ids=["chunk_001"],
                ai_generated=True,
            )
        ],
        source_chunk_ids=["chunk_001"],
    )
    card = Flashcard(
        card_id="card_c1_001",
        book_id=book_id,
        lesson_id="lesson_c1",
        chapter_id="c1",
        front="What is concept A?",
        back="Concept A is explained from the cited source.",
        concept="concept A",
        source_chunk_ids=["chunk_001"],
        page_start=2,
        page_end=2,
        reason="Generated from lesson evidence.",
    )
    quiz = QuizQuestion(
        question_id="quiz_c1_001",
        book_id=book_id,
        lesson_id="lesson_c1",
        chapter_id="c1",
        prompt="Which statement matches concept A?",
        choices=["A", "B"],
        answer="A",
        explanation="A is supported by the cited source.",
        concept="concept A",
        source_chunk_ids=["chunk_001"],
        page_start=2,
        page_end=2,
    )

    write_lessons(book_id, [lesson])
    write_flashcards(book_id, [card])
    write_quizzes(book_id, [quiz])

    assert read_lessons(book_id)[0].blocks[0].citations[0].chunk_id == "chunk_001"
    assert read_flashcards(book_id)[0].source_chunk_ids == ["chunk_001"]
    assert read_quizzes(book_id)[0].explanation.startswith("A is")
    get_settings.cache_clear()


def test_missing_lesson_artifacts_return_empty_lists(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()

    assert read_lessons("book_missing") == []
    assert read_flashcards("book_missing") == []
    assert read_quizzes("book_missing") == []
    get_settings.cache_clear()
