from __future__ import annotations

from app.core.errors import AppError
from app.rag.embedding import render_chunk_embedding_text
from app.rag.retrieval import reliable_chunks_for_chapter
from app.schemas.books import Chapter, ChapterSourcePackage, ChapterSourceWindow, Chunk
from app.services.artifact_store import read_chapters


def _find_chapter(chapters: list[Chapter], chapter_id: str) -> Chapter:
    for chapter in chapters:
        if chapter.chapter_id == chapter_id:
            return chapter
    raise AppError("chapter_not_found", "chapter does not exist", details={"chapter_id": chapter_id}, status_code=404)


def _build_windows(chapter_id: str, chunks: list[Chunk], max_window_chars: int) -> list[ChapterSourceWindow]:
    windows: list[ChapterSourceWindow] = []
    current: list[Chunk] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if not current:
            return
        index = len(windows) + 1
        windows.append(
            ChapterSourceWindow(
                window_id=f"{chapter_id}_w{index:03d}",
                page_start=min(chunk.page_start for chunk in current),
                page_end=max(chunk.page_end for chunk in current),
                source_chunk_ids=[chunk.chunk_id for chunk in current],
                text="\n\n".join(render_chunk_embedding_text(chunk) for chunk in current),
                plain_text="\n\n".join(chunk.text.strip() for chunk in current if chunk.text.strip()),
            )
        )
        current = []
        current_length = 0

    for chunk in chunks:
        rendered_text = render_chunk_embedding_text(chunk)
        projected = current_length + len(rendered_text)
        if current and projected > max_window_chars:
            flush()
        current.append(chunk)
        current_length += len(rendered_text)
        if len(rendered_text) >= max_window_chars:
            flush()

    flush()
    return windows


def build_chapter_source_package(book_id: str, chapter_id: str, max_window_chars: int = 6000) -> ChapterSourcePackage:
    if max_window_chars < 1000:
        raise AppError("invalid_window_size", "source window must be at least 1000 characters", status_code=400)
    chapters = read_chapters(book_id)
    chapter = _find_chapter(chapters, chapter_id)
    reliable, warnings = reliable_chunks_for_chapter(book_id, chapter_id)
    windows = _build_windows(chapter_id, reliable, max_window_chars)
    source_chunk_ids = [chunk.chunk_id for chunk in reliable]
    text_length = sum(len(render_chunk_embedding_text(chunk)) for chunk in reliable)

    return ChapterSourcePackage(
        book_id=book_id,
        chapter_id=chapter_id,
        chapter_title=chapter.source_title or chapter.ai_title,
        page_start=chapter.page_start,
        page_end=chapter.page_end,
        chapter_confidence=chapter.confidence,
        chunk_count=len(reliable),
        text_length=text_length,
        source_chunk_ids=source_chunk_ids,
        windows=windows,
        warnings=warnings,
    )


def build_chapter_source_packages(book_id: str, chapter_ids: list[str] | None = None, max_window_chars: int = 6000) -> list[ChapterSourcePackage]:
    chapters = read_chapters(book_id)
    selected_ids = chapter_ids or [chapter.chapter_id for chapter in chapters]
    return [build_chapter_source_package(book_id, chapter_id, max_window_chars=max_window_chars) for chapter_id in selected_ids]
