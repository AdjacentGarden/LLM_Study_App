"""Portable synthetic regressions for precise point-quote format feedback.

All fake responses traverse the production strict batch validators. No external
run evidence, cloned implementation, provider client or model call is needed.
"""

import json
from copy import deepcopy

import pytest
from test_teaching_content import Client, course, point_candidate

from adaptive_learning.personalization import teaching_content as teaching

GENERIC_CLAIM = "范围核验的断言片段必须逐字属于当前组件的真实字段"
GENERIC_LIMITATION = "bounded必须给出当前组件内真实存在的限缩原句，不能借另一组件补例外"


class SequenceClient:
    def __init__(self, responses):
        self.responses = iter(deepcopy(responses))
        self.requests = []

    def structured(self, *, system, user, **kwargs):
        assert system == teaching.FACT_REVIEW_PROMPT
        assert kwargs["max_tokens"] == 10000
        self.requests.append(json.loads(user))
        return next(self.responses)


@pytest.fixture
def batch():
    bundle, chapter, evidence = course(2)
    seed = Client()
    generator = teaching.TeachingContentGenerator(seed)
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = [{
            "boundary_type": "precondition",
            "condition": "温度保持不变且容器密闭时才能直接沿用体积稳定结论。",
            "scope_effect": "温度改变后不能直接沿用，应重新测量。",
            "source_ids": ["s1"],
        }]
    points = [teaching._Point.model_validate(point_candidate(item)) for item in inputs]
    job, = generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])
    raw = seed.structured(system=teaching.FACT_REVIEW_PROMPT,
        user=json.dumps({**job["payload"], "format_feedback": []}, ensure_ascii=False))
    assert "components" in raw and len(raw["components"]) == 6
    return job, raw


def component(raw, point, name="explanation"):
    return next(row for row in raw["components"]
                if row["point_id"] == point.point_id and row["component_id"] == name)


def boundary_check(raw, point, name="explanation"):
    check, = component(raw, point, name)["scope_checks"]
    return check


def invalid_claim(batch, name="explanation"):
    job, good = batch
    bad = deepcopy(good)
    point = job["batch"][1]
    boundary_check(bad, point, name)["claim_quote"] = "这段无效引用没有出现在当前教学字段中"
    return bad


def request(job, responses):
    client = SequenceClient(responses)
    generator = teaching.TeachingContentGenerator(client)
    return generator, client


@pytest.mark.parametrize("name", ["explanation", "flashcard", "practice"])
def test_correction_names_exact_owner_component_boundary_and_actual_field(batch, name):
    job, _ = batch
    point = job["batch"][1]
    bad = invalid_claim(batch, name)
    selected = teaching.TeachingContentGenerator._decode_fact_selection(bad, job["batch"], set())
    before = selected.model_dump()
    detail = teaching.TeachingContentGenerator._point_quote_format_correction(selected, job["batch"])
    check = boundary_check(bad, point, name)
    fields = teaching.TeachingContentGenerator._point_component_fields(point, name)
    assert detail["format_correction"] == "invalid_point_scope_quote"
    assert (detail["point_id"], detail["component_id"], detail["boundary_id"]) == (
        point.point_id, name, check["boundary_id"]
    )
    assert detail["quote_key"] == "claim_quote"
    assert detail["field"] == check["claim_field"]
    assert detail["invalid_quote"] == check["claim_quote"]
    assert detail["actual_field_text"] == fields[check["claim_field"]]
    assert detail["allowed_field_names"] == list(fields)
    assert detail["quote_max_characters"] == 240
    assert len(json.dumps(detail, ensure_ascii=False)) < 2000
    assert selected.model_dump() == before


def test_only_invalid_quote_changes_before_complete_batch_validation(batch):
    job, good = batch
    bad = invalid_claim(batch)
    point = job["batch"][1]
    repaired = deepcopy(bad)
    boundary_check(repaired, point)["claim_quote"] = boundary_check(good, point)["claim_quote"]
    assert repaired == good
    snapshot = deepcopy(bad)
    generator, client = request(job, [bad, repaired])
    review = generator._request_fact_batch(**job)
    assert review.accepted is True and len(review.points) == 2
    assert len(client.requests) == teaching.FACT_FORMAT_ATTEMPTS == 2
    first, second = deepcopy(client.requests)
    assert first.pop("format_feedback") == []
    feedback = second.pop("format_feedback")
    assert first == second
    assert feedback[0] == GENERIC_CLAIM
    assert json.loads(feedback[1])["invalid_quote"] == boundary_check(bad, point)["claim_quote"]
    assert bad == snapshot


@pytest.mark.parametrize("mode", ["deleted_middle", "cross_field", "cross_point", "source_text"])
def test_noncontiguous_or_foreign_quote_still_exhausts_two_attempts(batch, mode):
    job, good = batch
    bad = deepcopy(good)
    point = job["batch"][1]
    check = boundary_check(bad, point)
    if mode == "deleted_middle":
        check["claim_quote"] = point.explanation[:6] + point.explanation[9:36]
    elif mode == "cross_field":
        check["claim_quote"] = point.flashcard.front
    elif mode == "cross_point":
        check["claim_quote"] = job["batch"][0].explanation[:60]
    else:
        check["claim_quote"] = job["evidence"].sources["s1"].quote[:60]
    assert check["claim_quote"] not in point.explanation
    snapshot = deepcopy(bad)
    generator, client = request(job, [bad, bad])
    with pytest.raises(teaching._TeachingReviewFormatError) as caught:
        generator._request_fact_batch(**job)
    assert len(client.requests) == teaching.FACT_FORMAT_ATTEMPTS == 2
    issues = caught.value.review_feedback["format_issues"]
    assert len(issues) == 4 and issues[0] == issues[2] == GENERIC_CLAIM
    assert json.loads(issues[1])["point_id"] == point.point_id
    assert json.loads(issues[3])["invalid_quote"] == check["claim_quote"]
    assert bad == snapshot


def test_limitation_correction_reports_its_own_field_not_the_claim_field(batch):
    job, good = batch
    point = job["batch"][1]
    bad = deepcopy(good)
    check = boundary_check(bad, point, "practice")
    assert check["claim_field"] == "expected_answer"
    check["limitation_field"] = "prompt"
    check["limitation_quote"] = "该限制引文没有出现在真实题干中"
    repaired = deepcopy(bad)
    boundary_check(repaired, point, "practice")["limitation_quote"] = point.practice.prompt
    generator, client = request(job, [bad, repaired])
    assert generator._request_fact_batch(**job).accepted is True
    feedback = client.requests[1]["format_feedback"]
    assert feedback[0] == GENERIC_LIMITATION
    detail = json.loads(feedback[1])
    assert detail["quote_key"] == "limitation_quote"
    assert detail["component_id"] == "practice" and detail["field"] == "prompt"
    assert detail["actual_field_text"] == point.practice.prompt
    assert len(client.requests) == 2


def test_other_format_error_keeps_generic_feedback_without_unrelated_quote_advice(batch):
    job, good = batch
    bad = deepcopy(good)
    bad["components"].pop()
    generator, client = request(job, [bad, good])
    assert generator._request_fact_batch(**job).accepted is True
    feedback = client.requests[1]["format_feedback"]
    assert len(feedback) == 1
    assert "invalid_point_scope_quote" not in feedback[0]


@pytest.mark.parametrize("fault", ["unknown_id", "missing_id", "string_bool"])
def test_unknown_missing_identifier_and_nonboolean_remain_fail_closed(batch, fault):
    job, good = batch
    bad = deepcopy(good)
    if fault == "unknown_id":
        bad["components"][0]["point_id"] = "unknown_point"
    elif fault == "missing_id":
        bad["components"].pop()
    else:
        bad["components"][0]["source_entailment"] = "true"
    generator, client = request(job, [bad, bad])
    with pytest.raises(teaching._TeachingReviewFormatError):
        generator._request_fact_batch(**job)
    assert len(client.requests) == 2
    assert client.requests[1]["format_feedback"]


def test_quote_repair_cannot_turn_another_component_negative_verdict_true(batch):
    job, good = batch
    other = job["batch"][0]
    row = component(good, other)
    row.update(source_entailment=False, support_span_ids=[])
    row["findings"] = [{
        "kind": "unsupported_claim", "candidate_field": "explanation",
        "candidate_quote": other.explanation[:30],
        "issue": "该独立组件仍有审核器明确指出的原文支持问题。",
        "evidence_source_ids": ["s1"],
    }]
    bad = deepcopy(good)
    boundary_check(bad, job["batch"][1])["claim_quote"] = "该断言引用不是当前字段的连续原文"
    generator, client = request(job, [bad, good])
    review = generator._request_fact_batch(**job)
    rejected = next(row for row in review.points if row.point_id == other.point_id)
    assert review.accepted is False
    assert rejected.components.explanation.source_entailment is False
    assert rejected.components.explanation.findings[0].candidate_quote == other.explanation[:30]
    assert rejected.failed_components == ["explanation"]
    assert len(client.requests) == 2


def test_different_second_format_error_preserves_history_in_terminal_feedback(batch):
    job, good = batch
    missing = deepcopy(good)
    missing["components"].pop()
    bad_quote = invalid_claim(batch)
    generator, client = request(job, [missing, bad_quote])
    with pytest.raises(teaching._TeachingReviewFormatError) as caught:
        generator._request_fact_batch(**job)
    prior_feedback = client.requests[1]["format_feedback"]
    assert len(prior_feedback) == 1
    final_feedback = caught.value.review_feedback["format_issues"]
    assert final_feedback[0] == prior_feedback[0]
    assert final_feedback[1] == GENERIC_CLAIM
    assert json.loads(final_feedback[2])["point_id"] == job["batch"][1].point_id
    assert len(client.requests) == 2
