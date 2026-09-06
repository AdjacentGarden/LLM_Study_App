from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from adaptive_learning.api.app import app
from adaptive_learning.api.qa_dependency import require_qa_service
from adaptive_learning.llm.client import LLMError, LLMTimeoutError
from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
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


def test_timeout_is_reported_separately_from_configuration_failure() -> None:
    class TimedOut(FakeQAService):
        def answer(self, question: str) -> TextbookQAResult:
            raise LLMTimeoutError("deadline exceeded")
    app.dependency_overrides[require_qa_service] = TimedOut
    try:
        with TestClient(app) as client:
            response = client.post('/api/books/biology-required-2/qa',json={'question':'test'})
        assert response.status_code == 504
        assert '超时' in response.json()['detail']
    finally:
        app.dependency_overrides.clear()


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
    assert response.json()["detail"] == "回答模型暂时不可用"


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
