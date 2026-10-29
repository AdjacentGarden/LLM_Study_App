"""Offline routing regressions; no writer/reviewer call or content approval.

The stored v17 candidate, teacher review, factual review and repair responses
are immutable observations. These tests establish repair scope and preserve the
unchanged-content guard; they do not approve the stored repair's teaching.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning.personalization import teaching_content as teaching

ALL_FIELDS = {"explanation", "flashcard", "practice"}


@pytest.fixture(scope="module")
def actual():
    return json.loads(
        (Path(__file__).parent / "fixtures/teaching_v17_repair_regression.json").read_text()
    )


@pytest.fixture
def routing(actual):
    points = [teaching._Point.model_validate(row) for row in actual["candidate"]["points"]]
    lesson = teaching._Lesson.model_validate(actual["candidate"]["lesson"])
    proof = {"source_entailment": True, "scope_exceptions": True, "findings": []}
    summary = "此处仅建立修复路由测试所需的完整父级结构，不表示语义已获验证。"
    fact = teaching._FactReview.model_validate(
        {
            "accepted": True,
            "issues": [],
            "points": [
                {
                    "point_id": point.point_id,
                    "accepted": True,
                    "source_entailment": True,
                    "scope_exceptions": True,
                    "issues": [],
                    "support_summary": summary,
                    "failed_components": [],
                    "components": {name: deepcopy(proof) for name in ALL_FIELDS},
                }
                for point in points
            ],
            "lesson": {
                "accepted": True,
                "source_entailment": True,
                "scope_exceptions": True,
                "issues": [],
                "support_summary": summary,
                "components": {
                    name: deepcopy(proof)
                    for name in teaching.TeachingContentGenerator._lesson_components(lesson)
                },
            },
        }
    )
    teacher = teaching._Review.model_validate(
        {
            "accepted": True,
            "issues": [],
            "points": [
                {
                    "point_id": point.point_id,
                    "explanation_ok": True,
                    "flashcard_ok": True,
                    "practice_ok": True,
                    "evidence_ok": True,
                    "issues": [],
                }
                for point in points
            ],
            "lesson": {
                "reading_ok": True,
                "examples_ok": True,
                "checkpoints_ok": True,
                "evidence_ok": True,
                "issues": [],
            },
        }
    )
    return teaching.TeachingContentGenerator(None), points, lesson, fact, teacher


def alias_for(point, kind):
    return {
        "point_id": point.point_id,
        "point": f"point_{point.point_id}",
        "explanation": f"explanation_{point.point_id}",
        "card_id": point.flashcard.card_id,
        "flashcard": f"flashcard_{point.flashcard.card_id}",
        "card": f"card_{point.flashcard.card_id}",
        "item_id": point.practice.item_id,
        "practice": f"practice_{point.practice.item_id}",
    }[kind]


@pytest.mark.parametrize(
    "kind,fields",
    [
        ("point_id", ALL_FIELDS),
        ("point", ALL_FIELDS),
        ("explanation", {"explanation"}),
        ("card_id", {"flashcard"}),
        ("flashcard", {"flashcard"}),
        ("card", {"flashcard"}),
        ("item_id", {"practice"}),
        ("practice", {"practice"}),
    ],
)
def test_exact_leading_candidate_aliases_route_only_the_named_component(routing, kind, fields):
    generator, points, lesson, _, _ = routing
    point = points[0]
    issue = f"  {alias_for(point, kind)}：这个明确目标需要修复。"
    assert generator._teacher_issue_targets(issue, points, lesson) == (
        {point.point_id: fields},
        set(),
    )


@pytest.mark.parametrize(
    "alias,targets",
    [
        ("lesson_examples", {"worked_examples"}),
        ("lesson_checkpoints", {"checkpoints"}),
        ("lesson_opening", {"opening", "reading_source_ids"}),
        ("lesson_summary", {"summary", "reading_source_ids"}),
        ("lesson_example_1", {"example_1"}),
        ("lesson_example_3", {"example_3"}),
        ("lesson_checkpoint_1", {"checkpoint_1"}),
        ("lesson_checkpoint_3", {"checkpoint_3"}),
    ],
)
def test_exact_existing_lesson_aliases_do_not_expand_unrelated_targets(routing, alias, targets):
    generator, points, lesson, _, _ = routing
    assert generator._teacher_issue_targets(f"{alias} 需修改。", points, lesson) == ({}, targets)


@pytest.mark.parametrize("kind", ["card_id", "flashcard", "card", "item_id", "practice"])
def test_ascii_boundary_does_not_route_item_10_to_item_1(routing, kind):
    generator, points, lesson, _, _ = routing
    first, tenth = points[0], points[9]
    first_alias, tenth_alias = alias_for(first, kind), alias_for(tenth, kind)
    assert first_alias.endswith("_1") and tenth_alias.endswith("_10")
    component = "flashcard" if kind in {"card_id", "flashcard", "card"} else "practice"
    assert generator._teacher_issue_targets(f"{tenth_alias}: 修复。", points, lesson) == (
        {tenth.point_id: {component}},
        set(),
    )
    for suffix in ["0", "_unknown", "x"]:
        assert (
            generator._teacher_issue_targets(f"{tenth_alias}{suffix}: 未知。", points, lesson)
            is None
        )


@pytest.mark.parametrize(
    "issue",
    [
        "lesson_example_10: 当前没有这个例题。",
        "lesson_checkpoint_10: 当前没有这个检查题。",
        "lesson_example_0: 编号无效。",
        "lesson_example_4: 超出实际例题。",
        "lesson_examples_extra: 不是已知标签。",
        "example_1: 不接受无lesson前缀的猜测。",
        "checkpoint_1: 不接受无lesson前缀的猜测。",
        "全章还有未定位的问题。",
        "point_unknown: 不是当前候选。",
        "",
    ],
)
def test_unknown_global_or_out_of_range_labels_do_not_become_local_targets(routing, issue):
    generator, points, lesson, _, _ = routing
    assert generator._teacher_issue_targets(issue, points, lesson) is None


def test_nonleading_identifier_mentions_cannot_turn_global_prose_into_local_approval(routing):
    generator, points, lesson, _, _ = routing
    alias = alias_for(points[0], "practice")
    for issue in [f"全章问题，例如{alias}。", f"unknown_{alias}: 不是合法前缀。"]:
        assert generator._teacher_issue_targets(issue, points, lesson) is None


def test_top_issue_targets_an_all_true_row_and_keeps_original_review_issues(routing):
    generator, points, lesson, fact, teacher = routing
    teacher.accepted = False
    teacher.issues = [f"{alias_for(points[0], 'practice')} 答案仍存在问题。"]
    original = teacher.model_dump()
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == (
        {points[0].point_id: {"practice"}},
        set(),
    )
    assert teacher.model_dump() == original
    assert (
        generator._review_feedback(teacher, {point.point_id for point in points})[-1]["issues"]
        == teacher.issues
    )


def test_top_precise_lesson_issue_does_not_mark_any_approved_point_pending(routing):
    generator, points, lesson, fact, teacher = routing
    teacher.accepted = False
    teacher.issues = ["lesson_checkpoint_2: 改写这一项检查题。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, {"checkpoint_2"})


def assert_global_plan(generator, points, lesson, plan):
    assert plan == (
        {point.point_id: ALL_FIELDS for point in points},
        set(generator._lesson_components(lesson)) | {"reading_source_ids"},
    )


@pytest.mark.parametrize("kind", ["unknown_top", "false_parent_only", "fact_top"])
def test_unlocated_rejection_stays_pending_and_fact_veto_is_not_masked(routing, kind):
    generator, points, lesson, fact, teacher = routing
    teacher.accepted = False
    teacher.issues = [f"{alias_for(points[0], 'practice')} 已定位的练习问题。"]
    if kind == "unknown_top":
        teacher.issues.append("此外还有未定位的全章问题。")
    elif kind == "false_parent_only":
        teacher.issues = []
    else:
        fact.accepted = False
        fact.issues = ["事实审核还有未定位的全局问题。"]
    if kind in {"unknown_top", "false_parent_only"}:
        with pytest.raises(teaching._TeachingReviewFormatError):
            generator._combined_repair_plan(fact, teacher, points, lesson)
    else:
        assert_global_plan(
            generator, points, lesson, generator._combined_repair_plan(fact, teacher, points, lesson)
        )


def test_row_evidence_false_with_all_own_component_issues_only_repairs_those_components(routing):
    generator, points, lesson, fact, teacher = routing
    teacher.accepted = False
    row = teacher.points[0]
    row.evidence_ok = False
    row.issues = [f"{alias_for(points[0], 'practice')} 只定位自己的练习。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == (
        {points[0].point_id: {"practice"}},
        set(),
    )
    row.flashcard_ok = False
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == (
        {points[0].point_id: {"practice", "flashcard"}},
        set(),
    )


@pytest.mark.parametrize(
    "damage", ["no_issues", "unknown", "foreign_point", "lesson_target", "mixed"]
)
def test_row_evidence_false_without_complete_own_localization_keeps_point_fallback(routing, damage):
    generator, points, lesson, fact, teacher = routing
    teacher.accepted = False
    row = teacher.points[0]
    row.evidence_ok = False
    row.issues = {
        "no_issues": [],
        "unknown": ["本点另有未定位的依据问题。"],
        "foreign_point": [f"{alias_for(points[1], 'practice')} 不能替自己的组件定位。"],
        "lesson_target": ["lesson_example_1 不能代替本点的引用问题。"],
        "mixed": [f"{alias_for(points[0], 'practice')} 已定位。", "另有未知问题。"],
    }[damage]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == (
        {points[0].point_id: ALL_FIELDS},
        set(),
    )


def test_fact_and_teacher_targets_are_unioned_without_weakening_the_fact_rejection(routing):
    generator, points, lesson, fact, teacher = routing
    fact.accepted = False
    row = fact.points[0]
    row.accepted = row.source_entailment = False
    row.failed_components = ["explanation"]
    row.components.explanation.source_entailment = False
    teacher.accepted = False
    teacher.issues = [f"{alias_for(points[0], 'practice')} 另需修练习。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == (
        {points[0].point_id: {"explanation", "practice"}},
        set(),
    )


def test_actual_v17_routing_keeps_rna_approved_and_recorded_unchanged_guard_intact(actual):
    points = [teaching._Point.model_validate(row) for row in actual["candidate"]["points"]]
    lesson = teaching._Lesson.model_validate(actual["candidate"]["lesson"])
    teacher = teaching._Review.model_validate(actual["teacher"])
    fact = teaching._FactReview.model_validate(actual["fact_review"])
    generator = teaching.TeachingContentGenerator(None)
    original = deepcopy(actual)
    fields, targets = generator._combined_repair_plan(fact, teacher, points, lesson)
    expected = {
        row.point_id: set(row.failed_components) for row in fact.points if row.failed_components
    }
    assert fields == expected
    assert targets == {
        "opening",
        "summary",
        "reading_source_ids",
        "example_1",
        "example_2",
        "worked_examples",
        "checkpoints",
    }
    rna_id = actual["terminal_guard_issue"].split()[0]
    rna = next(point for point in points if point.point_id == rna_id)
    assert "RNA与DNA" in rna.title
    assert rna_id not in fields
    # Replay the two actual returned JSONs locally. RNA really was unchanged;
    # it must stay outside pending/rejected_points, rather than disabling the
    # program's unchanged-content check for genuinely requested repairs.
    for response in actual["repair_responses"]:
        returned = [teaching._Point.model_validate(row) for row in response["points"]]
        returned_rna = next(point for point in returned if point.point_id == rna_id)
        assert returned_rna.model_dump() == rna.model_dump()
        rejected_points = {point.point_id: point for point in points if point.point_id in fields}
        assert returned_rna.point_id not in rejected_points
    assert actual == original
    assert teacher.model_dump() == {**actual["teacher"], "lesson": {
        **actual["teacher"]["lesson"], "required_question_edits": []}}
    assert fact.model_dump() == actual["fact_review"]
    # An unlocated veto remains a failed review of the same candidate. It
    # cannot approve it or authorize rewriting every previously accepted point.
    teacher.issues.append("另有未定位的全局问题，不能只修已知组件。")
    with pytest.raises(teaching._TeachingReviewFormatError):
        generator._combined_repair_plan(fact, teacher, points, lesson)
    assert teacher.issues[-1] == "另有未定位的全局问题，不能只修已知组件。"
