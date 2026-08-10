from __future__ import annotations

import math

import pytest

from app.rag.index_pgvector import (
    DEFAULT_EMBEDDING_DIMENSION,
    InvalidIndexPayloadError,
    PGVECTOR_SCHEMA_SQL,
    PgVectorIndex,
)
from app.schemas.books import Chunk


def _chunk(chunk_id: str, *, indexable: bool = True) -> Chunk:
    quality = 0.9 if indexable else 0.1
    return Chunk(
        chunk_id=chunk_id,
        book_id="book",
        chapter_id="chapter",
        page_start=1,
        page_end=1,
        content_type="text",
        text="Cell membrane transport",
        asset_ids=["asset"],
        key_concepts=["membrane"],
        parser="mineru",
        parser_version="3.4.4",
        chunk_version="v2",
        heading_path=["Biology", "Cell"],
        source_block_ids=["block"],
        quality_score=quality,
        token_count=12,
        content_hash="hash",
        bbox=[1.0, 2.0, 3.0, 4.0],
        metadata={
            "quality_version": "quality-v1",
            "quality_threshold": 0.45,
            "indexable": indexable,
            "quarantined": not indexable,
            "index_status": "quarantined" if not indexable else "ready",
            "nested": {"values": [1, "two"]},
        },
    )


def test_standalone_migration_is_idempotent_and_generation_aware() -> None:
    lowered = PGVECTOR_SCHEMA_SQL.casefold()
    assert "create extension if not exists vector" in lowered
    assert "create table if not exists rag_index_state" in lowered
    assert "create table if not exists rag_chunk_vectors" in lowered
    assert "index_generation" in lowered
    assert "committed" in lowered
    assert "cache_failed" in lowered
    assert "vector(1024)" in lowered
    assert "create index if not exists" in lowered


def test_payload_validation_requires_pairing_v2_and_exact_dimensions() -> None:
    index = PgVectorIndex(None)
    vector = [0.0] * DEFAULT_EMBEDDING_DIMENSION

    pairs = index._validate_payload("book", [_chunk("good"), _chunk("quarantine", indexable=False)], [vector, vector])
    assert [chunk.chunk_id for chunk, _ in pairs] == ["good"]

    with pytest.raises(InvalidIndexPayloadError, match="length mismatch"):
        index._validate_payload("book", [_chunk("good")], [])
    with pytest.raises(InvalidIndexPayloadError, match="dimension"):
        index._validate_payload("book", [_chunk("good")], [[0.0] * 1023])
    with pytest.raises(InvalidIndexPayloadError, match="finite"):
        index._validate_payload(
            "book",
            [_chunk("good")],
            [[math.nan, *([0.0] * (DEFAULT_EMBEDDING_DIMENSION - 1))]],
        )


def test_payload_validation_rejects_cross_book_legacy_and_duplicates() -> None:
    index = PgVectorIndex(None)
    vector = [0.0] * DEFAULT_EMBEDDING_DIMENSION
    cross_book = _chunk("cross")
    cross_book.book_id = "other"
    legacy = _chunk("legacy")
    legacy.chunk_version = "v1"

    with pytest.raises(InvalidIndexPayloadError, match="belongs to"):
        index._validate_payload("book", [cross_book], [vector])
    with pytest.raises(InvalidIndexPayloadError, match="version"):
        index._validate_payload("book", [legacy], [vector])
    with pytest.raises(InvalidIndexPayloadError, match="duplicate chunk_id"):
        index._validate_payload("book", [_chunk("same"), _chunk("same")], [vector, vector])


def test_unavailable_compatibility_wrapper_is_non_mutating() -> None:
    index = PgVectorIndex(None)
    assert index.available is False
    assert index.upsert_chunks("book", [_chunk("chunk")], [[0.0] * DEFAULT_EMBEDDING_DIMENSION]) is None
    assert index.delete_book("book") is None

