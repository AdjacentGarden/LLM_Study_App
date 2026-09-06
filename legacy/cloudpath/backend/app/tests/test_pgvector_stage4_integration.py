from __future__ import annotations

import os
from uuid import uuid4

import pytest

from app.rag.index_pgvector import (
    DEFAULT_EMBEDDING_DIMENSION,
    InvalidIndexPayloadError,
    PgVectorIndex,
    StaleGenerationError,
)
from app.schemas.books import Chunk


APPROVED_DSN = "postgresql://cloudpath_stage4@127.0.0.1:55432/cloudpath_stage4"


def _vector(axis: int) -> list[float]:
    result = [0.0] * DEFAULT_EMBEDDING_DIMENSION
    result[axis] = 1.0
    return result


def _chunk(
    book_id: str,
    chunk_id: str,
    text: str,
    *,
    indexable: bool = True,
    chapter_id: str = "chapter",
) -> Chunk:
    quality = 0.91 if indexable else 0.2
    return Chunk(
        chunk_id=chunk_id,
        book_id=book_id,
        chapter_id=chapter_id,
        page_start=2,
        page_end=3,
        content_type="table",
        text=text,
        asset_ids=["asset-one"],
        key_concepts=["cell cycle", "G2"],
        parser="mineru",
        parser_version="3.4.4",
        chunk_version="v2",
        heading_path=["Biology", "Cell cycle"],
        source_block_ids=["p002_table_1"],
        quality_score=quality,
        token_count=37,
        content_hash=f"hash-{chunk_id}",
        bbox=[10.0, 20.0, 30.0, 40.0],
        metadata={
            "quality_version": "quality-v1",
            "quality_formula_version": "quality-v1",
            "quality_threshold": 0.45,
            "indexable": indexable,
            "quarantined": not indexable,
            "index_status": "quarantined" if not indexable else "ready",
            "table_rows": [["Stage", "State"], ["G2", "replicated"]],
            "nested": {"unicode": "缁嗚優", "values": [1, True, None]},
        },
    )


@pytest.fixture(scope="module")
def pg_index() -> PgVectorIndex:
    dsn = os.environ.get("CLOUDPATH_STAGE4_TEST_DSN")
    if dsn != APPROVED_DSN:
        pytest.skip("real pgvector tests require the explicitly isolated Stage 4 DSN")
    index = PgVectorIndex(dsn)
    with index._connect() as conn:
        identity = conn.execute(
            """
            select current_database(), current_user, inet_server_port(),
                   current_setting('server_version'),
                   (select extversion from pg_extension where extname = 'vector')
            """
        ).fetchone()
    assert identity == ("cloudpath_stage4", "cloudpath_stage4", 55432, "16.14", "0.8.3")
    index.apply_migration()
    index.apply_migration()
    return index


@pytest.fixture
def book_id(pg_index: PgVectorIndex) -> str:
    value = f"stage4_it_{uuid4().hex}"
    yield value
    with pg_index._connect() as conn:
        conn.execute("delete from rag_chunk_vectors where book_id = %s", (value,))
        conn.execute("delete from rag_index_state where book_id = %s", (value,))


def _publish(pg_index: PgVectorIndex, book_id: str, chunk: Chunk, embedding: list[float]):
    reservation = pg_index.reserve_generation(book_id)
    committed = pg_index.replace_generation(
        book_id,
        reservation.generation,
        [chunk],
        [embedding],
        build_id=reservation.build_id,
    )
    assert committed.state == "committed"
    return pg_index.finalize_ready(book_id, reservation.generation, build_id=reservation.build_id)


def test_committed_is_hidden_ready_roundtrips_complete_v2_metadata_and_filters_quarantine(
    pg_index: PgVectorIndex,
    book_id: str,
) -> None:
    good = _chunk(book_id, "good", "G2 replicated")
    quarantined = _chunk(book_id, "quarantined", "secret", indexable=False)
    embeddings = [_vector(0), _vector(1)]

    committed = pg_index.upsert_chunks(book_id, [good, quarantined], embeddings)
    assert committed is not None
    assert committed.state == "committed"
    assert committed.chunk_count == 1
    assert pg_index.search_vector(book_id, embeddings[0]) == []

    ready = pg_index.mark_ready(book_id, committed.generation, build_id=committed.build_id)
    assert ready.state == "ready"
    found = pg_index.search_vector(book_id, embeddings[0])
    assert [item.chunk.chunk_id for item in found] == ["good"]
    restored = found[0].chunk
    assert restored.model_dump() == good.model_dump()
    assert restored.metadata["table_rows"] == [["Stage", "State"], ["G2", "replicated"]]
    assert PgVectorIndex(APPROVED_DSN, embedding_revision="other").search_vector(book_id, embeddings[0]) == []


def test_reparse_atomically_removes_old_ids_and_committed_remains_hidden(
    pg_index: PgVectorIndex,
    book_id: str,
) -> None:
    old = _chunk(book_id, "old-id", "old material")
    first = _publish(pg_index, book_id, old, _vector(0))
    assert [item.chunk.chunk_id for item in pg_index.search_vector(book_id, _vector(0))] == ["old-id"]

    new = _chunk(book_id, "new-id", "new material")
    reservation = pg_index.reserve_generation(book_id)
    assert reservation.generation == first.generation + 1
    committed = pg_index.replace_generation(
        book_id,
        reservation.generation,
        [new],
        [_vector(1)],
        build_id=reservation.build_id,
    )
    assert committed.state == "committed"
    assert pg_index.search_vector(book_id, _vector(1)) == []
    pg_index.finalize_ready(book_id, reservation.generation, build_id=reservation.build_id)
    assert [item.chunk.chunk_id for item in pg_index.search_vector(book_id, _vector(1))] == ["new-id"]
    with pg_index._connect() as conn:
        ids = conn.execute(
            "select chunk_id from rag_chunk_vectors where book_id = %s order by chunk_id",
            (book_id,),
        ).fetchall()
    assert ids == [("new-id",)]


def test_transaction_failure_rolls_back_without_mixing_generations(
    pg_index: PgVectorIndex,
    book_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = _chunk(book_id, "stable-old", "stable")
    old_state = _publish(pg_index, book_id, old, _vector(0))
    reservation = pg_index.reserve_generation(book_id)
    original_insert = pg_index._insert_indexable_rows

    def insert_then_fail(*args, **kwargs):
        original_insert(*args, **kwargs)
        raise RuntimeError("injected transaction failure")

    monkeypatch.setattr(pg_index, "_insert_indexable_rows", insert_then_fail)
    with pytest.raises(RuntimeError, match="injected transaction failure"):
        pg_index.replace_generation(
            book_id,
            reservation.generation,
            [_chunk(book_id, "partial-new", "partial")],
            [_vector(1)],
            build_id=reservation.build_id,
        )

    state = pg_index.get_state(book_id)
    assert state is not None and state.state == "failed"
    assert state.generation == old_state.generation + 1
    assert pg_index.search_vector(book_id, _vector(0)) == []
    with pg_index._connect() as conn:
        rows = conn.execute(
            "select index_generation, chunk_id from rag_chunk_vectors where book_id = %s",
            (book_id,),
        ).fetchall()
    assert rows == [(old_state.generation, "stable-old")]


def test_late_generation_is_rejected(pg_index: PgVectorIndex, book_id: str) -> None:
    late = pg_index.reserve_generation(book_id, build_id="late")
    current = pg_index.reserve_generation(book_id, build_id="current")
    assert current.generation == late.generation + 1

    with pytest.raises(StaleGenerationError):
        pg_index.replace_generation(
            book_id,
            late.generation,
            [_chunk(book_id, "late-id", "late")],
            [_vector(0)],
            build_id=late.build_id,
        )
    state = pg_index.get_state(book_id)
    assert state is not None
    assert (state.generation, state.build_id, state.state) == (current.generation, current.build_id, "building")


def test_delete_leaves_monotonic_tombstone_and_blocks_late_worker(
    pg_index: PgVectorIndex,
    book_id: str,
) -> None:
    ready = _publish(pg_index, book_id, _chunk(book_id, "to-delete", "delete me"), _vector(0))
    tombstone = pg_index.delete_book(book_id)
    assert tombstone is not None
    assert tombstone.state == "deleted"
    assert tombstone.generation == ready.generation + 1
    assert tombstone.chunk_count == 0
    assert pg_index.search_vector(book_id, _vector(0)) == []

    with pytest.raises(StaleGenerationError):
        pg_index.replace_generation(
            book_id,
            ready.generation,
            [_chunk(book_id, "zombie", "zombie")],
            [_vector(0)],
            build_id=ready.build_id,
        )
    with pg_index._connect() as conn:
        count = conn.execute(
            "select count(*) from rag_chunk_vectors where book_id = %s",
            (book_id,),
        ).fetchone()[0]
    assert count == 0


def test_wrong_dimension_fails_generation_without_touching_existing_rows(
    pg_index: PgVectorIndex,
    book_id: str,
) -> None:
    ready = _publish(pg_index, book_id, _chunk(book_id, "old", "old"), _vector(0))
    reservation = pg_index.reserve_generation(book_id)
    with pytest.raises(InvalidIndexPayloadError, match="dimension"):
        pg_index.replace_generation(
            book_id,
            reservation.generation,
            [_chunk(book_id, "bad", "bad")],
            [[0.0] * 1023],
            build_id=reservation.build_id,
        )
    state = pg_index.get_state(book_id)
    assert state is not None and state.state == "failed"
    with pg_index._connect() as conn:
        rows = conn.execute(
            "select index_generation, chunk_id from rag_chunk_vectors where book_id = %s",
            (book_id,),
        ).fetchall()
    assert rows == [(ready.generation, "old")]


def test_cache_failed_is_unreadable_and_finalize_can_retry(
    pg_index: PgVectorIndex,
    book_id: str,
) -> None:
    reservation = pg_index.reserve_generation(book_id)
    committed = pg_index.replace_generation(
        book_id,
        reservation.generation,
        [_chunk(book_id, "retry", "retry")],
        [_vector(0)],
        build_id=reservation.build_id,
    )
    failed = pg_index.mark_cache_failed(
        book_id,
        committed.generation,
        build_id=committed.build_id,
        error="query cache invalidation failed",
    )
    assert failed.state == "cache_failed"
    assert pg_index.search_vector(book_id, _vector(0)) == []

    ready = pg_index.finalize_ready(book_id, failed.generation, build_id=failed.build_id)
    assert ready.state == "ready"
    assert [item.chunk.chunk_id for item in pg_index.search_vector(book_id, _vector(0))] == ["retry"]

