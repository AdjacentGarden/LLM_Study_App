"""Actual overgrading cases: repair suggestions never replace source evidence.

Frozen model judgments exercise the real parser and repair wire. They do not
claim a model will follow the writer's construction order or grade correctly.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError
from test_teaching_content import course, positive_rubric_alignment_response
from test_teaching_rubric_integration import RequestClient, point_models

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.rubric_alignment import (
    RubricAlignmentError,
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)
from adaptive_learning.personalization.teaching_feedback_wire import compact_writer_feedback

CASES = json.loads((Path(__file__).parent / "fixtures" / "teaching_atomic_rubrics_08.json").read_text())["items"]


def case_review(case):
    points, lesson, teacher, _ = point_models()
    points[0].practice = teaching._Practice.model_validate({
        **{key: case[key] for key in ("item_id", "prompt", "expected_answer", "rubric")},
        "source_ids": points[0].practice.source_ids,
    })
    batch = make_rubric_alignment_batch([
        point.practice.model_dump(exclude={"source_ids"}) for point in points
    ])
    raw = positive_rubric_alignment_response(batch.to_payload())
    raw["items"][0] = deepcopy(case["review"])
    review = parse_rubric_alignment_response(raw, batch)
    return points, lesson, teacher, batch, raw, review


@pytest.mark.parametrize("case", CASES, ids=["unasked_term", "unasked_history", "unasked_modifiers"])
def test_actual_negative_rubrics_reach_only_their_writer_as_unverified_suggestions(case):
    points, _, teacher, _, _, review = case_review(case)
    generator = teaching.TeachingContentGenerator(RequestClient([]))
    merged = generator._merge_rubric_reviews(teacher, [review], points)
    assert not merged.accepted and not merged.points[0].practice_ok
    assert merged.points[0].explanation_ok and merged.points[0].flashcard_ok
    assert merged.points[1].model_dump() == teacher.points[1].model_dump()
    # Preserve every exact negative judgment and its source-free suggestion in
    # the private audit, including the real case containing extra mechanism.
    assert merged._rubric_alignment["items"][0] == {**case["review"], "rubric_checks": [
        {**row, "repair_scope": "task_revision"} for row in case["review"]["rubric_checks"]]}
    assert teacher.accepted and teacher.points[0].practice_ok
    feedback = generator._review_feedback(merged, {point.point_id for point in points})
    own = generator._scoped_feedback(feedback, {points[0].point_id}, False)
    _, _, evidence = course(2)
    sources = {key: value.model_dump() for key, value in evidence.sources.items()}
    before_sources = deepcopy(sources)
    wire = compact_writer_feedback(own, boundaries=[], sources=sources)
    issue = next(row for row in wire if row.get("point_id") == points[0].point_id)["issues"][0]
    assert issue.startswith(f"practice_{case['item_id']} 审校提出的待核验最小答案建议为：")
    assert case["review"]["minimal_sufficient_answer"] in issue
    for row in case["review"]["rubric_checks"]:
        if not row["required"]:
            assert all(row[key] in issue for key in
                       ("rubric_text", "prompt_quote", "rubric_quote", "counterexample_answer", "repair"))
    assert sources == before_sources  # The reviewer never adds/replaces a book source.
    sibling = compact_writer_feedback(
        generator._scoped_feedback(feedback, {points[1].point_id}, False),
        boundaries=[], sources=sources,
    )
    assert not any(row.get("point_id") == points[0].point_id for row in sibling)


@pytest.mark.parametrize("case", CASES, ids=["unasked_term", "unasked_history", "unasked_modifiers"])
def test_deleting_only_unasked_rubric_keeps_expanded_answer_and_requires_new_review(case):
    points, _, teacher, _, raw, review = case_review(case)
    generator = teaching.TeachingContentGenerator(RequestClient([]))
    assert not generator._merge_rubric_reviews(teacher, [review], points).accepted
    original_answer = points[0].practice.expected_answer
    points[0].practice.rubric = list(case["repaired_rubric"])
    assert points[0].practice.expected_answer == original_answer == case["expected_answer"]
    repaired = make_rubric_alignment_batch([
        point.practice.model_dump(exclude={"source_ids"}) for point in points
    ])
    # A changed practice cannot reuse the old verdict, even if only the rubric
    # changed. Publication needs a complete new independent classification.
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response(raw, repaired)
    approved = parse_rubric_alignment_response(
        positive_rubric_alignment_response(repaired.to_payload()), repaired
    )
    teacher.accepted = False
    teacher.points[0].evidence_ok = False
    teacher.points[0].issues = ["practice: 教师仍拒绝原文不能支持的答案断言"]
    merged = generator._merge_rubric_reviews(teacher, [approved], points)
    assert not merged.accepted and not merged.points[0].evidence_ok
    assert merged.points[0].issues == teacher.points[0].issues


def test_construction_guidance_does_not_relax_the_two_to_six_criterion_contract():
    points, _, _, _ = point_models()
    practice = points[0].practice.model_dump()
    for rubric in ([practice["rubric"][0]], [*practice["rubric"], *["额外项"] * 5]):
        with pytest.raises(ValidationError):
            teaching._Practice.model_validate({**practice, "rubric": rubric})
