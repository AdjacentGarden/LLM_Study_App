from __future__ import annotations

import threading
from collections import OrderedDict
from typing import Any, Callable, Generic, Hashable, Optional, TypeVar

from app.core.config import get_settings

K = TypeVar("K", bound=Hashable)
V = TypeVar("V")
_CACHE_INIT_LOCK = threading.RLock()


class LRUCache(Generic[K, V]):
    """Simple thread-safe LRU cache.

    Args:
        capacity: max number of entries; older entries evicted in FIFO/LRU order via OrderedDict.move_to_end.
    """

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            capacity = 1
        self._capacity = capacity
        self._data: "OrderedDict[K, V]" = OrderedDict()
        self._lock = threading.RLock()

    @property
    def capacity(self) -> int:
        return self._capacity

    def get(self, key: K) -> Optional[V]:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
            return None

    def set(self, key: K, value: V) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                self._data[key] = value
                return
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)

    def get_or_set(self, key: K, builder: Callable[[], V]) -> tuple[V, bool]:
        """Return a cached value or build it while holding the cache lock.

        Keeping miss/build/set in one critical section makes invalidation a
        real barrier: an invalidator cannot finish and then have an older
        in-flight builder repopulate the cache with stale generation data.
        """

        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key], True
            value = builder()
            self._data[key] = value
            while len(self._data) > self._capacity:
                self._data.popitem(last=False)
            return value, False

    def pop(self, key: K) -> Optional[V]:
        with self._lock:
            return self._data.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def pop_where(self, predicate: Callable[[K], bool]) -> int:
        """Atomically remove every key accepted by ``predicate``."""

        with self._lock:
            keys = [key for key in self._data if predicate(key)]
            for key in keys:
                self._data.pop(key, None)
            return len(keys)

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def __contains__(self, key: object) -> bool:
        with self._lock:
            return key in self._data


# ---- Module-level caches ----
# All guarded by settings.rag_cache_enabled at access time.


def _chunks_cache() -> "LRUCache[str, Any]":
    global _CHUNKS_CACHE
    if _CHUNKS_CACHE is None:
        with _CACHE_INIT_LOCK:
            if _CHUNKS_CACHE is None:
                _CHUNKS_CACHE = LRUCache[str, Any](_capacity_from_settings())
    return _CHUNKS_CACHE


def _bm25_cache() -> "LRUCache[tuple[Hashable, ...], Any]":
    global _BM25_CACHE
    if _BM25_CACHE is None:
        with _CACHE_INIT_LOCK:
            if _BM25_CACHE is None:
                _BM25_CACHE = LRUCache[tuple[Hashable, ...], Any](_capacity_from_settings())
    return _BM25_CACHE


def _artifact_embedding_cache() -> "LRUCache[tuple[Hashable, ...], Any]":
    global _ARTIFACT_EMBEDDING_CACHE
    if _ARTIFACT_EMBEDDING_CACHE is None:
        with _CACHE_INIT_LOCK:
            if _ARTIFACT_EMBEDDING_CACHE is None:
                _ARTIFACT_EMBEDDING_CACHE = LRUCache[tuple[Hashable, ...], Any](_capacity_from_settings())
    return _ARTIFACT_EMBEDDING_CACHE


def _capacity_from_settings() -> int:
    try:
        return max(1, get_settings().rag_cache_max_books)
    except Exception:
        return 8


_CHUNKS_CACHE: Optional[LRUCache[str, Any]] = None
_BM25_CACHE: Optional[LRUCache[tuple[Hashable, ...], Any]] = None
_ARTIFACT_EMBEDDING_CACHE: Optional[LRUCache[tuple[Hashable, ...], Any]] = None


def _generation_cache_key(
    book_id: str,
    chapter_id: Optional[str],
    index_generation: Hashable | None,
    embedding_identity: Hashable | None = None,
) -> tuple[Hashable, ...]:
    # Preserve the original two-item key for legacy callers. This matters for
    # rolling upgrades where old workers and their tests still inspect/cache
    # entries without an index generation.
    if index_generation is None and embedding_identity is None:
        return (book_id, chapter_id)
    try:
        hash(index_generation)
        generation: Hashable = index_generation
    except TypeError:
        generation = repr(index_generation)
    if embedding_identity is None:
        return (book_id, chapter_id, generation)
    try:
        hash(embedding_identity)
        identity: Hashable = embedding_identity
    except TypeError:
        identity = repr(embedding_identity)
    return (book_id, chapter_id, generation, identity)


def is_cache_enabled() -> bool:
    try:
        return bool(get_settings().rag_cache_enabled)
    except Exception:
        return True


# ---- Public helper accessors (used by retrieval path) ----


def get_chunks(book_id: str, loader: Callable[[str], Any]):
    """Cache read_chunks() results for ``book_id``."""
    if not is_cache_enabled():
        return loader(book_id)
    cache = _chunks_cache()
    cached = cache.get(book_id)
    if cached is not None:
        return cached
    chunks = loader(book_id)
    cache.set(book_id, chunks)
    return chunks


def get_bm25_index(
    book_id: str,
    chapter_id: Optional[str],
    builder: Callable[[], Any],
    index_generation: Hashable | None = None,
):
    """Cache a per-(book, chapter, index generation) BM25Index.

    The builder constructs a fresh index from current chunks on miss.
    ``index_generation=None`` retains the pre-Stage-4 key contract.
    """
    if not is_cache_enabled():
        return builder()
    cache = _bm25_cache()
    key = _generation_cache_key(book_id, chapter_id, index_generation)
    index, _hit = cache.get_or_set(key, builder)
    return index


def get_artifact_embeddings(
    book_id: str,
    chapter_id: Optional[str],
    builder: Callable[[], Any],
    index_generation: Hashable | None = None,
    embedding_identity: Hashable | None = None,
):
    """Cache per-generation ``(chunk, embedding)`` artifact pairs.

    ``index_generation=None`` retains the pre-Stage-4 key contract.
    """
    if not is_cache_enabled():
        return builder()
    cache = _artifact_embedding_cache()
    key = _generation_cache_key(
        book_id,
        chapter_id,
        index_generation,
        embedding_identity,
    )
    value, _hit = cache.get_or_set(key, builder)
    return value


def invalidate_book(book_id: str) -> None:
    """Drop all cache entries related to a book (chunks + all chapter variants)."""
    invalidate_chunks(book_id)
    invalidate_bm25(book_id)
    invalidate_artifact_embeddings(book_id)


def invalidate_chunks(book_id: str) -> None:
    if _CHUNKS_CACHE is not None:
        _CHUNKS_CACHE.pop(book_id)


def invalidate_bm25(book_id: str) -> None:
    if _BM25_CACHE is not None:
        _BM25_CACHE.pop_where(lambda key: bool(key) and key[0] == book_id)


def invalidate_artifact_embeddings(book_id: str) -> None:
    if _ARTIFACT_EMBEDDING_CACHE is not None:
        _ARTIFACT_EMBEDDING_CACHE.pop_where(lambda key: bool(key) and key[0] == book_id)


def has_bm25_index(
    book_id: str,
    chapter_id: Optional[str],
    index_generation: Hashable | None = None,
) -> bool:
    if not is_cache_enabled() or _BM25_CACHE is None:
        return False
    return _generation_cache_key(book_id, chapter_id, index_generation) in _BM25_CACHE


def has_artifact_embeddings(
    book_id: str,
    chapter_id: Optional[str],
    index_generation: Hashable | None = None,
    embedding_identity: Hashable | None = None,
) -> bool:
    if not is_cache_enabled() or _ARTIFACT_EMBEDDING_CACHE is None:
        return False
    return _generation_cache_key(
        book_id,
        chapter_id,
        index_generation,
        embedding_identity,
    ) in _ARTIFACT_EMBEDDING_CACHE


def clear_rag_cache() -> None:
    """Clear every cache entry (test/maintenance hook)."""
    if _CHUNKS_CACHE is not None:
        _CHUNKS_CACHE.clear()
    if _BM25_CACHE is not None:
        _BM25_CACHE.clear()
    if _ARTIFACT_EMBEDDING_CACHE is not None:
        _ARTIFACT_EMBEDDING_CACHE.clear()


def cache_hit_description(*, chunks_hit: bool, bm25_hit: bool, vector_hit: bool) -> str:
    """Return a short symbolic label describing which cache hit during a retriever call.

    The order of preference is chunks_hit > bm25_hit > vector_hit > none.
    """
    if chunks_hit and bm25_hit:
        return "all_hit"
    if bm25_hit and vector_hit:
        return "bm25_vector_hit"
    if bm25_hit:
        return "bm25_hit"
    if vector_hit:
        return "vector_hit"
    if chunks_hit:
        return "chunks_hit"
    return "none"
