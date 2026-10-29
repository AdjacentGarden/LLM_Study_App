"""Replay the real d200 wire and identifier defects without approving content."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_content import course, lesson_candidate
from test_teaching_v15_integration import CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching

FIXTURES = Path(__file__).parent / "fixtures"
WIRE = json.loads((FIXTURES / "teaching_f_d200_lesson_slot_wire.json").read_text())
ROUTING = json.loads((FIXTURES / "teaching_f_d200_teacher_identifier_routes.json").read_text())


def materializer(index=0):
    row, audit = deepcopy(WIRE["repairs"][index]), []
    generator = teaching.TeachingContentGenerator(
        CandidateClient(), scope_planner=Planner(), audit_callback=audit.append
    )
    baseline = teaching._Lesson.model_validate(row["previous_candidate"]["lesson"])
    return generator, row, baseline, audit


@pytest.mark.parametrize("index", [0, 1])
def test_actual_full_permission_five_key_wire_maps_losslessly_to_slots_without_approval(index):
    generator, row, baseline, audit = materializer(index)
    raw = deepcopy(row["response"])
    generated = generator._materialize_generation_patch(raw, row["contract"], {}, baseline)
    assert raw == row["response"]
    assert generated.lesson.model_dump() == raw["lesson"]
    assert generated.points == []
    assert [entry["event"] for entry in audit] == [
        "generation_lesson_slots_normalized", "generation_patch_materialized"
    ]
    assert all(entry["approved"] is False and entry["independent_review_required"]
               for entry in audit)
    assert audit[-1]["patch"] == raw


@pytest.mark.parametrize("fault", ["subset", "superset", "mixed_keys", "extra_field",
                                   "two_examples", "two_checkpoints", "four_examples",
                                   "nested_extra", "bad_text", "unexpected_points"])
def test_full_lesson_normalization_rejects_permission_or_schema_ambiguity(fault):
    generator, row, baseline, audit = materializer()
    raw, contract = row["response"], row["contract"]
    if fault == "subset":
        contract["lesson_components"].remove("checkpoint_1")
    elif fault == "superset":
        contract["lesson_components"].append("checkpoint_4")
    elif fault == "mixed_keys":
        raw["lesson"]["checkpoint_1"] = raw["lesson"]["checkpoints"][0]
    elif fault == "extra_field":
        raw["lesson"]["approved"] = True
    elif fault == "two_examples":
        raw["lesson"]["worked_examples"].pop()
    elif fault == "two_checkpoints":
        raw["lesson"]["checkpoints"].pop()
    elif fault == "four_examples":
        raw["lesson"]["worked_examples"].append(raw["lesson"]["worked_examples"][0])
    elif fault == "nested_extra":
        raw["lesson"]["worked_examples"][0]["approved"] = True
    elif fault == "bad_text":
        raw["lesson"]["worked_examples"][0]["answer"] = ""
    else:
        raw["points"] = [{"point_id": "invented"}]
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        generator._materialize_generation_patch(raw, contract, {}, baseline)
    assert audit == []


def test_full_list_normalization_preserves_unchanged_rejection_and_source_ids():
    generator, row, baseline, _ = materializer()
    raw = {"points": [], "lesson": baseline.model_dump()}
    result = generator._materialize_generation_patch(raw, row["contract"], {}, baseline)
    with pytest.raises(teaching.TeachingContentError, match="未修改"):
        generator._validate_candidate_repair(
            [], result.lesson, {"points": [], "lesson": baseline.model_dump()},
            teaching._RepairRequirements(exact_lesson={"example_3"}),
        )
    assert result.lesson.reading_source_ids == baseline.reading_source_ids
    assert [item.source_ids for item in result.lesson.worked_examples] == [
        item.source_ids for item in baseline.worked_examples
    ]


def test_create_and_repair_payload_shapes_show_only_the_active_complete_field_schema():
    bundle, _, evidence = course(1)
    generator, _, baseline, _ = materializer()
    inputs = generator._inputs(bundle, evidence)
    initial = generator._initial_output_shape(inputs, False)
    assert initial["lesson"] is None
    assert set(initial["points"][0]) == set(teaching._Point.model_fields)
    assert initial["points"][0]["flashcard"]["card_id"] == inputs[0]["card_id"]
    contract = generator._repair_contract(
        set(), {}, {}, baseline, {"example_3", "checkpoint_2", "summary"}, True
    )
    shape = contract["output_shape"]
    assert shape["points"] == []
    assert set(shape["lesson"]) == {"example_3", "checkpoint_2", "summary"}
    assert set(shape["lesson"]["example_3"]) == set(teaching._Example.model_fields)
    assert set(shape["lesson"]["checkpoint_2"]) == set(teaching._Checkpoint.model_fields)
    assert "worked_examples" not in shape["lesson"] and "checkpoints" not in shape["lesson"]
    assert '"worked_examples":[' not in teaching.GENERATION_PROMPT
    assert '"points":[{' not in teaching.GENERATION_PROMPT


def routing_inputs():
    points = [teaching._Point.model_validate(row) for row in ROUTING["candidate"]["points"]]
    lesson = teaching._Lesson.model_validate(ROUTING["candidate"]["lesson"])
    return points, lesson


def test_actual_six_bracketed_identifiers_preserve_all_vetoes_but_avoid_spurious_global_rewrite():
    points, lesson = routing_inputs()
    fact = teaching._FactReview.model_validate(ROUTING["fact"])
    teacher = teaching._Review.model_validate(ROUTING["teacher"])
    before = teacher.model_dump()
    generator = teaching.TeachingContentGenerator(None)
    bracketed = [issue for issue in teacher.issues if "（kp_" in issue]
    assert len(bracketed) == 6
    assert all(generator._teacher_issue_targets(issue, points, lesson) for issue in bracketed)
    plan = generator._build_repair_plan(fact, teacher, points, lesson)
    assert plan.requirements.global_any is False
    assert sum(map(len, plan.allowed_points.values())) == 15
    assert len(plan.allowed_lesson) == 7
    assert teacher.model_dump() == before
    assert not teacher.accepted and not fact.accepted
    assert plan.allowed_points["kp_158bb6a882883c68"] == {"explanation", "flashcard"}


@pytest.mark.parametrize("fault", ["unknown_id", "unknown_component", "unknown_mixed", "multi_id", "mixed_component",
                                   "body_only", "mismatched_brackets", "prefix_text", "trailing_id"])
def test_ambiguous_or_nonleading_bracket_markers_remain_global_vetoes(fault):
    points, lesson = routing_inputs()
    first, second = points[0].point_id, points[1].point_id
    issue = f"explanation（{first}）：该组件缺少必要限定。"
    if fault == "unknown_id":
        issue = issue.replace(first, "kp_0000000000000000")
    elif fault == "unknown_component":
        issue = issue.replace("explanation", "unknown")
    elif fault == "unknown_mixed":
        issue += f"unknown（{first}）：不能猜测此标记的归属。"
    elif fault == "multi_id":
        issue += f"还涉及{second}。"
    elif fault == "mixed_component":
        issue += f"flashcard（{first}）：另一个组件也须修改。"
    elif fault == "body_only":
        issue = f"本课仍需修订，正文提及{first}但未定位组件。"
    elif fault == "mismatched_brackets":
        issue = issue.replace("）", ")")
    elif fault == "prefix_text":
        issue = "以下意见：" + issue
    else:
        issue = issue.replace("）：", "）x：")
    assert teaching.TeachingContentGenerator._teacher_issue_targets(issue, points, lesson) is None


def test_ascii_brackets_route_only_the_matching_owner():
    points, lesson = routing_inputs()
    point = points[0].point_id
    issue = f"practice({point}): 本题评分要求超出实际问项。"
    assert teaching.TeachingContentGenerator._teacher_issue_targets(issue, points, lesson) == (
        {point: {"practice"}}, set()
    )
    assert teaching.TeachingContentGenerator._teacher_issue_targets(
        issue, points, lesson, owner_point_id=points[1].point_id
    ) is None


def three_slot_lesson():
    value = lesson_candidate()
    value["worked_examples"].append({
        **deepcopy(value["worked_examples"][0]), "title": "分别核对两个条件",
        "problem": "比较两个观测记录时，如何分别核对温度与密闭条件，再说明可用结论？",
    })
    value["checkpoints"].append({
        **deepcopy(value["checkpoints"][0]),
        "question": "如果只知道温度不变而没有容器条件，可以直接使用原有结论吗？请说明理由。",
    })
    return value


def test_normalized_full_lesson_still_gets_independent_review_and_cannot_publish_its_veto():
    class Reviewer(CandidateClient):
        def teacher(self, payload):
            result = super().teacher(payload)
            result.update(accepted=False, issues=["整课仍有未定位的真实教学缺陷。"])
            return result

    class Writer:
        def __init__(self):
            self.inner = Reviewer()

        def structured(self, *, system, user, **kwargs):
            payload = json.loads(user)
            result = self.inner.structured(system=system, user=user, **kwargs)
            if system == teaching.GENERATION_PROMPT and payload["include_lesson"]:
                lesson = three_slot_lesson()
                if payload["repair_contract"]:
                    lesson["summary"] += "修订时再次核对适用的两个必要条件。"
                result["lesson"] = lesson  # Authored full-list wire, deliberately not a slot patch.
            return result

    bundle, chapter, evidence = course(2)
    audit = []
    with pytest.raises(teaching.TeachingContentError, match="未通过"):
        teaching.TeachingContentGenerator(
            Writer(), scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
        ).generate(bundle, chapter, evidence)
    assert len([row for row in audit if row["event"] == "candidate"]) == 2
    assert len([row for row in audit if row["event"] == "generation_lesson_slots_normalized"]) == 1
    assert all(not row["review"]["accepted"] for row in audit
               if row["event"] == "teacher_review")
    assert not any(row["event"] == "accepted" for row in audit)
