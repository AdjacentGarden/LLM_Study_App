from typing import Any

import pytest

from adaptive_learning.llm.client import LLMTimeoutError

from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
    EvidenceChunk,
    GroundedAnswerGenerator,
    GroundedAnswerValidationError,
)


class FakeClient:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response
        self.calls = 0

    def structured(self, **_: object) -> dict[str, Any]:
        self.calls += 1
        return self.response


class SequenceFakeClient:
    def __init__(self, responses: list[dict[str, Any]]) -> None:
        self.responses = iter(responses)
        self.calls = 0

    def structured(self, **_: object) -> dict[str, Any]:
        self.calls += 1
        return next(self.responses)


def _evidence() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            source_id="E1",
            page_number=11,
            text="减数分裂过程中，染色体只复制一次，而细胞分裂两次。",
        )
    ]


def test_slow_optional_planner_does_not_skip_answer_review() -> None:
    class SlowPlanner:
        def __init__(self) -> None:
            self.calls = 0

        def structured(self, **kwargs: Any) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                raise LLMTimeoutError("planner deadline exhausted")
            if self.calls == 2:
                return {"status":"supported","confidence":0.9,"claims":[{
                    "text":"染色体复制一次，细胞分裂两次。",
                    "citations":[{"source_id":"E1","quote":_evidence()[0].text}]}]}
            return {"relevant":True,"reviews":[{"claim_index":0,"supported":True,"reason":"原文支持"}]}

    client = SlowPlanner()
    result = GroundedAnswerGenerator(client, use_evidence_planner=True,
                                    use_semantic_review=True).answer(
        question="请介绍本章提到的减数分裂过程", evidence=_evidence(),retrieval_score=1)
    assert result.status == AnswerStatus.SUPPORTED
    assert result.semantic_checked
    assert client.calls == 3


def test_supported_answer_exposes_page_citation_without_internal_source_id() -> None:
    client = FakeClient(
        {
            "status": "supported",
            "claims": [
                {
                    "text": "减数分裂时染色体复制一次，细胞分裂两次。",
                    "citations": [
                        {
                            "source_id": "E1",
                            "quote": "染色体只复制一次，而细胞分裂两次",
                        },
                        {
                            "source_id": "E1",
                            "quote": "染色体只复制一次，而细胞分裂两次",
                        },
                    ],
                }
            ],
            "confidence": 0.96,
            "insufficiency_reason": None,
        }
    )
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    answer = generator.answer(
        question="减数分裂有什么特点？",
        evidence=_evidence(),
        retrieval_score=4.2,
    )

    assert answer.status == AnswerStatus.SUPPORTED
    assert answer.claims[0].citations[0].page_number == 11
    assert len(answer.claims[0].citations) == 1
    assert "source_id" not in answer.model_dump(mode="json")
    assert client.calls == 1


def test_low_retrieval_score_refuses_without_calling_model() -> None:
    client = FakeClient({})
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    answer = generator.answer(
        question="牛顿第二定律是什么？",
        evidence=_evidence(),
        retrieval_score=-2.5,
    )

    assert answer.status == AnswerStatus.INSUFFICIENT
    assert answer.claims == []
    assert client.calls == 0


def test_fabricated_quote_is_rejected() -> None:
    client = FakeClient(
        {
            "status": "supported",
            "claims": [
                {
                    "text": "这是一个没有教材依据的主张。",
                    "citations": [{"source_id": "E1", "quote": "原文中不存在的句子"}],
                }
            ],
            "confidence": 0.9,
        }
    )
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    with pytest.raises(GroundedAnswerValidationError, match="not present"):
        generator.answer(
            question="测试",
            evidence=_evidence(),
            retrieval_score=3,
        )


def test_unknown_source_is_rejected() -> None:
    client = FakeClient(
        {
            "status": "supported",
            "claims": [
                {
                    "text": "主张",
                    "citations": [{"source_id": "E99", "quote": "染色体"}],
                }
            ],
            "confidence": 0.9,
        }
    )
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    with pytest.raises(GroundedAnswerValidationError, match="unknown evidence"):
        generator.answer(
            question="测试",
            evidence=_evidence(),
            retrieval_score=3,
        )


def test_evidence_planner_is_verified_and_passed_before_answer_generation() -> None:
    client = SequenceFakeClient(
        [
            {
                "items": [
                    {
                        "focus": "复制和分裂次数",
                        "source_id": "E1",
                        "quote": "染色体只复制一次，而细胞分裂两次",
                    }
                ]
            },
            {
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
                "confidence": 0.98,
            },
        ]
    )
    generator = GroundedAnswerGenerator(  # type: ignore[arg-type]
        client, use_evidence_planner=True
    )

    answer = generator.answer(
        question="减数分裂有什么特点？",
        evidence=_evidence(),
        retrieval_score=4.2,
    )

    assert answer.status == AnswerStatus.SUPPORTED
    assert client.calls == 2


def test_invalid_model_citation_is_retried_without_relaxing_verification() -> None:
    client = SequenceFakeClient(
        [
            {
                "status": "supported",
                "claims": [
                    {
                        "text": "第一次输出引用错误。",
                        "citations": [{"source_id": "E1", "quote": "不存在的教材引语"}],
                    }
                ],
                "confidence": 0.9,
            },
            {
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
                "confidence": 0.98,
            },
        ]
    )
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    answer = generator.answer(
        question="减数分裂有什么特点？",
        evidence=_evidence(),
        retrieval_score=4.2,
    )

    assert answer.status == AnswerStatus.SUPPORTED
    assert client.calls == 2


def test_negative_retry_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        GroundedAnswerGenerator(FakeClient({}), max_validation_retries=-1)  # type: ignore[arg-type]


def test_invalid_optional_planner_item_does_not_block_strict_final_answer() -> None:
    client = SequenceFakeClient(
        [
            {
                "items": [
                    {
                        "focus": "错误的内部计划项",
                        "source_id": "E1",
                        "quote": "原文里不存在",
                    }
                ]
            },
            {
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
                "confidence": 0.98,
            },
        ]
    )
    generator = GroundedAnswerGenerator(  # type: ignore[arg-type]
        client, use_evidence_planner=True, max_validation_retries=0
    )

    answer = generator.answer(
        question="减数分裂有什么特点？",
        evidence=_evidence(),
        retrieval_score=4.2,
    )

    assert answer.status == AnswerStatus.SUPPORTED
    assert client.calls == 2
