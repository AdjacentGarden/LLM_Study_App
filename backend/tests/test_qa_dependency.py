from __future__ import annotations

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException, Request

from adaptive_learning.accounts import Accounts
from adaptive_learning.api import qa_dependency
from adaptive_learning.community import CommunityRepository
from adaptive_learning.ingestion.jobs import OCRRunResult, SQLiteOCRJobRepository


def test_concurrent_cold_start_builds_one_qa_service(monkeypatch: Any) -> None:
    sentinel = object()
    calls = 0

    def create() -> object:
        nonlocal calls
        calls += 1
        time.sleep(0.02)
        return sentinel

    monkeypatch.setattr(qa_dependency, "_service", None)
    monkeypatch.setattr(qa_dependency, "_create_qa_service", create)

    with ThreadPoolExecutor(max_workers=20) as executor:
        services = list(executor.map(lambda _: qa_dependency.build_qa_service(), range(40)))

    assert calls == 1
    assert all(service is sentinel for service in services)


def test_multi_book_routing_reuses_models_and_isolates_indexes(monkeypatch: Any, tmp_path: Any) -> None:
    import json
    manifest = tmp_path / "indexes.json"
    manifest.write_text(json.dumps({"cpp":str(tmp_path / "cpp"),"english":str(tmp_path / "english")}))
    monkeypatch.setenv("RAG_BOOK_INDEX_MANIFEST", str(manifest))
    settings = SimpleNamespace(rag_book_id="biology", rag_index_dir=tmp_path / "biology", published_book_ids=("biology","cpp","english"),
                               rag_top_pages=5, rag_max_evidence=10,
                               rag_retrieval_budget_ms=2500,
                               rag_use_evidence_planner=False,
                               rag_use_semantic_review=True)
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    encoder, reranker, generator = object(), object(), object()
    primary = SimpleNamespace(index=SimpleNamespace(encoder=encoder,reranker=reranker),generator=generator)
    monkeypatch.setattr(qa_dependency, "build_qa_service", lambda: primary)
    monkeypatch.setattr(qa_dependency, "_book_services", OrderedDict())
    loaded = []

    def load(path: Any, **kwargs: Any) -> Any:
        loaded.append(path)
        assert kwargs == {"encoder":encoder,"reranker":reranker}
        return SimpleNamespace(index_dir=path)

    monkeypatch.setattr(qa_dependency.PersistentRAGIndex,"load",load)
    cpp = qa_dependency.build_book_qa_service("cpp")
    english = qa_dependency.build_book_qa_service("english")
    assert cpp.book_id == "cpp" and english.book_id == "english"
    assert cpp.index is not english.index and cpp._cache is not english._cache
    assert cpp.generator is english.generator is generator
    assert qa_dependency.build_book_qa_service("cpp") is cpp
    assert qa_dependency.build_book_qa_service("biology") is primary
    assert len(loaded) == 2
    with pytest.raises(HTTPException) as error:
        qa_dependency.build_book_qa_service("../../secret")
    assert error.value.status_code == 404


def test_uploaded_books_share_models_without_a_default_index_and_refresh_revised_ocr(monkeypatch, tmp_path):
    from test_upload_index import Encoder, Reranker

    settings = SimpleNamespace(
        data_dir=tmp_path, rag_book_id="default", rag_index_dir=tmp_path / "missing-default",
        published_book_ids=(), rag_top_pages=5, rag_max_evidence=10,
        rag_retrieval_budget_ms=2500,
    )
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    monkeypatch.setattr(qa_dependency, "_components", None)
    monkeypatch.setattr(qa_dependency, "_book_services", OrderedDict())
    monkeypatch.setattr(qa_dependency, "_upload_indexes", OrderedDict())
    encoder, reranker, generator = Encoder(), Reranker(), object()
    components_calls = []

    def components():
        components_calls.append(True)
        return encoder, reranker, generator

    monkeypatch.setattr(qa_dependency, "_create_components", components)
    jobs = SQLiteOCRJobRepository(tmp_path / "state" / "ocr_jobs.sqlite3")
    for book in ("book_a", "book_b"):
        source = tmp_path / book / "source.pdf"
        source.parent.mkdir()
        source.write_bytes(b"%PDF-fixture")
        jobs.register_book(book_id=book, original_name=book, file_path=source, source_sha256=book)
        jobs.enqueue(book_id=book, output_dir=source.parent / "ocr", max_attempts=1)
        job = jobs.claim_next(owner="test", lease_seconds=30)
        assert job is not None
        normalized = job.output_dir / "normalized"
        normalized.mkdir(parents=True)
        (normalized / "pages.jsonl").write_text('{"page_number":1,"text":"' + book + '教材内容"}\n')
        jobs.succeed(job_id=job.job_id, owner="test", result=OCRRunResult(quality_score=1, needs_human_review=False, page_count=1))
    a = qa_dependency.build_book_qa_service("book_a")
    b = qa_dependency.build_book_qa_service("book_b")
    assert a.index.encoder is b.index.encoder is encoder
    assert a.index.reranker is b.index.reranker is reranker
    assert a.generator is b.generator is generator and components_calls == [True]
    assert a.index.index_dir != b.index.index_dir and a._cache is not b._cache
    assert qa_dependency.build_book_qa_service("book_a") is a and encoder.calls == 2
    (tmp_path / "book_a" / "ocr" / "normalized" / "pages.jsonl").write_text('{"page_number":1,"text":"修订后的教材内容"}\n')
    revised = qa_dependency.build_book_qa_service("book_a")
    assert revised is not a and revised.index.index_dir != a.index.index_dir
    assert revised.index.chunks[0].text == "修订后的教材内容" and encoder.calls == 3
    assert qa_dependency.build_book_qa_service("book_b") is b


def test_private_qa_checks_ownership_before_loading_models_and_rejects_expired_accounts(monkeypatch, tmp_path):
    settings = SimpleNamespace(data_dir=tmp_path, rag_book_id="default", published_book_ids=("public",))
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    accounts = Accounts(CommunityRepository(tmp_path / "state" / "community.sqlite3"))
    owner, token, _ = accounts.repo.visitor(None, [])
    _, attacker_token, _ = accounts.repo.visitor(None, [])
    accounts.repo.bind_upload(owner, "private")
    calls = []
    sentinel = object()

    def build(book):
        calls.append(book)
        return sentinel

    monkeypatch.setattr(qa_dependency, "build_book_qa_service", build)

    def request(cookie):
        return Request({"type": "http", "headers": [(b"cookie", cookie.encode())]})

    with pytest.raises(HTTPException) as error:
        qa_dependency.require_qa_service("private", request(f"zhiwo_visitor={attacker_token}"))
    assert error.value.status_code == 403 and not calls
    with pytest.raises(HTTPException) as error:
        qa_dependency.require_qa_service("private", request(f"zhiwo_account=expired; zhiwo_visitor={token}"))
    assert error.value.status_code == 401 and not calls
    assert qa_dependency.require_qa_service("private", request(f"zhiwo_visitor={token}")) is sentinel
    assert qa_dependency.require_qa_service("public", request("")) is sentinel
    assert calls == ["private", "public"]
