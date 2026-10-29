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

    def structured(self, **kwargs: object) -> dict[str, Any]:
        self.calls += 1
        if str(kwargs.get('system', '')).startswith('你是学习辅导老师'):
            return {'answer': '牛顿第二定律描述力、质量和加速度之间的关系。'}
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


def test_service_returns_general_answer_for_low_score() -> None:
    client = FakeClient()
    service = TextbookQAService(
        book_id="biology-required-2",
        index=FakeIndex(-0.1),  # type: ignore[arg-type]
        generator=GroundedAnswerGenerator(client),  # type: ignore[arg-type]
    )

    result = service.answer("牛顿第二定律是什么？")

    assert result.status == AnswerStatus.GENERAL
    assert result.evidence_pages == []
    assert result.answer.startswith('教材引用未核验，以下为模型解答。')
    assert not result.semantic_checked and client.calls == 1


def test_unverified_address_answer_keeps_notice_and_empty_citations_in_cache() -> None:
    class InvalidAddressClient(FakeClient):
        def structured(self, **kwargs: object) -> dict[str, Any]:
            value = super().structured(**kwargs)
            value['claims'][0]['citations'] = [{'source_id': 'E1', 'span_id': 'missing'}]
            return value

    client = InvalidAddressClient()
    service = TextbookQAService(
        book_id='biology-required-2', index=FakeIndex(3.5),
        generator=GroundedAnswerGenerator(client, use_semantic_review=True),
    )  # type: ignore[arg-type]
    first = service.answer('减数分裂有什么特点？')
    cached = service.answer('减数分裂有什么特点？')

    for result in (first, cached):
        assert result.status == AnswerStatus.GENERAL
        assert result.answer.startswith('教材引用未核验，以下为模型解答。')
        assert result.claims == [] and result.evidence_pages == []
        assert not result.semantic_checked and result.confidence == 0
    assert not first.cache_hit and cached.cache_hit and client.calls == 1


def test_personalized_answers_are_not_shared_across_accounts_or_preferences() -> None:
    client = FakeClient()
    service = TextbookQAService(
        book_id="shared-book", index=FakeIndex(3.5), generator=GroundedAnswerGenerator(client)
    )  # type: ignore[arg-type]
    question = "减数分裂有什么特点？"
    preferences = {"guidance_styles": ["examples"]}
    service.answer(question, teaching_context=preferences, cache_scope="account-a:course-a")
    assert service.answer(
        question, teaching_context=preferences, cache_scope="account-a:course-a"
    ).cache_hit
    service.answer(question, teaching_context=preferences, cache_scope="account-b:course-b")
    assert client.calls == 2
    service.answer(
        question,
        teaching_context={"guidance_styles": ["principles"]},
        cache_scope="account-a:course-a",
    )
    assert client.calls == 3


def test_conversation_context_expands_retrieval_without_becoming_evidence():
    import json

    queries, payloads = [], []

    class CapturingIndex(FakeIndex):
        def search(self, question, **kwargs):
            queries.append(question)
            return super().search(question, **kwargs)

    class CapturingClient(FakeClient):
        def structured(self, **kwargs):
            payloads.append(json.loads(kwargs['user']))
            return super().structured(**kwargs)

    service = TextbookQAService(book_id='book', index=CapturingIndex(3.5),
                                generator=GroundedAnswerGenerator(CapturingClient()))
    context = {'chapter': {'title': '减数分裂'}, 'history': [
        {'role': 'user', 'text': '同源染色体何时分离？'},
        {'role': 'assistant', 'text': 'unverified-assistant-answer'},
    ]}
    first = service.answer('为什么？', conversation_context=context, cache_scope='course-a')
    assert first.claims
    assert '同源染色体' in queries[0] and '减数分裂' in queries[0]
    assert 'unverified-assistant-answer' not in queries[0]
    assert payloads[0]['conversation_context'] == context
    assert 'unverified-assistant-answer' not in str(payloads[0]['evidence'])
    assert service.answer('为什么？', conversation_context=context, cache_scope='course-a').cache_hit
    assert not service.answer('为什么？', conversation_context={**context, 'history': []}, cache_scope='course-a').cache_hit
    assert not service.answer('为什么？', conversation_context=context, cache_scope='course-b').cache_hit
