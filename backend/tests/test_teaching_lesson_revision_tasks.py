"""Replay v7's copied lesson and scoped feedback, without model calls.

The fixture contains generated text and frozen judgments only. Controlled
reviewers below test orchestration, not the scientific quality of new content.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_content import course, lesson_candidate
from test_teaching_v15_integration import FIXED, CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.teaching_feedback_wire import compact_writer_feedback

OBSERVED = json.loads((Path(__file__).parent / "fixtures/teaching_v7_lesson_revision.json").read_text())


def requirements():
    data = OBSERVED["required_changes"]
    return teaching._RepairRequirements(
        exact_lesson=set(data["lesson_components"]),
        lesson_categories=set(data["lesson_categories_any"]),
        global_any=data["candidate_any"],
    )


def test_actual_named_practice_comparisons_survive_lesson_scope_and_compaction():
    raw = [{"accepted": False, "issues": OBSERVED["teacher_overall_issues"]}]
    before = deepcopy(raw)
    wanted = [issue for issue in raw[0]["issues"] if issue.startswith("lesson.")]
    assert len(wanted) == 2 and "kp_" in wanted[0]
    scoped = teaching.TeachingContentGenerator._scoped_feedback(raw, set(), True)
    compact = compact_writer_feedback(scoped, boundaries=[], sources={})
    assert all(issue in compact[0]["issues"] for issue in wanted)
    assert not any(issue.startswith("explanation（kp_") for issue in compact[0]["issues"])
    assert raw == before
    # The point IDs are comparisons, not authorization to repair those points.
    compared = OBSERVED["existing_questions"][0]["point_id"]
    point_wire = teaching.TeachingContentGenerator._scoped_feedback(raw, {compared}, False)
    assert not any(issue in wanted for row in point_wire for issue in row["issues"])


@pytest.mark.parametrize("wrapper", [None, "factual_overall", "previous_review_feedback"])
def test_lesson_comparison_and_foreign_point_feedback_remain_separately_scoped(wrapper):
    first, other = [row["point_id"] for row in OBSERVED["existing_questions"][:2]]
    lesson_issue = f"lesson.worked_examples：例题与{first}、{other}的practice同题，须更换作答目标。"
    point_issue = f"explanation（{other}）：该例题仍不支持本知识点的讲解。"
    raw = {"accepted": False, "issues": [lesson_issue, point_issue]}
    if wrapper == "factual_overall":
        raw = {wrapper: raw}
    elif wrapper:
        raw = {wrapper: [raw]}
    result = teaching.TeachingContentGenerator._scoped_feedback([raw], set(), True)
    overall = result[0].get("factual_overall", result[0])
    assert overall["issues"] == [lesson_issue]
    assert teaching.TeachingContentGenerator._scoped_feedback([raw], {first}, False) == []
    point = teaching.TeachingContentGenerator._scoped_feedback([raw], {other}, False)
    assert point[0].get("factual_overall", point[0])["issues"] == [point_issue]


def test_actual_slot_tasks_expose_each_category_obligation_without_inventing_content():
    lesson = teaching._Lesson.model_validate(OBSERVED["lesson"])
    assert hashlib.sha256(json.dumps(
        lesson.model_dump(), ensure_ascii=False, sort_keys=True
    ).encode()).hexdigest() == OBSERVED["provenance"]["raw_lesson_sha256"]
    tasks = teaching.TeachingContentGenerator._lesson_revision_tasks(
        lesson, set(OBSERVED["allowed_lesson_repairs"]), requirements()
    )
    assert tasks["mode"] == "repair" and tasks["compare_with"] == "existing_questions"
    assert tasks["category_obligations"] == "each_listed_category_not_one_of_them"
    assert len(OBSERVED["existing_questions"]) == 12
    assert {row["component_id"] for row in tasks["editable_slots"]} == {
        f"{kind}_{i}" for kind in ("example", "checkpoint") for i in range(1, 4)
    }
    for row in tasks["editable_slots"]:
        kind, index = row["component_id"].split("_")
        original = (lesson.worked_examples if kind == "example" else lesson.checkpoints)[int(index) - 1]
        assert row["rejected_question"] == getattr(original, "problem" if kind == "example" else "question")
        assert not row["exact_change_required"]  # Actual rejection is per-category.
        assert not ({"answer", "expected_answer", "approved"} & row.keys())
    categories = {row["category"]: row for row in tasks["required_category_edits"]}
    assert set(categories) == {"all", "examples", "checkpoints"}
    assert all(row["minimum_changed_members"] == 1 for row in categories.values())
    assert categories["examples"]["members"] == ["example_1", "example_2", "example_3"]
    assert categories["checkpoints"]["members"] == ["checkpoint_1", "checkpoint_2", "checkpoint_3"]
    assert tasks["preserve_components"] == []
    assert tasks["approval"] == "independent_reviews_required"


def test_narrow_and_list_permissions_do_not_expand_the_repair_or_lose_preservation():
    lesson = teaching._Lesson.model_validate(OBSERVED["lesson"])
    original = lesson.model_dump()
    required = teaching._RepairRequirements(exact_lesson={"checkpoint_2"})
    tasks = teaching.TeachingContentGenerator._lesson_revision_tasks(lesson, {"checkpoint_2"}, required)
    assert tasks["editable_components"] == ["checkpoint_2"]
    assert tasks["required_component_edits"] == ["checkpoint_2"]
    assert tasks["editable_slots"] == [{
        "component_id": "checkpoint_2", "rejected_question": lesson.checkpoints[1].question,
        "exact_change_required": True, "question_change_required": False,
    }]
    assert set(tasks["preserve_components"]) == (
        set(teaching.TeachingContentGenerator._lesson_components(lesson)) | {"reading_source_ids"}
    ) - {"checkpoint_2"}
    grouped = teaching.TeachingContentGenerator._lesson_revision_tasks(
        lesson, {"worked_examples"}, teaching._RepairRequirements(lesson_categories={"examples"})
    )
    assert grouped["editable_components"] == ["example_1", "example_2", "example_3"]
    assert lesson.model_dump() == original


@pytest.mark.parametrize("changed", [None, "examples", "checkpoints"])
def test_actual_copied_response_or_only_one_category_changed_is_still_rejected(changed):
    lesson = teaching._Lesson.model_validate(OBSERVED["lesson"])
    baseline = {"points": [], "lesson": lesson.model_dump()}
    if changed == "examples":
        lesson.worked_examples[0].problem += "请再说明理由。"
    elif changed == "checkpoints":
        lesson.checkpoints[0].question += "请再说明理由。"
    with pytest.raises(teaching._LessonRepairUnchangedError, match="未修改上次被拒绝的类别"):
        teaching.TeachingContentGenerator._validate_candidate_repair([], lesson, baseline, requirements())


class LessonClient(CandidateClient):
    def __init__(self, mode):
        super().__init__()
        self.mode = mode
        self.teacher_requests = []

    def structured(self, *, system, user, **kwargs):
        result = super().structured(system=system, user=user, **kwargs)
        if system != teaching.GENERATION_PROMPT:
            return result
        data = json.loads(user)
        if data["include_lesson"]:
            previous = data["previous_candidate"]
            result["lesson"] = deepcopy(previous["lesson"] if previous else lesson_candidate())
            if previous and self.mode != "unchanged":
                result["lesson"]["worked_examples"][0]["problem"] += "请说明条件变化后如何判断。"
                result["lesson"]["worked_examples"][0]["steps"][0] += FIXED
                result["lesson"]["checkpoints"][0]["question"] += "请说明适用条件。"
                result["lesson"]["checkpoints"][0]["expected_answer"] += FIXED
        return result

    def teacher(self, data):
        self.teacher_requests.append(deepcopy(data))
        result = super().teacher(data)
        # A superficial change can pass the mechanical guard, never the
        # independent semantic veto. This double intentionally keeps rejecting.
        result["accepted"] = False
        result["lesson"]["examples_ok"] = result["lesson"]["checkpoints_ok"] = False
        point_id = data["candidate"]["points"][0]["point_id"]
        result["lesson"]["issues"] = [
            "lesson.worked_examples：例题仍与练习同题。",
            "lesson.checkpoints：检查题仍与练习同题。",
        ]
        result["issues"] = [f"lesson.worked_examples：例1与{point_id}的practice重复，须改作答目标。"]
        return result


@pytest.mark.parametrize("mode", ["unchanged", "wording_only"])
def test_full_writer_path_has_early_tasks_and_inventory_without_bypassing_reviews(mode):
    client = LessonClient(mode)
    events = []
    bundle, chapter, evidence = course(12)
    with pytest.raises(teaching.TeachingContentError):
        teaching.TeachingContentGenerator(
            client, scope_planner=Planner(), audit_callback=events.append
        ).generate(bundle, chapter, evidence)
    repairs = [row for row in client.generations if row["include_lesson"] and row["previous_candidate"]]
    assert len(repairs) == 2  # Existing three-attempt budget, no extra call.
    for repair in repairs:
        assert list(repair).index("lesson_revision_tasks") < list(repair).index("sources")
        assert list(repair).index("existing_questions") < list(repair).index("sources")
        assert len(repair["existing_questions"]) == len(repair["existing_point_content"]) == 12
        expected = [{"point_id": p["point_id"], "flashcard": p["flashcard"]["front"],
                     "practice": p["practice"]["prompt"]} for p in repair["existing_point_content"]]
        assert repair["existing_questions"] == expected
        tasks = repair["lesson_revision_tasks"]
        assert set(tasks["editable_components"]) == {"example_1", "example_2", "checkpoint_1", "checkpoint_2"}
        assert tasks["preserve_components"] == ["opening", "reading_source_ids", "summary"]
        assert {x["category"] for x in tasks["required_category_edits"]} == {"examples", "checkpoints"}
        assert all(row["minimum_changed_members"] == 1 for row in tasks["required_category_edits"])
        assert "kp_" in next(row for row in repair["review_feedback"] if "accepted" in row)["issues"][0]
    assert len(client.teacher_requests) == (1 if mode == "unchanged" else 3)
    assert not any(event["event"] == "accepted" for event in events)
    assert not bundle.teaching_review  # A task list is never a publication approval.
