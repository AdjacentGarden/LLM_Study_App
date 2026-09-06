from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from threading import Event

import pytest

from app.core.config import get_settings
from app.core.errors import AppError
from app.document.rebuilder import rebuild_chunks_and_assets
from app.image_generation.service import (
    image_job_store,
    run_generation_job,
    source_chunks_for_payload,
)
from app.rag.cache import clear_rag_cache
from app.rag.retrieval import RetrievedChunk
from app.rag.service import _related_assets
from app.schemas.books import Asset, Chapter, Chunk, ImageGenerationRequest
from app.services import artifact_store
from app.services.artifact_store import (
    RagBundleStaleBuildError,
    mark_rag_bundle_building,
    read_assets,
    read_chunks,
    update_assets_and_chunks,
    write_assets_and_chunks,
)
from app.services.storage import artifact_dir


def _chunk(chunk_id: str, chapter_id: str = "ch1", text: str = "source evidence") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id="bundle_book",
        chapter_id=chapter_id,
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text=text,
    )


def _asset(
    asset_id: str,
    *,
    source_chunk_ids: list[str] | None = None,
    source_type: str = "mineru",
    review_status: str = "ready",
) -> Asset:
    return Asset(
        asset_id=asset_id,
        book_id="bundle_book",
        chapter_id="ch1",
        source_type=source_type,
        source_parser="mineru" if source_type == "mineru" else None,
        page=1 if source_type != "ai_generated" else None,
        type="figure",
        caption=f"caption {asset_id}",
        image_url=f"/assets/{asset_id}",
        thumbnail_url=f"/assets/{asset_id}/thumbnail",
        source_chunk_ids=source_chunk_ids or [],
        review_status=review_status,
    )


@pytest.fixture(autouse=True)
def _reset_runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
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


def test_second_bundle_file_failure_keeps_manifest_building_and_hides_old_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old_chunk = _chunk("old_chunk", text="old generation")
    old_asset = _asset("old_asset", source_chunk_ids=[old_chunk.chunk_id])
    write_assets_and_chunks("bundle_book", [old_asset], [old_chunk])
    artifacts = artifact_dir("bundle_book")
    old_assets_mirror = (artifacts / "assets.json").read_bytes()
    old_chunks_mirror = (artifacts / "chunks.jsonl").read_bytes()

    original = artifact_store._write_chunks_file

    def fail_second_file(path: Path, chunks: list[Chunk]) -> None:
        if path.parent.name.endswith(".building"):
            raise OSError("simulated second bundle file failure")
        original(path, chunks)

    monkeypatch.setattr(artifact_store, "_write_chunks_file", fail_second_file)
    with pytest.raises(OSError, match="second bundle file failure"):
        write_assets_and_chunks(
            "bundle_book",
            [_asset("new_asset")],
            [_chunk("new_chunk", text="new generation")],
        )

    manifest = json.loads(
        (artifacts / ".rag_bundles" / "manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["state"] == "building"
    assert read_assets("bundle_book") == []
    assert read_chunks("bundle_book") == []
    assert (artifacts / "assets.json").read_bytes() == old_assets_mirror
    assert (artifacts / "chunks.jsonl").read_bytes() == old_chunks_mirror
    assert not list((artifacts / ".rag_bundles").glob(".*.building"))


def test_late_bundle_build_cannot_publish_over_newer_build() -> None:
    old_chunk = _chunk("old")
    write_assets_and_chunks("bundle_book", [], [old_chunk])
    first = mark_rag_bundle_building("bundle_book")
    second = mark_rag_bundle_building("bundle_book")

    with pytest.raises(RagBundleStaleBuildError):
        write_assets_and_chunks(
            "bundle_book",
            [],
            [_chunk("late")],
            build_id=first.build_id,
        )

    write_assets_and_chunks(
        "bundle_book",
        [],
        [_chunk("current")],
        build_id=second.build_id,
    )
    assert [chunk.chunk_id for chunk in read_chunks("bundle_book")] == ["current"]


def test_rebuilder_chunker_failure_does_not_expose_previous_generation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    old_chunk = _chunk("old")
    old_asset = _asset("mineru_old", source_chunk_ids=["old"])
    write_assets_and_chunks("bundle_book", [old_asset], [old_chunk])
    assert read_chunks("bundle_book")

    def fail_chunker(*args: object, **kwargs: object) -> list[Chunk]:
        del args, kwargs
        raise RuntimeError("simulated chunker failure")

    monkeypatch.setattr("app.document.rebuilder.build_chunks", fail_chunker)
    chapter = Chapter(
        chapter_id="ch1",
        level=1,
        source_title="Chapter",
        ai_title="Chapter",
        page_start=1,
        page_end=1,
        confidence=90,
        status="confirmed",
        source="test",
    )
    with pytest.raises(RuntimeError, match="chunker failure"):
        rebuild_chunks_and_assets(
            "bundle_book",
            tmp_path / "source.png",
            artifact_dir("bundle_book"),
            [chapter],
        )

    assert read_assets("bundle_book") == []
    assert read_chunks("bundle_book") == []


def test_image_source_validation_rejects_duplicates_cross_chapter_and_mismatch() -> None:
    chunks = [_chunk("c1"), _chunk("c2", chapter_id="ch2")]
    write_assets_and_chunks("bundle_book", [], chunks)

    duplicate = ImageGenerationRequest(
        book_id="bundle_book",
        purpose="duplicate",
        source_chunk_ids=["c1", "c1"],
    )
    with pytest.raises(AppError) as duplicate_error:
        source_chunks_for_payload(duplicate)
    assert duplicate_error.value.code == "source_chunks_duplicate"

    cross_chapter = duplicate.model_copy(
        update={"purpose": "cross", "source_chunk_ids": ["c1", "c2"]}
    )
    with pytest.raises(AppError) as cross_error:
        source_chunks_for_payload(cross_chapter)
    assert cross_error.value.code == "source_chunks_cross_chapter"

    mismatch = duplicate.model_copy(
        update={"purpose": "mismatch", "source_chunk_ids": ["c1"], "chapter_id": "ch2"}
    )
    with pytest.raises(AppError) as mismatch_error:
        source_chunks_for_payload(mismatch)
    assert mismatch_error.value.code == "source_chunk_chapter_mismatch"


def test_concurrent_lesson_only_image_generation_merges_assets_and_backlinks() -> None:
    source = _chunk("source")
    write_assets_and_chunks("bundle_book", [], [source])
    payloads = [
        ImageGenerationRequest(
            book_id="bundle_book",
            lesson_id="lesson_without_saved_record",
            purpose=f"diagram {index}",
            source_chunk_ids=["source"],
        )
        for index in range(2)
    ]
    jobs = [image_job_store.create("bundle_book") for _ in payloads]

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [
            executor.submit(run_generation_job, job.job_id, payload)
            for job, payload in zip(jobs, payloads, strict=True)
        ]
        for future in futures:
            future.result(timeout=30)

    results = [image_job_store.get(job.job_id) for job in jobs]
    assert all(result is not None and result.status == "done" for result in results)
    assets = read_assets("bundle_book")
    chunks = read_chunks("bundle_book")
    assert len(assets) == 2
    assert {asset.chapter_id for asset in assets} == {"ch1"}
    assert all(asset.source_chunk_ids == ["source"] for asset in assets)
    assert len(chunks) == 1
    assert set(chunks[0].asset_ids) == {asset.asset_id for asset in assets}


def test_concurrent_source_update_rejects_stale_generated_asset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.image_generation.adapters import MockImageGenerationAdapter

    source = _chunk("source", text="original source generation")
    write_assets_and_chunks("bundle_book", [], [source])
    payload = ImageGenerationRequest(
        book_id="bundle_book",
        lesson_id="lesson_without_saved_record",
        purpose="stale generation guard",
        source_chunk_ids=["source"],
    )
    job = image_job_store.create("bundle_book")
    started = Event()
    release = Event()

    class BlockingAdapter:
        def generate(self, prompt: str, output_path: Path) -> None:
            started.set()
            assert release.wait(timeout=10)
            MockImageGenerationAdapter().generate(prompt, output_path)

    monkeypatch.setattr(
        "app.image_generation.service.get_image_adapter",
        lambda: BlockingAdapter(),
    )
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(run_generation_job, job.job_id, payload)
        assert started.wait(timeout=10)

        def replace_source(
            assets: list[Asset],
            chunks: list[Chunk],
        ) -> tuple[list[Asset], list[Chunk]]:
            return assets, [chunk.model_copy(update={"text": "updated source generation"}) for chunk in chunks]

        update_assets_and_chunks("bundle_book", replace_source)
        release.set()
        future.result(timeout=30)

    result = image_job_store.get(job.job_id)
    assert result is not None and result.status == "failed"
    assert read_assets("bundle_book") == []
    current = read_chunks("bundle_book")
    assert current[0].text == "updated source generation"
    assert current[0].asset_ids == []


def test_related_assets_require_direct_binding_and_hide_pending_ai() -> None:
    chunk = _chunk("source")
    direct = _asset("direct", source_chunk_ids=["source"], source_type="extracted")
    chapter_only = _asset("chapter_only", source_type="extracted")
    pending_ai = _asset(
        "pending_ai",
        source_chunk_ids=["source"],
        source_type="ai_generated",
        review_status="pending",
    )
    ready_ai = _asset(
        "ready_ai",
        source_chunk_ids=["source"],
        source_type="ai_generated",
        review_status="ready",
    )
    write_assets_and_chunks(
        "bundle_book",
        [direct, chapter_only, pending_ai, ready_ai],
        [chunk],
    )
    retrieved = [
        RetrievedChunk(
            chunk=chunk,
            score=1.0,
            bm25_score=1.0,
            dense_score=1.0,
        )
    ]

    assert [asset.asset_id for asset in _related_assets("bundle_book", retrieved)] == [
        "direct",
        "ready_ai",
    ]
