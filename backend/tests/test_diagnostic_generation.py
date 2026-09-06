from __future__ import annotations

from typing import Any

import pytest

from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    stable_knowledge_point_id,
)
from adaptive_learning.assessment.models import ResponseType
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote


class FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.calls = 0

    def structured(self, **_: object) -> dict[str, Any]:
        self.calls += 1
        return self.payload


def test_grounded_rewrite_requires_review_and_keeps_point_identity() -> None:
    class ReviewingClient:
        def structured(self, **kwargs: Any) -> dict[str, Any]:
            if "审校器" in kwargs["system"]:
                return {"reviews":[{"knowledge_point_id":stable_knowledge_point_id("ch_1",label),
                                    "accepted":True,"issues":[]}
                                   for label in ["甲的定义","乙的作用"]]}
            payload = valid_payload()
            payload["items"][0]["expected_answer"] = "甲是一个用于测试的概念。"
            return payload

    with pytest.raises(ValueError, match="require semantic review"):
        DiagnosticItemGenerator(ReviewingClient(), verify_items=False,
                                allow_grounded_answer_rewrite=True)
    items = DiagnosticItemGenerator(ReviewingClient(),allow_grounded_answer_rewrite=True).generate(structure())
    assert items[0].options[0] == "甲是一个用于测试的概念。"
    assert items[0].knowledge_point_labels == ["甲的定义"]
    assert items[0].source_pages == [1]


def structure() -> BookStructure:
    chapter = ChapterDraft(
        chapter_id="ch_1",
        order=1,
        title="第一章",
        start_page=1,
        end_page=2,
        summary="概述",
        knowledge_points=["甲的定义", "乙的作用"],
        source_block_ids=["b1"],
        evidence=[SourceQuote(page_number=1, quote="甲是一个用于测试的概念。")],
        knowledge_point_evidence={
            "甲的定义": [SourceQuote(page_number=1, quote="甲是一个用于测试的概念。")],
            "乙的作用": [SourceQuote(page_number=2, quote="乙能够帮助完成另一项任务。")],
        },
    )
    return BookStructure(
        title="测试书",
        summary="整书概述",
        chapters=[chapter],
        source_page_count=2,
    )


def valid_payload() -> dict[str, Any]:
    first = stable_knowledge_point_id("ch_1", "甲的定义")
    second = stable_knowledge_point_id("ch_1", "乙的作用")
    return {
        "items": [
            {
                "knowledge_point_id": first,
                "type": "choice",
                "prompt": "下面哪项准确说明了甲？",
                "options": [
                    "甲的定义",
                    "这是第一个干扰选项",
                    "这是第二个干扰选项",
                    "这是第三个干扰选项",
                ],
                "correct_index": 0,
                "expected_answer": "甲的定义",
                "rubric": [],
                "difficulty": -0.2,
                "estimated_seconds": 35,
            },
            {
                "knowledge_point_id": second,
                "type": "choice",
                "prompt": "下面哪项准确说明了乙的作用？",
                "options": [
                    "乙的作用",
                    "乙会阻止另一项任务正常完成",
                    "乙只负责记录而不会影响任务",
                    "乙会让所有任务立即停止运行",
                ],
                "correct_index": 0,
                "expected_answer": "乙的作用",
                "rubric": [],
                "difficulty": 0.4,
                "estimated_seconds": 45,
            },
        ]
    }


def test_generator_builds_balanced_private_items_from_verified_evidence() -> None:
    client = FakeClient(valid_payload())
    generator = DiagnosticItemGenerator(
        client,
        points_per_chapter=2,
        verify_items=False,  # type: ignore[arg-type]
    )

    items = generator.generate(structure())

    assert [item.response_type for item in items] == [
        ResponseType.SINGLE_CHOICE,
        ResponseType.SINGLE_CHOICE,
    ]
    assert items[0].correct_option_ids == ["0"]
    assert items[0].source_pages == [1]
    assert items[1].source_pages == [2]
    assert items[1].correct_option_ids == ["0"]
    assert client.calls == 1


def test_generator_rejects_duplicate_choice_options_after_retry() -> None:
    payload = valid_payload()
    payload["items"][0]["options"] = [
        "这是一个待替换选项",
        "这是两个相同的干扰项",
        "这是两个相同的干扰项",
        "这是另一个不同干扰项",
    ]
    client = FakeClient(payload)
    generator = DiagnosticItemGenerator(
        client,
        points_per_chapter=2,
        validation_retries=1,
        verify_items=False,  # type: ignore[arg-type]
    )

    with pytest.raises(DiagnosticGenerationError):
        generator.generate(structure())

    assert client.calls == 2


def test_generator_ignores_points_without_verified_evidence() -> None:
    value = structure()
    value.chapters[0].knowledge_point_evidence.pop("乙的作用")
    first_only = valid_payload()
    first_only["items"] = first_only["items"][:1]
    client = FakeClient(first_only)

    items = DiagnosticItemGenerator(
        client,
        points_per_chapter=2,
        verify_items=False,  # type: ignore[arg-type]
    ).generate(value)

    assert len(items) == 1
    assert items[0].knowledge_point_labels == ["甲的定义"]


def test_independent_review_rejects_unanswerable_item() -> None:
    class ReviewClient:
        def __init__(self) -> None:
            self.calls = 0

        def structured(self, **_: object) -> dict[str, Any]:
            self.calls += 1
            if self.calls == 1:
                return valid_payload()
            first = stable_knowledge_point_id("ch_1", "甲的定义")
            second = stable_knowledge_point_id("ch_1", "乙的作用")
            return {
                "reviews": [
                    {"knowledge_point_id": first, "accepted": True, "issues": []},
                    {
                        "knowledge_point_id": second,
                        "accepted": False,
                        "issues": ["原文不足以回答题目"],
                    },
                ]
            }

    client = ReviewClient()
    generator = DiagnosticItemGenerator(
        client,  # type: ignore[arg-type]
        points_per_chapter=2,
        validation_retries=0,
    )

    with pytest.raises(DiagnosticGenerationError, match="review rejected"):
        generator.generate(structure())

    assert client.calls == 2


def test_concept_value_filter_prefers_mechanism_over_historical_trivia() -> None:
    quote = [SourceQuote(page_number=2, quote="减数分裂时染色体复制一次，细胞连续分裂两次。")]
    conceptual = DiagnosticItemGenerator._point_value(
        "减数分裂是染色体复制一次而细胞分裂两次的过程", quote
    )
    historical = DiagnosticItemGenerator._point_value("1891年科学家描述了减数分裂的全过程", quote)

    assert conceptual > historical + 2


def test_visually_guessable_choice_is_rejected_instead_of_becoming_open_answer() -> None:
    payload = valid_payload()
    payload["items"][0]["options"] = ["甲的定义", "错一", "错二", "错三"]
    client = FakeClient(payload)

    with pytest.raises(DiagnosticGenerationError, match="distractors are too short"):
        DiagnosticItemGenerator(
            client,
            points_per_chapter=2,
            validation_retries=0,
            verify_items=False,  # type: ignore[arg-type]
        ).generate(structure())
