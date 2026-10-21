from __future__ import annotations

import json
import threading
from typing import Any

import pytest

from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    stable_knowledge_point_id,
)
from adaptive_learning.assessment.models import ResponseType
from adaptive_learning.ingestion.models import BookStructure, ChapterDraft, SourceQuote
from adaptive_learning.llm.client import OpenAICompatibleClient


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


@pytest.mark.parametrize("reject_second", [False, True])
def test_parallel_chapters_keep_semantic_review_and_return_only_a_complete_ordered_bank(
    reject_second: bool,
) -> None:
    value = structure()
    value.chapters.append(value.chapters[0].model_copy(update={"chapter_id": "ch_2", "order": 2}))
    both_generated = threading.Barrier(2)
    both_reviewing = threading.Barrier(2)
    reviewed: list[str] = []

    class ParallelClient:
        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, Any]:
            payload = json.loads(user)
            if "审校器" in system:
                both_reviewing.wait(timeout=2)
                chapter_id = payload["source"]["chapter_id"]
                reviewed.append(chapter_id)
                return {"reviews": [
                    {"knowledge_point_id": item["knowledge_point_id"],
                     "accepted": not (reject_second and chapter_id == "ch_2"),
                     "issues": ["证据不足"] if reject_second and chapter_id == "ch_2" else []}
                    for item in payload["items"]
                ]}
            both_generated.wait(timeout=2)
            result = valid_payload()
            for item, point in zip(result["items"], payload["knowledge_points"], strict=True):
                item["knowledge_point_id"] = point["knowledge_point_id"]
            return result

    generator = DiagnosticItemGenerator(
        ParallelClient(),  # type: ignore[arg-type]
        points_per_chapter=2, validation_retries=0,
    )
    if reject_second:
        with pytest.raises(DiagnosticGenerationError, match="review rejected"):
            generator.generate(value)
    else:
        assert [item.chapter_id for item in generator.generate(value)] == [
            "ch_1", "ch_1", "ch_2", "ch_2",
        ]
    assert sorted(reviewed) == ["ch_1", "ch_2"]


@pytest.mark.parametrize("accepted", [True, False])
def test_empty_tail_choices_are_format_only_and_do_not_bypass_semantic_review(
    accepted: bool,
) -> None:
    payload = valid_payload()
    for item in payload["items"]:
        item["options"].extend(["", " \t\n"])
    # The transport parser preserves the provider response; it does not invent a fifth choice.
    parsed = OpenAICompatibleClient._parse_object(json.dumps(payload, ensure_ascii=False))
    assert len(parsed["items"][0]["options"]) == 6
    reviewed: list[dict[str, Any]] = []

    class ReviewingClient:
        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, Any]:
            if "审校器" not in system:
                assert '"options":["第一项完整陈述","第二项完整陈述","第三项完整陈述","第四项完整陈述"]' in system
                return parsed
            source = json.loads(user)
            reviewed.extend(source["items"])
            return {"reviews": [
                {"knowledge_point_id": item["knowledge_point_id"], "accepted": accepted,
                 "issues": [] if accepted else ["原文不支持指定答案"]}
                for item in source["items"]
            ]}

    generator = DiagnosticItemGenerator(
        ReviewingClient(), validation_retries=0,  # type: ignore[arg-type]
    )
    if accepted:
        items = generator.generate(structure())
        assert [item.correct_option_ids for item in items] == [["0"], ["0"]]
        assert [item.options for item in items] == [item["options"][:4] for item in payload["items"]]
    else:
        with pytest.raises(DiagnosticGenerationError, match="review rejected"):
            generator.generate(structure())
    assert len(reviewed) == 2
    assert all(len(item["options"]) == 4 and all(item["options"]) for item in reviewed)
    assert len(parsed["items"][0]["options"]) == 6  # Normalization does not mutate provider data.


@pytest.mark.parametrize("case", ["real_fifth", "internal_blank", "answer_in_tail", "non_text_tail"])
def test_extra_real_choices_or_ambiguous_answer_positions_are_never_repaired(case: str) -> None:
    payload = valid_payload()
    item = payload["items"][0]
    if case == "real_fifth":
        item["options"].append("这是第五个有实际含义的选项")
    elif case == "internal_blank":
        item["options"].insert(1, "")
    elif case == "answer_in_tail":
        item["options"].append("")
        item["correct_index"] = 4
    else:
        item["options"].append(None)
    client = FakeClient(payload)
    with pytest.raises(DiagnosticGenerationError):
        DiagnosticItemGenerator(
            client, validation_retries=0, verify_items=False,  # type: ignore[arg-type]
        ).generate(structure())
    assert client.calls == 1


@pytest.mark.parametrize("answer", ["   ", "长" * 181])
def test_grounded_rewrite_cannot_replace_a_valid_option_with_blank_or_overlong_text(
    answer: str,
) -> None:
    payload = valid_payload()
    payload["items"][0]["expected_answer"] = answer
    with pytest.raises(DiagnosticGenerationError, match="1 to 180"):
        DiagnosticItemGenerator(
            FakeClient(payload), validation_retries=0,  # type: ignore[arg-type]
            allow_grounded_answer_rewrite=True,
        ).generate(structure())


def test_grounded_rewrite_uses_answer_length_instead_of_a_broad_label_for_readability() -> None:
    value = structure()
    chapter = value.chapters[0]
    label = "甲的概念同时涉及定义、历史及多种应用情景，需要根据原文缩小问题范围。" * 2
    chapter.knowledge_points = [label]
    chapter.knowledge_point_evidence = {label: [SourceQuote(page_number=1, quote="甲是一个用于测试的概念。")]}
    captured: list[dict[str, Any]] = []

    class ReviewingClient:
        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, Any]:
            payload = json.loads(user)
            if "审校器" in system:
                assert payload["source"]["knowledge_points"][0]["label"] == label
                return {"reviews": [{"knowledge_point_id": stable_knowledge_point_id("ch_1", label),
                                     "accepted": True, "issues": []}]}
            captured.append(payload)
            result = valid_payload()
            result["items"] = result["items"][:1]
            result["items"][0]["knowledge_point_id"] = stable_knowledge_point_id("ch_1", label)
            result["items"][0]["expected_answer"] = "甲是一个用于测试的概念。"
            return result

    items = DiagnosticItemGenerator(
        ReviewingClient(), allow_grounded_answer_rewrite=True,  # type: ignore[arg-type]
    ).generate(value)
    assert captured[0]["knowledge_points"][0]["minimum_distractor_characters"] == 8
    assert items[0].options[0] == "甲是一个用于测试的概念。"
    assert items[0].knowledge_point_labels == [label]
