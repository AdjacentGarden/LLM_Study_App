from __future__ import annotations

from dataclasses import dataclass, replace as dc_replace
from typing import Any, Hashable

from app.core.config import get_settings
from app.document.chunk_protocol import is_chunk_indexable, normalize_for_hash
from app.rag.bm25 import BM25Index
from app.rag.cache import (
    cache_hit_description,
    get_bm25_index,
    has_artifact_embeddings,
    has_bm25_index,
)
from app.rag.embedding import (
    get_embedding_service,
    get_hashing_fallback_service,
    render_chunk_embedding_text,
)
from app.rag.index_base import ArtifactVectorIndex, VectorSearchResult
from app.rag.index_factory import (
    get_rag_index,
    index_generation_from_status,
    read_query_index_status,
)
from app.rag.reranker import get_reranker
from app.schemas.books import Chunk
from app.services.artifact_store import read_chunks


@dataclass(frozen=True)
class RetrievedChunk:
    chunk: Chunk
    score: float
    bm25_score: float
    dense_score: float
    retrieval_method: str = "rrf"
    rerank_score: float = 0.0
    rank: int = 0
    index_name: str = "unknown"
    index_provider: str = "unknown"
    index_generation: Hashable | None = None
    fallback_reason: str | None = None
    embedding_descriptor: dict[str, str | int] | None = None
    cache_hit: str = "none"


class HybridRetriever:
    def retrieve(
        self,
        book_id: str,
        question: str,
        chapter_id: str | None = None,
        limit: int | None = None,
    ) -> list[RetrievedChunk]:
        settings = get_settings()
        final_limit = limit or settings.final_context_k
        chunks_hit = False
        bm25_hit = False
        vector_hit = False
        index_status, index_status_error = read_query_index_status(book_id)
        index_generation = index_generation_from_status(index_status)

        chunks = [chunk for chunk in read_chunks(book_id) if is_chunk_indexable(chunk)]
        if chapter_id:
            chunks = [chunk for chunk in chunks if chunk.chapter_id == chapter_id]
            if not chunks:
                return []

        bm25_hit = has_bm25_index(book_id, chapter_id, index_generation)

        bm25_index = get_bm25_index(
            book_id,
            chapter_id,
            lambda: BM25Index(chunks),
            index_generation=index_generation,
        )
        bm25_results = bm25_index.search(question, chapter_id=chapter_id, top_k=settings.bm25_top_k)

        embedding_fallback_reason: str | None = None
        try:
            embedding_service = get_embedding_service()
            query_embedding = embedding_service.embed_query(question)
        except Exception as exc:
            # The fallback descriptor remains "hashing"; it must never reuse
            # or claim the configured BGE identity after a model/load failure.
            embedding_fallback_reason = f"embedding_provider_failed:{type(exc).__name__}"
            embedding_service = get_hashing_fallback_service()
            query_embedding = embedding_service.embed_query(question)

        index = get_rag_index(
            embedding_service,
            book_id,
            index_status=index_status,
            index_status_error=index_status_error,
            forced_fallback_reason=embedding_fallback_reason,
        )
        if isinstance(index, ArtifactVectorIndex):
            vector_hit = has_artifact_embeddings(
                book_id,
                chapter_id,
                getattr(index, "index_generation", index_generation),
                getattr(index, "embedding_cache_identity", None),
            )
        try:
            dense_results = index.search_vector(
                book_id,
                query_embedding,
                chapter_id=chapter_id,
                top_k=settings.vector_top_k,
            )
        except Exception as exc:
            index = self._artifact_fallback(
                embedding_service,
                settings.rag_index_provider,
                f"provider_query_failed:{type(exc).__name__}",
                index_generation,
            )
            vector_hit = has_artifact_embeddings(
                book_id,
                chapter_id,
                index_generation,
                getattr(index, "embedding_cache_identity", None),
            )
            dense_results = index.search_vector(
                book_id,
                query_embedding,
                chapter_id=chapter_id,
                top_k=settings.vector_top_k,
            )
        candidates = self._fuse(
            bm25_results,
            dense_results,
            chunks,
            index_name=index.name,
            index_provider=str(getattr(index, "provider", index.name)),
            index_generation=getattr(index, "index_generation", index_generation),
            fallback_reason=getattr(index, "fallback_reason", None),
            embedding_descriptor=_descriptor_dict(embedding_service),
        )
        candidates = candidates[: settings.rerank_input_k]
        candidates = get_reranker().rerank(question, candidates, top_k=final_limit)
        ordered = [self._with_rank(candidate, i + 1) for i, candidate in enumerate(candidates[:final_limit])]
        hit = cache_hit_description(chunks_hit=chunks_hit, bm25_hit=bm25_hit, vector_hit=vector_hit)
        return [dc_replace(item, cache_hit=hit) for item in ordered]

    @staticmethod
    def _artifact_fallback(
        embedding_service: Any,
        requested_provider: str,
        reason: str,
        index_generation: Hashable | None,
    ) -> ArtifactVectorIndex:
        return ArtifactVectorIndex(
            embedding_service,
            requested_provider=requested_provider,
            fallback_reason=reason,
            index_generation=index_generation,
        )

    def _fuse(
        self,
        bm25_results: list,
        dense_results: list[VectorSearchResult],
        chunks: list[Chunk],
        index_name: str,
        index_provider: str = "unknown",
        index_generation: Hashable | None = None,
        fallback_reason: str | None = None,
        embedding_descriptor: dict[str, str | int] | None = None,
    ) -> list[RetrievedChunk]:
        # The artifact bundle is the authority for the active generation and
        # its persisted quality protocol. External indexes contribute scores
        # only: their rows may be stale or may omit V2 quarantine metadata.
        # Never let a dense-only database row introduce a chunk that is absent
        # from the current bundle, and never replace the canonical Chunk model
        # with the reduced representation returned by an external index.
        by_id = {
            chunk.chunk_id: chunk
            for chunk in chunks
            if is_chunk_indexable(chunk)
        }
        bm25_by_id = {
            item.chunk.chunk_id: item
            for item in bm25_results
            if item.chunk.chunk_id in by_id
        }
        dense_by_id = {
            item.chunk.chunk_id: item
            for item in dense_results
            if item.chunk.chunk_id in by_id
        }
        fused_scores = _rrf_scores([bm25_by_id, dense_by_id])
        allow_dense_only = index_name != ArtifactVectorIndex.name
        candidates: list[RetrievedChunk] = []
        for chunk_id, score in fused_scores.items():
            chunk = by_id.get(chunk_id)
            if chunk is None or not is_chunk_indexable(chunk):
                continue
            bm25_score = bm25_by_id.get(chunk_id).score if chunk_id in bm25_by_id else 0.0
            dense_score = dense_by_id.get(chunk_id).dense_score if chunk_id in dense_by_id else 0.0
            if chunk.content_type == "ocr_pending" and bm25_score <= 0:
                continue
            if bm25_score <= 0 and not allow_dense_only:
                continue
            candidates.append(
                RetrievedChunk(
                    chunk=chunk,
                    score=score,
                    bm25_score=bm25_score,
                    dense_score=dense_score,
                    index_name=index_name,
                    index_provider=index_provider,
                    index_generation=index_generation,
                    fallback_reason=fallback_reason,
                    embedding_descriptor=embedding_descriptor,
                )
            )
        candidates.sort(key=lambda item: (item.score, item.bm25_score, item.dense_score), reverse=True)
        return candidates

    def _with_rank(self, candidate: RetrievedChunk, rank: int) -> RetrievedChunk:
        return RetrievedChunk(
            chunk=candidate.chunk,
            score=candidate.score,
            bm25_score=candidate.bm25_score,
            dense_score=candidate.dense_score,
            retrieval_method=candidate.retrieval_method,
            rerank_score=candidate.rerank_score,
            rank=rank,
            index_name=candidate.index_name,
            index_provider=candidate.index_provider,
            index_generation=candidate.index_generation,
            fallback_reason=candidate.fallback_reason,
            embedding_descriptor=candidate.embedding_descriptor,
        )


def _rrf_scores(result_maps: list[dict[str, object]], k: int = 60) -> dict[str, float]:
    fused: dict[str, float] = {}
    for result_map in result_maps:
        ordered = sorted(
            result_map.items(),
            key=lambda item: getattr(item[1], "score", getattr(item[1], "dense_score", 0)),
            reverse=True,
        )
        for rank, (chunk_id, _) in enumerate(ordered, start=1):
            fused[chunk_id] = fused.get(chunk_id, 0.0) + 1 / (k + rank)
    return fused


def _descriptor_dict(embedding_service: Any) -> dict[str, str | int] | None:
    descriptor = getattr(embedding_service, "descriptor", None)
    if descriptor is None:
        return None
    as_dict = getattr(descriptor, "as_dict", None)
    if callable(as_dict):
        return dict(as_dict())
    if isinstance(descriptor, dict):
        return dict(descriptor)
    fields = ("provider", "model", "revision", "version", "dimension", "device")
    value = {
        field: getattr(descriptor, field)
        for field in fields
        if getattr(descriptor, field, None) is not None
    }
    return value or None


def retrieve_chunks(book_id: str, question: str, chapter_id: str | None = None, limit: int = 5) -> list[RetrievedChunk]:
    return HybridRetriever().retrieve(book_id, question, chapter_id=chapter_id, limit=limit)


def reliable_chunks_for_chapter(book_id: str, chapter_id: str) -> tuple[list[Chunk], list[str]]:
    warnings: list[str] = []
    reliable: list[Chunk] = []
    seen_text: set[str] = set()
    pending_count = 0
    duplicate_count = 0
    empty_count = 0
    non_indexable_count = 0
    for chunk in sorted(
        (chunk for chunk in read_chunks(book_id) if chunk.chapter_id == chapter_id),
        key=lambda item: (item.page_start, item.page_end, item.chunk_id),
    ):
        if chunk.content_type == "ocr_pending":
            pending_count += 1
            continue
        if not is_chunk_indexable(chunk):
            non_indexable_count += 1
            continue
        text = render_chunk_embedding_text(chunk)
        if not text:
            empty_count += 1
            continue
        fingerprint = normalize_for_hash(text)
        if fingerprint in seen_text:
            duplicate_count += 1
            continue
        seen_text.add(fingerprint)
        reliable.append(chunk)
    if pending_count:
        warnings.append(f"skipped_ocr_pending_chunks:{pending_count}")
    if duplicate_count:
        warnings.append(f"skipped_duplicate_chunks:{duplicate_count}")
    if empty_count:
        warnings.append(f"skipped_empty_chunks:{empty_count}")
    if non_indexable_count:
        warnings.append(f"skipped_non_indexable_chunks:{non_indexable_count}")
    if not reliable:
        warnings.append("no_reliable_text_chunks")
    return reliable, warnings
