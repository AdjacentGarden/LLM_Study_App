from __future__ import annotations

import json
import threading
from copy import deepcopy
from typing import Any

import pytest

from adaptive_learning.assessment.item_generation import (
    DiagnosticGenerationError,
    DiagnosticItemGenerator,
    diagnostic_bank_fingerprint,
    stable_knowledge_point_id,
    structure_fingerprint,
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


class ReviewBoundary:
    """Explicit second client boundary for existing scripted two-role fixtures."""
    def __init__(self, scripted):
        self.scripted = scripted

    def structured(self, **kwargs):
        assert "审校器" in kwargs["system"]
        return self.scripted.structured(**kwargs)


def make_generator(client, **kwargs):
    if kwargs.get("verify_items", True):
        kwargs.setdefault("review_client", ReviewBoundary(client))
    return DiagnosticItemGenerator(client, **kwargs)


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
    generator = make_generator(
        client,
        points_per_chapter=2,
        verify_items=False,  # type: ignore[arg-type]
    )

    items = generator.generate(structure())

    assert [item.response_type for item in items] == [
        ResponseType.SINGLE_CHOICE,
        ResponseType.SINGLE_CHOICE,
    ]
    assert items[0].options[int(items[0].correct_option_ids[0])] == "甲的定义"
    assert items[0].source_pages == [1]
    assert items[1].source_pages == [2]
    assert items[1].options[int(items[1].correct_option_ids[0])] == "乙的作用"
    assert items[0].correct_option_ids != items[1].correct_option_ids
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
    generator = make_generator(
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

    items = make_generator(
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
                    {"required_edits": None, "knowledge_point_id": first, "accepted": True, "issues": []},
                    {"required_edits": {"prompt": True, "option_indices": []},
                        "knowledge_point_id": second,
                        "accepted": False,
                        "issues": ["原文不足以回答题目"],
                    },
                ]
            }

    client = ReviewClient()
    generator = make_generator(
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
    writer = FakeClient(payload)
    class LengthReviewer:
        calls = 0
        def structured(self, *, user, **kwargs):
            self.calls += 1
            request = json.loads(user)
            assert request['items'][0]['choice_length_metrics']['correct_is_uniquely_longest']
            return {'reviews': [{"required_edits": {"prompt": True, "option_indices": []}, 'knowledge_point_id': row['knowledge_point_id'],
                'accepted': False, 'issues': ['正确项独自更完整，长度和具体程度明显暗示答案。']}
                for row in request['items']]}
    reviewer = LengthReviewer()

    with pytest.raises(DiagnosticGenerationError, match="review rejected"):
        DiagnosticItemGenerator(
            writer, review_client=reviewer,
            points_per_chapter=2,
            validation_retries=0,
        ).generate(structure())
    assert writer.calls == reviewer.calls == 1


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
                    {"required_edits": None if (not (reject_second and chapter_id == "ch_2")) else {"prompt": True, "option_indices": []}, "knowledge_point_id": item["knowledge_point_id"],
                     "accepted": not (reject_second and chapter_id == "ch_2"),
                     "issues": ["证据不足"] if reject_second and chapter_id == "ch_2" else []}
                    for item in payload["items"]
                ]}
            both_generated.wait(timeout=2)
            result = valid_payload()
            for item, point in zip(result["items"], payload["knowledge_points"], strict=True):
                item["knowledge_point_id"] = point["knowledge_point_id"]
            return result

    generator = make_generator(
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
                {"required_edits": None if (accepted) else {"prompt": True, "option_indices": []}, "knowledge_point_id": item["knowledge_point_id"], "accepted": accepted,
                 "issues": [] if accepted else ["原文不支持指定答案"]}
                for item in source["items"]
            ]}

    generator = make_generator(
        ReviewingClient(), validation_retries=0,  # type: ignore[arg-type]
    )
    if accepted:
        items = generator.generate(structure())
        for item, original in zip(items, payload["items"], strict=True):
            assert sorted(item.options) == sorted(original["options"][:4])
            assert item.options[int(item.correct_option_ids[0])] == original["expected_answer"]
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
        make_generator(
            client, validation_retries=0, verify_items=False,  # type: ignore[arg-type]
        ).generate(structure())
    assert client.calls == 1


@pytest.mark.parametrize("answer", ["   ", "长" * 601])
def test_grounded_rewrite_cannot_replace_a_valid_option_with_blank_or_overlong_text(
    answer: str,
) -> None:
    payload = valid_payload()
    payload["items"][0]["expected_answer"] = answer
    with pytest.raises(DiagnosticGenerationError, match="1 to 600|diagnostic generation failed"):
        make_generator(
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
                return {"reviews": [{"required_edits": None, "knowledge_point_id": stable_knowledge_point_id("ch_1", label),
                                     "accepted": True, "issues": []}]}
            captured.append(payload)
            result = valid_payload()
            result["items"] = result["items"][:1]
            result["items"][0]["knowledge_point_id"] = stable_knowledge_point_id("ch_1", label)
            result["items"][0]["expected_answer"] = "甲是一个用于测试的概念。"
            result["items"][0]["options"][0] = "甲是一个用于测试的概念。"
            return result

    items = make_generator(
        ReviewingClient(), allow_grounded_answer_rewrite=True,  # type: ignore[arg-type]
    ).generate(value)
    assert "minimum_distractor_characters" not in captured[0]["knowledge_points"][0]
    assert captured[0]["choice_length_rule"]["correct_answer_source"] == "expected_answer"
    assert captured[0]["choice_length_rule"]["evaluation"] == "independent_semantic_review"
    assert captured[0]["choice_length_rule"]["maximum_option_characters"] == 600
    assert items[0].options[int(items[0].correct_option_ids[0])] == "甲是一个用于测试的概念。"
    assert items[0].knowledge_point_labels == [label]


def test_production_default_uses_grounded_understanding_but_explicit_zero_is_legacy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaptive_learning.config import get_settings

    try:
        for raw, expected in [(None, True), ("0", False), ("1", True), ("false", False), ("true", True)]:
            if raw is None:
                monkeypatch.delenv("DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", raising=False)
            else:
                monkeypatch.setenv("DIAGNOSTIC_ALLOW_GROUNDED_ANSWER_REWRITE", raw)
            get_settings.cache_clear()
            assert get_settings().diagnostic_allow_grounded_answer_rewrite is expected
    finally:
        get_settings.cache_clear()


def test_grounded_answer_mismatch_is_rejected_without_rewriting_or_reviewing_it() -> None:
    payload = valid_payload()
    payload["items"][0]["expected_answer"] = "这是另一个与题目选项不同的回答"
    client = FakeClient(payload)

    with pytest.raises(DiagnosticGenerationError, match="do not silently replace"):
        make_generator(
            client, allow_grounded_answer_rewrite=True, validation_retries=0,
        ).generate(structure())  # type: ignore[arg-type]

    assert payload["items"][0]["options"][0] == "甲的定义"
    assert client.calls == 1


@pytest.mark.parametrize("tamper_accepted", [False, True])
def test_rejected_concept_question_receives_full_previous_candidate_and_localized_repair(
    tamper_accepted: bool,
) -> None:
    initial = valid_payload()
    first, second = [row["knowledge_point_id"] for row in initial["items"]]
    # A long, item-specific final issue must survive; the old concatenated
    # first-1000-character string lost precisely this repair instruction.
    issue = "定义逐字复述；" * 170 + "最后一条：应判断给定条件能支持的关系，不是机械换词。"
    repaired = deepcopy(initial)
    repaired["items"][0].update(
        prompt="观察对象满足原文中甲所要求的条件，应作出哪个判断？",
        options=["可以将该对象归入甲所描述的概念范围", "它只能证明另一对象必定完成任务",
                 "它意味着甲所需的条件可以完全省略", "它只能用来确定另一任务持续的时间"],
        expected_answer="可以将该对象归入甲所描述的概念范围",
    )
    generation_payloads = []
    reviewer_payloads = []

    class RepairClient:
        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, Any]:
            payload = json.loads(user)
            if "审校器" in system:
                reviewer_payloads.append(payload)
                assert set(payload["source"]) == {"book_title", "chapter_id", "chapter_title", "knowledge_points"}
                assert "previous_candidate" not in payload and "repair_feedback" not in payload
                rejected = len(reviewer_payloads) == 1
                return {"reviews": [
                    {"required_edits": None if (not (rejected and row['knowledge_point_id'] == first)) else {"prompt": True, "option_indices": []}, "knowledge_point_id": row['knowledge_point_id'],
                     "accepted": not (rejected and row['knowledge_point_id'] == first),
                     "issues": [issue] if rejected and row['knowledge_point_id'] == first else []}
                    for row in payload['items']]}
            generation_payloads.append(payload)
            assert "正确选项必须与 expected_answer 逐字相同" in system
            assert "先确定一个最小判断目标" in system
            assert "正确选项文字必须与输入的 label 逐字相同" not in system
            if len(generation_payloads) == 1:
                return deepcopy(initial)
            if len(generation_payloads) == 2:
                assert payload["previous_candidate"]["items"] == initial["items"]
                assert payload["repair_feedback"]["reviews"][0]["issues"] == [issue]
                assert payload["preserve_accepted_items"] == [initial["items"][1]]
                value = deepcopy(repaired)
                if tamper_accepted:
                    value["items"][1]["prompt"] = "不能改写已经审核通过的另一道题吗？"
                return value
            assert tamper_accepted
            assert payload["repair_feedback"]["stage"] == "repair_scope"
            assert payload["repair_feedback"]["changed_accepted_ids"] == [second]
            assert payload["previous_candidate"]["items"][1] == initial["items"][1]
            return deepcopy(repaired)

    items = make_generator(
        RepairClient(), allow_grounded_answer_rewrite=True, validation_retries=2,
    ).generate(structure())  # type: ignore[arg-type]

    assert items[0].prompt == repaired["items"][0]["prompt"]
    assert items[0].expected_answer == repaired["items"][0]["expected_answer"]
    assert items[1].prompt == initial["items"][1]["prompt"]
    assert len(generation_payloads) == (3 if tamper_accepted else 2)
    # Exact unchanged approvals are retained inside this invocation only;
    # unauthorized edits fail before spending another review request.
    assert len(reviewer_payloads) == 2
    assert reviewer_payloads[-1]["items"][0]["prompt"] == items[0].prompt
    assert len(reviewer_payloads[-1]["items"]) == 1


def test_reviewer_truthy_string_is_not_an_approval() -> None:
    class InvalidReviewer:
        def structured(self, *, system: str, user: str, **kwargs: object) -> dict[str, Any]:
            if "审校器" not in system:
                return valid_payload()
            return {"reviews": [{"required_edits": None, "knowledge_point_id": row["knowledge_point_id"], "accepted": "true", "issues": []}
                                for row in valid_payload()["items"]]}

    with pytest.raises(DiagnosticGenerationError, match="review schema is invalid"):
        make_generator(InvalidReviewer(), validation_retries=0).generate(structure())  # type: ignore[arg-type]


def test_answer_positions_are_balanced_deterministic_and_preserve_reviewed_semantics() -> None:
    from collections import Counter

    original = make_generator(FakeClient(valid_payload()), verify_items=False).generate(structure())
    bank = [item.model_copy(update={"item_id": f"reviewed_{i}"}, deep=True)
            for i in range(19) for item in original[:1]]
    before = [item.model_dump() for item in bank]
    first = DiagnosticItemGenerator._balance_options(bank, seed="book-source")
    second = DiagnosticItemGenerator._balance_options(bank, seed="book-source")
    assert first == second
    assert [item.model_dump() for item in bank] == before
    counts = Counter(item.correct_option_ids[0] for item in first)
    assert set(counts) == {"0", "1", "2", "3"}
    assert max(counts.values()) - min(counts.values()) == 1
    for old, shuffled in zip(bank, first, strict=True):
        assert sorted(old.options) == sorted(shuffled.options)
        assert old.options[int(old.correct_option_ids[0])] == shuffled.options[int(shuffled.correct_option_ids[0])]
        assert old.model_dump(exclude={"options", "correct_option_ids"}) == shuffled.model_dump(exclude={"options", "correct_option_ids"})
    assert first != DiagnosticItemGenerator._balance_options(bank, seed="other-book")


def test_semantic_review_never_falls_back_to_writer_client() -> None:
    writer = FakeClient(valid_payload())
    with pytest.raises(ValueError, match="separate independent"):
        DiagnosticItemGenerator(writer)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="separate independent"):
        DiagnosticItemGenerator(writer, review_client=writer)  # type: ignore[arg-type]
    assert writer.calls == 0


@pytest.mark.parametrize("accepted", [True, False])
def test_same_flash_model_still_requires_separate_review_verdict(accepted) -> None:
    from adaptive_learning.llm.client import LLMConfig

    writer = FakeClient(valid_payload())
    writer.config = LLMConfig(base_url="https://api.deepseek.com/v1", api_key="unused",
                              model="deepseek-flash", thinking_enabled=False)
    reviewer = FakeClient({"reviews": [{
        "knowledge_point_id": row["knowledge_point_id"], "accepted": accepted,
        "issues": [] if accepted else ["Controlled same-model independent semantic veto"],
        "required_edits": None if accepted else {"prompt": True, "option_indices": []},
    } for row in valid_payload()["items"]]})
    reviewer.config = writer.config
    generator = DiagnosticItemGenerator(writer, review_client=reviewer, validation_retries=0)
    if accepted:
        assert len(generator.generate(structure())) == 2
    else:
        with pytest.raises(DiagnosticGenerationError, match="same-model independent semantic veto"):
            generator.generate(structure())
    assert writer.calls == reviewer.calls == 1


def test_reviewer_failure_is_not_replaced_by_a_writer_verdict() -> None:
    from adaptive_learning.llm.client import LLMError

    writer = FakeClient(valid_payload())
    class UnavailableReviewer:
        def structured(self, **kwargs):
            assert "同一概念层级" in kwargs["system"]
            assert "题干已明示或同义给出完整答案" in kwargs["system"]
            raise LLMError("independent reviewer unavailable")
    with pytest.raises(LLMError, match="independent reviewer unavailable"):
        DiagnosticItemGenerator(writer, review_client=UnavailableReviewer(), validation_retries=2).generate(structure())  # type: ignore[arg-type]
    assert writer.calls == 1  # Unknown reviewer outcomes must not start a new paid writer attempt.
    assert writer.calls == 1


def test_bank_protocol_identity_invalidates_model_prompt_and_mode_changes(monkeypatch) -> None:
    import adaptive_learning.assessment.item_generation as module

    value = structure()
    structure_hash = structure_fingerprint(value)
    arguments = {"writer_revision": "deepseek:model-a", "review_revision": "pucoding:gpt-6-sol"}
    baseline = diagnostic_bank_fingerprint(value, **arguments)
    assert baseline == diagnostic_bank_fingerprint(value, **arguments)
    for change in ({"writer_revision": "deepseek:model-b"}, {"review_revision": "pucoding:gpt-other"},
                   {"allow_grounded_answer_rewrite": False}, {"points_per_chapter": 2}):
        assert diagnostic_bank_fingerprint(value, **(arguments | change)) != baseline
    monkeypatch.setattr(module, "_ITEM_REVIEW_SYSTEM", module._ITEM_REVIEW_SYSTEM + " new criteria")
    assert diagnostic_bank_fingerprint(value, **arguments) != baseline
    assert structure_fingerprint(value) == structure_hash
    prompt_revision = diagnostic_bank_fingerprint(value, **arguments)
    monkeypatch.setattr(module, "DIAGNOSTIC_BANK_VERSION", module.DIAGNOSTIC_BANK_VERSION + "+revision")
    assert diagnostic_bank_fingerprint(value, **arguments) != prompt_revision


def test_public_shuffle_keeps_original_option_ids_and_replays_pending_turn_verbatim() -> None:
    from adaptive_learning.assessment.engine import AdaptiveAssessmentEngine
    from adaptive_learning.assessment.interview import InterviewOrchestrator
    from adaptive_learning.assessment.models import AssessmentResponse, InterviewPhase

    item = make_generator(FakeClient(valid_payload()), verify_items=False).generate(structure())[0]
    orchestrator = InterviewOrchestrator(AdaptiveAssessmentEngine(min_items=1, max_items=1))
    positions = set()
    # This tests seed diversity, not a promise that any four sessions cover
    # all positions. Full-bank four-position balancing has its own regression.
    for number in range(128):
        session = orchestrator.start(user_id='reader', book_id='book', book_title='教材',
                                     chapter_titles=['第一章'], items=[item])
        session.session_id = f'deterministic-session-{number}'
        session.phase = InterviewPhase.ADAPTIVE_DIAGNOSIS
        session.pending_turn = None
        turn = orchestrator.next_diagnostic(session, [item])
        for option in turn.options:
            assert option['label'] == item.options[int(option['id'])]
        positions.add(next(index for index, option in enumerate(turn.options)
                           if option['id'] == item.correct_option_ids[0]))
        restored = type(session).model_validate_json(session.model_dump_json())
        assert orchestrator.next_diagnostic(restored, [item]) == turn
        answer = AssessmentResponse(item_id=item.item_id, answer='', selected_option_ids=item.correct_option_ids,
                                    confidence=0.8, response_seconds=12)
        assert orchestrator.record_diagnostic(restored, answer, [item]).score == 1
    assert positions == {0, 1, 2, 3}


def test_length_risks_reach_independent_review_and_all_feedback_reaches_writer() -> None:
    initial = valid_payload()
    for index, row in enumerate(initial['items']):
        answer = ('正确结论甲' if index == 0 else '正确结论乙') * 8
        row.update(expected_answer=answer, options=[answer, '干扰陈述甲乙丙丁', '干扰陈述乙丙丁甲', '干扰陈述丙丁甲乙'])
    captured, reviews = [], []
    class RepairingClient:
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            if '审校器' in system:
                reviews.append(payload)
                for row in payload['items']:
                    assert row['choice_length_metrics']['correct_characters'] == 40
                return {'reviews': [{"required_edits": None if len(reviews) > 1 else {"prompt": False,
                        "option_indices": [index for index, text in enumerate(row['options'])
                                           if text != row['expected_answer']]}, 'knowledge_point_id': row['knowledge_point_id'],
                    'accepted': len(reviews) > 1,
                    'issues': [] if len(reviews) > 1 else ['正确项独自明显更长、更具体，提示了答案；应重写同类选项。']}
                    for row in payload['items']]}
            captured.append(payload)
            assert payload['choice_length_rule']['evaluation'] == 'independent_semantic_review'
            assert all('minimum_distractor_characters' not in row for row in payload['knowledge_points'])
            if len(captured) == 1:
                return deepcopy(initial)
            assert payload['previous_candidate']['items'] == initial['items']
            feedback = payload['repair_feedback']
            assert feedback['stage'] == 'semantic_review'
            assert [row['knowledge_point_id'] for row in feedback['reviews']] == [row['knowledge_point_id'] for row in initial['items']]
            assert all(row['issues'] for row in feedback['reviews'])
            repaired = deepcopy(initial)
            for row in repaired['items']:
                row['options'][1:] = [('干扰结论' + term) * 5 for term in ('甲', '乙', '丙')]
            return repaired
    items = make_generator(RepairingClient(), allow_grounded_answer_rewrite=True, validation_retries=1).generate(structure())
    assert len(items) == 2 and len(captured) == len(reviews) == 2
    assert all(min(map(len, item.options)) >= 22 for item in items)


def test_batch_repairs_schema_and_content_failures_together_without_serializing_exceptions() -> None:
    from diagnostic_test_inputs import controlled_application
    value = structure()
    value.chapters[0].knowledge_points.append('丙的条件')
    value.chapters[0].knowledge_point_evidence['丙的条件'] = [SourceQuote(page_number=2, quote='过程乙需要条件甲、条件乙。')]
    repaired = valid_payload()
    repaired['items'].append({**deepcopy(repaired['items'][0]),
                              'knowledge_point_id': stable_knowledge_point_id('ch_1', '丙的条件'),
                              'expected_answer': '丙的条件',
                              'options': ['丙的条件', '这是第一个干扰选项', '这是第二个干扰选项', '这是第三个干扰选项']})
    broken = deepcopy(repaired)
    broken['items'][0]['type'] = 'explanation'
    broken['items'][1]['options'][1] = '长' * 601
    broken['items'][2]['expected_answer'] = '正确结论丙' * 8
    broken['items'][2]['options'][0] = broken['items'][2]['expected_answer']
    broken['items'][2]['options'][1] = broken['items'][2]['options'][2]
    calls = []
    review_calls = []
    class RepairingClient:
        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            if '审校器' in system:
                review_calls.append(payload)
                return {'reviews': [{"required_edits": None, 'knowledge_point_id': row['knowledge_point_id'], 'accepted': True, 'issues': []}
                                    for row in payload['items']]}
            calls.append(payload)
            repaired['items'][2] = controlled_application(payload)
            if len(calls) == 1:
                broken['items'][2] = deepcopy(repaired['items'][2])
                broken['items'][2]['application_task']['source_refs'] *= 2
                return broken
            errors = payload['repair_feedback']['items']
            assert [row['knowledge_point_id'] for row in errors] == [row['knowledge_point_id'] for row in broken['items']]
            assert 'choice' in errors[0]['issues'][0]
            assert errors[1]['issues'][0]['type'] == 'value_error'
            assert 'ctx' not in errors[1]['issues'][0] and 'input' not in errors[1]['issues'][0]
            assert errors[2]['issues'][0]['type'] == 'value_error'
            assert 'source_refs and four outcomes must be unique' in errors[2]['issues'][0]['msg']
            return repaired
    result = make_generator(RepairingClient(), allow_grounded_answer_rewrite=True, validation_retries=1).generate(value)
    assert len(result) == 3 and len(calls) == 2 and len(review_calls) == 1


@pytest.mark.parametrize('size', [2, 4, 7])
@pytest.mark.parametrize('accepted', [True, False])
def test_comparable_short_choices_pass_length_gate_but_still_require_concept_review(size, accepted) -> None:
    payload = valid_payload()
    payload['items'][0]['options'] = [letter * size for letter in ('甲', '乙', '丙', '丁')]
    payload['items'][0]['expected_answer'] = payload['items'][0]['options'][0]
    reviewed = []
    class ReviewingClient:
        def structured(self, *, system, user, **kwargs):
            if '审校器' not in system:
                return payload
            value = json.loads(user)
            reviewed.append(value)
            return {'reviews': [{"required_edits": None if (accepted) else {"prompt": True, "option_indices": []}, 'knowledge_point_id': row['knowledge_point_id'], 'accepted': accepted,
                                 'issues': [] if accepted else ['只是定义命名，未测理解']}
                                for row in value['items']]}
    generator = make_generator(ReviewingClient(), allow_grounded_answer_rewrite=True, validation_retries=0)
    if accepted:
        items = generator.generate(structure())
        assert sorted(map(len, items[0].options)) == [size] * 4
    else:
        with pytest.raises(DiagnosticGenerationError, match='review rejected'):
            generator.generate(structure())
    assert len(reviewed) == 1


def test_length_risk_cannot_override_an_independent_rejection() -> None:
    payload = valid_payload()
    for row in payload['items']:
        row['expected_answer'] = '正确结论甲' * 8
        row['options'] = [row['expected_answer'], '短小干扰陈述甲乙', '短小干扰陈述乙丙', '短小干扰陈述丙丁']
    writer = FakeClient(payload)
    class Reviewer:
        calls = 0
        def structured(self, *, user, **kwargs):
            self.calls += 1
            request = json.loads(user)
            assert all(row['choice_length_metrics']['correct_is_uniquely_longest']
                       for row in request['items'])
            return {'reviews': [{"required_edits": {"prompt": True, "option_indices": []}, 'knowledge_point_id': row['knowledge_point_id'], 'accepted': False,
                'issues': ['正确项明显独自更长，具体程度提示了答案。']} for row in request['items']]}
    reviewer = Reviewer()
    with pytest.raises(DiagnosticGenerationError) as failure:
        DiagnosticItemGenerator(writer, review_client=reviewer,
            allow_grounded_answer_rewrite=True, validation_retries=0).generate(structure())
    feedback = failure.value.feedback
    assert feedback['stage'] == 'semantic_review' and len(feedback['reviews']) == 2
    assert all(not row['accepted'] and row['issues'] for row in feedback['reviews'])
    assert writer.calls == reviewer.calls == 1
