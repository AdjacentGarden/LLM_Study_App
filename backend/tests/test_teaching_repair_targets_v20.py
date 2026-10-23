"""Actual v19 routing replay only; stored content is not quality-approved."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence

ALL = {"explanation", "flashcard", "practice"}


@pytest.fixture(scope="module")
def actual():
    return json.loads((Path(__file__).parent / "fixtures/teaching_v19_repair_routing.json").read_text())


def state(actual):
    points = [teaching._Point.model_validate(row) for row in actual["candidate"]["points"]]
    lesson = teaching._Lesson.model_validate(actual["candidate"]["lesson"])
    fact = teaching._FactReview.model_validate(actual["fact_review_routing_projection"])
    teacher = teaching._Review.model_validate(actual["teacher_review"])
    return teaching.TeachingContentGenerator(None), points, lesson, fact, teacher


@pytest.fixture
def clean(actual):
    generator, points, lesson, fact, teacher = state(actual)
    # Synthetic all-true routing state, never used as semantic approval.
    fact.accepted = True
    fact.issues.clear()
    for row in [*fact.points, fact.lesson]:
        row.accepted = row.source_entailment = row.scope_exceptions = True
        row.issues.clear()
        if hasattr(row, "failed_components"):
            row.failed_components = []
        components = row.components.values() if isinstance(row.components, dict) else [getattr(row.components, name) for name in ALL]
        for proof in components:
            proof.source_entailment = proof.scope_exceptions = True
            proof.findings.clear()
    teacher.accepted = True
    teacher.issues.clear()
    for row in teacher.points:
        row.explanation_ok = row.flashcard_ok = row.practice_ok = row.evidence_ok = True
        row.issues.clear()
    teacher.lesson.reading_ok = teacher.lesson.examples_ok = teacher.lesson.checkpoints_ok = teacher.lesson.evidence_ok = True
    teacher.lesson.issues.clear()
    return generator, points, lesson, fact, teacher


def assert_global(generator, points, lesson, plan):
    assert plan == (
        {point.point_id: ALL for point in points},
        set(generator._lesson_components(lesson)) | {"reading_source_ids"},
    )


def test_actual_ten_top_issue_grammars_route_complete_ids_and_zero_based_slots(actual):
    generator, points, lesson, _, teacher = state(actual)
    expected = [
        ({"kp_e54a48da99a35e27": {"explanation"}}, set()),
        ({"kp_e54a48da99a35e27": {"practice"}}, set()),
        ({"kp_876deca6f6081f84": {"practice"}}, set()),
        ({"kp_f31a91f60999a100": {"practice"}}, set()),
        ({}, {"reading_source_ids"}), ({}, {"example_1"}), ({}, {"example_2"}),
        ({}, {"checkpoint_1"}), ({}, {"checkpoint_2"}), ({}, {"checkpoint_3"}),
    ]
    assert len(teacher.issues) == 10
    assert [generator._teacher_issue_targets(issue, points, lesson) for issue in teacher.issues] == expected


def test_actual_full_plan_preserves_22_point_components_and_approved_example(actual):
    generator, points, lesson, fact, teacher = state(actual)
    before = (fact.model_dump(), teacher.model_dump())
    plan, lesson_targets = generator._combined_repair_plan(fact, teacher, points, lesson)
    expected = {point.point_id: {"practice"} for point in points}
    for point_id in ("kp_e54a48da99a35e27", "kp_f7e0f2c6d8b4d9dd", "kp_a9e36b6f0861c03e"):
        expected[point_id].add("explanation")
    expected["kp_a9e36b6f0861c03e"].add("flashcard")
    assert actual["before_v19"]["repair_component_count"] == 39
    assert plan == expected
    assert sum(len(fields) for fields in plan.values()) == 17
    assert 3 * len(points) - sum(len(fields) for fields in plan.values()) == 22
    assert "flashcard" not in plan["kp_e54a48da99a35e27"]
    assert "explanation" not in plan["kp_876deca6f6081f84"]
    assert lesson_targets == {"opening", "summary", "reading_source_ids", "example_1", "example_2", "checkpoint_1", "checkpoint_2", "checkpoint_3"}
    assert fact.lesson.components["example_3"].source_entailment
    assert fact.lesson.components["example_3"].scope_exceptions
    returned = lesson.model_copy(deep=True)
    for example in returned.worked_examples:
        example.title += "（模拟额外改写）"
    merged = generator._merge_lesson_repair(lesson, returned, lesson_targets)
    assert merged.worked_examples[0] != lesson.worked_examples[0]
    assert merged.worked_examples[1] != lesson.worked_examples[1]
    assert merged.worked_examples[2] == lesson.worked_examples[2]
    assert (fact.model_dump(), teacher.model_dump()) == before


@pytest.mark.parametrize("component", sorted(ALL))
@pytest.mark.parametrize("space", [" ", "  ", "\t"])
def test_component_space_complete_known_point_id_is_exact(clean, component, space):
    generator, points, lesson, _, _ = clean
    point_id = points[0].point_id
    assert generator._teacher_issue_targets(f"{component}{space}{point_id}：需修。", points, lesson) == ({point_id: {component}}, set())
    for suffix in ("0", "_extra", "x"):
        assert generator._teacher_issue_targets(f"{component}{space}{point_id}{suffix}：未知。", points, lesson) is None


@pytest.mark.parametrize("component", sorted(ALL))
def test_short_row_label_requires_known_owner_and_cannot_name_a_foreign_point(clean, component):
    generator, points, lesson, _, _ = clean
    owner, foreign = points[:2]
    issue = f"{component}：需修该组件。"
    assert generator._teacher_issue_targets(issue, points, lesson) is None
    assert generator._teacher_issue_targets(issue, points, lesson, owner_point_id=owner.point_id) == ({owner.point_id: {component}}, set())
    assert generator._teacher_issue_targets(issue, points, lesson, owner_point_id="kp_0000000000000000") is None
    assert generator._teacher_issue_targets(f"{component}：{foreign.point_id}的问题。", points, lesson, owner_point_id=owner.point_id) is None
    assert generator._teacher_issue_targets(f"{component} {foreign.point_id}：问题。", points, lesson, owner_point_id=owner.point_id) is None


@pytest.mark.parametrize("kind", ["item", "practice", "card", "flashcard", "card_alias"])
def test_short_row_foreign_item_card_aliases_veto_owner_without_prefix_collision(clean, kind):
    generator, points, lesson, fact, teacher = clean
    owner, foreign = points[9], points[0]

    def alias(point):
        return {
            "item": point.practice.item_id,
            "practice": f"practice_{point.practice.item_id}",
            "card": point.flashcard.card_id,
            "flashcard": f"flashcard_{point.flashcard.card_id}",
            "card_alias": f"card_{point.flashcard.card_id}",
        }[kind]

    assert alias(owner).endswith("_10") and alias(foreign).endswith("_1")
    own_issue = f"practice: {alias(owner)} 自身题目需修。"
    assert generator._teacher_issue_targets(own_issue, points, lesson, owner_point_id=owner.point_id) == ({owner.point_id: {"practice"}}, set())
    foreign_issue = f"practice: {alias(foreign)} 属于其他组件。"
    assert generator._teacher_issue_targets(foreign_issue, points, lesson, owner_point_id=owner.point_id) is None
    teacher.accepted = False
    row = teacher.points[9]
    row.issues = [foreign_issue]
    assert row.evidence_ok
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({owner.point_id: ALL}, set())


@pytest.mark.parametrize("label", [
    "practice kp_0000000000000000", "practice kp_00000000000000000",
    "explanation\nkp_0000000000000000", "practice_unknown", "global issue",
    "lesson.worked_examples[-1]", "lesson.worked_examples[3]", "lesson.worked_examples[10]",
    "lesson.worked_examples[01]", "lesson.worked_examples[1.0]", "lesson.worked_examples[0]_extra",
    "lesson.worked_examples[0][1]", "lesson.worked_examples[0].steps[0]",
    "lesson.checkpoints[-1]", "lesson.checkpoints[3]", "lesson.checkpoints[10]",
    "lesson.checkpoints[00]", "lesson.checkpoints[0]x", "lesson.reading_source_ids_extra",
    "lesson.unknown", "practice：无所属行的短标签",
])
def test_unknown_foreign_or_invalid_slot_top_issue_keeps_global_veto(clean, label):
    generator, points, lesson, fact, teacher = clean
    assert generator._teacher_issue_targets(label + "：需修。", points, lesson) is None
    teacher.accepted = False
    teacher.issues = [f"practice {points[0].point_id}：明确局部问题。", label + "：需修。"]
    assert_global(generator, points, lesson, generator._combined_repair_plan(fact, teacher, points, lesson))


@pytest.mark.parametrize("field,component", [("worked_examples", "example"), ("checkpoints", "checkpoint")])
@pytest.mark.parametrize("slot", [0, 1, 2])
def test_lesson_array_slots_are_zero_based_and_bound_to_existing_candidate(clean, field, component, slot):
    generator, points, lesson, _, _ = clean
    assert generator._teacher_issue_targets(f"lesson.{field}[{slot}]：需修。", points, lesson) == ({}, {f"{component}_{slot + 1}"})


@pytest.mark.parametrize("component", sorted(ALL))
def test_row_short_issue_localization_retains_false_sibling_flags(clean, component):
    generator, points, lesson, fact, teacher = clean
    row = teacher.points[0]
    row.evidence_ok = False
    row.issues = [f"{component}：自身依据不足。"]
    teacher.accepted = False
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({row.point_id: {component}}, set())
    row.flashcard_ok = False
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({row.point_id: {component, "flashcard"}}, set())
    row.issues.append("另有未定位的自身依据问题。")
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({row.point_id: ALL}, set())


@pytest.mark.parametrize("unknown", ["另有未定位的否决。", "foreign_owner"])
def test_mixed_row_issues_with_evidence_true_still_veto_all_own_components(clean, unknown):
    generator, points, lesson, fact, teacher = clean
    owner = teacher.points[0]
    teacher.accepted = False
    owner.issues = ["practice: 已定位的否决。"]
    if unknown == "foreign_owner":
        unknown = f"practice {points[1].point_id}: 属于另一点，不能替本行定位。"
    owner.issues.append(unknown)
    assert owner.evidence_ok and owner.explanation_ok and owner.flashcard_ok and owner.practice_ok
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({owner.point_id: ALL}, set())
    owner.issues.pop()
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({owner.point_id: {"practice"}}, set())
    owner.issues.clear()
    owner.practice_ok = False
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({owner.point_id: {"practice"}}, set())


@pytest.mark.parametrize("category,label,target", [
    ("examples_ok", "lesson.worked_examples[0]", "example_1"),
    ("checkpoints_ok", "lesson.checkpoints[0]", "checkpoint_1"),
    ("reading_ok", "lesson.reading_source_ids", "reading_source_ids"),
])
def test_located_false_lesson_category_retains_only_explicit_members(clean, category, label, target):
    generator, points, lesson, fact, teacher = clean
    teacher.accepted = False
    setattr(teacher.lesson, category, False)
    teacher.lesson.evidence_ok = False
    teacher.lesson.issues = [label + "：明确失败。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, {target})
    if target == "example_1":
        returned = lesson.model_copy(deep=True)
        returned.opening += "非目标导入不允许跟随修改。"
        returned.summary += "非目标总结不允许跟随修改。"
        for example in returned.worked_examples:
            example.title += "（模拟全部改写）"
        for checkpoint in returned.checkpoints:
            checkpoint.question += "并举例。"
        merged = generator._merge_lesson_repair(lesson, returned, {target}).model_dump()
        merged["worked_examples"][0] = lesson.worked_examples[0].model_dump()
        assert merged == lesson.model_dump()


@pytest.mark.parametrize("category,fallback", [
    ("examples_ok", {"worked_examples"}),
    ("checkpoints_ok", {"checkpoints"}),
    ("reading_ok", {"opening", "summary", "reading_source_ids"}),
])
def test_uncovered_false_lesson_category_still_expands_its_category(clean, category, fallback):
    generator, points, lesson, fact, teacher = clean
    teacher.accepted = False
    setattr(teacher.lesson, category, False)
    label, target = ("lesson.summary", {"summary", "reading_source_ids"}) if category != "reading_ok" else ("lesson.worked_examples[0]", {"example_1"})
    teacher.lesson.issues = [label + "：另一个类别有定位问题。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, target | fallback)


@pytest.mark.parametrize("evidence_ok", [True, False])
def test_unknown_lesson_row_and_false_fact_sibling_cannot_be_lost(clean, evidence_ok):
    generator, points, lesson, fact, teacher = clean
    teacher.accepted = False
    teacher.lesson.examples_ok = False
    teacher.lesson.evidence_ok = evidence_ok
    teacher.lesson.issues = ["lesson.worked_examples[0]：已定位。", "另有未定位的全章依据问题。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, set(generator._lesson_components(lesson)) | {"reading_source_ids"})
    teacher.lesson.issues.pop()
    teacher.lesson.evidence_ok = True
    fact.accepted = fact.lesson.accepted = fact.lesson.source_entailment = False
    fact.lesson.components["example_3"].source_entailment = False
    fact.lesson.issues = ["lesson.example_3: 独立事实审核拒绝。"]
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, {"example_1", "example_3"})


@pytest.mark.parametrize("category,fallback", [
    ("examples_ok", {"worked_examples"}),
    ("checkpoints_ok", {"checkpoints"}),
    ("reading_ok", {"opening", "summary", "reading_source_ids"}),
])
def test_pure_false_category_without_issues_keeps_its_conservative_category(clean, category, fallback):
    generator, points, lesson, fact, teacher = clean
    teacher.accepted = False
    setattr(teacher.lesson, category, False)
    assert not teacher.lesson.issues and teacher.lesson.evidence_ok
    assert generator._combined_repair_plan(fact, teacher, points, lesson) == ({}, fallback)


def test_actual_first_repair_batch_passes_unchanged_guard_before_next_batch(actual):
    _, points, lesson, fact, teacher = state(actual)
    source_fixture = json.loads((Path(__file__).parent / "fixtures/teaching_v18_checkpoint_collision_regression.json").read_text())
    evidence = TeachingEvidence(
        sources={key: SourceCitation.model_validate(value) for key, value in source_fixture["sources"].items()},
        point_sources={row["point_id"]: row["point_source_ids"] for row in actual["inputs"]},
        warnings=[], fingerprint="actual-v19-routing-replay",
    )
    reviewed = False
    repair_requests = []
    by_id = {point.point_id: point for point in points}

    class Client:
        def structured(self, **kwargs):
            payload = json.loads(kwargs["user"])
            if reviewed:
                repair_requests.append(payload)
                if len(repair_requests) == 1:
                    return deepcopy(actual["repair_responses"][0])
                raise teaching.TeachingServiceUnavailableError("sentinel reached second repair batch")
            return {
                "points": [by_id[row["point_id"]].model_dump() for row in payload["batch_points"]],
                "lesson": lesson.model_dump() if payload["include_lesson"] else None,
            }

    planner = SimpleNamespace(plan=lambda inputs, sources: {row["point_id"]: row["scope_conditions"] for row in actual["inputs"]})
    generator = teaching.TeachingContentGenerator(Client(), scope_planner=planner)
    generator._inputs = lambda bundle, sources: deepcopy(actual["inputs"])

    def review(*args, **kwargs):
        nonlocal reviewed
        reviewed = True
        return fact, teacher

    generator._review_candidate = review
    bundle = SimpleNamespace(decision=SimpleNamespace(depth=SimpleNamespace(value="standard"), scaffolds=[]))
    chapter = SimpleNamespace(title="actual v19 routing replay")
    with pytest.raises(teaching.TeachingServiceUnavailableError, match="sentinel reached second"):
        generator._generate(bundle, chapter, evidence)
    assert len(repair_requests) == 2
    first = repair_requests[0]["allowed_repairs"]
    assert first["kp_e54a48da99a35e27"] == ["explanation", "practice"]
    assert first["kp_876deca6f6081f84"] == ["practice"]
    assert "flashcard" not in first["kp_e54a48da99a35e27"]
