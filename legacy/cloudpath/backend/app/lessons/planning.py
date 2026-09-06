from __future__ import annotations

from dataclasses import dataclass

from app.core.errors import AppError
from app.lessons.source_builder import build_chapter_source_package
from app.schemas.books import (
    Chapter,
    ChapterSourcePackage,
    LessonBuildChapterResult,
    LessonBuildRequest,
)
from app.services.artifact_store import read_chapters


@dataclass(frozen=True)
class LessonGenerationPlanEntry:
    chapter: Chapter
    source: ChapterSourcePackage
    disposition: str
    lesson_kind: str | None = None

    def initial_result(self) -> LessonBuildChapterResult:
        title = self.chapter.ai_title or self.chapter.source_title
        if self.disposition == "target":
            return LessonBuildChapterResult(
                chapter_id=self.chapter.chapter_id,
                chapter_title=title,
                status="pending",
                lesson_kind=self.lesson_kind,
                message="等待生成课程",
            )
        if self.disposition == "container":
            return LessonBuildChapterResult(
                chapter_id=self.chapter.chapter_id,
                chapter_title=title,
                status="container",
                reason="container_node",
                message="作为目录分组，已展开下级章节",
            )
        return LessonBuildChapterResult(
            chapter_id=self.chapter.chapter_id,
            chapter_title=title,
            status="skipped",
            reason="lesson_source_missing",
            message="没有可靠直属全文片段",
        )


@dataclass(frozen=True)
class LessonGenerationPlan:
    entries: list[LessonGenerationPlanEntry]

    @property
    def targets(self) -> list[LessonGenerationPlanEntry]:
        return [entry for entry in self.entries if entry.disposition == "target"]

    @property
    def scope_ids(self) -> list[str]:
        return [entry.chapter.chapter_id for entry in self.entries]

    def initial_results(self) -> list[LessonBuildChapterResult]:
        return [entry.initial_result() for entry in self.entries]


def build_lesson_generation_plan(book_id: str, request: LessonBuildRequest) -> LessonGenerationPlan:
    chapters = read_chapters(book_id)
    if not chapters:
        raise AppError("chapters_missing", "章节结果不存在，请先解析并确认目录", status_code=404)

    chapters_by_id = {chapter.chapter_id: chapter for chapter in chapters}
    requested_ids = request.chapter_ids or [chapter.chapter_id for chapter in chapters]
    missing = [chapter_id for chapter_id in requested_ids if chapter_id not in chapters_by_id]
    if missing:
        raise AppError(
            "chapter_not_found",
            "课程生成引用了不存在的章节",
            details={"missing": missing},
            status_code=404,
        )

    children_by_parent: dict[str, list[Chapter]] = {}
    for chapter in chapters:
        parent_id = chapter.parent_id
        if not parent_id or parent_id == chapter.chapter_id or parent_id not in chapters_by_id:
            continue
        children_by_parent.setdefault(parent_id, []).append(chapter)

    entries: list[LessonGenerationPlanEntry] = []
    visited: set[str] = set()

    def visit(chapter_id: str) -> None:
        if chapter_id in visited:
            return
        visited.add(chapter_id)
        chapter = chapters_by_id[chapter_id]
        children = children_by_parent.get(chapter_id, [])
        source = build_chapter_source_package(book_id, chapter_id)
        if source.windows:
            entries.append(
                LessonGenerationPlanEntry(
                    chapter=chapter,
                    source=source,
                    disposition="target",
                    lesson_kind="module_intro" if children else "lesson",
                )
            )
        elif children:
            entries.append(
                LessonGenerationPlanEntry(
                    chapter=chapter,
                    source=source,
                    disposition="container",
                )
            )
        else:
            entries.append(
                LessonGenerationPlanEntry(
                    chapter=chapter,
                    source=source,
                    disposition="skipped",
                )
            )
        for child in children:
            visit(child.chapter_id)

    for chapter_id in requested_ids:
        visit(chapter_id)

    return LessonGenerationPlan(entries=entries)
