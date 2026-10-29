"""Actual F planner shape; scripted repairs verify mechanics, not model accuracy."""

import json
from copy import deepcopy
from pathlib import Path

import pytest

from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_scope import (
    MAX_CONDITIONS_PER_POINT,
    SCOPE_PLANNING_PROMPT,
    TEACHING_SCOPE_VERSION,
    TeachingScopePlanner,
    TeachingScopeServiceError,
)

POINT = "kp_5add9c58c78973b5"
MISSING = "src_p12_0_338_8132c4bcdcca"


def observed():
    value = json.loads((Path(__file__).parent / "fixtures" /
        "teaching_scope_f_a0dd_source_links.json").read_text())
    evidence = TeachingEvidence(
        sources={key: SourceCitation(**row) for key, row in value["sources"].items()},
        point_sources={row["point_id"]: row["point_source_ids"] for row in value["inputs"]},
        warnings=value["source_warnings"], fingerprint="actual-f-a0dd-bounded-source-fixture",
    )
    return value, evidence


def repair(raw, action):
    row = deepcopy(raw)
    if action == "recompare":
        candidate = next(value for value in row["comparisons"] if value["source_id"] == MISSING)
        candidate.update(changes_scope=False,
            reason="该候选描述雄性动物形成精子的局部过程，不应把该子类过程当作所有减数分裂定义的必要条件。")
    else:
        # The scripted writer merges two related conditions before expressing
        # the separately scoped process condition. Production never edits it.
        first, fourth = row["conditions"][0], row["conditions"].pop()
        first["condition"] += "；" + fourth["condition"]
        first["source_ids"] = list(dict.fromkeys(first["source_ids"] + fourth["source_ids"]))
        row["conditions"].append({
            "boundary_type": "process_scale",
            "condition": "在精子形成过程中，减数分裂形成的精细胞还需经过变形才成为精子",
            "scope_effect": "该限制仅针对精子形成的局部过程，不能把精细胞直接等同于成熟精子。",
            "source_ids": [MISSING],
        })
    return row


class Client:
    def __init__(self, responder):
        self.responder = responder
        self.calls = []

    def structured(self, **kwargs):
        request = json.loads(kwargs["user"])
        self.calls.append(request)
        return self.responder(request, len(self.calls))


@pytest.mark.parametrize("action", ["link", "recompare"])
def test_real_four_condition_failure_gets_exact_pending_raw_and_actionable_link_task(action):
    value, evidence = observed()
    original = deepcopy(value)
    raw = value["response"]["points"][0]
    assert len(raw["conditions"]) == MAX_CONDITIONS_PER_POINT == 4
    assert next(row for row in raw["comparisons"] if row["source_id"] == MISSING)["changes_scope"]
    assert not any(MISSING in condition["source_ids"] for condition in raw["conditions"])

    def respond(request, call):
        if call == 1:
            assert "previous_candidate" not in request and "repair_tasks" not in request
            return deepcopy(value["response"])
        assert [row["point_id"] for row in request["knowledge_points"]] == [POINT]
        assert request["previous_candidate"] == {"points": [raw]}
        task = request["repair_tasks"][0]
        assert task["point_id"] == POINT and task["missing_candidate_source_ids"] == [MISSING]
        assert task["candidate_comparisons"] == [next(
            row for row in raw["comparisons"] if row["source_id"] == MISSING)]
        assert task["condition_index_base"] == 0 and task["maximum_conditions"] == 4
        assert task["current_conditions"] == [
            {"condition_index": index, **condition} for index, condition in enumerate(raw["conditions"])]
        assert task["semantic_approval"] is False
        # SourceCitation now also serializes its default block_id metadata.
        assert request["sources"] == {key: source.model_dump() for key, source in evidence.sources.items()}
        assert {key: {field: source[field] for field in ('page_number', 'quote')}
                for key, source in request['sources'].items()} == value['sources']
        assert request["source_warnings"] == value["source_warnings"]
        assert "origin" not in request
        return {"points": [repair(raw, action)]}

    client = Client(respond)
    plan = TeachingScopePlanner(client).plan(value["inputs"], evidence)
    expected = repair(raw, action)
    assert plan[POINT] == expected["conditions"]
    accepted = value["response"]["points"][1]
    assert plan[accepted["point_id"]] == accepted["conditions"]
    assert len(client.calls) == 2 and value == original


@pytest.mark.parametrize("action", ["link", "recompare"])
def test_both_repair_paths_still_reject_invented_condition_source(action):
    value, evidence = observed()
    raw = value["response"]["points"][0]

    def respond(request, call):
        if call == 1:
            return deepcopy(value["response"])
        repaired = repair(raw, action)
        repaired["conditions"][0]["source_ids"].append("invented-source")
        return {"points": [repaired]}

    client = Client(respond)
    with pytest.raises(TeachingScopeServiceError, match="有界格式纠错"):
        TeachingScopePlanner(client).plan(value["inputs"], evidence)
    assert len(client.calls) == 3
    assert all("invented-source" not in call["sources"] for call in client.calls)
    assert any("未知" in message for message in client.calls[-1]["format_feedback"])


def test_unchanged_link_is_never_fixed_or_approved_by_program():
    value, evidence = observed()
    raw = value["response"]["points"][0]
    client = Client(lambda request, call: deepcopy(value["response"]) if call == 1
                    else {"points": [deepcopy(raw)]})
    with pytest.raises(TeachingScopeServiceError) as caught:
        TeachingScopePlanner(client).plan(value["inputs"], evidence)
    assert len(client.calls) == 3
    assert caught.value.review_feedback["pending_point_ids"] == [POINT]
    for call in client.calls[1:]:
        assert call["previous_candidate"]["points"] == [raw]
        assert call["repair_tasks"][0]["missing_candidate_source_ids"] == [MISSING]
        assert [row["point_id"] for row in call["knowledge_points"]] == [POINT]


def test_wrong_point_response_cannot_enter_previous_candidate_or_repair_tasks():
    value, evidence = observed()
    raw = value["response"]["points"][0]

    def respond(request, call):
        if call == 1:
            wrong = deepcopy(value["response"])
            wrong["points"][0]["point_id"] = "foreign-point"
            return wrong
        if call == 2:
            assert "previous_candidate" not in request and "repair_tasks" not in request
            return deepcopy(value["response"])
        assert request["previous_candidate"] == {"points": [raw]}
        assert "foreign-point" not in json.dumps(request)
        return {"points": [repair(raw, "recompare")]}

    client = Client(respond)
    assert set(TeachingScopePlanner(client).plan(value["inputs"], evidence)) == {
        row["point_id"] for row in value["inputs"]}
    assert len(client.calls) == 3


def test_repair_does_not_raise_condition_or_attempt_caps_and_has_explicit_local_scope_rule():
    value, evidence = observed()
    raw = value["response"]["points"][0]

    def respond(request, call):
        if call == 1:
            return deepcopy(value["response"])
        too_many = repair(raw, "link")
        too_many["conditions"].append(deepcopy(too_many["conditions"][0]))
        return {"points": [too_many]}

    client = Client(respond)
    with pytest.raises(TeachingScopeServiceError, match="有界格式纠错"):
        TeachingScopePlanner(client).plan(value["inputs"], evidence)
    assert len(client.calls) == 3 and MAX_CONDITIONS_PER_POINT == 4
    assert TEACHING_SCOPE_VERSION == "scope-plan-v4"
    assert "子类实例或局部过程的发生前提不自动成为整个类别定义的必要条件" in SCOPE_PLANNING_PROMPT
    assert "scope_effect不得把局部限制扩成全类限制" in SCOPE_PLANNING_PROMPT
