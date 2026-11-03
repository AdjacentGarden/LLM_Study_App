"""Prompt-cache accounting and stable-prefix layout of model requests."""

from __future__ import annotations

import json

from adaptive_learning.llm.prompt_cache import cached_input_tokens


def test_cached_input_tokens_reads_each_provider_dialect():
    assert cached_input_tokens({"prompt_cache_hit_tokens": 640, "prompt_cache_miss_tokens": 10}) == 640
    assert cached_input_tokens({"prompt_tokens_details": {"cached_tokens": 23040}}) == 23040
    assert cached_input_tokens({"input_tokens_details": {"cached_tokens": 5}}) == 5
    assert cached_input_tokens({"cache_read_input_tokens": 7}) == 7
    assert cached_input_tokens({"prompt_tokens": 9}) == 0
    assert cached_input_tokens({"prompt_cache_hit_tokens": True}) == 0


def test_client_usage_totals_count_deepseek_cache_hits():
    from adaptive_learning.llm.client import LLMConfig, OpenAICompatibleClient

    client = OpenAICompatibleClient(LLMConfig(base_url="https://api.deepseek.com", api_key="k",
                                              model="deepseek-v4-flash"))
    try:
        client._record_usage({"usage": {"prompt_tokens": 1000, "completion_tokens": 20,
                                        "prompt_cache_hit_tokens": 896,
                                        "prompt_cache_miss_tokens": 104}})
        totals = client.usage_totals()
    finally:
        client.close()
    assert totals["input_tokens"] == 1000 and totals["cached_input_tokens"] == 896


class _Recorder:
    def __init__(self, responses):
        self.calls: list[dict] = []
        self.responses = list(responses)

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses.pop(0)


def test_grounded_payloads_lead_with_stable_material_and_end_with_question():
    from typing import cast

    from adaptive_learning.llm.client import OpenAICompatibleClient
    from adaptive_learning.rag.grounded_qa import GroundedAnswerGenerator

    client = _Recorder([
        {"answer_type": "concept", "needs_retrieval": True},
        {"answer": "熵描述系统的无序程度。"},
    ])
    generator = GroundedAnswerGenerator(cast(OpenAICompatibleClient, client))
    route, _ = generator.route("什么是熵？", conversation_context={"history": []})
    generator._general_answer("什么是熵？", {"pages": 3}, None, {"history": []}, route=route)
    routed, general = (json.loads(call["user"]) for call in client.calls)
    assert list(routed)[-1] == "question"
    assert list(general)[-1] == "question"
    assert list(general).index("source_coverage") < list(general).index("answer_policy")


def test_grounded_answer_orders_teaching_preferences_before_scope_keys():
    from typing import cast

    from adaptive_learning.llm.client import OpenAICompatibleClient
    from adaptive_learning.rag.grounded_qa import EvidenceChunk, GroundedAnswerGenerator

    client = _Recorder([{
        "status": "supported",
        "claims": [{"text": "熵描述系统的无序程度。",
                    "citations": [{"source_id": "E1", "quote": "熵描述系统的无序程度。"}]}],
        "confidence": 0.9,
    }])
    generator = GroundedAnswerGenerator(cast(OpenAICompatibleClient, client))
    generator.answer(
        question="什么是熵？",
        evidence=[EvidenceChunk(source_id="E1", page_number=1, text="熵描述系统的无序程度。")],
        retrieval_score=1.0,
        teaching_context={"guidance_styles": ["examples"]},
    )
    keys = list(json.loads(client.calls[0]["user"]))
    for key in ("teaching_preferences", "presentation_instruction"):
        for later in ("source_coverage", "response_scope", "answer_policy", "coverage_plan"):
            assert keys.index(key) < keys.index(later)


def test_teaching_unit_writer_orders_stable_material_before_repair_context():
    from types import SimpleNamespace

    from adaptive_learning.personalization.teaching_unit_engine import UnitEngine

    seen: dict = {}

    class Generator:
        def _writer_shapes(self):
            return {"explanation": {"title": "", "explanation": "", "source_ids": []}}

        def _structured(self, **kwargs):
            seen.update(json.loads(kwargs["user"]))
            return {"component": {}}

    def pack(_unit):
        return {"sources": {}}

    engine = object.__new__(UnitEngine)
    engine.g = Generator()
    engine.by_id = {"p1": {"scope_conditions": []}}
    engine.chapter = SimpleNamespace(title="第一章")
    engine.run = SimpleNamespace(identity={"epoch": 1})
    engine._pack = pack
    unit = {
        "unit_id": "p1.explanation",
        "candidate": {"title": "旧稿"},
        "work": {
            "kind": "explanation", "owner_id": "p1", "focus_ids": ["p1"], "label": "目标",
            "mode": "repair", "revision": 2, "attempt_nonce": "n", "feedback": [], "history": [],
            "rejected_design": {"old": True}, "latest_invalid_draft": {"bad": True},
        },
    }
    engine._write_one(unit)
    keys = list(seen)
    for stable in ("output_shape", "engine"):
        for repair in ("previous_candidate", "rejected_design", "latest_invalid_draft",
                       "prior_failed_designs"):
            assert keys.index(stable) < keys.index(repair)


def test_assistant_turn_message_ends_with_request_text():
    from adaptive_learning.api.assistant_routes import TurnRequest, _user_message

    body = TurnRequest.model_validate({
        "contract_version": 1, "run_id": "r" * 16, "step_id": "s" * 16, "step_index": 0,
        "request": "帮我整理成笔记", "tools": ["save_note"],
        "context": {"book_id": "b1", "book_title": "物理", "course_id": "c1",
                    "source_version": "v1", "chapter_id": "ch1", "page": 3,
                    "selection": "能量守恒"},
        "evidence": [{"evidence_id": "e1", "page": 3, "quote": "能量守恒"}],
    })
    text = _user_message(body)
    assert text.endswith("用户请求：帮我整理成笔记")
    assert text.index("[e1]") < text.index("用户选中的原文")
