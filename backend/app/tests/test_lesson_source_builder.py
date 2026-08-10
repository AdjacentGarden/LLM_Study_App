from __future__ import annotations

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.lessons.source_builder import build_chapter_source_package, build_chapter_source_packages
from app.schemas.books import Chapter, Chunk
from app.services.artifact_store import write_chapters, write_chunks


def _seed_sources(monkeypatch, tmp_path) -> str:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    book_id = "book_source_builder"
    write_chapters(
        book_id,
        [
            Chapter(
                chapter_id="c1",
                level=1,
                source_title="Chapter 1",
                ai_title="Lesson 1",
                page_start=1,
                page_end=5,
                confidence=90,
                status="confirmed",
                source="test",
            ),
            Chapter(
                chapter_id="c2",
                level=1,
                source_title="Chapter 2",
                ai_title="Lesson 2",
                page_start=6,
                page_end=8,
                confidence=90,
                status="confirmed",
                source="test",
            ),
        ],
    )
    write_chunks(
        book_id,
        [
            Chunk(
                chunk_id="c1_chunk_002",
                book_id=book_id,
                chapter_id="c1",
                page_start=2,
                page_end=2,
                content_type="text",
                text="Second reliable chunk about concept B.",
            ),
            Chunk(
                chunk_id="c2_chunk_001",
                book_id=book_id,
                chapter_id="c2",
                page_start=6,
                page_end=6,
                content_type="text",
                text="This belongs to chapter two.",
            ),
            Chunk(
                chunk_id="c1_chunk_001",
                book_id=book_id,
                chapter_id="c1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="First reliable chunk about concept A.",
            ),
            Chunk(
                chunk_id="c1_pending",
                book_id=book_id,
                chapter_id="c1",
                page_start=3,
                page_end=3,
                content_type="ocr_pending",
                text="Pending OCR placeholder.",
            ),
            Chunk(
                chunk_id="c1_duplicate",
                book_id=book_id,
                chapter_id="c1",
                page_start=4,
                page_end=4,
                content_type="text",
                text="First reliable chunk about concept A.",
            ),
        ],
    )
    return book_id


def test_build_chapter_source_package_keeps_chapter_boundaries(monkeypatch, tmp_path) -> None:
    book_id = _seed_sources(monkeypatch, tmp_path)

    package = build_chapter_source_package(book_id, "c1", max_window_chars=1000)

    assert package.chapter_id == "c1"
    assert package.source_chunk_ids == ["c1_chunk_001", "c1_chunk_002"]
    assert "c2_chunk_001" not in package.source_chunk_ids
    assert package.windows[0].page_start == 1
    assert package.windows[0].page_end == 2
    assert any(item.startswith("skipped_ocr_pending_chunks") for item in package.warnings)
    assert any(item.startswith("skipped_duplicate_chunks") for item in package.warnings)
    get_settings.cache_clear()


def test_build_chapter_source_package_splits_long_windows(monkeypatch, tmp_path) -> None:
    book_id = _seed_sources(monkeypatch, tmp_path)
    write_chunks(
        book_id,
        [
            Chunk(
                chunk_id=f"c1_long_{index:03d}",
                book_id=book_id,
                chapter_id="c1",
                page_start=index,
                page_end=index,
                content_type="text",
                text=f"chunk {index} " + ("x" * 520),
            )
            for index in range(1, 7)
        ],
    )

    package = build_chapter_source_package(book_id, "c1", max_window_chars=1200)

    assert len(package.windows) >= 3
    assert all(window.source_chunk_ids for window in package.windows)
    assert package.chunk_count == 6
    get_settings.cache_clear()


def test_build_all_source_packages_and_missing_chapter(monkeypatch, tmp_path) -> None:
    book_id = _seed_sources(monkeypatch, tmp_path)

    packages = build_chapter_source_packages(book_id, max_window_chars=1000)
    assert [package.chapter_id for package in packages] == ["c1", "c2"]

    with pytest.raises(AppError):
        build_chapter_source_package(book_id, "missing", max_window_chars=1000)
    get_settings.cache_clear()
