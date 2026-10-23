"""Ground scope verdicts in each independent component's actual assertions."""
from copy import deepcopy

import pytest

from adaptive_learning.personalization.teaching_content import (
    TeachingContentError, TeachingContentGenerator, _ComponentFactSelection,
)


def selection(**updates):
    data = {"source_entailment": True, "scope_exceptions": True,
        "rationale": "当前组件明确给定了温度范围，所以没有泛化到条件改变后的情况。",
        "scope_checks": [{"boundary_id": "b1", "is_class_definition": True,
            "assigns_class_property": True, "has_same_class_counterexample": True,
            "candidate_explicitly_limited": True, "claim_field": "expected_answer",
            "claim_quote": "可以沿用原判断", "status": "bounded", "limitation_field": "prompt",
            "limitation_quote": "假设温度保持不变且容器密闭",
            "rationale": "题干已经明确本题适用条件，答案的判断受这个真实题设约束。"}]}
    data.update(updates)
    return _ComponentFactSelection.model_validate(data)


FIELDS = {"prompt": "假设温度保持不变且容器密闭，当前能否沿用原判断？",
          "expected_answer": "可以沿用原判断，因为题设满足结论的成立条件。"}
BOUNDARIES = [{"boundary_id": "b1"}]


def test_explicit_range_in_question_can_bound_answer_without_repetition():
    TeachingContentGenerator._validate_boundary_checks(selection(), FIELDS, BOUNDARIES)


@pytest.mark.parametrize("fault", ["missing", "duplicate", "unknown", "other_component_claim",
    "invented_limit", "other_component_limit", "unbounded_but_true", "irrelevant_with_fake_limit"])
def test_scope_approval_cannot_skip_boundary_or_borrow_another_components_text(fault):
    proof = selection()
    check = proof.scope_checks[0]
    if fault == "missing":
        proof.scope_checks = []
    elif fault == "duplicate":
        proof.scope_checks.append(check.model_copy(deep=True))
    elif fault == "unknown":
        check.boundary_id = "other_point_boundary"
    elif fault == "other_component_claim":
        check.claim_field = "explanation"
    elif fault == "invented_limit":
        check.limitation_quote = "当前组件未出现的温度条件"
    elif fault == "other_component_limit":
        check.limitation_field = "back"
    elif fault == "unbounded_but_true":
        check.status = "unbounded"
        check.limitation_field = check.limitation_quote = None
    else:
        check.status = "not_applicable"
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator._validate_boundary_checks(proof, FIELDS, BOUNDARIES)


def test_unqualified_definition_does_not_require_literal_all_for_scope_rejection():
    proof = selection(scope_exceptions=False)
    check = proof.scope_checks[0]
    check.claim_field, check.claim_quote = "back", "信号亮起表示可以测量"
    check.status, check.limitation_field, check.limitation_quote = "unbounded", None, None
    check.candidate_explicitly_limited = False
    # The semantic auditor identified an omitted condition. Mechanical consistency
    # checks use that verdict even though the class assertion contains no “所有”.
    TeachingContentGenerator._validate_boundary_checks(proof, {"back": "信号亮起表示可以测量。"}, BOUNDARIES)
    proof.scope_exceptions = True
    with pytest.raises(TeachingContentError, match="不能标scope_exceptions=true"):
        TeachingContentGenerator._validate_boundary_checks(proof, {"back": "信号亮起表示可以测量。"}, BOUNDARIES)


def test_boundary_identifiers_bind_condition_and_source_identity():
    from test_teaching_content import course
    _, _, evidence = course(1)
    condition = {"condition": "温度不变", "scope_effect": "温度改变后原判断不能直接沿用。",
        "boundary_type": "precondition", "source_ids": ["s1"]}
    item = {"scope_conditions": [condition]}
    first = TeachingContentGenerator._scope_boundaries(item, evidence)
    assert first == TeachingContentGenerator._scope_boundaries(deepcopy(item), evidence)
    assert first[0]["actual_source_text"][0]["quote"] == evidence.sources["s1"].quote
    changed = deepcopy(item)
    changed["scope_conditions"][0]["scope_effect"] = "温度改变后需要重新测量。"
    assert first[0]["boundary_id"] != TeachingContentGenerator._scope_boundaries(changed, evidence)[0]["boundary_id"]


def test_missing_citation_does_not_turn_a_correctly_bounded_claim_into_scope_failure():
    proof = selection(source_entailment=False, scope_exceptions=False, findings=[{
        "kind": "unsupported_claim", "candidate_field": "expected_answer",
        "candidate_quote": "可以沿用原判断", "issue": "这项结论缺少当前组件真正应引用的来源，需要补充来源。",
        "evidence_source_ids": ["boundary_source"]}])
    with pytest.raises(TeachingContentError, match="引用缺失与范围错误必须分开"):
        TeachingContentGenerator._validate_boundary_checks(proof, FIELDS, BOUNDARIES)
    proof.scope_exceptions = True
    TeachingContentGenerator._validate_boundary_checks(proof, FIELDS, BOUNDARIES)


def test_separate_real_scope_finding_is_still_rejected_when_other_boundaries_are_bounded():
    proof = selection(source_entailment=False, scope_exceptions=False, findings=[{
        "kind": "scope_overreach", "candidate_field": "expected_answer",
        "candidate_quote": "可以沿用原判断", "issue": "还存在另一项真实适用范围错误，需要独立对照原文纠正。",
        "evidence_source_ids": ["boundary_source"]}])
    TeachingContentGenerator._validate_boundary_checks(proof, FIELDS, BOUNDARIES)


def test_fact_wire_contract_is_one_flat_typed_batch_specific_object():
    points = TeachingContentGenerator._fact_response_schema(set(), True)
    assert points["type"] == "object" and points["required"] == ["components"]
    row = points["$defs"]["_FlatFactComponent"]
    assert row["properties"]["component_id"]["enum"] == ["explanation", "flashcard", "practice"]
    assert row["properties"]["point_id"] == {"type": "string"}
    assert "source_ids" not in row["properties"]
    assert {"point_id", "component_id", "findings", "scope_checks", "support_span_ids"} <= set(row["required"])
    lesson = TeachingContentGenerator._fact_response_schema({"example_2", "checkpoint_1"}, False)
    row = lesson["$defs"]["_FlatFactComponent"]
    assert row["properties"]["component_id"]["enum"] == ["checkpoint_1", "example_2"]
    assert row["properties"]["point_id"] == {"type": "null"}
