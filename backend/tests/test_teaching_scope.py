"""Planning contract tests; fake responses do not establish semantic accuracy."""
import json
from collections import Counter
from copy import deepcopy
from pathlib import Path
from threading import Lock

import pytest

from adaptive_learning.llm.client import LLMError
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_scope import (
    SCOPE_PLANNING_PROMPT,
    TeachingScopeError,
    TeachingScopePlanner,
    validate_scope_plan,
)


def fixture():
    evidence = TeachingEvidence(
        sources={
            "anchor": SourceCitation(page_number=1, quote="在温度不变的密闭容器中，液体体积保持稳定。"),
            "boundary": SourceCitation(page_number=3, quote="温度改变时不能直接套用这一结论，需要重新测量。"),
        },
        point_sources={"p1": ["anchor"], "p2": ["boundary"]},
        warnings=["不要猜补残缺图表。"], fingerprint="fixture-source",
    )
    inputs = [
        {"point_id": "p1", "label": "稳定结论", "point_source_ids": ["anchor"],
         "scope_context_source_ids": ["boundary"]},
        {"point_id": "p2", "label": "重新测量", "point_source_ids": ["boundary"],
         "scope_context_source_ids": []},
    ]
    response = {"points": [
        {"point_id": "p1", "comparisons": [{"source_id": "boundary", "changes_scope": True,
            "reason": "温度改变使得锚点的稳定结论不能直接套用，需要重新测量。"}], "conditions": [{"condition": "温度改变时不能直接套用稳定结论。",
                                         "boundary_type": "precondition",
                                         "scope_effect": "温度改变后原稳定结论不能直接使用，需要重新测量。",
                                         "source_ids": ["boundary"]}]},
        {"point_id": "p2", "comparisons": [], "conditions": []},
    ]}
    return inputs, evidence, response


class Client:
    def __init__(self, *responses):
        self.responses = responses
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(kwargs)
        response = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(response, Exception):
            raise response
        return deepcopy(response)


def test_full_coverage_empty_conditions_and_real_distant_sources_are_allowed():
    inputs, evidence, response = fixture()
    before = deepcopy(inputs)
    client = Client(response)
    plan = TeachingScopePlanner(client).plan(inputs, evidence)
    assert set(plan) == {"p1", "p2"} and plan["p2"] == []
    assert plan["p1"][0]["source_ids"] == ["boundary"]
    assert inputs == before
    payload = json.loads(client.calls[0]["user"])
    assert payload["sources"]["boundary"]["quote"] == evidence.sources["boundary"].quote
    assert payload["source_warnings"] == evidence.warnings
    assert client.calls[0]["system"] == SCOPE_PLANNING_PROMPT
    assert client.calls[0]["temperature"] == 0


def test_regression_labels_prior_answers_and_metadata_are_not_sent():
    inputs, evidence, response = fixture()
    for item in inputs:
        item.update(regression_metadata={"expected_rejected": "SECRET_EXPECTATION"},
                    expected_answer="SECRET_ANSWER", previous_review="SECRET_REVIEW")
    client = Client(response)
    TeachingScopePlanner(client).plan(inputs, evidence)
    assert "SECRET_" not in client.calls[0]["user"]
    assert set(json.loads(client.calls[0]["user"])["knowledge_points"][0]) == {
        "point_id", "label", "point_source_ids", "scope_context_source_ids", "anchor_sources", "candidate_sources"}


@pytest.mark.parametrize("fault", [
    "missing_point", "duplicate_point", "unknown_point", "unknown_source", "duplicate_source",
    "empty_source", "wrong_source_type", "blank_condition", "long_condition", "control",
    "extra_quote", "extra_accepted", "too_many_conditions", "duplicate_condition",
    "null_conditions", "not_object",
])
def test_malformed_plans_fail_closed_after_at_most_two_format_corrections(fault):
    inputs, evidence, response = fixture()
    row = response["points"][0]
    condition = row["conditions"][0]
    if fault == "missing_point":
        response["points"].pop()
    elif fault == "duplicate_point":
        response["points"].append(deepcopy(row))
    elif fault == "unknown_point":
        row["point_id"] = "invented"
    elif fault == "unknown_source":
        condition["source_ids"] = ["invented"]
    elif fault == "duplicate_source":
        condition["source_ids"] = ["boundary", "boundary"]
    elif fault == "empty_source":
        condition["source_ids"] = []
    elif fault == "wrong_source_type":
        condition["source_ids"] = "boundary"
    elif fault == "blank_condition":
        condition["condition"] = "  "
    elif fault == "long_condition":
        condition["condition"] = "长" * 301
    elif fault == "control":
        condition["condition"] += "\u200b"
    elif fault == "extra_quote":
        condition["quote"] = "UNTRUSTED_RAW_RESPONSE"
    elif fault == "extra_accepted":
        response["accepted"] = True
    elif fault == "too_many_conditions":
        row["conditions"] *= 5
    elif fault == "duplicate_condition":
        row["conditions"] *= 2
    elif fault == "null_conditions":
        row["conditions"] = None
    elif fault == "not_object":
        response = ["UNTRUSTED_RAW_RESPONSE"]
    client = Client(response)
    with pytest.raises(TeachingScopeError, match="有界格式纠错"):
        TeachingScopePlanner(client).plan(inputs, evidence)
    assert len(client.calls) == 3
    assert [len(json.loads(call["user"])["format_feedback"]) for call in client.calls] == [0, 1, 2]
    assert all("UNTRUSTED_RAW_RESPONSE" not in call["user"] for call in client.calls)


def test_repair_only_retries_planning_and_preserves_original_inputs():
    inputs, evidence, response = fixture()
    client = Client({"points": []}, response)
    plan = TeachingScopePlanner(client).plan(inputs, evidence)
    assert set(plan) == {"p1", "p2"} and len(client.calls) == 2
    first, second = [json.loads(call["user"]) for call in client.calls]
    assert second.pop("format_feedback")
    first.pop("format_feedback")
    assert first == second


def test_service_error_does_not_multiply_client_transport_retries():
    inputs, evidence, _ = fixture()
    client = Client(LLMError("PRIVATE_TRANSPORT_DETAIL"))
    with pytest.raises(TeachingScopeError, match="服务暂时不可用") as caught:
        TeachingScopePlanner(client).plan(inputs, evidence)
    assert len(client.calls) == 1
    assert "PRIVATE_TRANSPORT_DETAIL" not in str(caught.value)
    with pytest.raises(TeachingScopeError):
        TeachingScopePlanner(None).plan(inputs, evidence)


@pytest.mark.parametrize("fault", ["duplicate_point", "missing_anchor", "wrong_anchor", "unknown_context"])
def test_invalid_input_stops_before_any_model_call(fault):
    inputs, evidence, response = fixture()
    if fault == "duplicate_point":
        inputs.append(inputs[0])
    elif fault == "missing_anchor":
        inputs[0]["point_source_ids"] = []
    elif fault == "wrong_anchor":
        inputs[0]["point_source_ids"] = ["boundary"]
    else:
        inputs[0]["scope_context_source_ids"] = ["invented"]
    client = Client(response)
    with pytest.raises(TeachingScopeError):
        TeachingScopePlanner(client).plan(inputs, evidence)
    assert not client.calls


@pytest.mark.parametrize("attempts", [0, 4])
def test_retry_budget_is_bounded(attempts):
    with pytest.raises(ValueError):
        TeachingScopePlanner(None, max_attempts=attempts)


def test_alternate_planner_results_are_validated_and_copied():
    inputs, evidence, response = fixture()
    plan = {row["point_id"]: row["conditions"] for row in response["points"]}
    normalized = validate_scope_plan(plan, inputs, evidence)
    plan["p1"][0]["source_ids"].clear()
    assert normalized["p1"][0]["source_ids"] == ["boundary"]
    with pytest.raises(TeachingScopeError):
        validate_scope_plan(plan, inputs, evidence)


@pytest.mark.parametrize("fault", ["discovery_history", "definition", "empty_effect", "missing_effect"])
def test_scope_contract_requires_a_truth_relevant_boundary_not_a_history_category(fault):
    inputs, evidence, response = fixture()
    evidence.sources["history"] = SourceCitation(page_number=2,
        quote="研究者经过多次实验提出了这一结论，这是本章介绍的发现过程。")
    condition = response["points"][0]["conditions"][0]
    if fault in {"discovery_history", "definition"}:
        condition.update(boundary_type=fault, source_ids=["history"],
                         condition="研究者经过多次实验提出这一结论。")
    elif fault == "empty_effect":
        condition["scope_effect"] = " "
    else:
        condition.pop("scope_effect")
    client = Client(response)
    with pytest.raises(TeachingScopeError):
        TeachingScopePlanner(client).plan(inputs, evidence)
    assert len(client.calls) == 3


def test_scope_payload_places_each_immutable_anchor_and_candidate_next_to_point():
    inputs, evidence, response = fixture()
    client = Client(response)
    TeachingScopePlanner(client).plan(inputs, evidence)
    point = json.loads(client.calls[0]["user"])["knowledge_points"][0]
    assert point["anchor_sources"] == [{"source_id": "anchor", "quote": evidence.sources["anchor"].quote}]
    assert point["candidate_sources"] == [{"source_id": "boundary", "quote": evidence.sources["boundary"].quote}]


@pytest.mark.parametrize("fault", ["omitted", "duplicate", "unrelated", "string_boolean", "missing_condition"])
def test_scope_comparisons_cannot_silently_skip_candidate_boundaries(fault):
    inputs, evidence, response = fixture()
    row = response["points"][0]
    if fault == "omitted":
        row["comparisons"] = []
    elif fault == "duplicate":
        row["comparisons"] *= 2
    elif fault == "unrelated":
        row["comparisons"][0]["source_id"] = "anchor"
    elif fault == "string_boolean":
        row["comparisons"][0]["changes_scope"] = "true"
    else:
        row["conditions"] = []
    client = Client(response)
    with pytest.raises(TeachingScopeError):
        TeachingScopePlanner(client).plan(inputs, evidence)
    assert len(client.calls) == 3


def test_later_planning_batch_format_failure_keeps_completed_batches():
    inputs, evidence, response = fixture()
    for index in range(5):
        key = f"p{index + 3}"
        evidence.point_sources[key] = ["anchor"]
        inputs.append({"point_id": key, "label": "稳定结论", "point_source_ids": ["anchor"], "scope_context_source_ids": []})
    class Batches:
        def __init__(self):
            self.calls = []
            self.lock = Lock()
            self.attempts = Counter()

        def structured(self, **kwargs):
            data = json.loads(kwargs["user"])
            point_ids = tuple(row["point_id"] for row in data["knowledge_points"])
            with self.lock:
                self.calls.append(data)
                self.attempts[point_ids] += 1
                attempt = self.attempts[point_ids]
            if point_ids == ("p5", "p6", "p7") and attempt == 1:
                return {"points": []}
            return {"points": [{"point_id": row["point_id"], "comparisons": [
                {"source_id": key, "changes_scope": False, "reason": "该候选未改变当前主题所陈述的适用条件。"}
                for key in row["scope_context_source_ids"]], "conditions": []} for row in data["knowledge_points"]]}
    client = Batches()
    plan = TeachingScopePlanner(client).plan(inputs, evidence)
    assert len(plan) == 7
    assert Counter(tuple(row["point_id"] for row in call["knowledge_points"])
                   for call in client.calls) == {
        ("p1", "p2", "p3", "p4"): 1, ("p5", "p6", "p7"): 2}
    later_batch = [call for call in client.calls
                   if {row["point_id"] for row in call["knowledge_points"]} == {"p5", "p6", "p7"}]
    assert [len(call["format_feedback"]) for call in later_batch] == [0, 1]


def test_scope_repair_identifies_missing_candidate_and_keeps_other_valid_point():
    inputs, evidence, correct = fixture()
    wrong = deepcopy(correct)
    wrong["points"][0]["conditions"][0]["source_ids"] = ["anchor"]
    client = Client(wrong, {"points": [correct["points"][0]]})
    plan = TeachingScopePlanner(client).plan(inputs, evidence)
    assert plan["p2"] == [] and plan["p1"][0]["source_ids"] == ["boundary"]
    repair = json.loads(client.calls[1]["user"])
    assert [item["point_id"] for item in repair["knowledge_points"]] == ["p1"]
    feedback = " ".join(repair["format_feedback"])
    assert "p1" in feedback and "boundary" in feedback and "conditions.source_ids" in feedback
    assert wrong["points"][0]["comparisons"][0]["reason"] in feedback
    assert "p2" not in feedback


def test_structurally_invalid_row_only_retries_that_point():
    inputs, evidence, correct = fixture()
    wrong = deepcopy(correct)
    wrong["points"][1]["conditions"] = None
    client = Client(wrong, {"points": [correct["points"][1]]})
    plan = TeachingScopePlanner(client).plan(inputs, evidence)
    assert set(plan) == {"p1", "p2"}
    assert [item["point_id"] for item in json.loads(client.calls[1]["user"])["knowledge_points"]] == ["p2"]


def actual_scope_failure():
    data = json.loads((Path(__file__).parent / "fixtures/teaching_scope_v13_first_batch.json").read_text())
    raw = data["evidence"]
    evidence = TeachingEvidence(
        sources={key: SourceCitation.model_validate(value) for key, value in raw["sources"].items()},
        point_sources=raw["point_sources"], warnings=raw["warnings"], fingerprint=raw["fingerprint"])
    return data, evidence


def test_real_v13_three_responses_stay_rejected_and_expose_specific_pending_sources():
    data, evidence = actual_scope_failure()
    class Replay(Client):
        def structured(self, **kwargs):
            # Replay the recorded per-point answers verbatim. Remove previously
            # completed rows only to respect the new smaller request envelope.
            response = super().structured(**kwargs)
            ids = {row["point_id"] for row in json.loads(kwargs["user"])["knowledge_points"]}
            response["points"] = [row for row in response["points"] if row["point_id"] in ids]
            return response
    client = Replay(*data["responses"])
    with pytest.raises(TeachingScopeError, match="有界格式纠错") as caught:
        TeachingScopePlanner(client).plan(data["inputs"], evidence)
    point_id = "kp_e54a48da99a35e27"
    assert [[row["point_id"] for row in json.loads(call["user"])["knowledge_points"]]
            for call in client.calls][1:] == [[point_id], [point_id]]
    failure = caught.value.review_feedback
    assert failure["pending_point_ids"] == [point_id]
    assert set(failure["completed_point_ids"]) == {row["point_id"] for row in data["inputs"]} - {point_id}
    feedback = " ".join(failure["format_feedback"])
    assert all(source_id in feedback for source_id in (
        "src_p66_0_489_6750f559b21e", "src_p64_483_966_2bd366071914", "src_p63_0_471_286b45930c0c"))


def test_real_v13_first_response_can_be_repaired_only_for_its_actual_missing_relation():
    data, evidence = actual_scope_failure()
    correct = deepcopy(data["responses"][0]["points"][0])
    missing_source = "src_p66_0_489_6750f559b21e"
    # This is an offline correction fixture, not an alleged real model success.
    # The raw source explicitly supports the existing environmental condition.
    environment = next(condition for condition in correct["conditions"] if "环境" in condition["condition"])
    environment["source_ids"].append(missing_source)
    client = Client(data["responses"][0], {"points": [correct]})
    plan = TeachingScopePlanner(client).plan(data["inputs"], evidence)
    assert len(plan) == 4 and len(client.calls) == 2
    assert [item["point_id"] for item in json.loads(client.calls[1]["user"])["knowledge_points"]] == [correct["point_id"]]
    for row in data["responses"][0]["points"][1:]:
        assert plan[row["point_id"]] == row["conditions"]
    assert any(missing_source in condition["source_ids"] for condition in plan[correct["point_id"]])


def test_scope_repair_release_has_a_new_executable_course_revision():
    from adaptive_learning.personalization.teaching_content import TEACHING_CONTENT_VERSION
    assert TEACHING_CONTENT_VERSION == (
        "whole-course-evidence-review-v20+scope-plan-v3+native-units-v1+question-roles-v1+atomic-rubrics-v1+precise-citations-v1+practice-task-closure-v1+negative-findings-v1+lesson-task-revision-v1+candidate-context-v1"
    )
