from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.document.pipeline import parse_document
from app.rag.cache import clear_rag_cache
from app.schemas.books import Chunk
from app.services.artifact_store import read_chunks, write_assets_and_chunks
from app.services.storage import artifact_dir


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    get_settings.cache_clear()
    clear_rag_cache()
    yield
    clear_rag_cache()
    get_settings.cache_clear()


def test_new_parse_hides_previous_bundle_before_document_detection(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    book_id = "pipeline_visibility_book"
    old = Chunk(
        chunk_id="old_chunk",
        book_id=book_id,
        chapter_id="old_chapter",
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text="old generation evidence",
    )
    write_assets_and_chunks(book_id, [], [old])
    assert [chunk.chunk_id for chunk in read_chunks(book_id)] == ["old_chunk"]

    def fail_after_visibility_check(*args: object, **kwargs: object):
        del args, kwargs
        assert read_chunks(book_id) == []
        raise RuntimeError("stop after bundle visibility check")

    monkeypatch.setattr("app.document.pipeline.detect_document", fail_after_visibility_check)

    with pytest.raises(RuntimeError, match="visibility check"):
        parse_document(
            book_id,
            tmp_path / "source.pdf",
            artifact_dir(book_id),
        )

    assert read_chunks(book_id) == []

