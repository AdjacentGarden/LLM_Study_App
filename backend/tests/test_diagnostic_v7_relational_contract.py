"""Observed v6 failures verify shared repair inputs, not model quality by fiat."""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_diagnostic_v6_repair import structure_for

from adaptive_learning.assessment import item_generation as generation


@pytest.fixture
def observed_remaining():
    return json.loads((Path(__file__).parent / "fixtures" /
        "diagnostic_v6_remaining_regression.json").read_text())["cases"]


@pytest.mark.parametrize("case_index", [0, 1])
def test_real_unchanged_structural_failure_feedback_and_source_reach_every_repair(observed_remaining, case_index):
    case = observed_remaining[case_index]
    target = case["target_knowledge_point_id"]
    attempts = case["attempts"]
    writer_requests, review_requests = [], []
    class Writer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            writer_requests.append(request)
            contract = request["question_contract"]
            assert contract in system
            assert "不足四个同类名称" in contract
            assert "四个平行的关系、对应或顺序判断" in contract
            assert "技术或过程名称" in contract and "目的、作用对象、条件或关系" in contract
            assert "不能只替换人物和句尾" in contract
            if len(writer_requests) > 1:
                prior = attempts[len(writer_requests) - 2]
                actual = next(row for row in request["repair_feedback"]["reviews"]
                              if row["knowledge_point_id"] == target)
                expected = next(row for row in prior["review_verdict"]["reviews"]
                                if row["knowledge_point_id"] == target)
                assert actual == expected and actual["accepted"] is False
                assert request["previous_candidate"]["items"][-1]["knowledge_point_id"] == target
                # Exactly approved siblings remain intact; they must not flip
                # just because the rejected target needed another repair.
                accepted = {row["knowledge_point_id"] for previous in attempts[:len(writer_requests) - 1]
                            for row in previous["review_verdict"]["reviews"] if row["accepted"]}
                assert {row["knowledge_point_id"] for row in request["preserve_accepted_items"]} == accepted
            return deepcopy(attempts[len(writer_requests) - 1]["generation_response"])
    class Reviewer:
        def structured(self, *, system, user, **kwargs):
            request = json.loads(user)
            review_requests.append(request)
            assert request["question_contract"] == writer_requests[-1]["question_contract"]
            assert request["question_contract"] in system
            ids = [row["knowledge_point_id"] for row in request["items"]]
            prior = attempts[len(review_requests) - 1]["review_verdict"]["reviews"]
            return {"reviews": [deepcopy(row) for row in prior if row["knowledge_point_id"] in ids]}
    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        validation_retries=2, allow_grounded_answer_rewrite=True)
    with pytest.raises(generation.DiagnosticGenerationError, match="review rejected"):
        subject.generate(structure_for(case["source"]))
    assert len(writer_requests) == len(review_requests) == 3
    assert [len(request["items"]) for request in review_requests] == ([3, 1, 1] if case_index == 0 else [3, 2, 1])
    selected = next(row for row in review_requests[-1]["source"]["knowledge_points"]
                    if row["knowledge_point_id"] == target)
    source = next(row for row in case["source"]["knowledge_points"]
                  if row["knowledge_point_id"] == target)
    assert selected["evidence"] == source["evidence"]
    assert selected["cognitive_target"]["id"] == "source_application"


@pytest.mark.parametrize("case_index", [0, 1])
@pytest.mark.parametrize("accept_repair", [True, False])
def test_structural_redesign_keeps_evidence_and_still_requires_independent_approval(observed_remaining, case_index, accept_repair):
    case = observed_remaining[case_index]
    target = case["target_knowledge_point_id"]
    initial = case["attempts"][0]["generation_response"]
    revised = deepcopy(case["attempts"][1]["generation_response"])
    row = next(row for row in revised["items"] if row["knowledge_point_id"] == target)
    if case_index == 0:
        row.update(prompt="核糖体移动后，原位点2的tRNA记作甲，新携带氨基酸的tRNA记作乙。两者接下来的位置如何对应？",
            options=["甲进入位点1，乙进入位点2", "甲留在位点2，乙进入位点1",
                     "甲离开核糖体，乙进入位点1", "甲进入位点1，乙留在核糖体外"],
            correct_index=0, expected_answer="甲进入位点1，乙进入位点2", difficulty=1, estimated_seconds=60)
    else:
        row.update(prompt="研究人员已选择基因工程，计划将修饰后的某基因放入另一种生物的细胞。此操作有怎样的定向目标？",
            options=["按意愿改造生物的遗传性状", "只将提取的基因恢复到原细胞",
                     "仅记录性状，不改变遗传性状", "使提取的基因不再进入任何细胞"],
            correct_index=0, expected_answer="按意愿改造生物的遗传性状", difficulty=0, estimated_seconds=45)
    requests = []
    class Writer:
        calls = 0
        def structured(self, **kwargs):
            self.calls += 1
            return deepcopy(initial if self.calls == 1 else revised)
    class Reviewer:
        def structured(self, *, user, **kwargs):
            request = json.loads(user)
            requests.append(request)
            if len(requests) == 1:
                return deepcopy(case["attempts"][0]["review_verdict"])
            expected_ids = [entry["knowledge_point_id"] for entry in case["attempts"][1]["review_verdict"]["reviews"]]
            assert [entry["knowledge_point_id"] for entry in request["items"]] == expected_ids
            actual = next(entry for entry in request["items"] if entry["knowledge_point_id"] == target)
            assert actual["prompt"] == row["prompt"]
            assert set(actual["options"]) == set(row["options"])
            assert actual["options"][actual["correct_index"]] == row["expected_answer"]
            reviews = deepcopy(case["attempts"][1]["review_verdict"]["reviews"])
            verdict = next(entry for entry in reviews if entry["knowledge_point_id"] == target)
            verdict.update(accepted=accept_repair,
                issues=[] if accept_repair else ["Controlled independent source/uniqueness veto"])
            return {"reviews": reviews}
    subject = generation.DiagnosticItemGenerator(Writer(), review_client=Reviewer(),
        validation_retries=1, allow_grounded_answer_rewrite=True)
    if accept_repair:
        assert len(subject.generate(structure_for(case["source"]))) == 3
    else:
        with pytest.raises(generation.DiagnosticGenerationError, match="independent source/uniqueness veto"):
            subject.generate(structure_for(case["source"]))
    assert len(requests) == 2
