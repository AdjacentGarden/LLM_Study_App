from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import tempfile
import threading
from typing import Any, Callable, Mapping, Sequence

from app.core.config import get_settings
from app.document.chunk_protocol import is_chunk_indexable
from app.rag.cache import invalidate_book
from app.rag.embedding import EmbeddingDescriptor, EmbeddingService, get_embedding_service, render_chunk_embedding_text
from app.rag.index_pgvector import PgVectorIndex, RagIndexState
from app.schemas.books import Chunk
from app.services.artifact_store import RagBundleIdentity, get_rag_bundle_identity
from app.services.storage import resolve_under_root


STATUS_FILENAME = "rag_index_status.json"
STATUS_SCHEMA_VERSION = 1
_BOOK_LOCKS_GUARD = threading.Lock()
_BOOK_LOCKS: dict[str, threading.RLock] = {}
_DSN_RE = re.compile(r"(?i)postgres(?:ql)?://[^\s]+")
_PASSWORD_RE = re.compile(r"(?i)(password\s*=\s*)[^\s;]+")


class RagIndexCoordinatorError(RuntimeError):
    pass


class RagIndexStaleBuildError(RagIndexCoordinatorError):
    pass


class RagIndexPublishError(RagIndexCoordinatorError):
    pass


@dataclass(frozen=True, slots=True)
class RagIndexStatus:
    book_id: str
    status: str
    configured_provider: str
    active_provider: str
    index_generation: int | None
    build_id: str | None
    cause: str
    fallback_reason: str | None
    embedding: EmbeddingDescriptor
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = False
    timestamps: dict[str, str | None] = field(default_factory=dict)
    schema_version: int = STATUS_SCHEMA_VERSION

    def as_dict(self) -> dict[str, object]:
        payload = asdict(self)
        payload["embedding"] = self.embedding.as_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> RagIndexStatus:
        raw_embedding = payload.get("embedding")
        if not isinstance(raw_embedding, Mapping):
            raise ValueError("rag index status is missing embedding descriptor")
        descriptor = EmbeddingDescriptor(
            provider=str(raw_embedding.get("provider") or "unknown"),
            model=str(raw_embedding.get("model") or "unknown"),
            revision=str(raw_embedding.get("revision") or "unknown"),
            version=str(raw_embedding.get("version") or "unknown"),
            dimension=int(raw_embedding.get("dimension") or 0),
            device=str(raw_embedding.get("device") or "unknown"),
        )
        raw_counts = payload.get("counts")
        raw_timestamps = payload.get("timestamps")
        return cls(
            book_id=str(payload.get("book_id") or ""),
            status=str(payload.get("status") or "missing"),
            configured_provider=str(payload.get("configured_provider") or "unknown"),
            active_provider=str(payload.get("active_provider") or "unknown"),
            index_generation=(
                int(payload["index_generation"]) if payload.get("index_generation") is not None else None
            ),
            build_id=str(payload.get("build_id")) if payload.get("build_id") is not None else None,
            cause=str(payload.get("cause") or "unknown"),
            fallback_reason=(
                str(payload.get("fallback_reason")) if payload.get("fallback_reason") is not None else None
            ),
            embedding=descriptor,
            counts={str(key): int(value) for key, value in raw_counts.items()}
            if isinstance(raw_counts, Mapping)
            else {},
            error=_sanitize_error(payload.get("error")) if payload.get("error") else None,
            retryable=bool(payload.get("retryable", False)),
            timestamps={str(key): str(value) if value is not None else None for key, value in raw_timestamps.items()}
            if isinstance(raw_timestamps, Mapping)
            else {},
            schema_version=int(payload.get("schema_version") or STATUS_SCHEMA_VERSION),
        )


@dataclass(frozen=True, slots=True)
class RagIndexBuild:
    book_id: str
    build_id: str
    cause: str
    configured_provider: str
    active_provider: str
    index_generation: int | None
    embedding: EmbeddingDescriptor
    fallback_reason: str | None = None
    _index: Any = field(default=None, repr=False, compare=False)
    _embedding_service: Any = field(default=None, repr=False, compare=False)
    _cache_invalidator: Callable[[str], None] = field(default=invalidate_book, repr=False, compare=False)
    _identity_reader: Callable[[str], RagBundleIdentity] = field(
        default=get_rag_bundle_identity,
        repr=False,
        compare=False,
    )


@dataclass(frozen=True, slots=True)
class RagPublishReceipt:
    book_id: str
    build_id: str
    index_generation: int | None
    status: str
    active_provider: str
    embedding: EmbeddingDescriptor
    counts: dict[str, int]
    published_at: str


def read_index_status(
    book_id: str,
    *,
    settings: object | None = None,
    index: Any | None = None,
) -> RagIndexStatus | None:
    """Read status with a compatible database state taking precedence."""

    _validate_identity(book_id, "book_id")
    local = _read_local_status(book_id)
    configured = settings or get_settings()
    provider = _provider(configured)
    database_url = getattr(configured, "database_url", None)
    if provider != "pgvector" or not database_url:
        return local
    descriptor = local.embedding if local else _configured_descriptor(configured)
    pg_index = index or _make_index(configured, descriptor)
    try:
        state = pg_index.get_state(book_id)
    except Exception:
        return local
    if state is None:
        return local
    return _status_from_db(state, local=local, configured_provider=provider, descriptor=descriptor)


def reserve_index_build(
    book_id: str,
    build_id: str,
    cause: str,
    *,
    settings: object | None = None,
    index: Any | None = None,
    embedding_service: EmbeddingService | None = None,
    cache_invalidator: Callable[[str], None] = invalidate_book,
    identity_reader: Callable[[str], RagBundleIdentity] = get_rag_bundle_identity,
) -> RagIndexBuild:
    """Reserve an index generation tied to an in-progress artifact build."""

    _validate_identity(book_id, "book_id")
    _validate_identity(build_id, "build_id")
    cause = str(cause).strip() or "unspecified"
    _require_bundle_identity(identity_reader(book_id), state="building", build_id=build_id)
    configured = settings or get_settings()
    configured_provider = _provider(configured)
    descriptor = _configured_descriptor(configured)
    now = _now()
    fallback_reason = _fallback_reason(configured_provider, getattr(configured, "database_url", None))

    if fallback_reason is not None:
        status = RagIndexStatus(
            book_id=book_id,
            status="building",
            configured_provider=configured_provider,
            active_provider="artifact_fallback",
            index_generation=None,
            build_id=build_id,
            cause=cause,
            fallback_reason=fallback_reason,
            embedding=descriptor,
            counts=_empty_counts(),
            timestamps={"reserved_at": now, "updated_at": now},
        )
        _write_status(status)
        return RagIndexBuild(
            book_id=book_id,
            build_id=build_id,
            cause=cause,
            configured_provider=configured_provider,
            active_provider="artifact_fallback",
            index_generation=None,
            embedding=descriptor,
            fallback_reason=fallback_reason,
            _cache_invalidator=cache_invalidator,
            _identity_reader=identity_reader,
        )

    service: EmbeddingService | None = None
    pg_index: Any | None = None
    reserved: Any | None = None
    try:
        service = embedding_service or get_embedding_service()
        _require_descriptor(service.descriptor, descriptor)
        pg_index = index or _make_index(configured, descriptor)
        reserved = pg_index.reserve_generation(book_id, build_id=build_id)
        if reserved.build_id != build_id:
            raise RagIndexStaleBuildError("database returned a different build owner")
        current = pg_index.get_state(book_id)
        if current is None or current.index_generation != reserved.index_generation or current.build_id != build_id:
            raise RagIndexStaleBuildError("database generation was superseded during reservation")
        status = RagIndexStatus(
            book_id=book_id,
            status="building",
            configured_provider=configured_provider,
            active_provider="pgvector",
            index_generation=reserved.index_generation,
            build_id=build_id,
            cause=cause,
            fallback_reason=None,
            embedding=descriptor,
            counts=_empty_counts(),
            timestamps={"reserved_at": _iso(reserved.reserved_at), "updated_at": now},
        )
        _write_status(status)
        return RagIndexBuild(
            book_id=book_id,
            build_id=build_id,
            cause=cause,
            configured_provider=configured_provider,
            active_provider="pgvector",
            index_generation=reserved.index_generation,
            embedding=descriptor,
            _index=pg_index,
            _embedding_service=service,
            _cache_invalidator=cache_invalidator,
            _identity_reader=identity_reader,
        )
    except Exception as exc:
        failure_reason = _failure_reason(exc)
        if pg_index is not None and reserved is not None:
            try:
                pg_index.mark_failed(
                    book_id,
                    reserved.index_generation,
                    build_id=build_id,
                    error=_sanitize_error(exc),
                )
            except Exception:
                pass
        failed = RagIndexStatus(
            book_id=book_id,
            status="failed",
            configured_provider=configured_provider,
            active_provider="pgvector",
            index_generation=getattr(reserved, "index_generation", None),
            build_id=build_id,
            cause=cause,
            fallback_reason=failure_reason,
            embedding=descriptor,
            counts=_empty_counts(),
            error=failure_reason or _sanitize_error(exc),
            retryable=not isinstance(exc, (ValueError, RagIndexStaleBuildError)),
            timestamps={"failed_at": _now(), "updated_at": _now()},
        )
        _write_status(failed, expected_build_id=build_id)
        raise RagIndexCoordinatorError(failure_reason or _public_error(exc)) from exc


def publish_index_build(reservation: RagIndexBuild, chunks: Sequence[Chunk]) -> RagPublishReceipt:
    """Embed and publish one reserved generation without any silent fallback."""

    _assert_reservation_current(reservation)
    _require_bundle_identity(
        reservation._identity_reader(reservation.book_id),
        state="ready",
        build_id=reservation.build_id,
    )
    indexable = [chunk for chunk in chunks if is_chunk_indexable(chunk)]
    texts = [render_chunk_embedding_text(chunk) for chunk in indexable]
    counts = {
        "input_chunks": len(chunks),
        "indexable_chunks": len(indexable),
        "canonical_texts": len(texts),
        "embeddings": 0,
        "indexed_chunks": 0,
    }

    if reservation.active_provider == "artifact_fallback":
        try:
            reservation._cache_invalidator(reservation.book_id)
        except Exception as exc:
            _persist_transition(
                reservation,
                "cache_failed",
                counts,
                error=exc,
                retryable=True,
                timestamp_name="cache_failed_at",
            )
            raise RagIndexPublishError(_public_error(exc)) from exc
        _require_bundle_identity(
            reservation._identity_reader(reservation.book_id),
            state="ready",
            build_id=reservation.build_id,
        )
        ready = _persist_transition(
            reservation,
            "ready",
            {**counts, "indexed_chunks": len(indexable)},
            timestamp_name="ready_at",
        )
        return _receipt(ready)

    if reservation.index_generation is None or reservation._index is None or reservation._embedding_service is None:
        raise RagIndexPublishError("pgvector reservation is incomplete")

    try:
        _require_descriptor(reservation._embedding_service.descriptor, reservation.embedding)
        embeddings = reservation._embedding_service.embed_documents(texts)
        _validate_embeddings(embeddings, expected_count=len(indexable), descriptor=reservation.embedding)
        counts["embeddings"] = len(embeddings)
        committed = reservation._index.replace_generation(
            reservation.book_id,
            reservation.index_generation,
            indexable,
            embeddings,
            build_id=reservation.build_id,
        )
        counts["indexed_chunks"] = committed.chunk_count
        _persist_transition(
            reservation,
            "committed",
            counts,
            timestamp_name="committed_at",
        )
    except Exception as exc:
        _mark_database_failed(reservation, exc)
        _persist_transition(
            reservation,
            "failed",
            counts,
            error=exc,
            retryable=not isinstance(exc, ValueError),
            timestamp_name="failed_at",
        )
        raise RagIndexPublishError(_public_error(exc)) from exc

    try:
        reservation._cache_invalidator(reservation.book_id)
    except Exception as exc:
        try:
            reservation._index.mark_cache_failed(
                reservation.book_id,
                reservation.index_generation,
                build_id=reservation.build_id,
                error=_sanitize_error(exc),
            )
        finally:
            _persist_transition(
                reservation,
                "cache_failed",
                counts,
                error=exc,
                retryable=True,
                timestamp_name="cache_failed_at",
            )
        raise RagIndexPublishError(_public_error(exc)) from exc

    # Artifact identity is re-read after embedding, DB commit, and cache work.
    # A newer artifact generation may never be published under this index CAS.
    _require_bundle_identity(
        reservation._identity_reader(reservation.book_id),
        state="ready",
        build_id=reservation.build_id,
    )
    try:
        state = reservation._index.finalize_ready(
            reservation.book_id,
            reservation.index_generation,
            build_id=reservation.build_id,
        )
        counts["indexed_chunks"] = state.chunk_count
    except Exception as exc:
        _persist_transition(
            reservation,
            "failed",
            counts,
            error=exc,
            retryable=True,
            timestamp_name="failed_at",
        )
        raise RagIndexPublishError(_public_error(exc)) from exc
    ready = _persist_transition(reservation, "ready", counts, timestamp_name="ready_at")
    return _receipt(ready)


def fail_index_build(reservation: RagIndexBuild, error: object) -> bool:
    """Fail a still-building reservation without overwriting a newer build."""

    local = _read_local_status(reservation.book_id)
    if (
        local is None
        or local.status != "building"
        or local.build_id != reservation.build_id
        or local.index_generation != reservation.index_generation
    ):
        return False
    if reservation.active_provider == "pgvector" and reservation._index is not None and reservation.index_generation is not None:
        try:
            reservation._index.mark_failed(
                reservation.book_id,
                reservation.index_generation,
                build_id=reservation.build_id,
                error=_sanitize_error(error),
            )
        except Exception:
            # The DB CAS may already have been superseded.  The local CAS below
            # still prevents this worker from changing a newer status file.
            pass
    failed = _replace_status(
        local,
        status="failed",
        error=_sanitize_error(error),
        retryable=True,
        timestamp_name="failed_at",
    )
    return _write_status(
        failed,
        expected_build_id=reservation.build_id,
        expected_generation=reservation.index_generation,
    )


def fail_current_index_build(
    book_id: str,
    error: object,
    *,
    expected_cause: str | None = None,
) -> bool:
    """Best-effort job-boundary cleanup for a parse that failed mid-build."""

    local = _read_local_status(book_id)
    if (
        local is None
        or local.status != "building"
        or local.build_id is None
        or (expected_cause is not None and local.cause != expected_cause)
    ):
        return False
    if local.active_provider == "pgvector" and local.index_generation is not None:
        try:
            configured = get_settings()
            _make_index(configured, local.embedding).mark_failed(
                book_id,
                local.index_generation,
                build_id=local.build_id,
                error=_sanitize_error(error),
            )
        except Exception:
            pass
    failed = _replace_status(
        local,
        status="failed",
        error=_sanitize_error(error),
        retryable=True,
        timestamp_name="failed_at",
    )
    return _write_status(
        failed,
        expected_build_id=local.build_id,
        expected_generation=local.index_generation,
    )


def retry_finalize(
    book_id: str,
    generation: int,
    build_id: str,
    *,
    settings: object | None = None,
    index: Any | None = None,
    cache_invalidator: Callable[[str], None] = invalidate_book,
    identity_reader: Callable[[str], RagBundleIdentity] = get_rag_bundle_identity,
) -> RagPublishReceipt:
    """Retry cache invalidation and the committed/cache_failed -> ready CAS."""

    local = _read_local_status(book_id)
    if local is None or local.build_id != build_id or local.index_generation != generation:
        raise RagIndexStaleBuildError("local index reservation does not match retry request")
    if local.active_provider != "pgvector":
        raise RagIndexCoordinatorError("retry_finalize requires a pgvector generation")
    _require_bundle_identity(identity_reader(book_id), state="ready", build_id=build_id)
    configured = settings or get_settings()
    if _provider(configured) != "pgvector" or not getattr(configured, "database_url", None):
        raise RagIndexCoordinatorError("configured pgvector database is unavailable")
    pg_index = index or _make_index(configured, local.embedding)
    try:
        cache_invalidator(book_id)
    except Exception as exc:
        try:
            pg_index.mark_cache_failed(book_id, generation, build_id=build_id, error=_sanitize_error(exc))
        finally:
            failed = _replace_status(
                local,
                status="cache_failed",
                error=_sanitize_error(exc),
                retryable=True,
                timestamp_name="cache_failed_at",
            )
            _write_status(failed, expected_build_id=build_id, expected_generation=generation)
        raise RagIndexPublishError(_public_error(exc)) from exc
    _require_bundle_identity(identity_reader(book_id), state="ready", build_id=build_id)
    try:
        state = pg_index.finalize_ready(book_id, generation, build_id=build_id)
    except Exception as exc:
        failed = _replace_status(
            local,
            status="failed",
            error=_sanitize_error(exc),
            retryable=True,
            timestamp_name="failed_at",
        )
        _write_status(failed, expected_build_id=build_id, expected_generation=generation)
        raise RagIndexPublishError(_public_error(exc)) from exc
    ready = _replace_status(
        local,
        status="ready",
        counts={**local.counts, "indexed_chunks": state.chunk_count},
        error=None,
        retryable=False,
        timestamp_name="ready_at",
    )
    _write_status(ready, expected_build_id=build_id, expected_generation=generation)
    return _receipt(ready)


def delete_book_index(
    book_id: str,
    *,
    settings: object | None = None,
    index: Any | None = None,
    cache_invalidator: Callable[[str], None] = invalidate_book,
) -> RagIndexStatus:
    """Delete the configured index, preserving a pgvector generation tombstone."""

    _validate_identity(book_id, "book_id")
    configured = settings or get_settings()
    provider = _provider(configured)
    descriptor = _configured_descriptor(configured)
    fallback_reason = _fallback_reason(provider, getattr(configured, "database_url", None))
    now = _now()
    if fallback_reason is not None:
        try:
            cache_invalidator(book_id)
        except Exception as exc:
            status = RagIndexStatus(
                book_id=book_id,
                status="cache_failed",
                configured_provider=provider,
                active_provider="artifact_fallback",
                index_generation=None,
                build_id=None,
                cause="delete",
                fallback_reason=fallback_reason,
                embedding=descriptor,
                counts=_empty_counts(),
                error=_sanitize_error(exc),
                retryable=True,
                timestamps={"cache_failed_at": now, "updated_at": now},
            )
            _write_status(status)
            raise RagIndexCoordinatorError(_public_error(exc)) from exc
        status = RagIndexStatus(
            book_id=book_id,
            status="deleted",
            configured_provider=provider,
            active_provider="artifact_fallback",
            index_generation=None,
            build_id=None,
            cause="delete",
            fallback_reason=fallback_reason,
            embedding=descriptor,
            counts=_empty_counts(),
            timestamps={"deleted_at": now, "updated_at": now},
        )
        _write_status(status)
        return status

    pg_index = index or _make_index(configured, descriptor)
    try:
        tombstone = pg_index.delete_book(book_id)
        if tombstone is None:
            raise RagIndexCoordinatorError("pgvector delete did not return a tombstone")
    except Exception as exc:
        failure_reason = _failure_reason(exc)
        status = RagIndexStatus(
            book_id=book_id,
            status="failed",
            configured_provider=provider,
            active_provider="pgvector",
            index_generation=None,
            build_id=None,
            cause="delete",
            fallback_reason=failure_reason,
            embedding=descriptor,
            counts=_empty_counts(),
            error=failure_reason or _sanitize_error(exc),
            retryable=True,
            timestamps={"failed_at": now, "updated_at": now},
        )
        _write_status(status)
        raise RagIndexCoordinatorError(failure_reason or _public_error(exc)) from exc
    try:
        cache_invalidator(book_id)
    except Exception as exc:
        status = RagIndexStatus(
            book_id=book_id,
            status="cache_failed",
            configured_provider=provider,
            active_provider="pgvector",
            index_generation=tombstone.index_generation,
            build_id=tombstone.build_id,
            cause="delete",
            fallback_reason=None,
            embedding=descriptor,
            counts=_empty_counts(),
            error=_sanitize_error(exc),
            retryable=True,
            timestamps={"deleted_at": _iso(tombstone.deleted_at), "cache_failed_at": now, "updated_at": now},
        )
        _write_status(status)
        raise RagIndexCoordinatorError(_public_error(exc)) from exc
    status = RagIndexStatus(
        book_id=book_id,
        status="deleted",
        configured_provider=provider,
        active_provider="pgvector",
        index_generation=tombstone.index_generation,
        build_id=tombstone.build_id,
        cause="delete",
        fallback_reason=None,
        embedding=descriptor,
        counts=_empty_counts(),
        timestamps={"deleted_at": _iso(tombstone.deleted_at), "updated_at": now},
    )
    _write_status(status)
    return status


def _provider(settings: object) -> str:
    return str(getattr(settings, "rag_index_provider", "artifact")).strip().lower() or "artifact"


def _fallback_reason(provider: str, database_url: object) -> str | None:
    if provider != "pgvector":
        return f"configured_provider_{provider}"
    if not str(database_url or "").strip():
        return "pgvector_dsn_missing"
    return None


def _configured_descriptor(settings: object) -> EmbeddingDescriptor:
    provider = str(getattr(settings, "embedding_provider", "hashing")).strip().lower() or "hashing"
    dimension = int(getattr(settings, "embedding_dimensions", 1024))
    version = str(getattr(settings, "embedding_version", "v1")).strip() or "v1"
    device = str(getattr(settings, "embedding_device", "cpu")).strip() or "cpu"
    if provider in {"bge_m3", "bge-m3"}:
        return EmbeddingDescriptor(
            provider="bge_m3",
            model=str(getattr(settings, "bge_m3_model", "BAAI/bge-m3")),
            revision=str(getattr(settings, "bge_m3_revision", "unknown")),
            version=version,
            dimension=dimension,
            device=device,
        )
    return EmbeddingDescriptor(
        provider="hashing",
        model="sha1-feature-hashing",
        revision="algorithm-v1",
        version=version,
        dimension=dimension,
        device="cpu",
    )


def _make_index(settings: object, descriptor: EmbeddingDescriptor) -> PgVectorIndex:
    return PgVectorIndex(
        getattr(settings, "database_url", None),
        embedding_model=descriptor.model,
        embedding_revision=descriptor.revision,
        chunk_version=str(getattr(settings, "chunk_version", "v2")),
        embedding_dimension=descriptor.dimension,
    )


def _require_descriptor(actual: EmbeddingDescriptor, expected: EmbeddingDescriptor) -> None:
    if actual != expected:
        raise ValueError(f"embedding descriptor mismatch: {actual.as_dict()!r} != {expected.as_dict()!r}")
    if actual.dimension <= 0:
        raise ValueError("embedding descriptor dimension must be positive")


def _validate_embeddings(
    embeddings: object,
    *,
    expected_count: int,
    descriptor: EmbeddingDescriptor,
) -> None:
    if not isinstance(embeddings, list) or len(embeddings) != expected_count:
        raise ValueError(
            f"embedding count mismatch: expected {expected_count}, found "
            f"{len(embeddings) if isinstance(embeddings, list) else 'non-list'}"
        )
    for row_index, vector in enumerate(embeddings):
        if not isinstance(vector, list) or len(vector) != descriptor.dimension:
            raise ValueError(
                f"embedding[{row_index}] dimension must be {descriptor.dimension}, "
                f"found {len(vector) if isinstance(vector, list) else 'non-list'}"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            for value in vector
        ):
            raise ValueError(f"embedding[{row_index}] contains a non-finite value")


def _require_bundle_identity(identity: RagBundleIdentity, *, state: str, build_id: str) -> None:
    if identity.state != state or identity.build_id != build_id:
        raise RagIndexStaleBuildError(
            f"artifact build identity is {identity.state}/{identity.build_id or '<none>'}; "
            f"expected {state}/{build_id}"
        )


def _assert_reservation_current(reservation: RagIndexBuild) -> None:
    local = _read_local_status(reservation.book_id)
    if (
        local is None
        or local.build_id != reservation.build_id
        or local.index_generation != reservation.index_generation
        or local.status != "building"
    ):
        raise RagIndexStaleBuildError("index reservation is no longer current")


def _mark_database_failed(reservation: RagIndexBuild, exc: Exception) -> None:
    if reservation._index is None or reservation.index_generation is None:
        return
    try:
        reservation._index.mark_failed(
            reservation.book_id,
            reservation.index_generation,
            build_id=reservation.build_id,
            error=_sanitize_error(exc),
        )
    except Exception:
        pass


def _persist_transition(
    reservation: RagIndexBuild,
    status_name: str,
    counts: dict[str, int],
    *,
    error: object | None = None,
    retryable: bool = False,
    timestamp_name: str,
) -> RagIndexStatus:
    current = _read_local_status(reservation.book_id)
    if current is None:
        raise RagIndexStaleBuildError("local index status is missing")
    updated = _replace_status(
        current,
        status=status_name,
        counts=counts,
        error=_sanitize_error(error) if error else None,
        retryable=retryable,
        timestamp_name=timestamp_name,
    )
    if not _write_status(
        updated,
        expected_build_id=reservation.build_id,
        expected_generation=reservation.index_generation,
    ):
        raise RagIndexStaleBuildError("local index status was superseded")
    return updated


def _replace_status(
    current: RagIndexStatus,
    *,
    status: str,
    counts: dict[str, int] | None = None,
    error: str | None,
    retryable: bool,
    timestamp_name: str,
) -> RagIndexStatus:
    timestamps = dict(current.timestamps)
    timestamps[timestamp_name] = _now()
    timestamps["updated_at"] = _now()
    return RagIndexStatus(
        book_id=current.book_id,
        status=status,
        configured_provider=current.configured_provider,
        active_provider=current.active_provider,
        index_generation=current.index_generation,
        build_id=current.build_id,
        cause=current.cause,
        fallback_reason=current.fallback_reason,
        embedding=current.embedding,
        counts=dict(counts if counts is not None else current.counts),
        error=error,
        retryable=retryable,
        timestamps=timestamps,
    )


def _status_from_db(
    state: RagIndexState,
    *,
    local: RagIndexStatus | None,
    configured_provider: str,
    descriptor: EmbeddingDescriptor,
) -> RagIndexStatus:
    descriptor = EmbeddingDescriptor(
        provider=descriptor.provider,
        model=state.embedding_model,
        revision=state.embedding_revision,
        version=descriptor.version,
        dimension=state.embedding_dimension,
        device=descriptor.device,
    )
    timestamps = dict(local.timestamps) if local else {}
    for name, value in (
        ("reserved_at", state.reserved_at),
        ("committed_at", state.committed_at),
        ("ready_at", state.ready_at),
        ("deleted_at", state.deleted_at),
        ("updated_at", state.updated_at),
    ):
        if value is not None:
            timestamps[name] = _iso(value)
    counts = dict(local.counts) if local else _empty_counts()
    counts["indexed_chunks"] = state.chunk_count
    return RagIndexStatus(
        book_id=state.book_id,
        status=state.state,
        configured_provider=configured_provider,
        active_provider="pgvector",
        index_generation=state.index_generation,
        build_id=state.build_id,
        cause=local.cause if local else "database_state",
        fallback_reason=None,
        embedding=descriptor,
        counts=counts,
        error=_sanitize_error(state.error) if state.error else None,
        retryable=state.state in {"committed", "cache_failed", "failed"},
        timestamps=timestamps,
    )


def _receipt(status: RagIndexStatus) -> RagPublishReceipt:
    return RagPublishReceipt(
        book_id=status.book_id,
        build_id=status.build_id or "",
        index_generation=status.index_generation,
        status=status.status,
        active_provider=status.active_provider,
        embedding=status.embedding,
        counts=dict(status.counts),
        published_at=status.timestamps.get("ready_at") or status.timestamps.get("updated_at") or _now(),
    )


def _empty_counts() -> dict[str, int]:
    return {
        "input_chunks": 0,
        "indexable_chunks": 0,
        "canonical_texts": 0,
        "embeddings": 0,
        "indexed_chunks": 0,
    }


def _status_path(book_id: str) -> Path:
    return resolve_under_root("books", book_id, "artifacts", STATUS_FILENAME)


def _read_local_status(book_id: str) -> RagIndexStatus | None:
    path = _status_path(book_id)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return None
        status = RagIndexStatus.from_dict(payload)
        return status if status.book_id == book_id else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _write_status(
    status: RagIndexStatus,
    *,
    expected_build_id: str | None = None,
    expected_generation: int | None = None,
) -> bool:
    with _book_lock(status.book_id):
        if expected_build_id is not None:
            current = _read_local_status(status.book_id)
            if current is None or current.build_id != expected_build_id:
                return False
            if expected_generation is not None and current.index_generation != expected_generation:
                return False
        path = _status_path(status.book_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary = Path(handle.name)
                json.dump(status.as_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return True


def _book_lock(book_id: str) -> threading.RLock:
    with _BOOK_LOCKS_GUARD:
        return _BOOK_LOCKS.setdefault(book_id, threading.RLock())


def _sanitize_error(value: object) -> str:
    text = str(value or "").strip()
    text = _DSN_RE.sub("postgresql://<redacted>", text)
    text = _PASSWORD_RE.sub(r"\1<redacted>", text)
    return text[:1000]


def _public_error(exc: object) -> str:
    return f"{type(exc).__name__}: {_sanitize_error(exc)}"


def _failure_reason(exc: object) -> str | None:
    sqlstate = str(getattr(exc, "sqlstate", "") or "")
    message = str(exc).casefold()
    if sqlstate in {"42p01", "42703"} or (
        "does not exist" in message and ("rag_index_state" in message or "rag_chunk_vectors" in message)
    ):
        return "migration_required"
    return None


def _validate_identity(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must not be empty")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _iso(value: object) -> str:
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return str(value)
