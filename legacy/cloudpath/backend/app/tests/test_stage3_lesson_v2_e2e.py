from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.lessons.service import lesson_job_store, run_lesson_build_job
from app.rag.cache import clear_rag_cache
from app.schemas.books import Chapter, Chunk, LessonBuildRequest
from app.services.artifact_store import write_assets_and_chunks, write_chapters


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    monkeypatch.setenv("BOOKCOURSE_LLM_PROVIDER", "template")
    get_settings.cache_clear()
    clear_rag_cache()
    lesson_job_store.reload()
    yield
    clear_rag_cache()
    get_settings.cache_clear()
    lesson_job_store.reload()


def test_lesson_job_consumes_indexable_v2_chunk_end_to_end() -> None:
    book_id = "stage3_lesson_book"
    chapter_id = "chapter_transport"
    chapter = Chapter(
        chapter_id=chapter_id,
        level=1,
        source_title="Membrane Transport",
        ai_title="Membrane Transport",
        page_start=1,
        page_end=2,
        confidence=96,
        status="confirmed",
        source="stage3-test",
    )
    chunk = Chunk(
        chunk_id="stage3_lesson_v2_chunk",
        book_id=book_id,
        chapter_id=chapter_id,
        page_start=1,
        page_end=2,
        content_type="paragraph",
        text=(
            "Selective membranes regulate transport through channels, carriers, "
            "and energy-coupled pumps. Concentration gradients determine passive "
            "movement, while ATP can drive active transport against a gradient."
        ),
        chunk_version="v2",
        heading_path=["Cell Biology", "Membrane Transport"],
        quality_score=0.96,
        token_count=52,
        metadata={
            "indexable": True,
            "quality_threshold": 0.45,
            "quality_formula_version": "quality-v1",
        },
    )
    write_chapters(book_id, [chapter])
    write_assets_and_chunks(book_id, [], [chunk])
    job = lesson_job_store.create(book_id)

    run_lesson_build_job(
        job.job_id,
        book_id,
        LessonBuildRequest(chapter_ids=[chapter_id]),
    )

    result = lesson_job_store.get(job.job_id)
    assert result is not None and result.status == "done"
    assert len(result.lessons) == 1
    lesson = result.lessons[0]
    assert lesson.chapter_id == chapter_id
    assert chunk.chunk_id in lesson.source_chunk_ids
    assert any(chunk.chunk_id in block.source_chunk_ids for block in lesson.blocks)

