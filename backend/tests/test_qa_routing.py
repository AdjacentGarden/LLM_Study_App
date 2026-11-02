"""The model decides intent before retrieval, under the existing QA limits."""
from __future__ import annotations

import json

import pytest
from test_qa_service import FakeIndex

from adaptive_learning.learning_preferences import LearningDesignConfig
from adaptive_learning.llm.client import LLMRefusalError, LLMTimeoutError, remaining_model_time
from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator, GroundedAnswerValidationError
from adaptive_learning.rag.service import TextbookQAService


class Client:
    def __init__(self, replies, events):
        self.replies = iter(replies)
        self.events = events
        self.calls = []
        self.deadlines = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        self.deadlines.append(remaining_model_time())
        self.events.append("route" if kwargs["system"].startswith("你是学习问答路由器") else "answer")
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def setup(replies, *, score=3, **kwargs):
    events = []

    class Index(FakeIndex):
        def search(self, question, **options):
            events.append("retrieve")
            return super().search(question, **options)

    client = Client(replies, events)
    service = TextbookQAService(
        book_id="book", index=Index(score), generator=GroundedAnswerGenerator(client), **kwargs,
    )
    return service, client, events


def route(kind="concept", *, retrieval=True, answer=None, detail="normal"):
    return {"answer_type": kind, "needs_retrieval": retrieval, "answer": answer, "detail": detail}


def grounded():
    return {"status": "supported", "claims": [{"text": "染色体复制一次，细胞分裂两次。",
        "citations": [{"source_id": "E1", "quote": "染色体只复制一次，而细胞分裂两次。"}]}]}


@pytest.mark.parametrize("question,kind,answer", [
    ("生命的起源是什么？", "concept", "主流研究认为生命可能由非生命物质逐步演化而来，具体过程仍在研究。"),
    ("米勒实验说明什么？", "fact", "原始地球的某些模拟条件下能形成有机小分子，不能据此说明直接产生生命。"),
    ("DNA和RNA有什么不同？", "comparison", "两者的糖、部分碱基和常见结构不同。"),
    ("求解2x+1=5", "exercise", "x=2。两边减1得2x=4，再除以2。"),
    ("今天只有20分钟怎么复习？", "plan", "先回忆薄弱知识，再用一道练习检验。"),
    ("为什么不能这样判断？", "followup", "因为这个现象不能排除其他原因。"),
    ("帮我算这道题", "clarification", "请提供题干和已知条件。"),
])
def test_direct_routes_answer_once_without_retrieval(question, kind, answer):
    service, client, events = setup([route(kind, retrieval=False, answer=answer)])
    result = service.answer(question)
    assert events == ["route"]
    assert result.answer == answer and result.answer_type == kind
    assert not result.retrieval_requested and result.retrieval_duration_ms == 0
    assert result.claims == result.evidence_pages == [] and not result.semantic_checked
    assert result.generation_attempts == 1 and len(client.calls) == 1
    assert "教材引用未核验" not in result.answer
    cached = service.answer(question)
    assert cached.cache_hit and cached.answer == answer and events == ["route"]
    assert cached.generation_attempts == 0 and cached.stage_duration_ms == {}


@pytest.mark.parametrize("kind,detail", [("concept", "normal"), ("exercise", "normal"), ("concept", "detailed")])
def test_retrieval_happens_after_route_and_keeps_its_answer_policy(kind, detail):
    service, client, events = setup([route(kind, detail=detail), grounded()], generation_budget_seconds=2)
    context = {"chapter": {"title": "减数分裂"}, "history": [{"role": "user", "text": "上个问题"}]}
    result = service.answer("按本书解释", conversation_context=context)
    assert events == ["route", "retrieve", "answer"]
    assert result.status == "supported" and result.evidence_pages == [11]
    assert result.retrieval_requested and result.answer_type == kind
    assert result.generation_attempts == 2 and "routing" in result.stage_duration_ms
    first, second = [json.loads(call["user"]) for call in client.calls]
    assert first["conversation_context"] == context and "evidence" not in first
    assert second["answer_policy"] == {"answer_type": kind, "needs_retrieval": True, "detail": detail}
    assert "解题步骤" in second["response_scope"] if kind == "exercise" else "解题步骤" not in second["response_scope"]
    assert all(0 < value <= 2 for value in client.deadlines)
    assert client.deadlines[1] <= client.deadlines[0]


def test_preferences_do_not_force_course_modules_or_steps_into_chat():
    config = LearningDesignConfig(guidance_styles=["stepByStep"], learning_barriers=["understanding"])
    preferences = {**config.qa_preferences(), "presentation_order": ["steps", "checkpoint"],
                   "support_actions": ["主动追加检查题"]}
    service, client, _ = setup([route(), grounded()])
    service.answer("教材如何定义", teaching_context=preferences)
    for call, key in zip(client.calls, ["preferences", "teaching_preferences"], strict=True):
        payload = json.loads(call["user"])
        assert payload[key]["guidance_styles"] == ["stepByStep"]
        assert "presentation_order" not in payload[key] and "support_actions" not in payload[key]
        assert "非习题也不添加解题步骤" in call["system"]
    assert "presentation_order" not in config.qa_preferences()


def test_no_evidence_fallback_keeps_exercise_policy_and_no_banner():
    service, client, events = setup([route("exercise"), {"answer": "x=2。两边减1再除以2。"}], score=-1)
    result = service.answer("按教材方法求解2x+1=5")
    assert events == ["route", "retrieve", "answer"]
    assert result.status == "general" and result.claims == []
    assert result.answer == "x=2。两边减1再除以2。"
    payload = json.loads(client.calls[1]["user"])
    assert payload["answer_policy"]["answer_type"] == "exercise"
    assert "解题步骤" in payload["response_scope"]


@pytest.mark.parametrize("bad", [{}, {"answer_type": "unknown", "needs_retrieval": True},
    route(retrieval=False, answer=" "), {"answer_type": "concept", "needs_retrieval": "false"}])
def test_invalid_route_gets_one_format_repair_before_any_retrieval(bad):
    service, client, events = setup([bad, route(retrieval=False, answer="直接解释。")])
    result = service.answer("问题")
    assert result.answer == "直接解释。" and result.generation_attempts == 2
    assert events == ["route", "route"]
    assert json.loads(client.calls[1]["user"])["previous_response_for_correction"] == bad


def test_repeated_invalid_route_does_not_silently_start_retrieval():
    service, _, events = setup([{}, {}])
    with pytest.raises(GroundedAnswerValidationError, match="answer route"):
        service.answer("问题")
    assert events == ["route", "route"] and service._inflight == {}


@pytest.mark.parametrize("failure", [LLMTimeoutError("unknown"), LLMRefusalError("refused"),
                                   {"refusal": "refused", "answer": "不可展示"}])
def test_route_transport_failure_or_refusal_never_retries_or_retrieves(failure):
    service, _, events = setup([failure])
    with pytest.raises((LLMTimeoutError, LLMRefusalError)):
        service.answer("问题")
    assert events == ["route"] and service._inflight == {}


def test_unverified_pre_retrieval_answer_cannot_override_grounded_generation():
    service, _, events = setup([route(answer="不可直接展示的预设答复"), grounded()])
    result = service.answer("本书怎么说")
    assert result.status == "supported" and "不可直接展示" not in result.answer
    assert events == ["route", "retrieve", "answer"]


@pytest.mark.parametrize("relation", ["current_chapter", "current_book", "uncertain"])
def test_related_knowledge_searches_before_answer_even_when_model_can_answer_directly(relation):
    decision = {**route(retrieval=False, answer="不应直接展示的通用回答"), "topic_relation": relation}
    service, client, events = setup([decision, grounded()])
    context = {"textbook": {"title": "生物学", "outline": [{"title": "减数分裂"}]},
               "chapter": {"title": "减数分裂", "knowledge_points": ["染色体复制次数"]}}
    result = service.answer("解释一下减数分裂", conversation_context=context)
    assert events == ["route", "retrieve", "answer"]
    assert result.status == "supported" and result.claims and result.evidence_pages == [11]
    assert result.retrieval_requested and result.topic_relation == relation
    assert "不应直接展示" not in result.answer
    assert json.loads(client.calls[0]["user"])["conversation_context"] == context


def test_clearly_unrelated_concept_keeps_direct_answer_in_a_course():
    decision = {**route(retrieval=False, answer="生命起源仍在研究中。"), "topic_relation": "outside"}
    service, _, events = setup([decision])
    result = service.answer("生命的起源是什么", conversation_context={
        "textbook": {"title": "数学"}, "chapter": {"title": "平面向量"},
    })
    assert events == ["route"] and not result.retrieval_requested
    assert result.answer == "生命起源仍在研究中。" and result.topic_relation == "outside"


def test_related_question_uses_general_knowledge_only_after_unsuccessful_retrieval():
    decision = {**route(), "topic_relation": "current_chapter"}
    service, _, events = setup([decision, {"answer": "向量具有大小和方向。"}], score=-1)
    result = service.answer("解释一下向量", conversation_context={"chapter": {"title": "平面向量"}})
    assert events == ["route", "retrieve", "answer"]
    assert result.status == "general" and result.claims == []
    assert result.answer == "向量具有大小和方向。" and result.retrieval_requested


def test_textbook_summary_cannot_be_used_as_a_verified_quote():
    decision = {**route(), "topic_relation": "current_book"}
    invented = grounded()
    invented["claims"][0]["citations"][0]["quote"] = "仅在目录摘要中出现的说法"
    service, _, events = setup([decision, invented])
    result = service.answer("解释本章", conversation_context={
        "textbook": {"summary": "仅在目录摘要中出现的说法"},
    })
    assert events == ["route", "retrieve", "answer"]
    assert result.status == "general" and result.claims == [] and result.evidence_pages == []
