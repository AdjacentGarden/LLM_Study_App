"""A real delayed overgrading veto must survive all other positive reviews.

The release-02 fixture contains generated material and frozen judgments, not
textbook evidence. Host reviewers below are mechanical orchestration doubles;
these tests do not claim an LLM will follow the construction prompt or that the
biology was independently fact-checked by a test double.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_content import course
from test_teaching_v15_integration import CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.rubric_alignment import (
    RubricAlignmentError,
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)

OBSERVED = json.loads((Path(__file__).parent / "fixtures" /
                       "teaching_practice_task_closure_02.json").read_text())


def test_observed_task_minimum_covers_each_required_goal_without_optional_chain():
    # This explicit human-reviewed mapping documents what the repaired task
    # asks. It is not a string-based semantic grader for arbitrary questions.
    task_parts = OBSERVED["minimal_answer_parts"]
    practice = {key: value for key, value in OBSERVED["practice"].items() if key != "source_ids"}
    assert len(task_parts) == len(OBSERVED["repaired_rubric"]) == 4
    assert all(part["prompt_quote"] in practice["prompt"] for part in task_parts)
    assert OBSERVED["repaired_rubric"] == [part["rubric"] for part in task_parts]
    minimum = "".join(part["answer"] for part in task_parts)
    assert "母亲" in minimum and "女儿" in minimum and "儿子" in minimum
    assert "交叉遗传" in minimum and "不能推广" in minimum
    assert "携带者" not in minimum  # Not required to answer these four asks.
    assert "携带者" in practice["expected_answer"]  # May stay as a sourced expansion.
    assert practice["rubric"][OBSERVED["unasked_rubric_index"]] not in OBSERVED["repaired_rubric"]
    # The previous repair removed a particular disease example but left a
    # separate, unasked transmission requirement. Both must be checked at once.
    assert "抗维生素D佝偻病" in OBSERVED["initial_practice"]["rubric"][-1]
    assert "抗维生素D佝偻病" not in practice["rubric"][-1]
    assert practice["rubric"][2] == OBSERVED["initial_practice"]["rubric"][2]


def test_actual_all_positive_local_verdict_cannot_override_the_whole_teacher():
    practice = {key: value for key, value in OBSERVED["practice"].items() if key != "source_ids"}
    batch = make_rubric_alignment_batch([practice])
    verdict = parse_rubric_alignment_response(
        {"items": [deepcopy(OBSERVED["local_rubric_review"])]}, batch
    )
    assert verdict.accepted  # Exactly the actual release-02 local outcome.
    row = OBSERVED["teacher_final_point"]
    assert not row["practice_ok"] and row["explanation_ok"] and row["flashcard_ok"]
    assert "题干没有要求解释外孙" in row["issues"][0]
    # Replaying this verdict against a corrected rubric is forbidden; even a
    # deletion must get a fresh, complete independent classification.
    corrected = make_rubric_alignment_batch([{**practice, "rubric": OBSERVED["repaired_rubric"]}])
    with pytest.raises(RubricAlignmentError):
        parse_rubric_alignment_response({"items": [OBSERVED["local_rubric_review"]]}, corrected)


class ObservedPracticeClient(CandidateClient):
    def __init__(self, target_id, *, repair="remove", reject_after_repair=False):
        super().__init__()
        self.target_id = target_id
        self.repair = repair
        self.reject_after_repair = reject_after_repair

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        result = super().structured(system=system, user=user, **kwargs)
        if system == teaching.GENERATION_PROMPT:
            for point in result["points"]:
                if point["point_id"] != self.target_id:
                    continue
                old = point["practice"]
                practice = deepcopy(OBSERVED["practice"])
                # Keep real test input IDs and sources. Fact review remains a
                # mechanical double, never a biology-quality assertion.
                practice.update(item_id=old["item_id"], source_ids=old["source_ids"])
                if payload["previous_candidate"] is not None:
                    if self.repair == "remove":
                        practice["rubric"] = list(OBSERVED["repaired_rubric"])
                    elif self.repair == "ask":
                        practice["prompt"] += OBSERVED["explicit_additional_question"]
                    else:
                        # A superficial wording change satisfies no semantic
                        # veto; it cannot turn the old rubric into approval.
                        practice["expected_answer"] += "这就是本题所述的遗传情境。"
                point["practice"] = practice
        return result

    def teacher(self, payload):
        result = super().teacher(payload)
        for point, row in zip(payload["candidate"]["points"], result["points"], strict=True):
            if point["point_id"] != self.target_id:
                continue
            practice = point["practice"]
            unasked = OBSERVED["practice"]["rubric"][OBSERVED["unasked_rubric_index"]]
            unresolved = unasked in practice["rubric"] and not practice["prompt"].endswith(
                OBSERVED["explicit_additional_question"]
            )
            if unresolved or self.reject_after_repair:
                issue = OBSERVED["teacher_final_point"]["issues"][0].replace(
                    OBSERVED["practice"]["item_id"], practice["item_id"]
                ) if unresolved else "practice：新答案仍有尚未通过的独立教学问题。"
                row.update(practice_ok=False, issues=[issue])
                result["accepted"] = False
        return result


@pytest.mark.parametrize("repair", ["remove", "ask"])
def test_observed_extra_chain_is_fixed_only_in_its_practice_then_independently_reviewed(repair):
    bundle, chapter, evidence = course(2)
    target = bundle.knowledge_points[0].point_id
    client = ObservedPracticeClient(target, repair=repair)
    audit = []
    result = teaching.TeachingContentGenerator(
        client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
    ).generate(bundle, chapter, evidence)
    candidates = [event for event in audit if event["event"] == "candidate"]
    assert len(candidates) == 2
    assert candidates[0]["candidate_sha256"] != candidates[1]["candidate_sha256"]
    before, after = [event["candidate"] for event in candidates]
    assert before["lesson"] == after["lesson"]
    assert before["points"][1] == after["points"][1]
    assert before["points"][0]["explanation"] == after["points"][0]["explanation"]
    assert before["points"][0]["flashcard"] == after["points"][0]["flashcard"]
    original = before["points"][0]["practice"]
    corrected = after["points"][0]["practice"]
    assert original["expected_answer"] == corrected["expected_answer"]
    assert original["source_ids"] == corrected["source_ids"]
    if repair == "remove":
        assert corrected["rubric"] == OBSERVED["repaired_rubric"]
        assert corrected["prompt"] == original["prompt"]
    else:
        assert corrected["prompt"].endswith(OBSERVED["explicit_additional_question"])
        assert corrected["rubric"] == original["rubric"]
    request = client.generations[-1]
    assert request["allowed_repairs"] == {target: ["practice"]}
    assert request["required_changes"]["point_components"] == {target: ["practice"]}
    assert OBSERVED["teacher_final_point"]["issues"][0].split("：", 1)[1] in json.dumps(
        request["review_feedback"], ensure_ascii=False
    )
    teachers = [event for event in audit if event["event"] == "teacher_review"]
    assert [event["review"]["accepted"] for event in teachers] == [False, True]
    assert [event["attempt"] for event in teachers] == [1, 2]
    assert all(event["rubric_alignment"]["accepted"] for event in teachers)
    assert len([event for event in audit if event["event"] == "rubric_review"]) == 2
    assert result.teaching_review["accepted"] and result.teaching_fact_review["accepted"]
    assert result.practice_items[0].expected_answer == original["expected_answer"]


@pytest.mark.parametrize("repair,reject", [("cosmetic", False), ("remove", True)])
def test_positive_fact_and_local_rubric_never_publish_unresolved_or_new_teacher_veto(repair, reject):
    bundle, chapter, evidence = course(2)
    client = ObservedPracticeClient(
        bundle.knowledge_points[0].point_id, repair=repair, reject_after_repair=reject
    )
    audit = []
    with pytest.raises(teaching.TeachingContentError, match="未通过"):
        teaching.TeachingContentGenerator(
            client, scope_planner=Planner(), audit_callback=audit.append, max_attempts=2
        ).generate(bundle, chapter, evidence)
    teachers = [event for event in audit if event["event"] == "teacher_review"]
    assert teachers and all(not event["review"]["accepted"] for event in teachers)
    assert all(event["rubric_alignment"]["accepted"] for event in teachers)
    assert not any(event["event"] == "accepted" for event in audit)
