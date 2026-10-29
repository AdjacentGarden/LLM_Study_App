"""Actual H whole-course rejection must not reopen independently accepted points."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest
import test_teaching_repair_targets_v20 as routing

from adaptive_learning.personalization import teaching_content as teaching

actual = routing.actual
clean = routing.clean


@pytest.fixture
def h_routing(clean):
    generator, points, lesson, fact, _ = clean
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/teaching_h_first_assembly_teacher.json").read_text()
    )
    teacher = teaching._Review.model_validate(fixture["teacher_review"])
    assert len(teacher.points) == 12
    points = points[: len(teacher.points)]
    fact.points = fact.points[: len(teacher.points)]
    assert len(points) == len(fact.points) == 12
    # Synthetic all-true routing proofs are never semantic/publication approval.
    for point, proof, verdict in zip(points, fact.points, teacher.points, strict=True):
        point.point_id = proof.point_id = verdict.point_id
    points[0].practice.item_id = "practice_db1e93eed84dee57bb14"
    return generator, points, lesson, fact, teacher


def test_real_first_assembly_reopens_only_two_named_lesson_slots(h_routing):
    generator, points, lesson, fact, teacher = h_routing
    original = teacher.model_dump()
    assert not teacher.accepted
    assert all(
        row.explanation_ok
        and row.flashcard_ok
        and row.practice_ok
        and row.evidence_ok
        and row.issues == []
        for row in teacher.points
    )
    assert teacher.lesson.reading_ok and teacher.lesson.evidence_ok
    assert not teacher.lesson.examples_ok and not teacher.lesson.checkpoints_ok
    plan = generator._build_repair_plan(fact, teacher, points, lesson)
    assert plan.allowed_points == {}
    assert plan.allowed_lesson == {"example_2", "checkpoint_1"}
    assert plan.requirements.exact_lesson == {"example_2", "checkpoint_1"}
    assert not plan.requirements.global_any
    assert teacher.model_dump() == original
    assert all(
        generator._teacher_issue_targets(issue, points, lesson)
        in [({}, {"example_2"}), ({}, {"checkpoint_1"})]
        for issue in teacher.issues
    )


def test_comparison_id_is_not_target_but_independent_practice_veto_is_union(h_routing):
    generator, points, lesson, fact, teacher = h_routing
    target = points[0]
    teacher.points[0].practice_ok = False
    teacher.points[0].issues = [f"{target.practice.item_id}：答案缺少题干要求的必要前提。"]
    plan = generator._build_repair_plan(fact, teacher, points, lesson)
    assert plan.allowed_points == {target.point_id: {"practice"}}
    assert plan.allowed_lesson == {"example_2", "checkpoint_1"}
    assert not plan.requirements.global_any
    assert len(teacher.issues) == 2


@pytest.mark.parametrize("slot", ["example_1", "example_3", "checkpoint_1", "checkpoint_3"])
def test_dot_slot_and_existing_aliases_share_exact_identity(h_routing, slot):
    generator, points, lesson, _, _ = h_routing
    assert generator._teacher_issue_targets(
        f"lesson.{slot}与{points[0].practice.item_id}重复，应只修改前者。", points, lesson
    ) == ({}, {slot})
    assert generator._teacher_issue_targets(f"lesson_{slot}：需修改。", points, lesson) == (
        {},
        {slot},
    )
    group, number = slot.rsplit("_", 1)
    legacy = "worked_examples" if group == "example" else "checkpoints"
    assert generator._teacher_issue_targets(
        f"lesson.{legacy}[{int(number) - 1}]：需修改。", points, lesson
    ) == ({}, {slot})


@pytest.mark.parametrize(
    "label",
    [
        "lesson.example_0",
        "lesson.example_4",
        "lesson.example_20",
        "lesson.checkpoint_4",
        "lesson.example_2x",
        "lesson.example_2_extra",
        "lesson.example_2.problem",
        "lesson.example_2[0]",
        "全章重复，例如lesson.example_2",
        "lesson.unknown_2",
    ],
)
def test_unknown_nonleading_out_of_range_or_suffix_cannot_become_a_real_slot(h_routing, label):
    generator, points, lesson, _, _ = h_routing
    assert generator._teacher_issue_targets(label + "：问题仍存在。", points, lesson) is None


def test_unlocated_top_review_is_format_pending_not_global_rewrite(h_routing):
    generator, points, lesson, fact, teacher = h_routing
    teacher.issues.append("全章还有问题，但未说明在哪个组件。")
    original = teacher.model_dump()
    with pytest.raises(teaching._TeachingReviewFormatError, match="未定位"):
        generator._build_repair_plan(fact, teacher, points, lesson)
    assert teacher.model_dump() == original
    assert not teacher.accepted


def test_unknown_feedback_rechecks_same_candidate_with_existing_format_budget(h_routing):
    generator, points, lesson, _, teacher = h_routing
    bad = teacher.model_dump()
    bad["issues"] = ["lesson.example_99：引用有误，需要重新核对。"]
    good = teacher.model_dump()
    responses = iter([bad, good])
    calls = []

    def structured(**kwargs):
        calls.append(json.loads(kwargs["user"]))
        return deepcopy(next(responses))

    generator._structured = structured
    candidate = {"points": [point.model_dump() for point in points], "lesson": lesson.model_dump()}
    review = generator._request_teacher_review(
        {"candidate": candidate}, {point.point_id for point in points}
    )
    assert len(calls) == teaching.FACT_FORMAT_ATTEMPTS == 2
    assert all(call["candidate"] == candidate for call in calls)
    assert calls[0]["format_feedback"] == []
    assert any("未定位" in row for row in calls[1]["format_feedback"])
    assert review.model_dump() == good and not review.accepted


def test_exhausted_unknown_feedback_stays_fail_closed_without_approval(h_routing):
    generator, points, lesson, _, teacher = h_routing
    bad = teacher.model_dump()
    bad["issues"] = ["无法定位的全章否决。"]
    calls = []

    def structured(**kwargs):
        calls.append(json.loads(kwargs["user"]))
        return deepcopy(bad)

    generator._structured = structured
    candidate = {"points": [point.model_dump() for point in points], "lesson": lesson.model_dump()}
    with pytest.raises(teaching._TeachingReviewFormatError):
        generator._request_teacher_review(
            {"candidate": candidate}, {point.point_id for point in points}
        )
    assert len(calls) == teaching.FACT_FORMAT_ATTEMPTS
    assert all(call["candidate"] == candidate for call in calls)
