from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.rag.embedding import EmbeddingDescriptor
from app.api import routes_books
from app.core.auth import Principal
from app.rag.indexing import (
    RagIndexCoordinatorError,
    RagIndexPublishError,
    RagIndexStaleBuildError,
    delete_book_index,
    fail_current_index_build,
    publish_index_build,
    read_index_status,
    reserve_index_build,
    retry_finalize,
)
from app.schemas.books import Chunk
from app.services.artifact_store import RagBundleIdentity
from app.services.job_store import job_store
from app.services.storage import original_file_path


def _settings(database_url: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        rag_index_provider="pgvector",
        database_url=database_url,
        embedding_provider="hashing",
        embedding_dimensions=1024,
        embedding_version="hash-v1",
        embedding_device="cpu",
        chunk_version="v2",
    )


def _descriptor() -> EmbeddingDescriptor:
    return EmbeddingDescriptor(
        provider="hashing",
        model="sha1-feature-hashing",
        revision="algorithm-v1",
        version="hash-v1",
        dimension=1024,
        device="cpu",
    )


def _chunk(chunk_id: str = "chunk-new", *, indexable: bool = True) -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        book_id="stage4_book",
        chapter_id="chapter-1",
        page_start=1,
        page_end=1,
        content_type="paragraph",
        text="membrane transport stage four",
        parser="mineru",
        parser_version="3.4.4",
        chunk_version="v2",
        quality_score=0.9 if indexable else 0.1,
        token_count=8,
        metadata={
            "quality_version": "quality-v1",
            "quality_threshold": 0.45,
            "indexable": indexable,
            "quarantined": not indexable,
            "index_status": "ready" if indexable else "quarantined",
        },
    )


class _EmbeddingService:
    name = "hashing"
    dimensions = 1024
    descriptor = _descriptor()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        assert all("membrane transport" in text for text in texts)
        return [[1.0, *([0.0] * 1023)] for _ in texts]


class _FakePgIndex:
    def __init__(self, *, replace_error: Exception | None = None, delete_error: Exception | None = None) -> None:
        self.replace_error = replace_error
        self.delete_error = delete_error
        self.generation = 7
        self.build_id = "build-stage4"
        self.state = "building"
        self.chunk_count = 0
        self.calls: list[str] = []

    def reserve_generation(self, book_id: str, *, build_id: str):
        self.calls.append("reserve")
        self.build_id = build_id
        self.state = "building"
        return self._state()

    def get_state(self, book_id: str):
        self.calls.append("get_state")
        return self._state()

    def replace_generation(self, book_id: str, generation: int, chunks, embeddings, *, build_id: str):
        self.calls.append("replace")
        if self.replace_error:
            raise self.replace_error
        assert generation == self.generation and build_id == self.build_id
        assert len(chunks) == len(embeddings) == 1
        self.state = "committed"
        self.chunk_count = len(chunks)
        return self._state()

    def mark_failed(self, book_id: str, generation: int, *, build_id: str, error: str):
        self.calls.append("mark_failed")
        self.state = "failed"
        return self._state(error=error)

    def mark_cache_failed(self, book_id: str, generation: int, *, build_id: str, error: str):
        self.calls.append("mark_cache_failed")
        self.state = "cache_failed"
        return self._state(error=error)

    def finalize_ready(self, book_id: str, generation: int, *, build_id: str):
        self.calls.append("finalize_ready")
        self.state = "ready"
        return self._state()

    def delete_book(self, book_id: str):
        self.calls.append("delete")
        if self.delete_error:
            raise self.delete_error
        self.generation += 1
        self.build_id = "delete-stage4"
        self.state = "deleted"
        self.chunk_count = 0
        return self._state(deleted=True)

    def _state(self, *, error: str | None = None, deleted: bool = False):
        now = datetime.now(timezone.utc)
        return SimpleNamespace(
            book_id="stage4_book",
            index_generation=self.generation,
            generation=self.generation,
            build_id=self.build_id,
            state=self.state,
            embedding_model="sha1-feature-hashing",
            embedding_revision="algorithm-v1",
            chunk_version="v2",
            embedding_dimension=1024,
            chunk_count=self.chunk_count,
            error=error,
            reserved_at=now,
            committed_at=now if self.state in {"committed", "ready", "cache_failed"} else None,
            ready_at=now if self.state == "ready" else None,
            deleted_at=now if deleted else None,
            updated_at=now,
        )


@pytest.fixture(autouse=True)
def _storage(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BOOKCOURSE_STORAGE_ROOT", str(tmp_path))
    from app.core.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _identity(state: str) -> RagBundleIdentity:
    return RagBundleIdentity(
        book_id="stage4_book",
        state=state,
        build_id="build-stage4",
        generation="rag-current" if state == "ready" else None,
    )


def test_missing_dsn_is_explicit_artifact_fallback_and_ready() -> None:
    invalidated: list[str] = []
    reservation = reserve_index_build(
        "stage4_book",
        "build-stage4",
        "parse:1",
        settings=_settings(None),
        identity_reader=lambda _: _identity("building"),
        cache_invalidator=invalidated.append,
    )
    # Publication requires the artifact pair to be ready; replace the frozen
    # reservation reader in this focused test before publishing.
    object.__setattr__(reservation, "_identity_reader", lambda _: _identity("ready"))
    receipt = publish_index_build(reservation, [_chunk(), _chunk("quarantine", indexable=False)])

    status = read_index_status("stage4_book", settings=_settings(None))
    assert receipt.status == "ready"
    assert status is not None
    assert status.active_provider == "artifact_fallback"
    assert status.fallback_reason == "pgvector_dsn_missing"
    assert status.counts["indexable_chunks"] == 1
    assert invalidated == ["stage4_book"]


def test_pgvector_commit_cache_failure_is_hidden_and_retryable() -> None:
    fake = _FakePgIndex()
    reservation = reserve_index_build(
        "stage4_book",
        "build-stage4",
        "parse:2",
        settings=_settings("postgresql://isolated/stage4"),
        index=fake,
        embedding_service=_EmbeddingService(),
        identity_reader=lambda _: _identity("building"),
        cache_invalidator=lambda _: (_ for _ in ()).throw(RuntimeError("cache barrier failed")),
    )
    object.__setattr__(reservation, "_identity_reader", lambda _: _identity("ready"))
    with pytest.raises(RagIndexPublishError):
        publish_index_build(reservation, [_chunk()])

    status = read_index_status("stage4_book", settings=_settings(None))
    assert status is not None and status.status == "cache_failed" and status.retryable
    assert fake.state == "cache_failed"
    receipt = retry_finalize(
        "stage4_book",
        fake.generation,
        fake.build_id,
        settings=_settings("postgresql://isolated/stage4"),
        index=fake,
        cache_invalidator=lambda _: None,
        identity_reader=lambda _: _identity("ready"),
    )
    assert receipt.status == "ready"
    assert fake.calls[-1] == "finalize_ready"


def test_database_failure_marks_task_failed_and_redacts_connection_text() -> None:
    fake = _FakePgIndex(
        replace_error=RuntimeError(
            "postgresql://user:supersecret@shared.example/db password=supersecret"
        )
    )
    reservation = reserve_index_build(
        "stage4_book",
        "build-stage4",
        "parse:3",
        settings=_settings("postgresql://isolated/stage4"),
        index=fake,
        embedding_service=_EmbeddingService(),
        identity_reader=lambda _: _identity("building"),
    )
    object.__setattr__(reservation, "_identity_reader", lambda _: _identity("ready"))
    with pytest.raises(RagIndexPublishError):
        publish_index_build(reservation, [_chunk()])

    status_path = Path(__import__("os").environ["BOOKCOURSE_STORAGE_ROOT"]) / "books" / "stage4_book" / "artifacts" / "rag_index_status.json"
    raw = status_path.read_text(encoding="utf-8")
    assert "supersecret" not in raw and "shared.example" not in raw
    assert read_index_status("stage4_book", settings=_settings(None)).status == "failed"
    assert "mark_failed" in fake.calls


def test_stale_artifact_identity_cannot_publish_or_embed() -> None:
    fake = _FakePgIndex()
    reservation = reserve_index_build(
        "stage4_book",
        "build-stage4",
        "chapter_rebuild",
        settings=_settings("postgresql://isolated/stage4"),
        index=fake,
        embedding_service=_EmbeddingService(),
        identity_reader=lambda _: _identity("building"),
    )
    object.__setattr__(
        reservation,
        "_identity_reader",
        lambda _: RagBundleIdentity("stage4_book", "ready", "newer-build", "rag-newer"),
    )
    with pytest.raises(RagIndexStaleBuildError):
        publish_index_build(reservation, [_chunk()])
    assert "replace" not in fake.calls


def test_delete_database_failure_is_not_reported_as_success() -> None:
    fake = _FakePgIndex(delete_error=RuntimeError("database unavailable"))
    with pytest.raises(RagIndexCoordinatorError):
        delete_book_index(
            "stage4_book",
            settings=_settings("postgresql://isolated/stage4"),
            index=fake,
        )
    status = read_index_status("stage4_book", settings=_settings(None))
    assert status is not None and status.status == "failed" and status.cause == "delete"


def test_fail_current_respects_parse_cause_cas() -> None:
    reservation = reserve_index_build(
        "stage4_book",
        "build-stage4",
        "parse:9",
        settings=_settings(None),
        identity_reader=lambda _: _identity("building"),
    )
    assert not fail_current_index_build("stage4_book", "old worker", expected_cause="parse:8")
    assert fail_current_index_build("stage4_book", "quality gate", expected_cause="parse:9")
    status = read_index_status("stage4_book", settings=_settings(None))
    assert status is not None and status.status == "failed"
    assert reservation.build_id == status.build_id


def test_delete_route_keeps_local_book_when_database_delete_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = "stage4_book"
    target = routes_books.resolve_under_root("books", book_id)
    target.mkdir(parents=True, exist_ok=True)
    (target / "keep.txt").write_text("must remain", encoding="utf-8")
    removed: list[str] = []
    monkeypatch.setattr(routes_books, "_assert_book_owner", lambda *_: None)
    monkeypatch.setattr(routes_books.mineru_task_store, "get_current", lambda *_: None)
    monkeypatch.setattr(
        routes_books,
        "delete_book_index",
        lambda *_: (_ for _ in ()).throw(RagIndexCoordinatorError("database unavailable")),
    )
    monkeypatch.setattr(routes_books, "remove_book", removed.append)

    with pytest.raises(RagIndexCoordinatorError, match="database unavailable"):
        routes_books.delete_course(book_id, Principal(user_id="test", is_admin=True))

    assert target.is_dir() and (target / "keep.txt").is_file()
    assert removed == []
    assert not (target / "artifacts" / routes_books.DELETE_SENTINEL).exists()


def test_parse_job_does_not_report_done_when_index_publication_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    book_id = "stage4_index_job_failure"
    original_file_path(book_id, "source.pdf").write_bytes(b"stage4")
    job = job_store.create(book_id)
    monkeypatch.setattr(
        routes_books,
        "parse_document",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RagIndexPublishError("database index transaction failed")
        ),
    )

    routes_books.run_parse_job(job.job_id, book_id)

    result = job_store.get(job.job_id)
    assert result is not None
    assert result.status == "failed" and result.stage == "failed"
    assert "database index transaction failed" in (result.error or "")
