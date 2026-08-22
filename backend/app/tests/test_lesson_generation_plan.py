from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.lessons import service as lesson_service
from app.lessons.planning import build_lesson_generation_plan
from app.lessons.service import lesson_job_store, run_lesson_build_job
from app.schemas.books import (
    Chapter,
    Chunk,
    Flashcard,
    Lesson,
    LessonBuildRequest,
    QuizQuestion,
)
from app.services.artifact_store import (
    read_flashcards,
    read_lessons,
    read_quizzes,
    write_assets_and_chunks,
    write_chapters,
    write_flashcards,
    write_lessons,
    write_quizzes,
)


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "template")
    get_settings.cache_clear()
    lesson_job_store.reload()
    yield
    get_settings.cache_clear()
    lesson_job_store.reload()


def _chapter(
    chapter_id: str,
    title: str,
    *,
    parent_id: str | None = None,
    level: int = 1,
) -> Chapter:
    return Chapter(
        chapter_id=chapter_id,
        level=level,
        source_title=title,
        ai_title=title,
        page_start=1,
        page_end=2,
        confidence=95,
        status="confirmed",
        source="test",
        parent_id=parent_id,
    )


def _chunk(book_id: str, chapter_id: str, suffix: str) -> Chunk:
    return Chunk(
        chunk_id=f"chunk_{chapter_id}_{suffix}",
        book_id=book_id,
        chapter_id=chapter_id,
        page_start=1,
        page_end=2,
        content_type="paragraph",
        text=f"Reliable source text for {chapter_id} {suffix}. " * 12,
        quality_score=0.96,
        metadata={"indexable": True, "quality_threshold": 0.45},
    )


def _run_job(book_id: str, chapter_ids: list[str] | None = None):
    job = lesson_job_store.create(book_id)
    run_lesson_build_job(job.job_id, book_id, LessonBuildRequest(chapter_ids=chapter_ids, force=True))
    result = lesson_job_store.get(job.job_id)
    assert result is not None
    return result


def test_container_without_direct_chunks_expands_to_child_and_cleans_stale_artifacts() -> None:
    book_id = "book_container"
    parent = _chapter("parent", "Parent module")
    child = _chapter("child", "Child lesson", parent_id="parent", level=2)
    write_chapters(book_id, [parent, child])
    write_assets_and_chunks(book_id, [], [_chunk(book_id, "child", "one")])
    write_lessons(
        book_id,
        [
            Lesson(
                book_id=book_id,
                lesson_id="lesson_parent",
                chapter_id="parent",
                title="Stale parent lesson",
                source_title="Parent module",
                page_start=1,
                page_end=2,
            )
        ],
    )
    write_flashcards(
        book_id,
        [
            Flashcard(
                card_id="card_parent_001",
                book_id=book_id,
                lesson_id="lesson_parent",
                chapter_id="parent",
                front="Old",
                back="Old",
                concept="Old",
                page_start=1,
                page_end=2,
            )
        ],
    )
    write_quizzes(
        book_id,
        [
            QuizQuestion(
                question_id="quiz_parent_001",
                book_id=book_id,
                lesson_id="lesson_parent",
                chapter_id="parent",
                prompt="Old?",
                choices=["Yes", "No"],
                answer="Yes",
                explanation="Old",
                concept="Old",
                page_start=1,
                page_end=2,
            )
        ],
    )

    result = _run_job(book_id, ["parent"])

    assert result.status == "done"
    assert [lesson.chapter_id for lesson in result.lessons] == ["child"]
    assert [(item.chapter_id, item.status) for item in result.chapter_results] == [
        ("parent", "container"),
        ("child", "done"),
    ]
    assert [lesson.chapter_id for lesson in read_lessons(book_id)] == ["child"]
    assert read_flashcards(book_id) == []
    assert read_quizzes(book_id) == []


def test_parent_direct_chunks_create_module_intro_without_child_chunk_aggregation() -> None:
    book_id = "book_module_intro"
    parent = _chapter("parent", "Parent module")
    child = _chapter("child", "Child lesson", parent_id="parent", level=2)
    parent_chunk = _chunk(book_id, "parent", "intro")
    child_chunk = _chunk(book_id, "child", "body")
    write_chapters(book_id, [parent, child])
    write_assets_and_chunks(book_id, [], [parent_chunk, child_chunk])

    plan = build_lesson_generation_plan(book_id, LessonBuildRequest(chapter_ids=["parent", "child"]))
    assert [(entry.chapter.chapter_id, entry.lesson_kind) for entry in plan.targets] == [
        ("parent", "module_intro"),
        ("child", "lesson"),
    ]

    result = _run_job(book_id, ["parent", "child"])

    assert result.status == "done"
    lessons = {lesson.chapter_id: lesson for lesson in result.lessons}
    assert lessons["parent"].lesson_kind == "module_intro"
    assert lessons["child"].lesson_kind == "lesson"
    assert lessons["parent"].source_chunk_ids == [parent_chunk.chunk_id]
    assert lessons["child"].source_chunk_ids == [child_chunk.chunk_id]


def test_empty_leaf_is_reported_without_stopping_other_lessons() -> None:
    book_id = "book_partial"
    write_chapters(
        book_id,
        [
            _chapter("ready", "Ready lesson"),
            _chapter("empty", "Empty lesson"),
        ],
    )
    write_assets_and_chunks(book_id, [], [_chunk(book_id, "ready", "body")])

    result = _run_job(book_id)

    assert result.status == "done_with_warnings"
    assert [lesson.chapter_id for lesson in result.lessons] == ["ready"]
    statuses = {item.chapter_id: item.status for item in result.chapter_results}
    assert statuses == {"ready": "done", "empty": "skipped"}
    empty = next(item for item in result.chapter_results if item.chapter_id == "empty")
    assert empty.reason == "lesson_source_missing"


def test_job_fails_only_when_no_course_target_can_be_generated() -> None:
    book_id = "book_all_empty"
    write_chapters(book_id, [_chapter("empty", "Empty lesson")])
    write_assets_and_chunks(book_id, [], [])

    result = _run_job(book_id)

    assert result.status == "failed"
    assert result.lessons == []
    assert result.chapter_results[0].status == "skipped"
    assert "没有可生成课程" in (result.error or "")


def test_one_generation_exception_does_not_abort_other_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    book_id = "book_generation_warning"
    write_chapters(
        book_id,
        [
            _chapter("ready", "Ready lesson"),
            _chapter("broken", "Broken lesson"),
        ],
    )
    write_assets_and_chunks(
        book_id,
        [],
        [
            _chunk(book_id, "ready", "body"),
            _chunk(book_id, "broken", "body"),
        ],
    )
    original_build = lesson_service._build_target_lesson

    def flaky_build(book_id_arg, entry, adapter):
        if entry.chapter.chapter_id == "broken":
            raise RuntimeError("simulated provider failure")
        return original_build(book_id_arg, entry, adapter)

    monkeypatch.setattr(lesson_service, "_build_target_lesson", flaky_build)

    result = _run_job(book_id)

    assert result.status == "done_with_warnings"
    assert [lesson.chapter_id for lesson in result.lessons] == ["ready"]
    statuses = {item.chapter_id: item.status for item in result.chapter_results}
    assert statuses == {"ready": "done", "broken": "failed"}
    assert [lesson.chapter_id for lesson in read_lessons(book_id)] == ["ready"]


def test_invalid_provider_content_is_retried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    book_id = "book_generation_retry"
    write_chapters(book_id, [_chapter("ready", "Ready lesson")])
    write_assets_and_chunks(book_id, [], [_chunk(book_id, "ready", "body")])
    original_build = lesson_service._build_target_lesson
    attempts = 0

    def invalid_once(book_id_arg, entry, adapter):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise AppError("lesson_llm_invalid_schema", "invalid provider content", status_code=502)
        return original_build(book_id_arg, entry, adapter)

    monkeypatch.setattr(lesson_service, "_build_target_lesson", invalid_once)

    result = _run_job(book_id)

    assert attempts == 2
    assert result.status == "done"
    assert [lesson.chapter_id for lesson in result.lessons] == ["ready"]


def test_requesting_parent_and_child_deduplicates_the_expanded_child() -> None:
    book_id = "book_deduplicate"
    parent = _chapter("parent", "Parent module")
    child = _chapter("child", "Child lesson", parent_id="parent", level=2)
    write_chapters(book_id, [parent, child])
    write_assets_and_chunks(book_id, [], [_chunk(book_id, "child", "body")])

    plan = build_lesson_generation_plan(book_id, LessonBuildRequest(chapter_ids=["parent", "child"]))

    assert plan.scope_ids == ["parent", "child"]
    assert [entry.chapter.chapter_id for entry in plan.targets] == ["child"]
