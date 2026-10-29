from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from adaptive_learning.api.app import app
from adaptive_learning.api.qa_dependency import require_qa_service
from adaptive_learning.llm.client import LLMError
from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
    EvidenceChunk,
    GroundedAnswerGenerator,
    VerifiedCitation,
    VerifiedClaim,
)
from adaptive_learning.rag.service import QABusyError, TextbookQAResult


class FakeQAService:
    book_id = "biology-required-2"

    def answer(self, question: str) -> TextbookQAResult:
        return TextbookQAResult(
            book_id=self.book_id,
            status=AnswerStatus.SUPPORTED,
            answer=f"已依据教材回答：{question}",
            claims=[
                VerifiedClaim(
                    text="染色体只复制一次，细胞分裂两次。",
                    citations=[
                        VerifiedCitation(
                            page_number=11,
                            quote="染色体只复制一次，而细胞分裂两次",
                        )
                    ],
                )
            ],
            confidence=0.98,
            evidence_pages=[11],
            retrieval_duration_ms=5,
            generation_duration_ms=20,
        )


class FailingQAService(FakeQAService):
    def answer(self, question: str) -> TextbookQAResult:
        raise LLMError("provider unavailable")


@pytest.fixture
def client() -> Iterator[TestClient]:
    app.dependency_overrides[require_qa_service] = FakeQAService
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def test_grounded_qa_endpoint_returns_public_page_citations(client: TestClient) -> None:
    response = client.post(
        "/api/books/biology-required-2/qa",
        json={"question": "减数分裂有什么特点？"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "supported"
    assert payload["evidence_pages"] == [11]
    assert payload["claims"][0]["citations"][0]["page_number"] == 11
    assert "chunk_id" not in response.text


def test_grounded_qa_endpoint_returns_answer_instead_of_502_for_invalid_span() -> None:
    class InvalidAddressClient:
        calls = 0

        def structured(self, **_: object) -> dict:
            self.calls += 1
            return {'status': 'supported', 'claims': [{
                'text': '染色体只复制一次，细胞分裂两次。',
                'citations': [{'source_id': 'E1', 'span_id': 'missing'}],
            }]}

    model = InvalidAddressClient()

    class InvalidAddressService(FakeQAService):
        def answer(self, question: str) -> TextbookQAResult:
            result = GroundedAnswerGenerator(model, use_semantic_review=True).answer(
                question=question, evidence=[EvidenceChunk(
                    source_id='E1', page_number=11, text='染色体只复制一次，而细胞分裂两次。'
                )], retrieval_score=3.5,
            )
            return TextbookQAResult(
                book_id=self.book_id, **result.model_dump(), evidence_pages=[],
                retrieval_duration_ms=0, generation_duration_ms=0,
            )

    app.dependency_overrides[require_qa_service] = InvalidAddressService
    try:
        with TestClient(app) as client:
            response = client.post('/api/books/biology-required-2/qa',
                                   json={'question': '减数分裂有什么特点？'})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    payload = response.json()
    assert payload['status'] == 'general' and payload['claims'] == []
    assert payload['answer'].startswith('教材引用未核验，以下为模型解答。')
    assert '染色体只复制一次，细胞分裂两次。' in payload['answer']
    assert payload['semantic_checked'] is False and model.calls == 1


def test_grounded_qa_endpoint_rejects_unknown_book(client: TestClient) -> None:
    response = client.post("/api/books/other/qa", json={"question": "测试"})

    assert response.status_code == 404


@pytest.mark.parametrize("question", ["", "  \n\t", "x" * 2001])
def test_grounded_qa_endpoint_validates_question(client: TestClient, question: str) -> None:
    response = client.post("/api/books/biology-required-2/qa", json={"question": question})

    assert response.status_code == 422


def test_grounded_qa_endpoint_maps_model_failure_to_bad_gateway() -> None:
    app.dependency_overrides[require_qa_service] = FailingQAService
    try:
        with TestClient(app) as client:
            response = client.post("/api/books/biology-required-2/qa", json={"question": "测试"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 502
    assert response.json()["detail"] == "回答服务暂时不可用，请稍后重试。"
    assert 'provider unavailable' not in response.text


def test_grounded_qa_endpoint_reports_unavailable_dependency() -> None:
    def unavailable() -> None:
        raise HTTPException(status_code=503, detail="教材答疑服务尚未就绪")

    app.dependency_overrides[require_qa_service] = unavailable
    try:
        with TestClient(app) as client:
            response = client.post("/api/books/biology-required-2/qa", json={"question": "测试"})
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 503


def test_overload_is_retryable_not_internal_error():
    class Busy(FakeQAService):
        def answer(self,question): raise QABusyError("full")
    app.dependency_overrides[require_qa_service]=Busy
    try:
        with TestClient(app) as client:
            response=client.post("/api/books/biology-required-2/qa",json={"question":"测试"})
        assert response.status_code==503 and response.headers["retry-after"]=="5"
    finally:
        app.dependency_overrides.clear()
