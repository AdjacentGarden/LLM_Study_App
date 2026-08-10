from __future__ import annotations

import sys
from types import ModuleType

import pytest

from app.core.config import BGE_M3_REVISION, get_settings
from app.rag.cache import (
    _artifact_embedding_cache,
    _bm25_cache,
    clear_rag_cache,
    get_artifact_embeddings,
    get_bm25_index,
    has_artifact_embeddings,
    has_bm25_index,
    invalidate_artifact_embeddings,
    invalidate_bm25,
)
from app.rag.embedding import (
    BGEM3EmbeddingService,
    EmbeddingConfigurationError,
    EmbeddingDimensionError,
    HashingEmbeddingService,
    get_embedding_service,
)
from app.rag.index_base import ArtifactVectorIndex
from app.rag.index_factory import get_rag_index
from app.schemas.books import Chunk
from app.services.artifact_store import write_chunks


@pytest.fixture(autouse=True)
def _reset_runtime() -> None:
    get_settings.cache_clear()
    clear_rag_cache()
    yield
    clear_rag_cache()
    get_settings.cache_clear()


def test_bge_descriptor_loads_frozen_revision_device_and_dimension(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class FakeSentenceTransformer:
        def __init__(self, model_name: str, **kwargs: object) -> None:
            captured.update(model=model_name, **kwargs)

        @staticmethod
        def get_sentence_embedding_dimension() -> int:
            return 1024

        @staticmethod
        def encode(texts: list[str], **_: object) -> list[list[float]]:
            return [[0.0] * 1024 for _text in texts]

    fake_module = ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "bge_m3")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DEVICE", "cuda:0")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "1024")
    get_settings.cache_clear()

    service = get_embedding_service()
    vector = service.embed_query("auditable vector")

    assert len(vector) == 1024
    assert service.descriptor.as_dict() == {
        "provider": "bge_m3",
        "model": "BAAI/bge-m3",
        "revision": BGE_M3_REVISION,
        "version": "v1",
        "dimension": 1024,
        "device": "cuda:0",
    }
    assert captured == {
        "model": "BAAI/bge-m3",
        "revision": BGE_M3_REVISION,
        "device": "cuda:0",
    }


def test_bge_rejects_configured_and_runtime_dimension_mismatch(monkeypatch) -> None:
    with pytest.raises(EmbeddingConfigurationError, match="dimension must be 1024"):
        BGEM3EmbeddingService("BAAI/bge-m3", dimensions=768)

    class WrongDimensionModel:
        @staticmethod
        def get_sentence_embedding_dimension() -> int:
            return 768

    service = BGEM3EmbeddingService("BAAI/bge-m3")
    service._model = WrongDimensionModel()
    with pytest.raises(EmbeddingDimensionError, match="reported dimension 768"):
        service.embed_query("wrong dimension")


def test_hashing_uses_configured_dimension_and_never_claims_bge(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "37")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_VERSION", "hash-contract-v2")
    get_settings.cache_clear()

    service = get_embedding_service()

    assert len(service.embed_query("hash me")) == 37
    assert service.descriptor.provider == "hashing"
    assert service.descriptor.model == "sha1-feature-hashing"
    assert service.descriptor.revision == "algorithm-v1"
    assert service.descriptor.version == "hash-contract-v2"
    assert service.descriptor.dimension == 37


def test_frozen_bge_revision_rejects_environment_drift(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_BGE_M3_REVISION", "main")
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="Stage 0 frozen revision"):
        get_settings()


def test_index_factory_marks_building_incompatible_and_unavailable_fallbacks(monkeypatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.delenv("BOOKCOURSE_DATABASE_URL", raising=False)
    get_settings.cache_clear()
    embedding = HashingEmbeddingService(dimensions=1024)
    descriptor = embedding.descriptor.as_dict()

    building = get_rag_index(
        embedding,
        "book",
        index_status={
            "status": "building",
            "configured_provider": "pgvector",
            "active_provider": "artifact_fallback",
            "index_generation": "g2",
            "fallback_reason": "index_building",
            "embedding": descriptor,
        },
    )
    incompatible = get_rag_index(
        embedding,
        "book",
        index_status={
            "status": "ready",
            "configured_provider": "pgvector",
            "active_provider": "pgvector",
            "index_generation": "g1",
            "embedding": {**descriptor, "dimension": 768},
        },
    )
    unavailable = get_rag_index(
        embedding,
        "book",
        index_status={
            "status": "ready",
            "configured_provider": "pgvector",
            "active_provider": "pgvector",
            "index_generation": "g1",
            "embedding": descriptor,
        },
    )

    assert isinstance(building, ArtifactVectorIndex)
    assert building.name == "artifact_fallback"
    assert building.fallback_reason == "index_building"
    assert building.index_generation == "g2"
    assert incompatible.name == "artifact_fallback"
    assert incompatible.fallback_reason == "embedding_descriptor_incompatible"
    assert unavailable.name == "artifact_fallback"
    assert unavailable.fallback_reason == "pgvector_unavailable"
    assert all(item.requested_provider == "pgvector" for item in (building, incompatible, unavailable))


def test_pgvector_factory_uses_query_embedding_descriptor(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class CapturingPgVectorIndex:
        name = "pgvector"
        available = True

        def __init__(self, database_url: str | None, **kwargs: object) -> None:
            captured.update(database_url=database_url, **kwargs)

    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.setenv("BOOKCOURSE_DATABASE_URL", "postgresql://isolated.invalid/test")
    monkeypatch.setenv("BOOKCOURSE_CHUNK_VERSION", "v2")
    get_settings.cache_clear()
    from app.rag import index_factory as factory_module

    monkeypatch.setattr(factory_module, "PgVectorIndex", CapturingPgVectorIndex)
    embedding = HashingEmbeddingService(dimensions=1024, version="hash-v3")
    descriptor = embedding.descriptor.as_dict()

    selected = factory_module.get_rag_index(
        embedding,
        "book",
        index_status={
            "status": "ready",
            "configured_provider": "pgvector",
            "active_provider": "pgvector",
            "index_generation": 9,
            "embedding": descriptor,
        },
    )

    assert selected.name == "pgvector"
    assert captured == {
        "database_url": "postgresql://isolated.invalid/test",
        "embedding_model": "sha1-feature-hashing",
        "embedding_revision": "algorithm-v1",
        "embedding_dimension": 1024,
        "chunk_version": "v2",
    }
    assert selected.embedding_descriptor == embedding.descriptor
    assert selected.index_generation == 9


def test_pgvector_empty_result_keeps_pgvector_label_and_bm25_results(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "64")
    get_settings.cache_clear()
    write_chunks(
        "empty_vector_book",
        [
            Chunk(
                chunk_id="chunk-empty-vector",
                book_id="empty_vector_book",
                chapter_id="chapter-1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="photosynthesis converts light energy into chemical energy",
                asset_ids=[],
            )
        ],
    )
    from app.rag import retrieval as retrieval_module

    class EmptyPgVectorIndex:
        name = "pgvector"
        provider = "pgvector"
        fallback_reason = None
        index_generation = "ready-4"

        @staticmethod
        def search_vector(*_args: object, **_kwargs: object) -> list[object]:
            return []

    monkeypatch.setattr(retrieval_module, "read_query_index_status", lambda _book_id: (None, None))
    monkeypatch.setattr(retrieval_module, "get_rag_index", lambda *_args, **_kwargs: EmptyPgVectorIndex())
    monkeypatch.setattr(
        ArtifactVectorIndex,
        "search_vector",
        lambda *_args, **_kwargs: pytest.fail("valid pgvector empty result must not trigger artifact fallback"),
    )

    results = retrieval_module.retrieve_chunks("empty_vector_book", "photosynthesis light energy")

    assert results
    assert results[0].bm25_score > 0
    assert results[0].dense_score == 0
    assert results[0].index_name == "pgvector"
    assert results[0].index_provider == "pgvector"
    assert results[0].fallback_reason is None


def test_retrieval_exposes_real_artifact_provider_and_fallback_reason(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "hashing")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "64")
    get_settings.cache_clear()
    write_chunks(
        "fallback_book",
        [
            Chunk(
                chunk_id="chunk-1",
                book_id="fallback_book",
                chapter_id="chapter-1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="cell membrane transport uses selective permeability",
                asset_ids=[],
            )
        ],
    )
    from app.rag import retrieval as retrieval_module

    monkeypatch.setattr(
        retrieval_module,
        "read_query_index_status",
        lambda _book_id: (
            {
                "status": "building",
                "configured_provider": "pgvector",
                "active_provider": "artifact_fallback",
                "index_generation": "generation-2",
                "fallback_reason": "index_building",
                "embedding": HashingEmbeddingService(dimensions=64).descriptor.as_dict(),
            },
            None,
        ),
    )

    results = retrieval_module.retrieve_chunks("fallback_book", "membrane transport")

    assert results
    assert results[0].index_name == "artifact_fallback"
    assert results[0].index_provider == "artifact"
    assert results[0].index_generation == "generation-2"
    assert results[0].fallback_reason == "index_building"
    assert results[0].embedding_descriptor["provider"] == "hashing"


def test_bge_query_failure_uses_auditable_hashing_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("BOOKCOURSE_RAG_INDEX_PROVIDER", "pgvector")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_PROVIDER", "bge_m3")
    monkeypatch.setenv("BOOKCOURSE_EMBEDDING_DIMENSIONS", "1024")
    get_settings.cache_clear()
    write_chunks(
        "bge_failure_book",
        [
            Chunk(
                chunk_id="chunk-bge",
                book_id="bge_failure_book",
                chapter_id="chapter-1",
                page_start=1,
                page_end=1,
                content_type="text",
                text="mitosis separates chromosomes during cell division",
                asset_ids=[],
            )
        ],
    )
    from app.rag import retrieval as retrieval_module

    class BrokenBge:
        def embed_query(self, _text: str) -> list[float]:
            raise RuntimeError("model unavailable")

    monkeypatch.setattr(retrieval_module, "get_embedding_service", lambda: BrokenBge())
    monkeypatch.setattr(retrieval_module, "read_query_index_status", lambda _book_id: (None, None))

    results = retrieval_module.retrieve_chunks("bge_failure_book", "chromosome division")

    assert results
    assert results[0].index_name == "artifact_fallback"
    assert results[0].fallback_reason == "embedding_provider_failed:RuntimeError"
    assert results[0].embedding_descriptor["provider"] == "hashing"
    assert results[0].embedding_descriptor["model"] == "sha1-feature-hashing"
    assert results[0].embedding_descriptor["dimension"] == 1024


def test_generation_aware_caches_isolate_entries_and_keep_legacy_keys() -> None:
    builds = {"bm25": 0, "artifact": 0}

    def build_bm25() -> object:
        builds["bm25"] += 1
        return object()

    def build_artifact() -> object:
        builds["artifact"] += 1
        return object()

    legacy_bm25 = get_bm25_index("book", None, build_bm25)
    assert get_bm25_index("book", None, build_bm25) is legacy_bm25
    generation_one = get_bm25_index("book", None, build_bm25, "g1")
    assert get_bm25_index("book", None, build_bm25, "g1") is generation_one
    assert get_bm25_index("book", None, build_bm25, "g2") is not generation_one

    legacy_artifact = get_artifact_embeddings("book", None, build_artifact)
    assert get_artifact_embeddings("book", None, build_artifact) is legacy_artifact
    generation_artifact = get_artifact_embeddings("book", None, build_artifact, "g1")
    assert get_artifact_embeddings("book", None, build_artifact, "g1") is generation_artifact
    assert get_artifact_embeddings("book", None, build_artifact, "g2") is not generation_artifact

    assert builds == {"bm25": 3, "artifact": 3}
    assert ("book", None) in _bm25_cache()
    assert ("book", None, "g1") in _bm25_cache()
    assert ("book", None) in _artifact_embedding_cache()
    assert has_bm25_index("book", None, "g2")
    assert has_artifact_embeddings("book", None, "g2")

    invalidate_bm25("book")
    invalidate_artifact_embeddings("book")
    assert not has_bm25_index("book", None, "g1")
    assert not has_artifact_embeddings("book", None, "g1")
