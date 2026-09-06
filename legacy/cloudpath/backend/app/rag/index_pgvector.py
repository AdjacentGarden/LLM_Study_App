from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import math
from pathlib import Path
from typing import Any, Iterable, Sequence
from uuid import uuid4

from app.document.chunk_protocol import is_chunk_indexable
from app.rag.index_base import RagIndex, VectorSearchResult
from app.schemas.books import Chunk


DEFAULT_EMBEDDING_MODEL = "BAAI/bge-m3"
DEFAULT_EMBEDDING_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
DEFAULT_CHUNK_VERSION = "v2"
DEFAULT_EMBEDDING_DIMENSION = 1024
MIGRATION_SQL_PATH = Path(__file__).resolve().parents[2] / "migrations" / "001_pgvector_v2.sql"


class PgVectorIndexError(RuntimeError):
    """Base error for generation-aware pgvector operations."""


class StaleGenerationError(PgVectorIndexError):
    """A delayed worker tried to mutate a generation it no longer owns."""


class InvalidGenerationStateError(PgVectorIndexError):
    """A generation transition was requested from an incompatible state."""


class IndexCountMismatchError(PgVectorIndexError):
    """The committed row count differs from the validated indexable payload."""


class InvalidIndexPayloadError(ValueError):
    """Chunks and embeddings do not form a valid frozen V2 index payload."""


@dataclass(frozen=True, slots=True)
class RagIndexState:
    book_id: str
    index_generation: int
    build_id: str
    state: str
    embedding_model: str
    embedding_revision: str
    chunk_version: str
    embedding_dimension: int
    chunk_count: int
    error: str | None
    reserved_at: datetime
    committed_at: datetime | None
    ready_at: datetime | None
    deleted_at: datetime | None
    updated_at: datetime

    @property
    def generation(self) -> int:
        return self.index_generation


_STATE_COLUMNS = """
  book_id, index_generation, build_id, state,
  embedding_model, embedding_revision, chunk_version, embedding_dimension,
  chunk_count, error, reserved_at, committed_at, ready_at, deleted_at, updated_at
"""


class PgVectorIndex:
    """Generation-aware pgvector storage for complete frozen Chunk V2 records.

    A replacement intentionally stops in ``committed``. Callers must invalidate
    every book-scoped retrieval cache and only then call ``finalize_ready``.
    Search joins the state row and therefore cannot observe building,
    committed, failed, cache_failed, or deleted generations.
    """

    name = "pgvector"

    def __init__(
        self,
        database_url: str | None,
        *,
        embedding_model: str = DEFAULT_EMBEDDING_MODEL,
        embedding_revision: str = DEFAULT_EMBEDDING_REVISION,
        chunk_version: str = DEFAULT_CHUNK_VERSION,
        embedding_dimension: int = DEFAULT_EMBEDDING_DIMENSION,
    ) -> None:
        self.database_url = database_url
        self.embedding_model = embedding_model.strip()
        self.embedding_revision = embedding_revision.strip()
        self.chunk_version = chunk_version.strip()
        self.embedding_dimension = int(embedding_dimension)
        if not self.embedding_model or not self.embedding_revision or not self.chunk_version:
            raise ValueError("model, revision, and chunk_version must not be empty")
        if self.embedding_dimension != DEFAULT_EMBEDDING_DIMENSION:
            raise ValueError(f"pgvector V2 dimension must be {DEFAULT_EMBEDDING_DIMENSION}")

    @property
    def available(self) -> bool:
        if not self.database_url:
            return False
        try:
            import psycopg  # noqa: F401
        except Exception:
            return False
        return True

    def _connect(self):
        if not self.available:
            raise PgVectorIndexError("psycopg and database_url are required for pgvector")
        import psycopg

        return psycopg.connect(self.database_url)

    def apply_migration(self) -> None:
        """Apply the standalone idempotent schema migration."""

        with self._connect() as conn:
            conn.execute(PGVECTOR_SCHEMA_SQL)

    migrate = apply_migration

    def get_state(self, book_id: str) -> RagIndexState | None:
        _validate_book_id(book_id)
        if not self.available:
            return None
        with self._connect() as conn:
            row = conn.execute(
                f"select {_STATE_COLUMNS} from rag_index_state where book_id = %s",
                (book_id,),
            ).fetchone()
        return _state_from_row(row) if row else None

    def reserve_generation(self, book_id: str, *, build_id: str | None = None) -> RagIndexState:
        """Atomically reserve the next monotonic generation for one book."""

        _validate_book_id(book_id)
        owner = (build_id or f"build_{uuid4().hex}").strip()
        if not owner:
            raise ValueError("build_id must not be empty")
        with self._connect() as conn:
            row = conn.execute(
                f"""
                insert into rag_index_state (
                  book_id, index_generation, build_id, state,
                  embedding_model, embedding_revision, chunk_version,
                  embedding_dimension, chunk_count, error,
                  reserved_at, committed_at, ready_at, deleted_at, updated_at
                ) values (%s, 1, %s, 'building', %s, %s, %s, %s, 0, null,
                          now(), null, null, null, now())
                on conflict (book_id) do update set
                  index_generation = rag_index_state.index_generation + 1,
                  build_id = excluded.build_id,
                  state = 'building',
                  embedding_model = excluded.embedding_model,
                  embedding_revision = excluded.embedding_revision,
                  chunk_version = excluded.chunk_version,
                  embedding_dimension = excluded.embedding_dimension,
                  chunk_count = 0,
                  error = null,
                  reserved_at = now(),
                  committed_at = null,
                  ready_at = null,
                  deleted_at = null,
                  updated_at = now()
                returning {_STATE_COLUMNS}
                """,
                (
                    book_id,
                    owner,
                    self.embedding_model,
                    self.embedding_revision,
                    self.chunk_version,
                    self.embedding_dimension,
                ),
            ).fetchone()
        if row is None:
            raise PgVectorIndexError(f"Unable to reserve generation for {book_id}")
        return _state_from_row(row)

    reserve = reserve_generation

    def replace_generation(
        self,
        book_id: str,
        generation: int,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
        *,
        build_id: str,
    ) -> RagIndexState:
        """CAS-replace a book in one transaction and stop at ``committed``."""

        _validate_book_id(book_id)
        generation = _validate_generation(generation)
        owner = _validate_build_id(build_id)
        try:
            indexed_pairs = self._validate_payload(book_id, chunks, embeddings)
        except Exception as exc:
            self._best_effort_mark_failed(book_id, generation, owner, str(exc))
            raise

        try:
            with self._connect() as conn:
                with conn.cursor() as cursor:
                    current = self._lock_state(cursor, book_id)
                    self._require_owned_state(current, generation, owner, {"building"})
                    self._require_compatible_state(current)
                    cursor.execute("delete from rag_chunk_vectors where book_id = %s", (book_id,))
                    self._insert_indexable_rows(cursor, book_id, generation, indexed_pairs)
                    cursor.execute(
                        """
                        select count(*)
                        from rag_chunk_vectors
                        where book_id = %s and index_generation = %s
                        """,
                        (book_id, generation),
                    )
                    actual_count = int(cursor.fetchone()[0])
                    expected_count = len(indexed_pairs)
                    if actual_count != expected_count:
                        raise IndexCountMismatchError(
                            f"Expected {expected_count} rows for {book_id}/{generation}, found {actual_count}"
                        )
                    cursor.execute(
                        f"""
                        update rag_index_state set
                          state = 'committed',
                          chunk_count = %s,
                          error = null,
                          committed_at = now(),
                          ready_at = null,
                          deleted_at = null,
                          updated_at = now()
                        where book_id = %s
                          and index_generation = %s
                          and build_id = %s
                          and state = 'building'
                        returning {_STATE_COLUMNS}
                        """,
                        (expected_count, book_id, generation, owner),
                    )
                    row = cursor.fetchone()
                    if row is None:
                        raise StaleGenerationError(
                            f"Generation ownership changed before commit: {book_id}/{generation}"
                        )
            return _state_from_row(row)
        except StaleGenerationError:
            raise
        except Exception as exc:
            self._best_effort_mark_failed(book_id, generation, owner, str(exc))
            raise

    replace = replace_generation

    def finalize_ready(
        self,
        book_id: str,
        generation: int,
        *,
        build_id: str,
    ) -> RagIndexState:
        """Publish a committed generation after all external caches are invalidated."""

        _validate_book_id(book_id)
        generation = _validate_generation(generation)
        owner = _validate_build_id(build_id)
        with self._connect() as conn:
            with conn.cursor() as cursor:
                current = self._lock_state(cursor, book_id)
                self._require_owned_state(current, generation, owner, {"committed", "cache_failed"})
                self._require_compatible_state(current)
                cursor.execute(
                    """
                    select count(*) from rag_chunk_vectors
                    where book_id = %s and index_generation = %s
                    """,
                    (book_id, generation),
                )
                actual_count = int(cursor.fetchone()[0])
                if actual_count != current.chunk_count:
                    raise IndexCountMismatchError(
                        f"Committed count changed for {book_id}/{generation}: "
                        f"state={current.chunk_count}, rows={actual_count}"
                    )
                cursor.execute(
                    f"""
                    update rag_index_state set
                      state = 'ready', error = null, ready_at = now(), updated_at = now()
                    where book_id = %s
                      and index_generation = %s
                      and build_id = %s
                      and state in ('committed', 'cache_failed')
                    returning {_STATE_COLUMNS}
                    """,
                    (book_id, generation, owner),
                )
                row = cursor.fetchone()
                if row is None:
                    raise StaleGenerationError(f"Unable to finalize stale generation {book_id}/{generation}")
        return _state_from_row(row)

    def mark_ready(self, book_id: str, generation: int, *, build_id: str) -> RagIndexState:
        return self.finalize_ready(book_id, generation, build_id=build_id)

    def mark_cache_failed(
        self,
        book_id: str,
        generation: int,
        *,
        build_id: str,
        error: str,
    ) -> RagIndexState:
        """Keep complete committed rows retryable while making them unsearchable."""

        return self._mark_state(
            book_id,
            generation,
            build_id=build_id,
            from_states={"committed", "cache_failed"},
            target_state="cache_failed",
            error=error,
        )

    def mark_failed(
        self,
        book_id: str,
        generation: int,
        *,
        build_id: str,
        error: str,
    ) -> RagIndexState:
        return self._mark_state(
            book_id,
            generation,
            build_id=build_id,
            from_states={"building"},
            target_state="failed",
            error=error,
        )

    fail_generation = mark_failed

    def upsert_chunks(
        self,
        book_id: str,
        chunks: list[Chunk],
        embeddings: list[list[float]] | None = None,
    ) -> RagIndexState | None:
        """Compatibility wrapper that reserves and commits, but never publishes.

        The returned state contains the generation/build_id required by the
        cache coordinator's later ``finalize_ready`` call.
        """

        if not self.available:
            return None
        if embeddings is None:
            raise InvalidIndexPayloadError("embeddings are required for pgvector replacement")
        reservation = self.reserve_generation(book_id)
        return self.replace_generation(
            book_id,
            reservation.index_generation,
            chunks,
            embeddings,
            build_id=reservation.build_id,
        )

    def search_vector(
        self,
        book_id: str,
        query_embedding: list[float],
        *,
        chapter_id: str | None = None,
        top_k: int = 80,
    ) -> list[VectorSearchResult]:
        _validate_book_id(book_id)
        self._validate_vector(query_embedding, label="query_embedding")
        if not self.available or top_k <= 0:
            return []
        vector = _vector_literal(query_embedding)
        results: list[VectorSearchResult] = []
        with self._connect() as conn:
            rows = conn.execute(
                """
                with query as (select %s::vector as embedding)
                select
                  c.chunk_id, c.book_id, c.chapter_id, c.page_start, c.page_end,
                  c.content_type, c.text, c.asset_ids, c.key_concepts,
                  c.parser, c.parser_version, c.chunk_version, c.heading_path,
                  c.source_block_ids, c.quality_score, c.token_count,
                  c.content_hash, c.bbox, c.metadata,
                  1 - (c.embedding <=> query.embedding) as dense_score
                from rag_chunk_vectors c
                join rag_index_state s
                  on s.book_id = c.book_id
                 and s.index_generation = c.index_generation
                cross join query
                where c.book_id = %s
                  and s.state = 'ready'
                  and s.embedding_model = %s
                  and s.embedding_revision = %s
                  and s.chunk_version = %s
                  and s.embedding_dimension = %s
                  and (%s::text is null or c.chapter_id = %s::text)
                order by c.embedding <=> query.embedding
                limit %s
                """,
                (
                    vector,
                    book_id,
                    self.embedding_model,
                    self.embedding_revision,
                    self.chunk_version,
                    self.embedding_dimension,
                    chapter_id,
                    chapter_id,
                    int(top_k),
                ),
            ).fetchall()
        for rank, row in enumerate(rows, start=1):
            results.append(
                VectorSearchResult(
                    chunk=_chunk_from_row(row),
                    dense_score=float(row[19] or 0.0),
                    rank=rank,
                )
            )
        return results

    def delete_book(self, book_id: str) -> RagIndexState | None:
        """Atomically remove vectors and advance a durable deletion tombstone."""

        _validate_book_id(book_id)
        if not self.available:
            return None
        owner = f"delete_{uuid4().hex}"
        with self._connect() as conn:
            with conn.cursor() as cursor:
                current = self._lock_state(cursor, book_id, required=False)
                generation = (current.index_generation + 1) if current else 1
                cursor.execute("delete from rag_chunk_vectors where book_id = %s", (book_id,))
                cursor.execute(
                    f"""
                    insert into rag_index_state (
                      book_id, index_generation, build_id, state,
                      embedding_model, embedding_revision, chunk_version,
                      embedding_dimension, chunk_count, error,
                      reserved_at, committed_at, ready_at, deleted_at, updated_at
                    ) values (%s, %s, %s, 'deleted', %s, %s, %s, %s, 0, null,
                              now(), null, null, now(), now())
                    on conflict (book_id) do update set
                      index_generation = excluded.index_generation,
                      build_id = excluded.build_id,
                      state = 'deleted',
                      embedding_model = excluded.embedding_model,
                      embedding_revision = excluded.embedding_revision,
                      chunk_version = excluded.chunk_version,
                      embedding_dimension = excluded.embedding_dimension,
                      chunk_count = 0,
                      error = null,
                      reserved_at = now(),
                      committed_at = null,
                      ready_at = null,
                      deleted_at = now(),
                      updated_at = now()
                    returning {_STATE_COLUMNS}
                    """,
                    (
                        book_id,
                        generation,
                        owner,
                        self.embedding_model,
                        self.embedding_revision,
                        self.chunk_version,
                        self.embedding_dimension,
                    ),
                )
                row = cursor.fetchone()
                if row is None:
                    raise PgVectorIndexError(f"Unable to persist tombstone for {book_id}")
        return _state_from_row(row)

    def _validate_payload(
        self,
        book_id: str,
        chunks: Sequence[Chunk],
        embeddings: Sequence[Sequence[float]],
    ) -> list[tuple[Chunk, Sequence[float]]]:
        if len(chunks) != len(embeddings):
            raise InvalidIndexPayloadError(
                f"chunks/embeddings length mismatch: {len(chunks)} != {len(embeddings)}"
            )
        seen: set[str] = set()
        indexed: list[tuple[Chunk, Sequence[float]]] = []
        for position, (chunk, embedding) in enumerate(zip(chunks, embeddings, strict=True)):
            if chunk.chunk_id in seen:
                raise InvalidIndexPayloadError(f"duplicate chunk_id: {chunk.chunk_id}")
            seen.add(chunk.chunk_id)
            if chunk.book_id != book_id:
                raise InvalidIndexPayloadError(
                    f"chunk {chunk.chunk_id} belongs to {chunk.book_id}, not {book_id}"
                )
            if chunk.chunk_version != self.chunk_version:
                raise InvalidIndexPayloadError(
                    f"chunk {chunk.chunk_id} version {chunk.chunk_version!r} is not {self.chunk_version!r}"
                )
            self._validate_vector(embedding, label=f"embeddings[{position}]")
            if is_chunk_indexable(chunk):
                try:
                    json.dumps(chunk.metadata, ensure_ascii=False)
                except (TypeError, ValueError) as exc:
                    raise InvalidIndexPayloadError(
                        f"chunk {chunk.chunk_id} metadata is not JSON serializable"
                    ) from exc
                indexed.append((chunk, embedding))
        return indexed

    def _validate_vector(self, vector: Sequence[float], *, label: str) -> None:
        if len(vector) != self.embedding_dimension:
            raise InvalidIndexPayloadError(
                f"{label} dimension must be {self.embedding_dimension}, found {len(vector)}"
            )
        for position, raw in enumerate(vector):
            if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not math.isfinite(float(raw)):
                raise InvalidIndexPayloadError(f"{label}[{position}] must be a finite number")

    def _insert_indexable_rows(
        self,
        cursor: Any,
        book_id: str,
        generation: int,
        pairs: Sequence[tuple[Chunk, Sequence[float]]],
    ) -> None:
        if not pairs:
            return
        from psycopg.types.json import Jsonb

        cursor.executemany(
            """
            insert into rag_chunk_vectors (
              book_id, index_generation, chunk_id, chapter_id, page_start, page_end,
              content_type, text, asset_ids, key_concepts, parser, parser_version,
              chunk_version, heading_path, source_block_ids, quality_score,
              token_count, content_hash, bbox, metadata, embedding
            ) values (
              %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
              %s, %s, %s, %s, %s, %s, %s, %s::vector
            )
            """,
            [
                (
                    book_id,
                    generation,
                    chunk.chunk_id,
                    chunk.chapter_id,
                    chunk.page_start,
                    chunk.page_end,
                    chunk.content_type,
                    chunk.text,
                    Jsonb(chunk.asset_ids),
                    Jsonb(chunk.key_concepts),
                    chunk.parser,
                    chunk.parser_version,
                    chunk.chunk_version,
                    Jsonb(chunk.heading_path),
                    Jsonb(chunk.source_block_ids),
                    chunk.quality_score,
                    chunk.token_count,
                    chunk.content_hash,
                    Jsonb(chunk.bbox) if chunk.bbox is not None else None,
                    Jsonb(chunk.metadata),
                    _vector_literal(embedding),
                )
                for chunk, embedding in pairs
            ],
        )

    def _lock_state(self, cursor: Any, book_id: str, *, required: bool = True) -> RagIndexState | None:
        cursor.execute(
            f"select {_STATE_COLUMNS} from rag_index_state where book_id = %s for update",
            (book_id,),
        )
        row = cursor.fetchone()
        if row is None:
            if required:
                raise StaleGenerationError(f"No reserved generation exists for {book_id}")
            return None
        return _state_from_row(row)

    def _require_owned_state(
        self,
        current: RagIndexState | None,
        generation: int,
        build_id: str,
        allowed_states: set[str],
    ) -> None:
        if current is None or current.index_generation != generation or current.build_id != build_id:
            raise StaleGenerationError(f"Stale generation owner for {current.book_id if current else '<missing>'}")
        if current.state not in allowed_states:
            raise InvalidGenerationStateError(
                f"Generation {current.book_id}/{generation} is {current.state}, expected {sorted(allowed_states)}"
            )

    def _require_compatible_state(self, current: RagIndexState | None) -> None:
        if current is None:
            raise StaleGenerationError("Missing generation state")
        observed = (
            current.embedding_model,
            current.embedding_revision,
            current.chunk_version,
            current.embedding_dimension,
        )
        expected = (
            self.embedding_model,
            self.embedding_revision,
            self.chunk_version,
            self.embedding_dimension,
        )
        if observed != expected:
            raise InvalidGenerationStateError(
                f"Generation protocol mismatch for {current.book_id}: {observed!r} != {expected!r}"
            )

    def _mark_state(
        self,
        book_id: str,
        generation: int,
        *,
        build_id: str,
        from_states: set[str],
        target_state: str,
        error: str,
    ) -> RagIndexState:
        _validate_book_id(book_id)
        generation = _validate_generation(generation)
        owner = _validate_build_id(build_id)
        message = str(error).strip()[:4000] or target_state
        with self._connect() as conn:
            with conn.cursor() as cursor:
                current = self._lock_state(cursor, book_id)
                self._require_owned_state(current, generation, owner, from_states)
                cursor.execute(
                    f"""
                    update rag_index_state set state = %s, error = %s, updated_at = now()
                    where book_id = %s and index_generation = %s and build_id = %s
                      and state = any(%s)
                    returning {_STATE_COLUMNS}
                    """,
                    (target_state, message, book_id, generation, owner, list(from_states)),
                )
                row = cursor.fetchone()
                if row is None:
                    raise StaleGenerationError(f"Unable to mark {book_id}/{generation} as {target_state}")
        return _state_from_row(row)

    def _best_effort_mark_failed(self, book_id: str, generation: int, build_id: str, error: str) -> None:
        try:
            self.mark_failed(book_id, generation, build_id=build_id, error=error)
        except Exception:
            # The original validation/transaction error remains authoritative.
            pass


def _state_from_row(row: Sequence[object]) -> RagIndexState:
    return RagIndexState(
        book_id=str(row[0]),
        index_generation=int(row[1]),
        build_id=str(row[2]),
        state=str(row[3]),
        embedding_model=str(row[4]),
        embedding_revision=str(row[5]),
        chunk_version=str(row[6]),
        embedding_dimension=int(row[7]),
        chunk_count=int(row[8]),
        error=str(row[9]) if row[9] is not None else None,
        reserved_at=row[10],
        committed_at=row[11],
        ready_at=row[12],
        deleted_at=row[13],
        updated_at=row[14],
    )


def _chunk_from_row(row: Sequence[object]) -> Chunk:
    return Chunk(
        chunk_id=str(row[0]),
        book_id=str(row[1]),
        chapter_id=str(row[2]),
        page_start=int(row[3]),
        page_end=int(row[4]),
        content_type=str(row[5]),
        text=str(row[6]),
        asset_ids=_json_list(row[7]),
        key_concepts=_json_list(row[8]),
        parser=str(row[9]) if row[9] is not None else None,
        parser_version=str(row[10]) if row[10] is not None else None,
        chunk_version=str(row[11]) if row[11] is not None else None,
        heading_path=_json_list(row[12]),
        source_block_ids=_json_list(row[13]),
        quality_score=float(row[14]) if row[14] is not None else None,
        token_count=int(row[15]) if row[15] is not None else None,
        content_hash=str(row[16]) if row[16] is not None else None,
        bbox=[float(value) for value in _json_list(row[17])] if row[17] is not None else None,
        metadata=_json_dict(row[18]),
    )


def _json_list(value: object) -> list[Any]:
    if isinstance(value, str):
        value = json.loads(value)
    return list(value) if isinstance(value, list) else []


def _json_dict(value: object) -> dict[str, object]:
    if isinstance(value, str):
        value = json.loads(value)
    return dict(value) if isinstance(value, dict) else {}


def _validate_book_id(book_id: str) -> None:
    if not isinstance(book_id, str) or not book_id.strip():
        raise ValueError("book_id must not be empty")


def _validate_generation(generation: int) -> int:
    if isinstance(generation, bool) or not isinstance(generation, int) or generation <= 0:
        raise ValueError("generation must be a positive integer")
    return generation


def _validate_build_id(build_id: str) -> str:
    if not isinstance(build_id, str) or not build_id.strip():
        raise ValueError("build_id must not be empty")
    return build_id.strip()


def _vector_literal(vector: Iterable[float]) -> str:
    values = [float(value) for value in vector]
    if any(not math.isfinite(value) for value in values):
        raise InvalidIndexPayloadError("vector values must be finite")
    return "[" + ",".join(repr(value) for value in values) + "]"


def _json_literal(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


PGVECTOR_SCHEMA_SQL = MIGRATION_SQL_PATH.read_text(encoding="utf-8")


def is_pgvector_index(index: RagIndex) -> bool:
    return getattr(index, "name", "") == PgVectorIndex.name
