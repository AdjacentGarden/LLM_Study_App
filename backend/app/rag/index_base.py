from __future__ import annotations

from dataclasses import dataclass
from typing import Hashable, Protocol

from app.document.chunk_protocol import is_chunk_indexable
from app.rag.cache import get_artifact_embeddings, invalidate_artifact_embeddings
from app.rag.embedding import EmbeddingService, cosine_similarity, render_chunk_embedding_text
from app.schemas.books import Chunk
from app.services.artifact_store import read_chunks


@dataclass(frozen=True)
class VectorSearchResult:
    chunk: Chunk
    dense_score: float
    rank: int


class RagIndex(Protocol):
    name: str

    def upsert_chunks(self, book_id: str, chunks: list[Chunk], embeddings: list[list[float]] | None = None) -> None:
        ...

    def search_vector(
        self,
        book_id: str,
        query_embedding: list[float],
        *,
        chapter_id: str | None = None,
        top_k: int = 80,
    ) -> list[VectorSearchResult]:
        ...

    def delete_book(self, book_id: str) -> None:
        ...


class ArtifactVectorIndex:
    # This label describes the index that actually produced dense results.
    # It deliberately contains no configured provider name (for example,
    # pgvector) so an operational fallback cannot masquerade as that provider.
    name = "artifact_fallback"
    provider = "artifact"

    def __init__(
        self,
        embedding_service: EmbeddingService,
        *,
        requested_provider: str | None = None,
        fallback_reason: str | None = None,
        index_generation: Hashable | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.requested_provider = requested_provider
        self.fallback_reason = fallback_reason or "artifact_provider_selected"
        self.index_generation = index_generation
        self.embedding_cache_identity = _embedding_cache_identity(embedding_service)

    def upsert_chunks(self, book_id: str, chunks: list[Chunk], embeddings: list[list[float]] | None = None) -> None:
        # invalidate cached artifacts since chunks have changed
        invalidate_artifact_embeddings(book_id)
        return None

    def search_vector(
        self,
        book_id: str,
        query_embedding: list[float],
        *,
        chapter_id: str | None = None,
        top_k: int = 80,
    ) -> list[VectorSearchResult]:
        def _build():
            chunks = [chunk for chunk in read_chunks(book_id) if is_chunk_indexable(chunk)]
            if chapter_id:
                chunks = [chunk for chunk in chunks if chunk.chapter_id == chapter_id]
            if not chunks:
                return []
            try:
                doc_embeddings = self.embedding_service.embed_documents([_chunk_embedding_text(chunk) for chunk in chunks])
            except Exception:
                return []
            return list(zip(chunks, doc_embeddings, strict=False))

        pairs = get_artifact_embeddings(
            book_id,
            chapter_id,
            _build,
            index_generation=self.index_generation,
            embedding_identity=self.embedding_cache_identity,
        )
        if not pairs:
            return []
        results: list[VectorSearchResult] = []
        for chunk, embedding in pairs:
            score = cosine_similarity(query_embedding, embedding)
            if score > 0:
                results.append(VectorSearchResult(chunk=chunk, dense_score=score, rank=0))
        results.sort(key=lambda item: item.dense_score, reverse=True)
        return [VectorSearchResult(chunk=item.chunk, dense_score=item.dense_score, rank=index + 1) for index, item in enumerate(results[:top_k])]

    def delete_book(self, book_id: str) -> None:
        invalidate_artifact_embeddings(book_id)
        return None


def _chunk_embedding_text(chunk: Chunk) -> str:
    # Backward-compatible private alias retained for callers/tests that used
    # the original helper before Chunk V2 introduced a canonical renderer.
    return render_chunk_embedding_text(chunk)


def _embedding_cache_identity(embedding_service: EmbeddingService) -> tuple[object, ...]:
    descriptor = getattr(embedding_service, "descriptor", None)
    if descriptor is not None:
        if isinstance(descriptor, dict):
            value = descriptor
        else:
            as_dict = getattr(descriptor, "as_dict", None)
            value = as_dict() if callable(as_dict) else {
                field: getattr(descriptor, field, None)
                for field in ("provider", "model", "revision", "version", "dimension", "device")
            }
        return tuple(
            value.get(field)
            for field in ("provider", "model", "revision", "version", "dimension", "device")
        )
    return (
        getattr(embedding_service, "name", type(embedding_service).__name__),
        getattr(embedding_service, "dimensions", None),
    )
