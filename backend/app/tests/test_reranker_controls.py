from __future__ import annotations

import pytest

from app.rag.reranker import BGERerankerService


def test_bge_reranker_fail_open_is_explicit(monkeypatch) -> None:
    service = BGERerankerService("missing-model", fail_open=True)
    monkeypatch.setattr(service, "_load_model", lambda: (_ for _ in ()).throw(RuntimeError("missing")))

    assert service.rerank("question", [], top_k=5) == []


def test_bge_reranker_can_fail_closed(monkeypatch) -> None:
    service = BGERerankerService("missing-model", fail_open=False)
    monkeypatch.setattr(service, "_load_model", lambda: (_ for _ in ()).throw(RuntimeError("missing")))

    with pytest.raises(RuntimeError, match="configured BGE reranker is unavailable"):
        service.rerank("question", [], top_k=5)
