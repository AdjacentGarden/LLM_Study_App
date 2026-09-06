from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import get_settings
from app.rag.cache import clear_rag_cache, invalidate_book
from app.rag.embedding import HashingEmbeddingService
from app.rag.index_pgvector import PgVectorIndex
from app.rag.indexing import (
    RagIndexPublishError,
    delete_book_index,
    publish_index_build,
    read_index_status,
    reserve_index_build,
    retry_finalize,
)
from app.rag.retrieval import retrieve_chunks
from app.schemas.books import Chunk
from app.services.artifact_store import mark_rag_bundle_building, write_assets_and_chunks
from app.services.storage import remove_book, resolve_under_root


APPROVED_DSN = "postgresql://cloudpath_stage4@127.0.0.1:55432/cloudpath_stage4"


def _chunk(book_id: str, chunk_id: str, text: str, *, chapter_id: str = "chapter-a") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id=book_id,
        chapter_id=chapter_id,
        page_start=1,
        page_end=2,
        content_type="paragraph",
        text=text,
        parser="mineru",
        parser_version="3.4.4",
        chunk_version="v2",
        heading_path=["Stage 4", chapter_id],
        source_block_ids=[f"block-{chunk_id}"],
        quality_score=0.93,
        token_count=12,
        content_hash=f"hash-{chunk_id}",
        metadata={
            "quality_version": "quality-v1",
            "quality_threshold": 0.45,
            "indexable": True,
            "quarantined": False,
            "index_status": "ready",
        },
    )


@pytest.fixture
def isolated_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    dsn = os.environ.get("CLOUDPATH_STAGE4_TEST_DSN")
    if dsn != APPROVED_DSN:
        pytest.skip("requires the explicitly approved Stage-4 loopback database")
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_DATABASE_URL", APPROVED_DSN)
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "1024")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_VERSION", "stage4-isolated-v1")
    get_settings.cache_clear()
    clear_rag_cache()
    descriptor = HashingEmbeddingService(dimensions=1024, version="stage4-isolated-v1").descriptor
    index = PgVectorIndex(
        APPROVED_DSN,
        embedding_model=descriptor.model,
        embedding_revision=descriptor.revision,
        embedding_dimension=descriptor.dimension,
        chunk_version="v2",
    )
    # This test fixture is the declared isolated migration target. Application
    # indexing paths deliberately never invoke apply_migration themselves.
    index.apply_migration()
    with index._connect() as conn:
        identity = conn.execute(
            """
            select current_database(), current_user, inet_server_addr()::text,
                   inet_server_port(), current_setting('server_version'),
                   (select extversion from pg_extension where extname='vector')
            """
        ).fetchone()
    assert identity == ("cloudpath_stage4", "cloudpath_stage4", "127.0.0.1/32", 55432, "16.14", "0.8.3")
    book_ids: list[str] = []
    yield index, book_ids
    with index._connect() as conn:
        for book_id in book_ids:
            conn.execute("delete from rag_chunk_vectors where book_id = %s", (book_id,))
            conn.execute("delete from rag_index_state where book_id = %s", (book_id,))
    clear_rag_cache()
    get_settings.cache_clear()


def _publish(book_id: str, chunks: list[Chunk], cause: str):
    build = mark_rag_bundle_building(book_id)
    reservation = reserve_index_build(book_id, build.build_id, cause)
    write_assets_and_chunks(book_id, [], chunks, build_id=build.build_id)
    return publish_index_build(reservation, chunks)


def test_parse_reparse_chapter_sync_retrieval_and_delete(isolated_runtime) -> None:
    index, book_ids = isolated_runtime
    book_id = f"stage4_business_{uuid4().hex}"
    book_ids.append(book_id)

    first = _chunk(book_id, "old-chunk", "obsolete chloroplast material")
    first_receipt = _publish(book_id, [first], "parse:1")
    assert first_receipt.status == "ready" and first_receipt.counts["indexed_chunks"] == 1
    first_results = retrieve_chunks(book_id, "obsolete chloroplast material")
    assert first_results
    assert first_results[0].chunk.chunk_id == "old-chunk"
    assert first_results[0].index_name == "pgvector"
    assert first_results[0].bm25_score > 0 and first_results[0].dense_score > 0

    replacement = _chunk(book_id, "new-chunk", "selective membrane transport sodium channel")
    second_receipt = _publish(book_id, [replacement], "parse:2")
    assert second_receipt.index_generation == first_receipt.index_generation + 1
    with index._connect() as conn:
        rows = conn.execute(
            "select index_generation, chunk_id from rag_chunk_vectors where book_id=%s",
            (book_id,),
        ).fetchall()
    assert rows == [(second_receipt.index_generation, "new-chunk")]
    second_results = retrieve_chunks(book_id, "selective membrane transport sodium channel")
    assert second_results and second_results[0].chunk.chunk_id == "new-chunk"
    assert all(item.chunk.chunk_id != "old-chunk" for item in second_results)

    moved = replacement.model_copy(update={"chapter_id": "chapter-b", "chunk_id": "chapter-adjusted"})
    chapter_receipt = _publish(book_id, [moved], "chapter_rebuild")
    assert chapter_receipt.index_generation == second_receipt.index_generation + 1
    chapter_results = retrieve_chunks(
        book_id,
        "membrane transport sodium channel",
        chapter_id="chapter-b",
    )
    assert chapter_results and chapter_results[0].chunk.chunk_id == "chapter-adjusted"
    with index._connect() as conn:
        count, chapter = conn.execute(
            "select count(*), min(chapter_id) from rag_chunk_vectors where book_id=%s",
            (book_id,),
        ).fetchone()
    assert (count, chapter) == (1, "chapter-b")

    tombstone = delete_book_index(book_id)
    assert tombstone.status == "deleted"
    with index._connect() as conn:
        vector_count = conn.execute(
            "select count(*) from rag_chunk_vectors where book_id=%s",
            (book_id,),
        ).fetchone()[0]
    assert vector_count == 0
    remove_book(book_id)
    assert not resolve_under_root("books", book_id).exists()


def test_committed_cache_failure_is_unready_then_same_generation_retries(isolated_runtime) -> None:
    index, book_ids = isolated_runtime
    book_id = f"stage4_cache_{uuid4().hex}"
    book_ids.append(book_id)
    chunk = _chunk(book_id, "cache-retry", "cache barrier retry evidence")
    build = mark_rag_bundle_building(book_id)

    def fail_cache(_: str) -> None:
        raise RuntimeError("injected cache invalidation failure")

    reservation = reserve_index_build(
        book_id,
        build.build_id,
        "parse:1",
        cache_invalidator=fail_cache,
    )
    write_assets_and_chunks(book_id, [], [chunk], build_id=build.build_id)
    with pytest.raises(RagIndexPublishError):
        publish_index_build(reservation, [chunk])
    failed = read_index_status(book_id)
    assert failed is not None and failed.status == "cache_failed" and failed.retryable
    assert index.search_vector(book_id, HashingEmbeddingService(1024).embed_query(chunk.text)) == []

    receipt = retry_finalize(
        book_id,
        failed.index_generation,
        failed.build_id,
        cache_invalidator=invalidate_book,
    )
    assert receipt.status == "ready" and receipt.index_generation == failed.index_generation
    results = retrieve_chunks(book_id, "cache barrier retry evidence")
    assert results and results[0].index_name == "pgvector"
