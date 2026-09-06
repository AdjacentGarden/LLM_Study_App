import pytest

from adaptive_learning.rag.grounded_qa import (
    EvidenceChunk,
    GroundedAnswerGenerator,
    GroundedAnswerValidationError,
    is_definition_question,
)
from adaptive_learning.personalization.generator import _point_title
from test_grounded_qa import SequenceFakeClient


def draft(claim, quote):
    return {
        "status": "supported",
        "confidence": 0.9,
        "claims": [{"text": claim, "citations": [{"source_id": "E1", "quote": quote}]}],
    }


def verdict(supported=True):
    return {
        "relevant": True,
        "reviews": [{"claim_index": 0, "supported": supported, "reason": "检查原文条件与结论"}],
    }


@pytest.mark.parametrize(
    "text",
    [
        "对于非零实数 x，x 的平方大于零。",
        "二分查找要求序列有序，每次将查找区间缩小一半。",
        "辛亥革命发生于1911年。",
        "需求不变时，供给增加通常使均衡价格下降。",
    ],
)
def test_review_protocol_is_not_biology_specific(text):
    client = SequenceFakeClient([draft(text, text), verdict()])
    answer = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question="这个结论有哪些条件？",
        evidence=[EvidenceChunk(source_id="E1", page_number=2, text=text)],
        retrieval_score=3,
    )
    assert answer.semantic_checked and answer.status == "supported"
    assert client.calls == 2


def test_real_quote_with_contradictory_claim_is_rejected():
    text = "只有当 x 不等于零时，才能在等式两边同时除以 x。"
    client = SequenceFakeClient([draft("任何时候都可以除以 x。", text), verdict(False)])
    with pytest.raises(GroundedAnswerValidationError, match="semantic review rejected"):
        GroundedAnswerGenerator(client, use_semantic_review=True, max_validation_retries=0).answer(
            question="可以总是除以 x 吗？",
            evidence=[EvidenceChunk(source_id="E1", page_number=1, text=text)],
            retrieval_score=3,
        )


def test_rejected_claim_is_repaired_then_reviewed_again():
    text = "二分查找要求序列有序。"
    client = SequenceFakeClient(
        [draft("二分查找适用于无序序列。", text), verdict(False), draft(text, text), verdict()]
    )
    answer = GroundedAnswerGenerator(client, use_semantic_review=True).answer(
        question="有什么条件？",
        evidence=[EvidenceChunk(source_id="E1", page_number=1, text=text)],
        retrieval_score=3,
    )
    assert answer.answer == text and client.calls == 4


@pytest.mark.parametrize(
    "bad",
    [
        {"relevant": True, "reviews": []},
        {
            "relevant": True,
            "reviews": [{"claim_index": 1, "supported": True, "reason": "跳过了第0条"}],
        },
        {
            "relevant": True,
            "reviews": [{"claim_index": 0, "supported": "true", "reason": "非布尔类型"}],
        },
        {
            "relevant": False,
            "reviews": [{"claim_index": 0, "supported": True, "reason": "不回答问题"}],
        },
    ],
)
def test_invalid_or_incomplete_reviewer_output_fails_closed(bad):
    text = "测试的原文"
    client = SequenceFakeClient([draft(text, text), bad])
    with pytest.raises(GroundedAnswerValidationError):
        GroundedAnswerGenerator(client, use_semantic_review=True, max_validation_retries=0).answer(
            question="测试？",
            evidence=[EvidenceChunk(source_id="E1", page_number=1, text=text)],
            retrieval_score=2,
        )


@pytest.mark.parametrize(
    "label",
    [
        "摩尔根的果蝇实验：证明基因位于染色体上",
        "条件概率 P(A|B)，要求 P(B)>0",
        "这是一个长度超过三十六个汉字仍然包含非常重要前提和限定条件且绝对不能被机械截断的知识点",
    ],
)
def test_concept_titles_preserve_conditions_and_punctuation(label):
    assert _point_title(label) == label


@pytest.mark.parametrize(
    "question,expected",
    [
        ("DNA 半保留复制是什么意思？", True),
        ("什么是条件概率？", True),
        ("What is a binary search tree?", True),
        ("比较两个概念的含义", False),
        ("怎么用实验证明DNA复制方式是什么？", False),
        ("What is recursion and how does it work?", False),
    ],
)
def test_answer_scope_is_subject_neutral(question, expected):
    assert is_definition_question(question) is expected


def test_definitions_skip_planning_but_not_semantic_review():
    text = "二分查找是每次缩小一半区间的查找方法。"
    client = SequenceFakeClient([draft(text, text), verdict()])
    answer = GroundedAnswerGenerator(
        client, use_semantic_review=True, use_evidence_planner=True
    ).answer(
        question="什么是二分查找？",
        evidence=[EvidenceChunk(source_id="E1", page_number=1, text=text)],
        retrieval_score=3,
    )
    assert answer.semantic_checked and client.calls == 2


@pytest.mark.parametrize("claim,quote", [("主张", "   "), ("   ", "真实的原文")])
def test_whitespace_cannot_bypass_citation_or_claim_validation(claim, quote):
    client = SequenceFakeClient([draft(claim, quote)])
    with pytest.raises(GroundedAnswerValidationError):
        GroundedAnswerGenerator(client, max_validation_retries=0).answer(
            question="测试",
            evidence=[EvidenceChunk(source_id="E1", page_number=1, text="真实的原文")],
            retrieval_score=3,
        )
