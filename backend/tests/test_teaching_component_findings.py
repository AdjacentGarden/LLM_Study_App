"""Observed reviewer false positives must repair the review, not correct text."""
import json
from collections import Counter
from threading import Lock

import pytest
from test_teaching_content import Client, advertised_fact_response, course

from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    GENERATION_PROMPT,
    REVIEW_PROMPT,
    TeachingContentError,
    TeachingContentGenerator,
    _ComponentFactSelection,
)


class RecordingClient(Client):
    """Keep each concurrent request paired with its actual prompt and IDs."""

    def __init__(self):
        super().__init__()
        self.recorded_requests = []
        self.record_lock = Lock()

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        with self.record_lock:
            self.recorded_requests.append((system, payload))
        result = super().structured(system=system, user=user, **kwargs)
        # super() bypasses Client's outer adapter. Convert this positive base
        # response before the subclasses inject their explicit flat-wire fault.
        return advertised_fact_response(result, payload) if system == FACT_REVIEW_PROMPT else result


def requests_for(client, system):
    return [payload for prompt, payload in client.recorded_requests if prompt == system]


def fact_pair_counts(client):
    pairs = Counter()
    for payload in requests_for(client, FACT_REVIEW_PROMPT):
        contract = payload["output_contract"]
        for point_id in contract["point_ids"]:
            for name in contract["component_ids_by_point"][point_id]:
                pairs[point_id, name] += 1
        pairs.update((None, name) for name in contract["lesson_component_ids"])
    return pairs


def finding(*, field="explanation", quote="判断容器0中液体体积是否稳定", kind="missing_condition",
            missing="温度不变", source="s1"):
    return {"kind": kind, "candidate_field": field, "candidate_quote": quote,
            "issue": "该组件的结论遗漏适用条件，需要核对当前文字后准确定位。",
            "evidence_source_ids": [source], "missing_text": missing}


@pytest.mark.parametrize("field,text,missing", [
    ("explanation", "mRNA上的密码子由3个相邻碱基组成。所有密码子都必然决定1个氨基酸。", "mRNA"),
    ("explanation", "mRNA上的密码子由3个相邻碱基组成。所有密码子都必然决定1个氨基酸。", "相邻"),
    ("prompt", "假设温度不变且容器密闭，判断观测结论是否适用。", "温度不变"),
    ("front", "在密闭容器内，体积稳定结论要求什么温度条件？", "密闭容器"),
])
def test_already_written_conditions_cannot_be_rejected_as_textual_omissions(field, text, missing):
    _, _, evidence = course(1)
    evidence.sources["s1"].quote = "mRNA上的密码子由3个相邻碱基组成。在温度不变的密闭容器中观察。"
    proof = _ComponentFactSelection(source_entailment=True, scope_exceptions=False,
        rationale="需要独立核对这个组件自己的成立条件，不能借另一项理由。",
        findings=[finding(field=field, quote=text[:15], missing=missing)])
    with pytest.raises(TeachingContentError, match="已存在"):
        TeachingContentGenerator._validate_component_findings(proof, {field: text}, {"s1"}, evidence)


@pytest.mark.parametrize("fault", ["borrowed_field", "borrowed_quote", "invented_source", "invented_missing", "approved_with_finding"])
def test_rejection_findings_cannot_borrow_another_component_or_invent_material(fault):
    _, _, evidence = course(1)
    item = finding(field="back", quote="液体体积保持稳定", missing="温度不变")
    fields = {"front": "如何判断当前观测？", "back": "液体体积保持稳定，但不能任意推广。"}
    if fault == "borrowed_field":
        item["candidate_field"] = "explanation"
    elif fault == "borrowed_quote":
        item["candidate_quote"] = "这句话只存在于另一个组件，不能复制过来。"
    elif fault == "invented_source":
        item["evidence_source_ids"] = ["made_up"]
    elif fault == "invented_missing":
        item["missing_text"] = "原文中从来没有的附加条件"
    proof = _ComponentFactSelection(source_entailment=True,
        scope_exceptions=fault == "approved_with_finding", rationale="独立核对当前组件的边界与实际原文依据。",
        findings=[item])
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator._validate_component_findings(proof, fields, {"s1"}, evidence)


def test_present_words_do_not_override_a_real_logical_scope_error():
    _, _, evidence = course(1)
    fields = {"back": "温度不变时保持稳定，所以温度发生变化后也必然保持稳定。"}
    item = finding(field="back", quote="所以温度发生变化后也必然保持稳定。",
                   kind="scope_overreach", missing=None)
    proof = _ComponentFactSelection(source_entailment=False, scope_exceptions=False,
        rationale="原文前提限制结论，不能把改变温度后的情况也当作必然成立。", findings=[item])
    TeachingContentGenerator._validate_component_findings(proof, fields, {"s1"}, evidence)


def test_false_omission_repairs_only_the_reviewer_without_rewriting_teaching():
    bundle, chapter, evidence = course(2)
    target = bundle.knowledge_points[0].point_id

    class FalseOmissionOnce(RecordingClient):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and not data["format_feedback"]:
                for row in result["components"]:
                    if (row["point_id"], row["component_id"]) == (target, "explanation"):
                        row.update(scope_exceptions=False, findings=[finding()])
            return result

    client = FalseOmissionOnce()
    result = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    generations = requests_for(client, GENERATION_PROMPT)
    assert Counter(tuple(row["point_id"] for row in payload["batch_points"])
                   for payload in generations) == {
        tuple(point.point_id for point in bundle.knowledge_points): 1, (): 1}
    assert all(payload["previous_candidate"] is None for payload in generations)
    point_reviews = [payload for payload in requests_for(client, FACT_REVIEW_PROMPT)
                     if target in payload["output_contract"]["point_ids"]]
    assert len(point_reviews) == 2
    retry = next(payload for payload in point_reviews if payload["format_feedback"])
    assert "已存在" in json.dumps(retry["format_feedback"], ensure_ascii=False)
    pairs = fact_pair_counts(client)
    assert all(pairs[point.point_id, name] == (2 if point.point_id == target else 1)
               for point in bundle.knowledge_points
               for name in ("explanation", "flashcard", "practice"))
    assert all(count == 1 for (owner, _), count in pairs.items() if owner is None)
    assert len(requests_for(client, REVIEW_PROMPT)) == 1
    assert result.teaching_fact_review["accepted"] is True


def test_fact_repair_of_one_example_preserves_approved_lesson_components_and_reaudits():
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="测量开始前需要记录观测时刻，以便区分两次观测。")

    class RepairOneExample(RecordingClient):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT and result["lesson"]:
                lesson = result["lesson"]
                lesson["worked_examples"][0]["steps"].append("测量开始前需要记录观测时刻，以便区分两次观测。")
                if data["allowed_lesson_repairs"] == ["example_1"]:
                    lesson["worked_examples"][0]["source_ids"].append("s2")
                    lesson["summary"] += "这段额外文字不应替换已通过的总结。"
                    lesson["worked_examples"][1]["answer"] += "这段意外重写应被舍弃。"
                    lesson["checkpoints"][0]["question"] += "这个已通过的问题也不应改变。"
            if system == FACT_REVIEW_PROMPT and "example_1" in data["lesson_components"]:
                part = data["lesson_components"]["example_1"]
                if "s2" not in part["source_ids"]:
                    row = next(row for row in result["components"]
                               if (row["point_id"], row["component_id"]) == (None, "example_1"))
                    assertions = {item["assertion_id"]: item
                                  for item in part["assertion_contract"]["assertions"]}
                    rejected = []
                    for check in row["assertion_checks"]:
                        assertion = assertions[check["assertion_id"]]
                        if assertion["field"] == "step_3":
                            check.update(source_entailment=False, support_span_ids=[], findings=[{
                                "kind": "unsupported_claim", "quote": assertion["text"],
                                "issue": "例题记录观测时刻的步骤未引用真正来源s2。",
                                "evidence_source_ids": ["s2"]}])
                            rejected.append(check["assertion_id"])
                    assert rejected
            return result

    client = RepairOneExample()
    output = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    repairs = [data for data in requests_for(client, GENERATION_PROMPT)
               if data["allowed_lesson_repairs"]]
    assert len(repairs) == 1
    assert repairs[0]["batch_points"] == []
    assert repairs[0]["allowed_lesson_repairs"] == ["example_1"]
    pairs = fact_pair_counts(client)
    assert pairs[None, "example_1"] == 2
    assert all(pairs[None, name] == 1 for name in ("example_2", "checkpoint_1", "checkpoint_2"))
    assert all(pairs[point.point_id, name] == 1 for point in bundle.knowledge_points
               for name in ("explanation", "flashcard", "practice"))
    assert "s2" in {source for source in output.teaching_fact_review["lesson"]["components"]["example_1"]["source_ids"]}
    assert output.teaching_review["accepted"] is True
    assert "额外文字" not in output.summary
    assert "意外重写" not in output.worked_examples[1].content
    assert all("不应改变" not in question for question in output.checkpoint_questions)


def test_changing_one_failed_component_cannot_hide_another_unchanged_failed_component():
    bundle, chapter, evidence = course(2)
    evidence.sources["s2"] = SourceCitation(page_number=2, quote="测量开始前需要记录观测时刻，以便区分两次观测。")
    target = bundle.knowledge_points[0].point_id

    class PartialRepair(RecordingClient):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == GENERATION_PROMPT:
                for point in result["points"]:
                    if point["point_id"] == target:
                        point["explanation"] += "测量开始前需要记录观测时刻。"
                        point["practice"]["expected_answer"] += "还必须记录观测时刻。"
                        if target in data["allowed_repairs"]:
                            point["source_ids"].append("s2")  # Only explanation repaired.
            if system == FACT_REVIEW_PROMPT:
                point = next((point for point in data["candidate"]["points"]
                              if point["point_id"] == target), None)
                for row in result["components"]:
                    name = row["component_id"]
                    if row["point_id"] != target or name not in {"explanation", "practice"}:
                        continue
                    assert point is not None
                    source_ids = point["source_ids"] if name == "explanation" else point["practice"]["source_ids"]
                    if "s2" not in source_ids:
                        field, quote = (("explanation", "测量开始前需要记录观测时刻。") if name == "explanation"
                                        else ("expected_answer", "还必须记录观测时刻。"))
                        row.update(source_entailment=False, findings=[{
                            "kind": "unsupported_claim", "candidate_field": field, "candidate_quote": quote,
                            "issue": "记录时刻的要求缺少该组件自身对s2的实际引用。", "evidence_source_ids": ["s2"]}])
            return result

    client = PartialRepair()
    with pytest.raises(TeachingContentError) as caught:
        TeachingContentGenerator(client, max_attempts=2).generate(bundle, chapter, evidence)
    feedback = json.dumps(caught.value.review_feedback, ensure_ascii=False)
    assert f"{target}.practice 未修改上次被拒绝的组件" in feedback
    repairs = [data for data in requests_for(client, GENERATION_PROMPT) if data["allowed_repairs"]]
    assert len(repairs) == 1
    assert repairs[0]["allowed_repairs"] == {target: ["explanation", "practice"]}
    pairs = fact_pair_counts(client)
    assert pairs[target, "explanation"] == pairs[target, "practice"] == 1
    # The teacher may run concurrently with a factual rejection; neither that
    # pass nor one changed component can publish the unchanged rejected item.
    assert len(requests_for(client, REVIEW_PROMPT)) == 1
