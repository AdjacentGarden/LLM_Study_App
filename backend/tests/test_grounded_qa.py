import json
from typing import Any

import pytest

from adaptive_learning.llm.client import LLMTimeoutError
from adaptive_learning.rag.grounded_qa import (
    AnswerStatus,
    EvidenceChunk,
    GroundedAnswerGenerator,
    _citation_repair_hints,
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


def test_review_keeps_full_cited_and_neighbouring_passages():
    from adaptive_learning.rag.grounded_qa import GroundedAnswer, VerifiedCitation, VerifiedClaim

    class ReviewClient:
        payload: dict = {}

        def structured(self, **kwargs):
            self.payload = json.loads(kwargs["user"])
            return {"relevant": True, "reviews": [
                {"claim_index": 0, "supported": True, "reason": "包含完整条件"}
            ]}

    evidence = [EvidenceChunk(source_id=f"E{i}", page_number=p, text=t) for i, (p, t) in enumerate([
        (10, "仅在满足前页条件时，"), (11, "并非所有情形都成立。结论仅适用于此例。"),
        (11, "同页另一段：仍需检查例外。"), (12, "下一页说明例外情形。"),
        (80, "另一章的不相关内容。"),
    ])]
    answer = GroundedAnswer(status="supported", answer="结论仅适用于此例。", confidence=.9,
                            claims=[VerifiedClaim(text="结论仅适用于此例。", citations=[
                                VerifiedCitation(page_number=11, quote="结论仅适用于此例。")])])
    client = ReviewClient()
    GroundedAnswerGenerator(client)._review("哪些情形成立？", answer, evidence)
    assert client.payload["source_context"] == [e.model_dump() for e in evidence[:4]]
    assert "并非所有情形" in client.payload["source_context"][1]["text"]


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
def test_missing_self_score_still_requires_literal_and_semantic_evidence():
    draft = {'status': 'supported', 'claims': [{'text':'染色体只复制一次。',
        'citations':[{'source_id':'E1', 'quote':'染色体只复制一次'}]}]}
    client = SequenceFakeClient([draft, {'relevant':True, 'reviews':[
        {'claim_index':0, 'supported':True, 'reason':'该陈述与所引教材一致'}]}])
    answer = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question='复制几次？', evidence=_evidence(), retrieval_score=4)
    assert answer.semantic_checked and answer.confidence == 0
    assert client.calls == 2
    # An absent self-score never excuses a fabricated quotation.
    draft['claims'][0]['citations'][0]['quote'] = '染色体复制十次'
    unverified = GroundedAnswerGenerator(FakeClient(draft), max_validation_retries=0).answer(
        question='复制几次？', evidence=_evidence(), retrieval_score=4)
    assert unverified.status == AnswerStatus.GENERAL and unverified.claims == []
    assert not unverified.semantic_checked


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


def test_low_retrieval_score_calls_model_for_general_answer() -> None:
    client = FakeClient({"answer": "牛顿第二定律描述力、质量和加速度之间的关系。"})
    generator = GroundedAnswerGenerator(client)  # type: ignore[arg-type]

    answer = generator.answer(
        question="牛顿第二定律是什么？",
        evidence=_evidence(),
        retrieval_score=-2.5,
    )

    assert answer.status == AnswerStatus.GENERAL
    assert answer.claims == []
    assert "牛顿第二定律" in answer.answer
    assert client.calls == answer.generation_attempts == 1


def test_fabricated_quote_is_never_published_as_evidence() -> None:
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

    answer = generator.answer(question="测试", evidence=_evidence(), retrieval_score=3)
    assert answer.status == AnswerStatus.GENERAL and answer.claims == []
    assert "这是一个没有教材依据的主张。" in answer.answer
    assert client.calls == 1


def test_unknown_source_is_not_exposed_as_evidence() -> None:
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

    answer = generator.answer(question="测试", evidence=_evidence(), retrieval_score=3)
    assert answer.status == AnswerStatus.GENERAL and answer.claims == []
    assert client.calls == 1


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


def test_invalid_model_citation_keeps_answer_without_retry_or_false_proof() -> None:
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

    assert answer.status == AnswerStatus.GENERAL and answer.claims == []
    assert "第一次输出引用错误。" in answer.answer
    assert client.calls == 1


def test_interleaved_source_text_is_not_invented_as_a_verified_quote():
    source = "蓝色按钮每按一次，\n旁注：这是虚构设备。\n计数器增加7。"
    rejected = {"status": "supported", "claims": [{
        "text": "蓝色按钮每按一次，计数器增加7。",
        "citations": [{"source_id": "E1", "quote": "蓝色按钮每按一次，计数器增加7。"}],
    }], "confidence": 0.9}
    calls = []

    class RepairClient:
        def structured(self, **kwargs):
            payload = json.loads(kwargs["user"])
            calls.append(payload)
            assert len(calls) == 1
            assert payload["evidence"][0]["text"] == source
            return rejected

    answer = GroundedAnswerGenerator(RepairClient()).answer(
        question="蓝色按钮的作用是什么？",
        evidence=[EvidenceChunk(source_id="E1", page_number=1, text=source)],
        retrieval_score=1,
    )
    assert answer.generation_attempts == 1 and len(calls) == 1
    assert answer.status == AnswerStatus.GENERAL and answer.claims == []
    assert "蓝色按钮每按一次，计数器增加7。" in answer.answer


@pytest.mark.parametrize("raw", [
    {"claims": None},
    {"claims": [None, {"citations": None}]},
    {"claims": [{"citations": [{"source_id": [], "quote": "invalid"}]}]},
    {"claims": [{"citations": [{"source_id": "E1", "quote": "这是完全不存在的虚构引文内容。"}]}]},
])
def test_citation_repair_hints_do_not_invent_sources_or_fail_on_bad_schema(raw):
    assert _citation_repair_hints(raw, _evidence()) == []


def test_negative_retry_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="must not be negative"):
        GroundedAnswerGenerator(FakeClient({}), max_validation_retries=-1)  # type: ignore[arg-type]


@pytest.mark.parametrize("question", ["减数分裂是什么？", "请详细比较本章所述过程及其条件。"])
@pytest.mark.parametrize("budget", [4096, 10000])
def test_answer_output_budget_is_applied_to_both_short_and_detailed_questions(question, budget):
    calls = []

    class BudgetClient:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 2:
                return {"answer": "模型直接解释相关概念。"}
            return {"status": "insufficient", "claims": [], "confidence": 0,
                    "insufficiency_reason": "测试证据不足"}

    result = GroundedAnswerGenerator(BudgetClient(), max_output_tokens=budget).answer(
        question=question, evidence=_evidence(), retrieval_score=1,
    )
    assert result.status == AnswerStatus.GENERAL
    assert len(calls) == result.generation_attempts == 2
    assert all(call["max_tokens"] == budget for call in calls)


@pytest.mark.parametrize("budget", [0, -1, 10001])
def test_answer_output_budget_must_be_within_supported_limits(budget):
    with pytest.raises(ValueError, match="max_output_tokens"):
        GroundedAnswerGenerator(FakeClient({}), max_output_tokens=budget)


def test_mobile_length_preference_does_not_discard_answer() -> None:
    client = FakeClient(
        {
            "status": "supported",
            "claims": [{
                "text": "这是一段没有控制长度的回答。" * 40,
                "citations": [{"source_id": "E1", "quote": "染色体只复制一次"}],
            }],
            "confidence": 0.9,
        }
    )
    answer = GroundedAnswerGenerator(client, max_validation_retries=0).answer(  # type: ignore[arg-type]
        question="减数分裂有什么特点？", evidence=_evidence(), retrieval_score=3)
    assert answer.status == AnswerStatus.GENERAL and answer.claims == []
    assert answer.answer.endswith("这是一段没有控制长度的回答。" * 40)
    assert client.calls == 1


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
