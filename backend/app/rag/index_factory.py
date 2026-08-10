from __future__ import annotations

from importlib import import_module
from typing import Any, Hashable

from app.core.config import get_settings
from app.rag.embedding import EmbeddingService
from app.rag.index_base import ArtifactVectorIndex, RagIndex
from app.rag.index_chroma import ChromaIndex
from app.rag.index_faiss import FaissIndex
from app.rag.index_milvus import MilvusIndex
from app.rag.index_pgvector import PgVectorIndex


_STATUS_NOT_PROVIDED = object()


def read_query_index_status(book_id: str) -> tuple[Any | None, str | None]:
    """Read the Stage-4 index status without creating an import cycle.

    ``(None, None)`` is the explicit legacy contract: an older deployment has
    no status module/reader yet, so callers use ``index_generation=None``.
    Other failures are returned as a redacted reason and must fail closed to
    the canonical artifact index.
    """

    try:
        module = import_module("app.rag.indexing")
    except ModuleNotFoundError as exc:
        if exc.name == "app.rag.indexing":
            return None, None
        return None, f"index_status_import_failed:{type(exc).__name__}"
    except Exception as exc:
        return None, f"index_status_import_failed:{type(exc).__name__}"
    reader = getattr(module, "read_index_status", None)
    if not callable(reader):
        return None, None
    try:
        return reader(book_id), None
    except FileNotFoundError:
        return None, None
    except Exception as exc:
        return None, f"index_status_read_failed:{type(exc).__name__}"


def index_status_value(status: Any | None, field: str, default: Any = None) -> Any:
    if status is None:
        return default
    if isinstance(status, dict):
        return status.get(field, default)
    return getattr(status, field, default)


def index_generation_from_status(status: Any | None) -> Hashable | None:
    generation = index_status_value(status, "index_generation")
    if generation is None:
        return None
    try:
        hash(generation)
        return generation
    except TypeError:
        return repr(generation)


def embedding_descriptors_compatible(status: Any | None, embedding_service: EmbeddingService) -> bool:
    """Return whether persisted vector metadata matches the query embedder.

    Missing metadata is treated as a legacy status. Once any descriptor field
    is present, every supplied identity field must match.
    """

    actual = index_status_value(status, "embedding")
    if actual is None:
        actual = index_status_value(status, "embedding_descriptor")
    if actual is None:
        return True
    expected = getattr(embedding_service, "descriptor", None)
    if expected is None:
        return False
    aliases = {
        "provider": ("provider",),
        "model": ("model", "model_name"),
        "revision": ("revision",),
        "version": ("version",),
        "dimension": ("dimension", "dimensions"),
    }
    compared = False
    for expected_name, actual_names in aliases.items():
        actual_value = None
        for name in actual_names:
            actual_value = index_status_value(actual, name)
            if actual_value is not None:
                break
        if actual_value is None:
            continue
        compared = True
        expected_value = getattr(expected, expected_name, None)
        if expected_name == "dimension":
            try:
                if int(actual_value) != int(expected_value):
                    return False
            except (TypeError, ValueError):
                return False
        elif str(actual_value) != str(expected_value):
            return False
    return True if not compared else compared


def _artifact_fallback(
    embedding_service: EmbeddingService,
    *,
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


def _provider_index(provider: str, embedding_service: EmbeddingService) -> RagIndex | None:
    settings = get_settings()
    if provider == "pgvector":
        descriptor = getattr(embedding_service, "descriptor", None)
        model = str(index_status_value(descriptor, "model", getattr(embedding_service, "name", "unknown")))
        revision = str(index_status_value(descriptor, "revision", "legacy"))
        dimension = int(
            index_status_value(
                descriptor,
                "dimension",
                getattr(embedding_service, "dimensions", settings.embedding_dimensions),
            )
        )
        index = PgVectorIndex(
            settings.database_url,
            embedding_model=model,
            embedding_revision=revision,
            embedding_dimension=dimension,
            chunk_version=settings.chunk_version,
        )
        setattr(index, "embedding_descriptor", descriptor)
        return index
    if provider == "faiss":
        return FaissIndex(embedding_service)
    if provider == "chroma":
        return ChromaIndex(embedding_service)
    if provider == "milvus":
        return MilvusIndex(embedding_service)
    return None


def get_rag_index(
    embedding_service: EmbeddingService,
    book_id: str | None = None,
    *,
    index_status: Any = _STATUS_NOT_PROVIDED,
    index_status_error: str | None = None,
    forced_fallback_reason: str | None = None,
) -> RagIndex:
    """Select the real query provider and attach auditable runtime metadata."""

    settings = get_settings()
    provider = settings.rag_index_provider
    if index_status is _STATUS_NOT_PROVIDED:
        if book_id is None:
            index_status = None
        else:
            index_status, discovered_error = read_query_index_status(book_id)
            index_status_error = index_status_error or discovered_error
    generation = index_generation_from_status(index_status)

    reason = forced_fallback_reason or index_status_error
    state = str(index_status_value(index_status, "status", "")).strip().lower()
    configured_provider = str(index_status_value(index_status, "configured_provider", "")).strip().lower()
    active_provider = str(index_status_value(index_status, "active_provider", "")).strip().lower()
    status_reason = index_status_value(index_status, "fallback_reason")
    if reason is None and state and state != "ready":
        reason = str(status_reason or f"index_{state}")
    if reason is None and configured_provider and configured_provider != provider:
        reason = f"configured_provider_mismatch:{configured_provider}"
    if reason is None and active_provider and active_provider != provider:
        reason = str(status_reason or f"active_provider:{active_provider}")
    if reason is None and not embedding_descriptors_compatible(index_status, embedding_service):
        reason = "embedding_descriptor_incompatible"
    if reason is not None:
        return _artifact_fallback(
            embedding_service,
            requested_provider=provider,
            reason=reason,
            index_generation=generation,
        )

    try:
        index = _provider_index(provider, embedding_service)
    except Exception as exc:
        return _artifact_fallback(
            embedding_service,
            requested_provider=provider,
            reason=f"provider_initialization_failed:{type(exc).__name__}",
            index_generation=generation,
        )
    if index is None:
        return _artifact_fallback(
            embedding_service,
            requested_provider=provider,
            reason=f"unsupported_provider:{provider}",
            index_generation=generation,
        )
    try:
        available = bool(getattr(index, "available", True))
    except Exception as exc:
        return _artifact_fallback(
            embedding_service,
            requested_provider=provider,
            reason=f"provider_availability_failed:{type(exc).__name__}",
            index_generation=generation,
        )
    if not available:
        return _artifact_fallback(
            embedding_service,
            requested_provider=provider,
            reason=str(status_reason or f"{provider}_unavailable"),
            index_generation=generation,
        )

    # Existing provider classes predate the Stage-4 status contract. Runtime
    # attributes keep their public method surface stable while making the
    # selected provider and generation inspectable by retrieval/audit code.
    setattr(index, "provider", provider)
    setattr(index, "requested_provider", provider)
    setattr(index, "fallback_reason", None)
    setattr(index, "index_generation", generation)
    return index
