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


def test_review_receives_context_that_prevents_cropped_condition_bypass():
    import json
    text = "在忽略空气阻力时，不同质量的物体同时落地。"
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return draft("不同质量的物体总是同时落地。", "不同质量的物体同时落地。")
            data = json.loads(kwargs["user"])
            assert data["source_context"] == [{"source_id": "E1", "page_number": 1, "text": text}]
            return verdict(False)
    with pytest.raises(GroundedAnswerValidationError):
        GroundedAnswerGenerator(Client(), use_semantic_review=True, max_validation_retries=0).answer(
            question="不同质量的物体是否总是同时落地？", evidence=[EvidenceChunk(source_id="E1", page_number=1, text=text)], retrieval_score=3,
        )
    assert len(calls) == 2


def test_review_timeout_never_publishes_or_regenerates_unverified_answer():
    from adaptive_learning.llm.client import LLMTimeoutError
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return draft("二分查找要求有序。", "二分查找要求有序。")
            raise LLMTimeoutError("review deadline")
    with pytest.raises(LLMTimeoutError):
        GroundedAnswerGenerator(Client(), use_semantic_review=True).answer(
            question="二分查找的条件？", evidence=[EvidenceChunk(source_id="E1", page_number=1, text="二分查找要求有序。")], retrieval_score=3,
        )
    assert len(calls) == 2


@pytest.mark.parametrize('stitched', [False, True])
def test_cross_page_evidence_requires_separate_exact_quotes(stitched):
    first = '比较中A使用材料甲，而B使用'
    second = '材料乙，因此两者的材料不同。'
    citations = ([{'source_id': 'E1', 'quote': first + second}] if stitched else [
        {'source_id': 'E1', 'quote': first}, {'source_id': 'E2', 'quote': second}])
    client = SequenceFakeClient([{
        'status': 'supported', 'confidence': .9,
        'claims': [{'text': 'A使用材料甲，B使用材料乙。', 'citations': citations}],
    }, verdict()])
    generator = GroundedAnswerGenerator(client, use_semantic_review=True, max_validation_retries=0)
    kwargs = dict(question='A和B材料有什么区别？', retrieval_score=3, evidence=[
        EvidenceChunk(source_id='E1', page_number=5, text=first),
        EvidenceChunk(source_id='E2', page_number=6, text=second),
    ])
    if stitched:
        with pytest.raises(GroundedAnswerValidationError):
            generator.answer(**kwargs)
        assert client.calls == 1
    else:
        answer = generator.answer(**kwargs)
        assert answer.semantic_checked
        assert [c.page_number for c in answer.claims[0].citations] == [5, 6]


def test_verified_code_quote_retains_original_whitespace_for_semantic_review():
    import json
    text = 'const: immutable interface.\n\tconstexpr: compile-time evaluation.'
    calls = []
    class Client:
        def structured(self, **kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                return draft('The two terms have different roles.', text.replace('\n\t', ' '))
            claim = json.loads(kwargs['user'])['claims'][0]
            assert claim['citations'][0]['quote'] == text
            return verdict()
    answer = GroundedAnswerGenerator(Client(), use_semantic_review=True).answer(
        question='What are their roles?', retrieval_score=3,
        evidence=[EvidenceChunk(source_id='E1', page_number=2, text=text)])
    assert answer.claims[0].citations[0].quote == text
    assert answer.generation_attempts == 1


def test_same_wording_on_different_pages_is_not_dropped():
    value = draft('A supported conclusion.', 'same words')
    value['claims'][0]['citations'].append({'source_id': 'E2', 'quote': 'same words'})
    answer = GroundedAnswerGenerator(SequenceFakeClient([value])).answer(
        question='Explain', retrieval_score=3, evidence=[
            EvidenceChunk(source_id='E1', page_number=2, text='same words'),
            EvidenceChunk(source_id='E2', page_number=3, text='same words')])
    assert [c.page_number for c in answer.claims[0].citations] == [2, 3]
