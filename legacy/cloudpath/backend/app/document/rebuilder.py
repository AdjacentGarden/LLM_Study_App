from __future__ import annotations

from pathlib import Path

from app.assets.source_figure_extractor import extract_source_figures
from app.document.chunker import bind_assets_to_chunks, build_chunks
from app.rag.indexing import fail_index_build, publish_index_build, reserve_index_build
from app.schemas.books import Asset, Chapter, Chunk
from app.services.artifact_store import RagBundleBuild, mark_rag_bundle_building, write_assets_and_chunks
from app.services.storage import asset_dir


def _reassign_asset_chapter(asset: Asset, chapters: list[Chapter]) -> Asset:
    if asset.page is None:
        return asset
    candidates = [chapter for chapter in chapters if chapter.page_start <= asset.page <= chapter.page_end]
    chapter = max(candidates, key=lambda item: item.level, default=None)
    return asset.model_copy(update={"chapter_id": chapter.chapter_id if chapter else asset.chapter_id})


def _reconcile_generated_assets(assets: list[Asset], chunks: list[Chunk]) -> list[Asset]:
    """Rebind generated figures to current evidence or mark them orphaned.

    Chapter edits can change stable chunk IDs or remove a chapter entirely.
    Keeping the old chapter ID after its source chunks disappear makes the
    generated image look like valid evidence for a chapter that no longer
    exists. The file and audit record are retained, but the semantic relation
    fails closed until a user generates or binds a new image.
    """

    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    reconciled: list[Asset] = []
    for asset in assets:
        if asset.source_type != "ai_generated":
            reconciled.append(asset)
            continue
        related = [chunks_by_id[chunk_id] for chunk_id in asset.source_chunk_ids if chunk_id in chunks_by_id]
        chapter_ids = {chunk.chapter_id for chunk in related}
        if related and len(chapter_ids) == 1:
            metadata = dict(asset.metadata)
            for key in (
                "orphaned",
                "orphan_reason",
                "previous_chapter_id",
                "previous_source_chunk_ids",
            ):
                metadata.pop(key, None)
            reconciled.append(
                asset.model_copy(
                    update={
                        "chapter_id": next(iter(chapter_ids)),
                        "source_chunk_ids": list(dict.fromkeys(chunk.chunk_id for chunk in related)),
                        "metadata": metadata,
                    }
                )
            )
            continue
        reconciled.append(
            asset.model_copy(
                update={
                    "chapter_id": None,
                    "source_chunk_ids": [],
                    "metadata": {
                        **asset.metadata,
                        "orphaned": True,
                        "orphan_reason": "source_chunks_removed_by_chapter_rebuild",
                        "previous_chapter_id": asset.chapter_id,
                        "previous_source_chunk_ids": list(asset.source_chunk_ids),
                    },
                }
            )
        )
    return reconciled


def rebuild_chunks_and_assets(
    book_id: str,
    file_path: Path,
    artifact_path: Path,
    chapters: list[Chapter],
    *,
    reserved_build: RagBundleBuild | None = None,
) -> tuple[list[Chunk], list[Asset]]:
    bundle_build = reserved_build or mark_rag_bundle_building(book_id)
    if getattr(bundle_build, "book_id", book_id) != book_id:
        raise ValueError("Reserved RAG bundle belongs to a different book")
    index_build = reserve_index_build(book_id, bundle_build.build_id, "chapter_rebuild")
    try:
        # Chapter edits must not discard MinerU or generated assets. Only
        # legacy extracted candidates are regenerated because their
        # chapter/page binding depends on the current structure.
        preserved = [
            _reassign_asset_chapter(asset, chapters)
            for asset in bundle_build.assets
            if asset.source_type != "extracted"
        ]
        has_mineru_assets = any(
            asset.source_type == "mineru" or asset.source_parser == "mineru"
            for asset in preserved
        )
        extracted = (
            extract_source_figures(book_id, file_path, asset_dir(book_id), chapters)
            if file_path.suffix.lower() == ".pdf" and not has_mineru_assets
            else []
        )
        assets = list({asset.asset_id: asset for asset in preserved + extracted}.values())
        source_assets = [asset for asset in assets if asset.source_type != "ai_generated"]
        chunks = build_chunks(book_id, artifact_path, chapters, assets=source_assets)
        assets = _reconcile_generated_assets(assets, chunks)
        chunks = bind_assets_to_chunks(chunks, assets)
        write_assets_and_chunks(
            book_id,
            assets,
            chunks,
            build_id=bundle_build.build_id,
        )
        publish_index_build(index_build, chunks)
        return chunks, assets
    except Exception as exc:
        fail_index_build(index_build, exc)
        raise
