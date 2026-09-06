from __future__ import annotations

from hashlib import sha256
import os

from app.image_generation.service import image_job_store, run_generation_job
from app.schemas.books import ImageGenerationRequest
from app.schemas.books import Chunk
from app.services.artifact_store import read_assets, write_chunks
from app.services.storage import asset_dir


def test_mock_image_generation_creates_ai_asset(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_IMAGE_PROVIDER", "mock")
    from app.core.config import get_settings

    get_settings.cache_clear()
    payload = ImageGenerationRequest(
        book_id="book_img_test",
        lesson_id="lesson_c2s1",
        chapter_id="c2s1",
        concepts=["减数分裂", "同源染色体"],
        purpose="解释同源染色体分离",
        source_chunk_ids=["chunk_001"],
    )
    write_chunks(
        payload.book_id,
        [
            Chunk(
                chunk_id="chunk_001",
                book_id=payload.book_id,
                chapter_id="c2s1",
                page_start=30,
                page_end=30,
                content_type="text",
                text="同源染色体在减数第一次分裂后期分离。",
            )
        ],
    )
    job = image_job_store.create(payload.book_id)
    run_generation_job(job.job_id, payload)
    result = image_job_store.get(job.job_id)
    assert result is not None
    assert result.status == "done"
    assert result.asset is not None
    assert result.asset.source_type == "ai_generated"
    assert result.asset.source_chunk_ids == ["chunk_001"]
    generated_path = asset_dir(payload.book_id) / f"{result.asset.asset_id}.png"
    expected_hash = sha256(generated_path.read_bytes()).hexdigest()
    assert result.asset.content_hash == expected_hash
    assets = read_assets(payload.book_id)
    assert assets[0].generation_prompt
    assert assets[0].generation_provider == "mock"
    assert assets[0].content_hash == expected_hash
    get_settings.cache_clear()
    os.environ.pop("BOOKCOURSE_STORAGE_ROOT", None)
