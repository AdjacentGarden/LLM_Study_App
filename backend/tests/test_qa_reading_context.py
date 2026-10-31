"""Bounded reader hints stay separate from both the question and source proof."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError
from test_native_api import native  # noqa: F401 - shared real HTTP/ownership fixture
from test_qa_service import FakeClient, FakeIndex

from adaptive_learning import native_catalog
from adaptive_learning.api import native_routes
from adaptive_learning.api.schemas import BookQuestionRequest
from adaptive_learning.rag.grounded_qa import AnswerStatus, GroundedAnswerGenerator
from adaptive_learning.rag.service import TextbookQAResult, TextbookQAService


def _reading(note: str = "批注" * 450) -> dict:
    return {"pdf_page": 3, "page_text": "页" * 1200, "note_text": note, "note_truncated": False}


def _capture_services(monkeypatch):
    calls = []

    class Service:
        def __init__(self, book_id):
            self.book_id = book_id

        def answer(self, question, **kwargs):
            calls.append({"book_id": self.book_id, "question": question, **kwargs})
            return TextbookQAResult(
                book_id=self.book_id, status=AnswerStatus.GENERAL, answer="未核验的测试答复",
                claims=[], confidence=0, evidence_pages=[], retrieval_duration_ms=0,
                generation_duration_ms=0,
            )

    monkeypatch.setattr(native_routes, "require_qa_service", Service)
    return calls


def _workspace(client):
    response = client.post("/api/native/catalog/community_genetics/import", json={
        "id": "workspace_reader_context", "name": "阅读上下文合同",
    })
    assert response.status_code == 200, response.text
    workspace = response.json()
    resource = workspace["resources"][0]
    return workspace, f"/api/workspaces/{workspace['id']}/resources/{resource['id']}"


def test_short_question_with_long_reading_context_and_permissions(native, monkeypatch):  # noqa: F811
    owner, other, _ = native
    calls = _capture_services(monkeypatch)
    _, path = _workspace(owner)
    reading = _reading("注" * 4000)
    response = owner.post(path + "/qa", json={"question": "请检查我的理解", "reading_context": reading})
    assert response.status_code == 200, response.text
    assert calls[-1]["question"] == "请检查我的理解"
    assert calls[-1]["conversation_context"]["reading_context"] == reading
    assert response.json()["claims"] == []
    before = len(calls)
    assert other.post(path + "/qa", json={"question": "问题", "reading_context": reading}).status_code == 404
    assert owner.post(path + "/qa", json={"question": "问题", "book_ids": ["book_local_unuploaded"]}).status_code == 422
    assert len(calls) == before, "Reader context must not bypass ownership or admit an unbound book"


def test_reading_context_only_belongs_to_primary_book(native, monkeypatch):  # noqa: F811
    owner, _, _ = native
    calls = _capture_services(monkeypatch)
    workspace, path = _workspace(owner)
    from native_harness import textbook_pdf

    uploaded = owner.post("/api/native/uploads", files={"file": ("second.pdf", textbook_pdf(), "application/pdf")})
    assert uploaded.status_code == 200, uploaded.text
    second = uploaded.json()["book_id"]
    added = owner.post(f"/api/workspaces/{workspace['id']}/resources", json={"book_id": second})
    assert added.status_code == 200, added.text
    response = owner.post(path + "/qa", json={"question": "请比较", "book_ids": [second], "reading_context": _reading()})
    assert response.status_code == 200, response.text
    assert [call["book_id"] for call in calls] == [native_catalog.BOOK_ID, second]
    assert calls[0]["conversation_context"]["reading_context"] == _reading()
    assert "reading_context" not in calls[1].get("conversation_context", {})


@pytest.mark.parametrize("change", [
    {"pdf_page": 0}, {"pdf_page": 100001}, {"page_text": "页" * 1201},
    {"note_text": "注" * 4001}, {"unknown": "not accepted"},
])
def test_reading_context_is_bounded_without_relaxing_question(change):
    with pytest.raises(ValidationError):
        BookQuestionRequest(question="短问题", reading_context={**_reading(), **change})
    with pytest.raises(ValidationError):
        BookQuestionRequest(question="问" * 2001, reading_context=_reading())


def test_reading_hints_expand_query_with_question_first_and_are_cache_scoped():
    queries, payloads = [], []

    class Index(FakeIndex):
        def search(self, question, **kwargs):
            queries.append(question)
            return super().search(question, **kwargs)

    class Client(FakeClient):
        def structured(self, **kwargs):
            payloads.append(json.loads(kwargs["user"]))
            return super().structured(**kwargs)

    service = TextbookQAService(book_id="book", index=Index(3.5),
        generator=GroundedAnswerGenerator(Client()))
    context = {"history": [], "reading_context": _reading("用户批注线索" + "注" * 3900)}
    first = service.answer("请检查我的理解", conversation_context=context, cache_scope="owner:workspace")
    assert first.claims
    assert queries[0].startswith("请检查我的理解\n")
    assert "用户批注线索" in queries[0] and len(queries[0]) < 1000
    assert payloads[0]["question"] == "请检查我的理解"
    assert payloads[0]["conversation_context"] == context
    assert "用户批注线索" not in json.dumps(payloads[0]["evidence"], ensure_ascii=False)
    assert service.answer("请检查我的理解", conversation_context=context, cache_scope="owner:workspace").cache_hit
    changed = {**context, "reading_context": _reading("另一条批注")}
    assert not service.answer("请检查我的理解", conversation_context=changed, cache_scope="owner:workspace").cache_hit
    assert not service.answer("请检查我的理解", conversation_context=context, cache_scope="another:workspace").cache_hit


def test_client_reading_text_cannot_supply_a_verified_citation():
    class Client:
        calls = 0

        def structured(self, **kwargs):
            self.calls += 1
            return {"status": "supported", "claims": [{"text": "批注中的错误说法",
                "citations": [{"source_id": "E1", "quote": "仅存在于用户批注中的文字"}]}]}

    client = Client()
    service = TextbookQAService(book_id="book", index=FakeIndex(3.5),
        generator=GroundedAnswerGenerator(client))
    result = service.answer("检查我的理解", conversation_context={
        "reading_context": _reading("仅存在于用户批注中的文字"), "history": [],
    })
    assert result.status == AnswerStatus.GENERAL and result.claims == []
    assert not result.semantic_checked and client.calls == 1
