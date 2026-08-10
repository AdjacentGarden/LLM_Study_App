from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
import json
import hashlib
from typing import Any

from app.core.config import get_settings
from app.document.chunk_protocol import FrozenChunkConfig
from app.schemas.books import Asset, Chapter, Chunk


def _stable_chunk_id(book_id: str, chapter_id: str, source: str) -> str:
    digest = hashlib.sha1(source.encode("utf-8")).hexdigest()[:12]
    return f"{book_id}_{chapter_id}_{digest}"


def build_chunks_v1(book_id: str, artifact_path: Path, chapters: list[Chapter]) -> list[Chunk]:
    """Legacy block-per-chunk implementation retained for explicit rollback."""

    pages_path = artifact_path / "pages.json"
    pages = json.loads(pages_path.read_text(encoding="utf-8")) if pages_path.exists() else []
    chunks: list[Chunk] = []

    for chapter in chapters:
        chapter_pages = [page for page in pages if chapter.page_start <= int(page["page"]) <= chapter.page_end]
        if not chapter_pages:
            continue

        for page in chapter_pages:
            if page.get("needs_ocr") or not page.get("blocks"):
                continue
            for block in page.get("blocks", []):
                text = block.get("text", "").strip()
                if not text or block.get("type") == "ocr_pending" or block.get("source_parser") == "mock":
                    continue
                block_id = str(block.get("block_id") or f"page_{page['page']}_block")
                chunks.append(
                    Chunk(
                        chunk_id=_stable_chunk_id(book_id, chapter.chapter_id, f"{chapter.chapter_id}:{page['page']}:{block_id}"),
                        book_id=book_id,
                        chapter_id=chapter.chapter_id,
                        page_start=int(page["page"]),
                        page_end=int(page["page"]),
                        content_type=str(block.get("type") or "text"),
                        text=text,
                        key_concepts=[],
                    )
                )
    return chunks


def build_chunks(
    book_id: str,
    artifact_path: Path,
    chapters: list[Chapter],
    assets: list[Asset] | None = None,
    *,
    version: str | None = None,
    config: Any | None = None,
    token_counter: Any | None = None,
) -> list[Chunk]:
    """Dispatch to Chunk V2 by default while keeping V1 explicitly selectable."""

    settings = get_settings()
    explicit_version = _normalized_version(version) if version is not None else None
    config_version = _version_from_config(config)
    if explicit_version is not None and config_version is not None and explicit_version != config_version:
        raise ValueError(
            f"Conflicting chunk versions: version={version!r}, config={config_version!r}"
        )
    if explicit_version is not None:
        selected = explicit_version
    elif config_version is not None:
        selected = config_version
    else:
        selected = _normalized_version(settings.chunk_version)
    if selected == "v1":
        return build_chunks_v1(book_id, artifact_path, chapters)
    if not selected.startswith("v2"):
        raise ValueError(f"Unsupported chunk version: {selected}")

    from app.document.chunker_v2 import build_chunks_v2

    resolved_config = _resolve_v2_config(config, selected=selected, settings=settings)
    return build_chunks_v2(
        book_id,
        artifact_path,
        chapters,
        assets=assets,
        config=resolved_config,
        token_counter=token_counter,
    )


def _normalized_version(value: object) -> str:
    return str(value).strip().lower()


def _version_from_config(config: Any | None) -> str | None:
    if config is None:
        return None
    if isinstance(config, Mapping):
        if "chunk_version" in config:
            return _normalized_version(config["chunk_version"])
        if "version" in config:
            return _normalized_version(config["version"])
        return None
    raw = getattr(config, "chunk_version", None)
    if raw is None:
        raw = getattr(config, "version", None)
    return _normalized_version(raw) if raw is not None else None


def _settings_chunk_config(settings: object) -> FrozenChunkConfig:
    required = (
        "chunk_target_tokens",
        "chunk_max_tokens",
        "chunk_min_tokens",
        "chunk_overlap_tokens",
        "chunk_atomic_content_hard_max_tokens",
        "chunk_quality_threshold",
        "chunk_version",
    )
    if all(hasattr(settings, name) for name in required):
        return FrozenChunkConfig.from_settings(settings)  # type: ignore[arg-type]
    # Lightweight test settings and older integrations may expose only the
    # dispatcher version. Keep the frozen Stage 0 numeric defaults in that
    # compatibility case.
    return FrozenChunkConfig()


def _resolve_v2_config(
    config: Any | None,
    *,
    selected: str,
    settings: object,
) -> FrozenChunkConfig:
    base = config if isinstance(config, FrozenChunkConfig) else _settings_chunk_config(settings)
    values: dict[str, object] = {
        "target_tokens": base.target_tokens,
        "max_tokens": base.max_tokens,
        "min_tokens": base.min_tokens,
        "overlap_tokens": base.overlap_tokens,
        "atomic_content_hard_max_tokens": base.atomic_content_hard_max_tokens,
        "quality_threshold": base.quality_threshold,
        "chunk_version": selected,
    }
    aliases = {
        "target_tokens": "target_tokens",
        "max_tokens": "max_tokens",
        "min_tokens": "min_tokens",
        "overlap_tokens": "overlap_tokens",
        "atomic_content_hard_max_tokens": "atomic_content_hard_max_tokens",
        "atomic_hard_max_tokens": "atomic_content_hard_max_tokens",
        "quality_threshold": "quality_threshold",
    }
    if isinstance(config, Mapping):
        for source, destination in aliases.items():
            if source in config:
                values[destination] = config[source]
    elif config is not None and not isinstance(config, FrozenChunkConfig):
        for source, destination in aliases.items():
            if hasattr(config, source):
                values[destination] = getattr(config, source)
    if isinstance(config, FrozenChunkConfig):
        # dataclasses.replace preserves the protocol's frozen validation while
        # ensuring an explicit dispatcher sub-version reaches stable IDs.
        return replace(config, chunk_version=selected)
    return FrozenChunkConfig(**values)  # type: ignore[arg-type]


def bind_assets_to_chunks(chunks: list[Chunk], assets: list[Asset]) -> list[Chunk]:
    chunks_by_chapter: dict[str, list[Chunk]] = {}
    for chunk in chunks:
        chunks_by_chapter.setdefault(chunk.chapter_id, []).append(chunk)

    figure_chunks: list[Chunk] = []
    for asset in assets:
        if not asset.chapter_id:
            continue
        chapter_chunks = chunks_by_chapter.get(asset.chapter_id, [])
        v2_mode = any(
            str(chunk.chunk_version or "").lower().startswith("v2")
            for chunk in chapter_chunks
        ) or (
            not chapter_chunks
            and str(get_settings().chunk_version).lower().startswith("v2")
        )
        valid_source_ids = {
            chunk_id
            for chunk_id in asset.source_chunk_ids
            if any(chunk.chunk_id == chunk_id for chunk in chapter_chunks)
        }
        explicitly_related = [
            chunk
            for chunk in chapter_chunks
            if asset.asset_id in chunk.asset_ids or chunk.chunk_id in valid_source_ids
        ]
        page_related = (
            [
                chunk
                for chunk in chapter_chunks
                if chunk.page_start <= asset.page <= chunk.page_end
            ]
            if not v2_mode and asset.page is not None
            else []
        )
        related = explicitly_related or page_related
        if not v2_mode and not related and chapter_chunks and asset.page is not None:
            related = [chapter_chunks[0]]
        asset.source_chunk_ids = [chunk.chunk_id for chunk in related]
        for chunk in related:
            if asset.asset_id not in chunk.asset_ids:
                chunk.asset_ids.append(asset.asset_id)
        # Asset paths alone are not semantic evidence.  Keep the Asset and its
        # relation to a nearby text chunk, but do not create an empty figure
        # chunk that could enter BM25/vector retrieval.
        has_semantic_asset_chunk = any(
            asset.asset_id in chunk.asset_ids
            and chunk.content_type in {"figure", "image", "chart"}
            for chunk in chapter_chunks
        )
        if (
            not v2_mode
            and asset.source_type != "ai_generated"
            and asset.caption.strip()
            and not has_semantic_asset_chunk
        ):
            figure_chunk = Chunk(
                chunk_id=_stable_chunk_id(asset.book_id, asset.chapter_id, f"asset:{asset.asset_id}"),
                book_id=asset.book_id,
                chapter_id=asset.chapter_id,
                page_start=asset.page or 1,
                page_end=asset.page or 1,
                content_type="figure",
                text=asset.caption,
                asset_ids=[asset.asset_id],
                key_concepts=asset.concepts,
            )
            figure_chunks.append(figure_chunk)
            if figure_chunk.chunk_id not in asset.source_chunk_ids:
                asset.source_chunk_ids.append(figure_chunk.chunk_id)

    unique: dict[str, Chunk] = {}
    for chunk in chunks + figure_chunks:
        unique.setdefault(chunk.chunk_id, chunk)
    return list(unique.values())
