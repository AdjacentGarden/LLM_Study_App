from __future__ import annotations

from typing import Any

from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
    GroundedAnswerGenerator,
)
from adaptive_learning.rag.index import RetrievalResult, RetrievedEvidence
from adaptive_learning.rag.service import TextbookQAService


class FakeIndex:
    def __init__(self, score: float) -> None:
        self.score = score

    def search(self, question: str, **_: object) -> RetrievalResult:
        return RetrievalResult(
            question=question,
            score=self.score,
            pages=(11,),
            evidence=(
                RetrievedEvidence(
                    chunk_id="internal-chunk-id",
                    page_number=11,
                    text="染色体只复制一次，而细胞分裂两次。",
                    granularity="parent",
                ),
            ),
        )


class FakeClient:
    def __init__(self) -> None:
        self.calls = 0

    def structured(self, **_: object) -> dict[str, Any]:
        self.calls += 1
        return {
            "status": "supported",
            "claims": [
                {
                    "text": "染色体复制一次，细胞分裂两次。",
                    "citations": [
                        {
                            "source_id": "E1",
                            "quote": "染色体只复制一次，而细胞分裂两次",
                        }
                    ],
                }
            ],
            "confidence": 0.97,
        }


def test_service_connects_retrieval_to_verified_public_answer() -> None:
    client = FakeClient()
    service = TextbookQAService(
        book_id="biology-required-2",
        index=FakeIndex(3.5),  # type: ignore[arg-type]
        generator=GroundedAnswerGenerator(client),  # type: ignore[arg-type]
    )

    result = service.answer("减数分裂有什么特点？")

    assert result.status == AnswerStatus.SUPPORTED
    assert result.evidence_pages == [11]
    assert "internal-chunk-id" not in result.model_dump_json()
    assert client.calls == 1


def test_service_refuses_low_score_without_model_call() -> None:
    client = FakeClient()
    service = TextbookQAService(
        book_id="biology-required-2",
        index=FakeIndex(-0.1),  # type: ignore[arg-type]
        generator=GroundedAnswerGenerator(client),  # type: ignore[arg-type]
    )

    result = service.answer("牛顿第二定律是什么？")

    assert result.status == AnswerStatus.INSUFFICIENT
    assert result.evidence_pages == []
    assert client.calls == 0
