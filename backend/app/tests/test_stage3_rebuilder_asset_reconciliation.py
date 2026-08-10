from __future__ import annotations

from app.document.chunker import bind_assets_to_chunks
from app.document.rebuilder import _reconcile_generated_assets
from app.schemas.books import Asset, Chunk


def _chunk(chunk_id: str, chapter_id: str) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id="rebuild_book",
        chapter_id=chapter_id,
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text=f"current evidence {chunk_id}",
        chunk_version="v2",
        quality_score=0.95,
        metadata={"indexable": True, "quality_threshold": 0.45},
    )


def _generated(asset_id: str, chapter_id: str, source_ids: list[str]) -> Asset:
    return Asset(
        asset_id=asset_id,
        book_id="rebuild_book",
        chapter_id=chapter_id,
        source_type="ai_generated",
        type="diagram",
        caption=f"generated {asset_id}",
        image_url=f"/{asset_id}.png",
        thumbnail_url=f"/thumb_{asset_id}.png",
        source_chunk_ids=source_ids,
        review_status="pending",
    )


def test_generated_asset_rebinds_to_current_source_and_backlink() -> None:
    current = _chunk("stable_source", "current_chapter")
    asset = _generated("generated_valid", "old_chapter", [current.chunk_id])

    reconciled = _reconcile_generated_assets([asset], [current])
    chunks = bind_assets_to_chunks([current], reconciled)

    assert reconciled[0].chapter_id == "current_chapter"
    assert reconciled[0].source_chunk_ids == [current.chunk_id]
    assert reconciled[0].metadata.get("orphaned") is None
    assert chunks[0].asset_ids == [asset.asset_id]


def test_generated_asset_with_removed_sources_loses_deleted_chapter_reference() -> None:
    current = _chunk("new_source", "new_chapter")
    asset = _generated("generated_orphan", "deleted_chapter", ["removed_source"])

    reconciled = _reconcile_generated_assets([asset], [current])
    chunks = bind_assets_to_chunks([current], reconciled)
    orphan = reconciled[0]

    assert orphan.chapter_id is None
    assert orphan.source_chunk_ids == []
    assert orphan.review_status == "pending"
    assert orphan.metadata["orphaned"] is True
    assert orphan.metadata["previous_chapter_id"] == "deleted_chapter"
    assert orphan.metadata["previous_source_chunk_ids"] == ["removed_source"]
    assert chunks[0].asset_ids == []

