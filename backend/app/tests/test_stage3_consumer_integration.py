from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.config import get_settings
from app.document.chunker import bind_assets_to_chunks, build_chunks
from app.document.chunk_protocol import is_chunk_indexable
from app.document.rebuilder import rebuild_chunks_and_assets
from app.lessons.source_builder import build_chapter_source_package
from app.rag.bm25 import BM25Index
from app.rag.cache import clear_rag_cache
from app.rag.embedding import render_chunk_embedding_text
from app.rag.index_base import ArtifactVectorIndex
from app.rag.retrieval import retrieve_chunks
from app.schemas.books import Asset, Chapter, Chunk
from app.services import artifact_store
from app.services.artifact_store import write_chapters, write_chunks
from app.services.storage import artifact_dir


def _chapter(book_id: str = "stage3_book") -> Chapter:
    del book_id
    return Chapter(
        chapter_id="ch_001",
        level=1,
        source_title="Cell Biology",
        ai_title="Cell Biology",
        page_start=1,
        page_end=2,
        confidence=95,
        status="confirmed",
        source="test",
    )


def _chunk(chunk_id: str, text: str, **updates: object) -> Chunk:
    values: dict[str, object] = {
        "chunk_id": chunk_id,
        "book_id": "stage3_book",
        "chapter_id": "ch_001",
        "page_start": 1,
        "page_end": 1,
        "content_type": "paragraph",
        "text": text,
    }
    values.update(updates)
    return Chunk(**values)


class _CapturingEmbedding:
    name = "capture"
    dimensions = 2

    def __init__(self) -> None:
        self.documents: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.documents.extend(texts)
        return [[1.0, 0.0] for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        del text
        return [1.0, 0.0]


def test_rag_and_lesson_consumers_share_v2_render_and_keep_legacy_compatible(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "artifact")
    get_settings.cache_clear()
    clear_rag_cache()

    legacy = _chunk("legacy", "legacy compatibility phrase", page_start=2, page_end=2)
    v2 = _chunk(
        "v2_good",
        "membrane transport body",
        chunk_version="v2",
        heading_path=["Unit One", "Cell Transport"],
        quality_score=0.95,
        metadata={"indexable": True, "overlap_text": "prior context"},
    )
    excluded = _chunk(
        "v2_excluded",
        "secret quarantined phrase",
        chunk_version="v2",
        quality_score=0.99,
        metadata={"indexable": False, "quarantined": True},
    )

    assert is_chunk_indexable(legacy)
    assert is_chunk_indexable(v2)
    assert not is_chunk_indexable(excluded)
    assert render_chunk_embedding_text(v2) == (
        "Unit One > Cell Transport\n\nprior context\n\nmembrane transport body"
    )

    bm25 = BM25Index([legacy, v2, excluded])
    assert {item.chunk.chunk_id for item in bm25.search("Cell Transport")} == {"v2_good"}
    assert {item.chunk.chunk_id for item in bm25.search("legacy compatibility")} == {"legacy"}
    assert bm25.search("secret quarantined") == []

    write_chapters("stage3_book", [_chapter()])
    write_chunks("stage3_book", [legacy, v2, excluded])
    capture = _CapturingEmbedding()
    vector_results = ArtifactVectorIndex(capture).search_vector("stage3_book", [1.0, 0.0])
    assert {item.chunk.chunk_id for item in vector_results} == {"legacy", "v2_good"}
    assert capture.documents == [
        "legacy compatibility phrase",
        "Unit One > Cell Transport\n\nprior context\n\nmembrane transport body",
    ]

    retrieved = retrieve_chunks("stage3_book", "Cell Transport")
    assert retrieved and retrieved[0].chunk.chunk_id == "v2_good"
    assert all(item.chunk.chunk_id != "v2_excluded" for item in retrieved)

    package = build_chapter_source_package("stage3_book", "ch_001", max_window_chars=1000)
    assert "Unit One > Cell Transport" in package.windows[0].text
    assert "secret quarantined phrase" not in package.windows[0].text
    assert any(warning == "skipped_non_indexable_chunks:1" for warning in package.warnings)
    clear_rag_cache()
    get_settings.cache_clear()


def test_write_chunks_is_atomic_when_publish_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    get_settings.cache_clear()
    clear_rag_cache()
    write_chunks("atomic_book", [_chunk("before", "old complete index")])
    path = artifact_dir("atomic_book") / "chunks.jsonl"
    before = path.read_bytes()

    def fail_replace(source: object, destination: object) -> None:
        del source, destination
        raise OSError("simulated publish failure")

    monkeypatch.setattr(artifact_store.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated publish failure"):
        write_chunks("atomic_book", [_chunk("after", "new partial index")])

    assert path.read_bytes() == before
    assert list(path.parent.glob(".chunks.jsonl.*.tmp")) == []
    clear_rag_cache()
    get_settings.cache_clear()


def test_chunk_dispatch_defaults_to_v2_and_v1_remains_available(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pages = [
        {
            "page": 1,
            "parser": "mineru",
            "quality_score": 0.95,
            "blocks": [
                {"block_id": "h1", "type": "title", "text": "Cell Biology", "heading_level": 1},
                {
                    "block_id": "b1",
                    "type": "paragraph",
                    "text": "Cells exchange material through membrane transport systems.",
                    "source_parser": "mineru",
                    "confidence": 0.95,
                },
            ],
        }
    ]
    (tmp_path / "pages.json").write_text(json.dumps(pages), encoding="utf-8")
    monkeypatch.setattr(
        "app.document.chunker.get_settings",
        lambda: SimpleNamespace(chunk_version="v2"),
    )
    token_counter = lambda text: len(text.split())

    v2_chunks = build_chunks(
        "stage3_book",
        tmp_path,
        [_chapter()],
        token_counter=token_counter,
    )
    v1_chunks = build_chunks("stage3_book", tmp_path, [_chapter()], version="v1")

    assert v2_chunks and all(chunk.chunk_version == "v2" for chunk in v2_chunks)
    assert all(chunk.token_count is not None for chunk in v2_chunks)
    assert v1_chunks and all(chunk.chunk_version is None for chunk in v1_chunks)
    with pytest.raises(ValueError, match="Unsupported chunk version"):
        build_chunks("stage3_book", tmp_path, [_chapter()], version="2")
    with pytest.raises(ValueError, match="Unsupported chunk version"):
        build_chunks("stage3_book", tmp_path, [_chapter()], version="legacy")


def test_v2_asset_binding_is_exact_and_idempotent() -> None:
    exact = _chunk(
        "figure_chunk",
        "A labeled cell diagram",
        content_type="figure",
        chunk_version="v2",
        quality_score=0.9,
        asset_ids=["asset_exact"],
    )
    unrelated = _chunk(
        "body_chunk",
        "Nearby body text",
        chunk_version="v2",
        quality_score=0.9,
    )
    exact_asset = Asset(
        asset_id="asset_exact",
        book_id="stage3_book",
        chapter_id="ch_001",
        source_type="mineru",
        source_parser="mineru",
        page=1,
        type="figure",
        caption="A labeled cell diagram",
        image_url="/asset/exact",
        thumbnail_url="/asset/exact/thumb",
    )
    unbound_asset = exact_asset.model_copy(
        update={"asset_id": "asset_unbound", "caption": "Unbound visual"}
    )

    first = bind_assets_to_chunks([exact, unrelated], [exact_asset, unbound_asset])
    second = bind_assets_to_chunks(first, [exact_asset, unbound_asset])

    assert [chunk.chunk_id for chunk in second] == ["figure_chunk", "body_chunk"]
    assert exact_asset.source_chunk_ids == ["figure_chunk"]
    assert unbound_asset.source_chunk_ids == []
    assert unrelated.asset_ids == []

    legacy = _chunk("legacy_body", "Legacy page body")
    legacy_asset = exact_asset.model_copy(
        update={
            "asset_id": "legacy_asset",
            "source_type": "extracted",
            "caption": "Legacy figure caption",
            "source_chunk_ids": [],
        }
    )
    legacy_bound = bind_assets_to_chunks([legacy], [legacy_asset])
    assert legacy.asset_ids == ["legacy_asset"]
    assert legacy_asset.source_chunk_ids[0] == "legacy_body"
    assert any(chunk.content_type == "figure" for chunk in legacy_bound)


def test_rebuilder_preserves_mineru_and_generated_assets_without_reextracting(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    mineru = Asset(
        asset_id="mineru_asset",
        book_id="stage3_book",
        chapter_id="old",
        source_type="mineru",
        source_parser="mineru",
        page=1,
        type="table",
        caption="MinerU table",
        image_url="/asset/mineru",
        thumbnail_url="/asset/mineru/thumb",
    )
    generated = Asset(
        asset_id="generated_asset",
        book_id="stage3_book",
        chapter_id="ch_001",
        source_type="ai_generated",
        page=None,
        type="diagram",
        caption="Generated diagram",
        image_url="/asset/generated",
        thumbnail_url="/asset/generated/thumb",
        source_chunk_ids=["rebuilt"],
    )
    captured: dict[str, object] = {}
    rebuilt = _chunk("rebuilt", "Rebuilt body", chunk_version="v2", quality_score=0.9)

    monkeypatch.setattr(
        "app.document.rebuilder.mark_rag_bundle_building",
        lambda _: SimpleNamespace(assets=[mineru, generated], build_id="build_test"),
    )
    monkeypatch.setattr(
        "app.document.rebuilder.extract_source_figures",
        lambda *args, **kwargs: pytest.fail("MinerU assets must suppress legacy extraction"),
    )

    def fake_build(*args: object, **kwargs: object) -> list[Chunk]:
        del args
        captured["chunk_assets"] = kwargs["assets"]
        return [rebuilt]

    monkeypatch.setattr("app.document.rebuilder.build_chunks", fake_build)
    monkeypatch.setattr("app.document.rebuilder.bind_assets_to_chunks", lambda chunks, assets: chunks)
    index_reservation = object()
    monkeypatch.setattr(
        "app.document.rebuilder.reserve_index_build",
        lambda book_id, build_id, cause: index_reservation,
    )
    monkeypatch.setattr(
        "app.document.rebuilder.publish_index_build",
        lambda reservation, chunks: captured.update(
            {"index_reservation": reservation, "indexed_chunks": chunks}
        ),
    )
    monkeypatch.setattr(
        "app.document.rebuilder.fail_index_build",
        lambda *_args, **_kwargs: pytest.fail("successful rebuild must not fail its index reservation"),
    )

    def fake_publish(book_id: str, assets: list[Asset], chunks: list[Chunk], **kwargs: object) -> None:
        captured.update(
            {
                "written_assets": assets,
                "written_chunks": chunks,
                "build_id": kwargs.get("build_id"),
            }
        )

    monkeypatch.setattr("app.document.rebuilder.write_assets_and_chunks", fake_publish)

    chunks, assets = rebuild_chunks_and_assets(
        "stage3_book",
        tmp_path / "source.pdf",
        tmp_path,
        [_chapter()],
    )

    assert chunks == [rebuilt]
    assert {asset.asset_id for asset in assets} == {"mineru_asset", "generated_asset"}
    assert [asset.asset_id for asset in captured["chunk_assets"]] == ["mineru_asset"]
    assert mineru.chapter_id == "old"
    written = captured["written_assets"]
    assert isinstance(written, list)
    assert next(asset for asset in written if asset.asset_id == "mineru_asset").chapter_id == "ch_001"
    assert captured["build_id"] == "build_test"
    assert captured["index_reservation"] is index_reservation
    assert captured["indexed_chunks"] == [rebuilt]
