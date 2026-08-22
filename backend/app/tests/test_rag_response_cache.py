from __future__ import annotations

from types import SimpleNamespace

from app.rag.cache import clear_rag_cache, invalidate_rag_answers
from app.rag import service
from app.schemas.books import RagQuery


class _CountingAdapter:
    name = "counting"

    def __init__(self) -> None:
        self.calls = 0

    def answer(self, question, citations, history=None):
        self.calls += 1
        return f"answer:{question}", f"prompt:{question}"


def test_identical_rag_queries_reuse_complete_response(monkeypatch) -> None:
    clear_rag_cache()
    adapter = _CountingAdapter()
    retrieval_calls = 0

    def retrieve(*_args, **_kwargs):
        nonlocal retrieval_calls
        retrieval_calls += 1
        return []

    monkeypatch.setattr(service, "get_rag_bundle_identity", lambda _book_id: SimpleNamespace(
        generation="rag_generation_1",
        build_id="build_1",
        state="ready",
    ))
    monkeypatch.setattr(service, "retrieve_chunks", retrieve)
    monkeypatch.setattr(service, "read_chapters", lambda _book_id: [])
    monkeypatch.setattr(service, "get_rag_answer_adapter", lambda: adapter)

    payload = RagQuery(book_id="book_cache", chapter_id="chapter_1", question="解释减数分裂")
    first = service.answer_query(payload)
    second = service.answer_query(payload)

    assert first.answer == second.answer
    assert first.performance is not None
    assert first.performance.response_cache_hit is False
    assert second.performance is not None
    assert second.performance.response_cache_hit is True
    assert retrieval_calls == 1
    assert adapter.calls == 1

    invalidate_rag_answers("book_cache")
    third = service.answer_query(payload)
    assert third.performance is not None
    assert third.performance.response_cache_hit is False
    assert retrieval_calls == 2
    assert adapter.calls == 2
