from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4
import time

from app.core.errors import AppError
from app.lessons.audit import write_lesson_generation_audit
from app.lessons.llm import get_lesson_adapter
from app.lessons.planning import (
    LessonGenerationPlan,
    LessonGenerationPlanEntry,
    build_lesson_generation_plan,
)
from app.schemas.books import (
    Flashcard,
    Lesson,
    LessonBuildChapterResult,
    LessonBuildRequest,
    QuizQuestion,
)
from app.services.artifact_store import (
    read_assets,
    read_flashcards,
    read_lessons,
    read_quizzes,
    write_flashcards,
    write_lessons,
    write_quizzes,
)
from app.services.kv_store import _PersistedKVStore


@dataclass
class LessonBuildJob:
    job_id: str
    book_id: str
    status: str
    stage: str
    progress: int
    lessons: list[Lesson] = field(default_factory=list)
    chapter_results: list[LessonBuildChapterResult] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "book_id": self.book_id,
            "status": self.status,
            "stage": self.stage,
            "progress": self.progress,
            "lessons": [item.model_dump(mode="json") for item in self.lessons],
            "chapter_results": [item.model_dump(mode="json") for item in self.chapter_results],
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "LessonBuildJob":
        lessons = [Lesson.model_validate(item) for item in data.get("lessons", [])]
        chapter_results = [LessonBuildChapterResult.model_validate(item) for item in data.get("chapter_results", [])]
        return cls(
            job_id=data["job_id"],
            book_id=data["book_id"],
            status=data["status"],
            stage=data["stage"],
            progress=data["progress"],
            lessons=lessons,
            chapter_results=chapter_results,
            error=data.get("error"),
        )


class LessonBuildJobStore:
    def __init__(self) -> None:
        self._kv = _PersistedKVStore("lesson_jobs")

    def create(self, book_id: str) -> LessonBuildJob:
        job = LessonBuildJob(job_id=f"lesson_job_{uuid4().hex[:12]}", book_id=book_id, status="pending", stage="queued", progress=0)
        self._kv.upsert(job.job_id, job.to_dict())
        return job

    def get(self, job_id: str) -> LessonBuildJob | None:
        data = self._kv.get(job_id)
        return LessonBuildJob.from_dict(data) if data else None

    def update(self, job_id: str, **updates: object) -> LessonBuildJob:
        def _merge(current: dict | None) -> dict:
            data = dict(current or {})
            for key, value in updates.items():
                if key == "progress" and isinstance(value, int):
                    value = max(0, min(100, value))
                if key == "lessons" and isinstance(value, list):
                    data["lessons"] = [item.model_dump(mode="json") for item in value]
                elif key == "chapter_results" and isinstance(value, list):
                    data["chapter_results"] = [item.model_dump(mode="json") for item in value]
                else:
                    data[key] = value
            return data

        updated = self._kv.update_in_place(job_id, _merge)
        return LessonBuildJob.from_dict(updated)

    def reload(self) -> None:
        self._kv.reload()


lesson_job_store = LessonBuildJobStore()


def _attach_assets(book_id: str, lesson: Lesson) -> Lesson:
    asset_ids = [
        asset.asset_id
        for asset in read_assets(book_id)
        if asset.chapter_id == lesson.chapter_id
    ]
    if not asset_ids:
        return lesson
    existing = set(lesson.asset_ids)
    return lesson.model_copy(update={"asset_ids": lesson.asset_ids + [asset_id for asset_id in asset_ids if asset_id not in existing]})


def _ordered_results(
    plan: LessonGenerationPlan,
    results_by_id: dict[str, LessonBuildChapterResult],
) -> list[LessonBuildChapterResult]:
    return [
        results_by_id.get(entry.chapter.chapter_id, entry.initial_result())
        for entry in plan.entries
    ]


def _record_skipped_source_audits(plan: LessonGenerationPlan, provider: str) -> None:
    for entry in plan.entries:
        if entry.disposition != "skipped":
            continue
        write_lesson_generation_audit(
            entry.source,
            provider=provider,
            status="skipped",
            duration_ms=0,
            error_code="lesson_source_missing",
        )


def _build_target_lesson(book_id: str, entry: LessonGenerationPlanEntry, adapter: object) -> Lesson:
    started = time.perf_counter()
    try:
        lesson = adapter.build_lesson(entry.source)  # type: ignore[attr-defined]
        lesson = lesson.model_copy(update={"lesson_kind": entry.lesson_kind or "lesson"})
        lesson = _attach_assets(book_id, lesson)
        write_lesson_generation_audit(
            entry.source,
            provider=adapter.name,  # type: ignore[attr-defined]
            status="done",
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return lesson
    except Exception as exc:
        error_code = exc.code if isinstance(exc, AppError) else exc.__class__.__name__
        write_lesson_generation_audit(
            entry.source,
            provider=adapter.name,  # type: ignore[attr-defined]
            status="failed",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error_code=error_code,
        )
        raise


def _persist_generation_scope(book_id: str, plan: LessonGenerationPlan, generated: list[Lesson]) -> None:
    scope_ids = set(plan.scope_ids)
    existing = read_lessons(book_id)
    merged = [lesson for lesson in existing if lesson.chapter_id not in scope_ids]
    merged.extend(generated)
    write_lessons(book_id, merged)

    write_flashcards(
        book_id,
        [card for card in read_flashcards(book_id) if card.chapter_id not in scope_ids],
    )
    write_quizzes(
        book_id,
        [question for question in read_quizzes(book_id) if question.chapter_id not in scope_ids],
    )


def build_lessons(book_id: str, request: LessonBuildRequest) -> list[Lesson]:
    plan = build_lesson_generation_plan(book_id, request)
    adapter = get_lesson_adapter()
    _record_skipped_source_audits(plan, adapter.name)
    if not plan.targets:
        raise AppError(
            "lesson_sources_missing",
            "所选目录中没有可生成课程的可靠全文片段",
            details={"chapter_results": [item.model_dump(mode="json") for item in plan.initial_results()]},
            status_code=409,
        )
    generated = [_build_target_lesson(book_id, entry, adapter) for entry in plan.targets]
    _persist_generation_scope(book_id, plan, generated)
    return generated


def run_lesson_build_job(job_id: str, book_id: str, request: LessonBuildRequest) -> None:
    try:
        lesson_job_store.update(job_id, status="processing", stage="planning_lessons", progress=5)
        plan = build_lesson_generation_plan(book_id, request)
        adapter = get_lesson_adapter()
        _record_skipped_source_audits(plan, adapter.name)
        results_by_id = {
            result.chapter_id: result
            for result in plan.initial_results()
        }
        lesson_job_store.update(
            job_id,
            stage="collecting_sources",
            progress=10,
            chapter_results=_ordered_results(plan, results_by_id),
        )

        if not plan.targets:
            _persist_generation_scope(book_id, plan, [])
            lesson_job_store.update(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                chapter_results=_ordered_results(plan, results_by_id),
                error="所选目录中没有可生成课程的可靠全文片段",
            )
            return

        generated: list[Lesson] = []
        total = len(plan.targets)
        for index, entry in enumerate(plan.targets, start=1):
            chapter_id = entry.chapter.chapter_id
            chapter_title = entry.chapter.ai_title or entry.chapter.source_title
            results_by_id[chapter_id] = LessonBuildChapterResult(
                chapter_id=chapter_id,
                chapter_title=chapter_title,
                status="processing",
                lesson_kind=entry.lesson_kind,
                message="正在生成课程",
            )
            lesson_job_store.update(
                job_id,
                stage="generating_lesson",
                progress=10 + int((index - 1) / total * 80),
                lessons=generated,
                chapter_results=_ordered_results(plan, results_by_id),
            )
            try:
                lesson = _build_target_lesson(book_id, entry, adapter)
            except Exception as exc:
                error_code = exc.code if isinstance(exc, AppError) else exc.__class__.__name__
                results_by_id[chapter_id] = LessonBuildChapterResult(
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    status="failed",
                    reason=str(error_code),
                    lesson_kind=entry.lesson_kind,
                    message=str(exc),
                )
            else:
                generated.append(lesson)
                results_by_id[chapter_id] = LessonBuildChapterResult(
                    chapter_id=chapter_id,
                    chapter_title=chapter_title,
                    status="done",
                    lesson_kind=entry.lesson_kind,
                    lesson_id=lesson.lesson_id,
                    message="课程生成完成",
                )

        _persist_generation_scope(book_id, plan, generated)
        chapter_results = _ordered_results(plan, results_by_id)
        has_warnings = any(result.status in {"skipped", "failed"} for result in chapter_results)
        if generated:
            status = "done_with_warnings" if has_warnings else "done"
            lesson_job_store.update(
                job_id,
                status=status,
                stage=status,
                progress=100,
                lessons=generated,
                chapter_results=chapter_results,
                error=None,
            )
        else:
            lesson_job_store.update(
                job_id,
                status="failed",
                stage="failed",
                progress=100,
                lessons=[],
                chapter_results=chapter_results,
                error="所有课程目标均生成失败",
            )
    except Exception as exc:
        lesson_job_store.update(job_id, status="failed", stage="failed", progress=100, error=str(exc))


def _lesson_source_chunk_ids(lesson: Lesson) -> list[str]:
    ids = list(lesson.source_chunk_ids)
    for block in lesson.blocks:
        ids.extend(block.source_chunk_ids)
        ids.extend(citation.chunk_id for citation in block.citations)
    unique: list[str] = []
    for chunk_id in ids:
        if chunk_id and chunk_id not in unique:
            unique.append(chunk_id)
    return unique


def _selected_lessons(book_id: str, chapter_ids: list[str] | None = None) -> list[Lesson]:
    lessons = read_lessons(book_id)
    if not lessons:
        raise AppError("lessons_missing", "课程内容不存在，请先生成课程", status_code=404)
    if chapter_ids:
        lessons = [lesson for lesson in lessons if lesson.chapter_id in chapter_ids]
    if not lessons:
        raise AppError("lesson_not_found", "未找到可生成复习内容的课程", status_code=404)
    return lessons


def build_flashcards(book_id: str, chapter_ids: list[str] | None = None) -> list[Flashcard]:
    lessons = _selected_lessons(book_id, chapter_ids)
    generated: list[Flashcard] = []
    for lesson in lessons:
        source_chunk_ids = _lesson_source_chunk_ids(lesson)
        if not source_chunk_ids:
            continue
        concepts = []
        for concept in lesson.key_concepts + [block.title for block in lesson.blocks if block.block_type in {"explanation", "misconception"}]:
            cleaned = concept.strip()
            if cleaned and cleaned not in concepts:
                concepts.append(cleaned)
            if len(concepts) >= 6:
                break
        if not concepts:
            concepts = [lesson.title]
        for index, concept in enumerate(concepts, start=1):
            explanation = next((block.content for block in lesson.blocks if concept in block.title or concept in block.content), lesson.summary)
            generated.append(
                Flashcard(
                    card_id=f"card_{lesson.chapter_id}_{index:03d}",
                    book_id=book_id,
                    lesson_id=lesson.lesson_id,
                    chapter_id=lesson.chapter_id,
                    front=f"解释：{concept}",
                    back=explanation[:360],
                    concept=concept,
                    source_chunk_ids=source_chunk_ids[:5],
                    page_start=lesson.page_start,
                    page_end=lesson.page_end,
                    reason="根据结构化课程内容和原文证据生成",
                )
            )

    existing = read_flashcards(book_id)
    selected_lesson_ids = {lesson.lesson_id for lesson in lessons}
    merged = [card for card in existing if card.lesson_id not in selected_lesson_ids]
    merged.extend(generated)
    write_flashcards(book_id, merged)
    return generated


def build_quizzes(book_id: str, chapter_ids: list[str] | None = None) -> list[QuizQuestion]:
    lessons = _selected_lessons(book_id, chapter_ids)
    generated: list[QuizQuestion] = []
    for lesson in lessons:
        source_chunk_ids = _lesson_source_chunk_ids(lesson)
        if not source_chunk_ids:
            continue
        concepts = lesson.key_concepts[:3] or [lesson.title]
        for index, concept in enumerate(concepts, start=1):
            generated.append(
                QuizQuestion(
                    question_id=f"quiz_{lesson.chapter_id}_{index:03d}",
                    book_id=book_id,
                    lesson_id=lesson.lesson_id,
                    chapter_id=lesson.chapter_id,
                    prompt=f"请判断并说明理由：学习“{concept}”时，需要结合原文页码和上下文证据。",
                    choices=["正确", "错误"],
                    answer="正确",
                    explanation=f"该题要求回到第 {lesson.page_start}-{lesson.page_end} 页和相关 source chunks 核对概念条件，避免脱离教材上下文记忆。",
                    concept=concept,
                    source_chunk_ids=source_chunk_ids[:5],
                    page_start=lesson.page_start,
                    page_end=lesson.page_end,
                )
            )

    existing = read_quizzes(book_id)
    selected_lesson_ids = {lesson.lesson_id for lesson in lessons}
    merged = [question for question in existing if question.lesson_id not in selected_lesson_ids]
    merged.extend(generated)
    write_quizzes(book_id, merged)
    return generated
