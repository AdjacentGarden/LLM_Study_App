from __future__ import annotations

from pathlib import Path

import pytest

from app.core.config import get_settings
from app.image_generation.service import image_job_store, run_generation_job
from app.rag.cache import clear_rag_cache
from app.schemas.books import Chunk, ImageGenerationRequest
from app.services.artifact_store import write_assets_and_chunks
from app.services.storage import asset_dir


@pytest.fixture(autouse=True)
def _isolated_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_PERSIST_STATE", "false")
    monkeypatch.setenv("BOOKCOURSE_IMAGE_PROVIDER", "mock")
    get_settings.cache_clear()
    clear_rag_cache()
    image_job_store.reload()
    yield
    clear_rag_cache()
    get_settings.cache_clear()
    image_job_store.reload()


def test_failed_bundle_commit_removes_unreferenced_generated_files(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = "image_cleanup_book"
    source = Chunk(
        chunk_id="source_chunk",
        book_id=book_id,
        chapter_id="chapter_one",
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text="Source evidence for a generated diagram.",
    )
    write_assets_and_chunks(book_id, [], [source])
    payload = ImageGenerationRequest(
        book_id=book_id,
        purpose="commit failure cleanup",
        source_chunk_ids=[source.chunk_id],
    )
    job = image_job_store.create(book_id)

    def fail_commit(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("simulated bundle commit failure")

    monkeypatch.setattr(
        "app.image_generation.service.write_assets_and_chunks",
        fail_commit,
    )

    run_generation_job(job.job_id, payload)

    result = image_job_store.get(job.job_id)
    assert result is not None and result.status == "failed"
    assert list(asset_dir(book_id).glob("*.png")) == []
