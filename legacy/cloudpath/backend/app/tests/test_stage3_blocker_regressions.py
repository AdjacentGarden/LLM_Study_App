from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.document import chunker as chunk_dispatcher
from app.document.chunk_protocol import FrozenChunkConfig
from app.rag.index_base import VectorSearchResult
from app.rag.retrieval import HybridRetriever
from app.schemas.books import Chapter, Chunk


def _chunk(chunk_id: str, text: str, *, indexable: bool = True) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id="book",
        chapter_id="chapter",
        page_start=1,
        page_end=1,
        content_type="text",
        text=text,
        chunk_version="v2.1",
        heading_path=["Current heading"],
        quality_score=0.9,
        metadata={
            "indexable": indexable,
            "quarantined": not indexable,
            "quality_threshold": 0.45,
        },
    )


def test_external_dense_scores_are_intersected_with_active_ids_and_use_canonical_chunk() -> None:
    active = _chunk("active", "current canonical body")
    quarantined = _chunk("quarantined", "must stay excluded", indexable=False)
    reduced_database_copy = Chunk(
        chunk_id="active",
        book_id="book",
        chapter_id="chapter",
        page_start=99,
        page_end=99,
        content_type="text",
        text="stale database body without V2 metadata",
    )
    stale_dense_only = Chunk(
        chunk_id="deleted_generation",
        book_id="book",
        chapter_id="chapter",
        page_start=9,
        page_end=9,
        content_type="text",
        text="deleted generation",
    )
    reduced_quarantine_copy = Chunk(
        chunk_id="quarantined",
        book_id="book",
        chapter_id="chapter",
        page_start=1,
        page_end=1,
        content_type="text",
        text="database copy that would look legacy-indexable",
    )

    fused = HybridRetriever()._fuse(
        [],
        [
            VectorSearchResult(chunk=reduced_database_copy, dense_score=0.91, rank=1),
            VectorSearchResult(chunk=stale_dense_only, dense_score=0.99, rank=2),
            VectorSearchResult(chunk=reduced_quarantine_copy, dense_score=0.98, rank=3),
        ],
        [active, quarantined],
        "pgvector",
    )

    assert [item.chunk.chunk_id for item in fused] == ["active"]
    assert fused[0].chunk is active
    assert fused[0].chunk.text == "current canonical body"
    assert fused[0].chunk.page_start == 1
    assert fused[0].dense_score == pytest.approx(0.91)


def _write_source(path: Path) -> tuple[Path, list[Chapter]]:
    path.mkdir(parents=True, exist_ok=True)
    (path / "pages.json").write_text(
        json.dumps(
            [
                {
                    "page": 1,
                    "parser": "mineru",
                    "quality_score": 1.0,
                    "blocks": [
                        {
                            "block_id": "block",
                            "type": "paragraph",
                            "text": "Versioned semantic content remains stable and searchable.",
                            "source_parser": "mineru",
                        }
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )
    chapter = Chapter(
        chapter_id="chapter",
        level=1,
        source_title="Chapter",
        ai_title="Chapter",
        page_start=1,
        page_end=1,
        confidence=100,
        status="confirmed",
        source="test",
    )
    return path, [chapter]


def _count_words(text: str) -> int:
    return len(text.split())


def test_explicit_v2_subversion_is_persisted_in_chunks_and_stable_ids(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_path, chapters = _write_source(tmp_path)
    monkeypatch.setattr(
        chunk_dispatcher,
        "get_settings",
        lambda: SimpleNamespace(chunk_version="v2"),
    )

    chunks = chunk_dispatcher.build_chunks(
        "book",
        artifact_path,
        chapters,
        version=" V2.1 ",
        token_counter=_count_words,
    )

    assert chunks
    assert {chunk.chunk_version for chunk in chunks} == {"v2.1"}
    assert all("_v2.1_" in chunk.chunk_id for chunk in chunks)


@pytest.mark.parametrize(
    "config, expected",
    [
        ({"chunk_version": "v2.2"}, "v2.2"),
        ({"version": "V2.3"}, "v2.3"),
        (SimpleNamespace(version="v2.4"), "v2.4"),
        (FrozenChunkConfig(chunk_version="v2.5"), "v2.5"),
    ],
)
def test_config_version_overrides_v1_settings_and_selects_v2(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config: object,
    expected: str,
) -> None:
    artifact_path, chapters = _write_source(tmp_path)
    monkeypatch.setattr(
        chunk_dispatcher,
        "get_settings",
        lambda: SimpleNamespace(chunk_version="v1"),
    )

    chunks = chunk_dispatcher.build_chunks(
        "book",
        artifact_path,
        chapters,
        config=config,
        token_counter=_count_words,
    )

    assert chunks
    assert {chunk.chunk_version for chunk in chunks} == {expected}


def test_explicit_and_config_version_conflict_fails_before_chunking(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_path, chapters = _write_source(tmp_path)
    monkeypatch.setattr(
        chunk_dispatcher,
        "get_settings",
        lambda: SimpleNamespace(chunk_version="v2"),
    )

    with pytest.raises(ValueError, match="Conflicting chunk versions"):
        chunk_dispatcher.build_chunks(
            "book",
            artifact_path,
            chapters,
            version="v2.1",
            config={"chunk_version": "v2.2"},
            token_counter=_count_words,
        )


def test_explicit_version_can_combine_with_versionless_mapping_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    artifact_path, chapters = _write_source(tmp_path)
    monkeypatch.setattr(
        chunk_dispatcher,
        "get_settings",
        lambda: SimpleNamespace(chunk_version="v1"),
    )

    chunks = chunk_dispatcher.build_chunks(
        "book",
        artifact_path,
        chapters,
        version="v2.6",
        config={
            "target_tokens": 20,
            "max_tokens": 30,
            "min_tokens": 5,
            "overlap_tokens": 2,
            "atomic_hard_max_tokens": 40,
            "quality_threshold": 0.5,
        },
        token_counter=_count_words,
    )

    assert chunks and chunks[0].chunk_version == "v2.6"
    assert chunks[0].metadata["quality_threshold"] == 0.5

