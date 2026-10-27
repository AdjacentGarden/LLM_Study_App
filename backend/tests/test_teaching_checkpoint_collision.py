"""Offline structural replay only; mocked reviews make no quality claim."""

from __future__ import annotations

import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_feedback_wire import (
    compact_writer_feedback,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/teaching_v18_checkpoint_collision_regression.json").read_text()
)


def evidence():
    return TeachingEvidence(
        sources={key: SourceCitation.model_validate(value) for key, value in FIXTURE["sources"].items()},
        point_sources={item["point_id"]: item["point_source_ids"] for item in FIXTURE["inputs"]},
        warnings=[],
        fingerprint="actual-v18-diagnostic",
    )


def rows():
    return [teaching._Point.model_validate(point) for point in FIXTURE["points"]]


@pytest.mark.parametrize("index", range(3))
def test_actual_three_failed_lessons_keep_original_rejection_and_precise_target(index):
    raw = FIXTURE["lessons"][index]
    with pytest.raises(teaching.TeachingContentError, match="检查题不能直接复制") as caught:
        teaching.TeachingContentGenerator._validate_lesson(
            teaching._Lesson.model_validate(raw), rows(), evidence()
        )
    detail = caught.value.review_feedback["lesson"]["structural_validation"]
    assert detail["lesson_components"] == ["checkpoint_3"]
    collision, = detail["collisions"]
    assert collision["checkpoint_index"] == 2
    match, = collision["matches"]
    assert match["point_id"] == "kp_0d811232f25fadef"
    assert match["component"] == "practice"
    assert match["component_id"] == "practice_ca3f2b4315c881a297e7_12"
    assert collision["checkpoint_question"] == match["question"]
    assert collision["normalized_question"] == match["normalized_question"]


def test_every_exact_collision_is_named_including_flashcard_without_fuzzy_matching():
    lesson = teaching._Lesson.model_validate(FIXTURE["lessons"][0])
    points = rows()
    lesson.checkpoints[0].question = points[0].flashcard.front
    with pytest.raises(teaching.TeachingContentError) as caught:
        teaching.TeachingContentGenerator._validate_lesson(lesson, points, evidence())
    detail = caught.value.review_feedback["lesson"]["structural_validation"]
    assert detail["lesson_components"] == ["checkpoint_1", "checkpoint_3"]
    assert detail["collisions"][0]["matches"][0]["component_id"] == points[0].flashcard.card_id
    assert detail["collisions"][0]["matches"][0]["component"] == "flashcard"
    # The unchanged second checkpoint resembles an existing task but is not
    # equal under the existing normalized comparison. No new fuzzy gate.
    assert "checkpoint_2" not in detail["lesson_components"]


def test_punctuation_alignment_keeps_the_existing_normalized_equality_gate():
    lesson = teaching._Lesson.model_validate(FIXTURE["lessons"][0])
    lesson.checkpoints[2].question = "  " + lesson.checkpoints[2].question.replace("，", "！")
    with pytest.raises(teaching.TeachingContentError) as caught:
        teaching.TeachingContentGenerator._validate_lesson(lesson, rows(), evidence())
    collision, = caught.value.review_feedback["lesson"]["structural_validation"]["collisions"]
    assert collision["checkpoint_question"] != collision["matches"][0]["question"]
    assert collision["normalized_question"] == collision["matches"][0]["normalized_question"]


@pytest.mark.parametrize("mode", ["exact", "punctuation", "whitespace", "case", "extra_word", "near_task"])
def test_collision_targets_equal_the_legacy_normalized_set_membership(mode):
    lesson = teaching._Lesson.model_validate(FIXTURE["lessons"][0])
    points = rows()
    question = points[0].practice.prompt
    variants = {
        "exact": question,
        "punctuation": question.replace("，", "！") + "？",
        "whitespace": " \n " + question,
        "case": question.swapcase(),
        "extra_word": question + "并举例。",
        "near_task": lesson.checkpoints[0].question,
    }
    lesson.checkpoints[0].question = variants[mode]
    lesson.checkpoints[2].question = "比较两种条件不同的情境时，应如何整理判断所需的前提？"
    # This is the exact original v18 threshold: no fuzzy score, task-level
    # similarity, or additional exclusions are introduced by the metadata.
    legacy_questions = {teaching._normalized(point.practice.prompt) for point in points}
    legacy_questions.update(teaching._normalized(point.flashcard.front) for point in points)
    expected = {
        f"checkpoint_{index}"
        for index, row in enumerate(lesson.checkpoints, 1)
        if teaching._normalized(row.question) in legacy_questions
    }
    if expected:
        with pytest.raises(teaching.TeachingContentError) as caught:
            teaching.TeachingContentGenerator._validate_lesson(lesson, points, evidence())
        actual = caught.value.review_feedback["lesson"]["structural_validation"]["lesson_components"]
        assert set(actual) == expected
    else:
        teaching.TeachingContentGenerator._validate_lesson(lesson, points, evidence())


class ReplayClient:
    def __getattribute__(self, name):
        method = object.__getattribute__(self, name)
        if name != "structured":
            return method

        def adapt(**kwargs):
            from teaching_writer_test_support import advertised_writer_response

            return advertised_writer_response(method(**kwargs), json.loads(kwargs["user"]))

        return adapt

    def __init__(self, *, repair):
        self.requests = []
        self.repair = repair

    def structured(self, **kwargs):
        payload = json.loads(kwargs["user"])
        self.requests.append(payload)
        if payload["batch_points"]:
            ids = {row["point_id"] for row in payload["batch_points"]}
            return {"points": [deepcopy(p) for p in FIXTURE["points"] if p["point_id"] in ids], "lesson": None}
        if payload["previous_candidate"] is None:
            return {"points": [], "lesson": deepcopy(FIXTURE["lessons"][0])}
        lesson = deepcopy(payload["previous_candidate"]["lesson"])
        if self.repair:
            # A synthetic non-identical question tests scheduling and immutable
            # sibling preservation; it is NOT a semantic quality approval.
            lesson["checkpoints"][2]["question"] = "请比较两个满足不同前提的表述，并说明你会如何核对条件后判断结论。"
            lesson["opening"] += "模型擅自改写的非目标导入必须被程序还原。"
            lesson["summary"] += "模型擅自改写的非目标总结必须被程序还原。"
            lesson["worked_examples"][0]["title"] = "非目标例题必须还原"
            lesson["checkpoints"][0]["question"] = "非目标检查题必须还原"
        return {"points": [], "lesson": lesson}


def replay_generator(client):
    planner = SimpleNamespace(plan=lambda inputs, sources: {row["point_id"]: deepcopy(row["scope_conditions"]) for row in FIXTURE["inputs"]})
    generator = teaching.TeachingContentGenerator(client, scope_planner=planner)
    generator._inputs = lambda bundle, sources: deepcopy(FIXTURE["inputs"])
    reviews = []
    applied = []

    def review(points, lesson, *args, **kwargs):
        reviews.append((deepcopy(points), lesson.model_copy(deep=True)))
        passed = SimpleNamespace(model_dump=lambda: {"accepted": True}, _rubric_alignment={})
        return passed, passed

    generator._review_candidate = review
    generator._fact_feedback = lambda *args: []
    generator._review_feedback = lambda *args: []

    def apply(bundle, points, lesson, sources):
        applied.append((deepcopy(points), lesson.model_copy(deep=True)))
        return SimpleNamespace()

    generator._apply = apply
    bundle = SimpleNamespace(decision=SimpleNamespace(depth=SimpleNamespace(value="standard"), scaffolds=[]))
    chapter = SimpleNamespace(title="actual chapter diagnostic")
    return generator, bundle, chapter, reviews, applied


def test_targeted_structural_repair_preserves_all_points_and_every_noncolliding_field():
    client = ReplayClient(repair=True)
    generator, bundle, chapter, reviews, applied = replay_generator(client)
    generator._generate(bundle, chapter, evidence())
    assert Counter(len(row["batch_points"]) for row in client.requests[:3]) == {6: 2, 1: 1}
    assert [len(row["batch_points"]) for row in client.requests[3:]] == [0, 0]
    retry = client.requests[-1]
    assert retry["allowed_lesson_repairs"] == ["checkpoint_3"]
    assert retry["output_contract"]["points_must_be_empty"] is True
    assert retry["previous_candidate"]["points"] == []
    assert len(retry["existing_point_content"]) == 13
    original = deepcopy(retry["previous_candidate"]["lesson"])
    assert len(reviews) == len(applied) == 1
    reviewed_points, reviewed_lesson = reviews[0]
    assert [point.model_dump() for point in reviewed_points] == FIXTURE["points"]
    current = reviewed_lesson.model_dump()
    assert current["checkpoints"][2] != original["checkpoints"][2]
    current["checkpoints"][2] = original["checkpoints"][2]
    assert current == original
    detail = next(row["lesson"]["structural_validation"] for row in retry["review_feedback"] if "lesson" in row)
    assert detail["lesson_components"] == ["checkpoint_3"]
    assert detail["collisions"][0]["matches"][0]["component_id"].endswith("_12")
    assert detail["repair_instruction"] == (
        "只重写lesson_components列出的检查题及其答案与引用，"
        "使题目区别于matches中的练习或闪卡；保留其余检查题、"
        "导入、总结、例题和所有知识点。"
    )


def test_unchanged_duplicate_never_reaches_mocked_reviews_or_apply():
    client = ReplayClient(repair=False)
    generator, bundle, chapter, reviews, applied = replay_generator(client)
    with pytest.raises(teaching.TeachingContentError):
        generator._generate(bundle, chapter, evidence())
    assert Counter(len(row["batch_points"]) for row in client.requests[:3]) == {6: 2, 1: 1}
    assert [len(row["batch_points"]) for row in client.requests[3:]] == [0, 0, 0]
    assert [row["allowed_lesson_repairs"] for row in client.requests[-2:]] == [["checkpoint_3"], ["checkpoint_3"]]
    assert not reviews and not applied


def test_rewriting_only_non_target_siblings_cannot_disguise_an_unchanged_collision():
    class SiblingOnlyClient(ReplayClient):
        def structured(self, **kwargs):
            response = super().structured(**kwargs)
            payload = self.requests[-1]
            if response["lesson"] is not None and payload["previous_candidate"] is not None:
                response["lesson"]["checkpoints"][2] = deepcopy(
                    payload["previous_candidate"]["lesson"]["checkpoints"][2]
                )
            return response

    client = SiblingOnlyClient(repair=True)
    generator, bundle, chapter, reviews, applied = replay_generator(client)
    with pytest.raises(teaching.TeachingContentError):
        generator._generate(bundle, chapter, evidence())
    assert len(client.requests) == 6
    assert not reviews and not applied
    assert client.requests[-1]["allowed_lesson_repairs"] == ["checkpoint_3"]


def test_collision_feedback_survives_lesson_scope_and_compaction_with_named_point():
    with pytest.raises(teaching.TeachingContentError) as caught:
        teaching.TeachingContentGenerator._validate_lesson(
            teaching._Lesson.model_validate(FIXTURE["lessons"][0]), rows(), evidence()
        )
    raw = [caught.value.review_feedback]
    scoped = teaching.TeachingContentGenerator._scoped_feedback(raw, set(), True)
    compact = compact_writer_feedback(scoped, boundaries=[], sources=FIXTURE["sources"])
    assert compact == raw
    assert teaching.TeachingContentGenerator._scoped_feedback(raw, set(), False) == []
