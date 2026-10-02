from __future__ import annotations

import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import HTTPException

from adaptive_learning.api import qa_dependency


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
    settings = SimpleNamespace(rag_book_id="biology", published_book_ids=("biology","cpp","english"),
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
        return SimpleNamespace(path=path)

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


def test_imported_book_uses_its_own_local_index_when_absent_from_external_manifest(monkeypatch, tmp_path):
    monkeypatch.delenv("RAG_BOOK_INDEX_MANIFEST", raising=False)
    settings = SimpleNamespace(rag_book_id="original", published_book_ids=("original", "imported"),
                               data_dir=tmp_path, rag_top_pages=5, rag_max_evidence=10,
                               rag_retrieval_budget_ms=2500)
    monkeypatch.setattr(qa_dependency, "get_settings", lambda: settings)
    monkeypatch.setattr(qa_dependency, "_book_services", OrderedDict())
    target = tmp_path / "books/imported/rag-index"
    target.mkdir(parents=True)
    (target / "index_manifest.json").write_text("{}")
    primary = SimpleNamespace(index=SimpleNamespace(encoder=object(), reranker=object()), generator=object())
    monkeypatch.setattr(qa_dependency, "build_qa_service", lambda: primary)
    loaded = []

    def load(path, **kwargs):
        loaded.append(path)
        return SimpleNamespace(path=path)

    monkeypatch.setattr(qa_dependency.PersistentRAGIndex, "load", load)
    service = qa_dependency.build_book_qa_service("imported")
    assert loaded == [target]
    assert service.book_id == "imported"
    assert service.index is not primary.index
    assert service.generator is primary.generator
