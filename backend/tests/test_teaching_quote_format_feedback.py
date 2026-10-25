"""Fixed assertion IDs remove copied point-quote responses from production.

Keep direct coverage of the legacy literal-location helper, and exercise the
new provider contract's same-candidate bounded format correction. All responses
are offline controls; no fake semantic verdict is evidence of lesson quality.
"""

import json
from copy import deepcopy

import pytest
from test_teaching_content import Client, course, point_candidate

from adaptive_learning.personalization import teaching_content as teaching


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
def batch(monkeypatch):
    monkeypatch.setattr(teaching, "FACT_BATCH_SIZE", 2)
    bundle, chapter, evidence = course(2)
    seed = Client()
    generator = teaching.TeachingContentGenerator(seed)
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = [{"boundary_type": "precondition",
            "condition": "温度保持不变且容器密闭时才能直接沿用体积稳定结论。",
            "scope_effect": "温度改变后不能直接沿用，应重新测量。", "source_ids": ["s1"]}]
    points = [teaching._Point.model_validate(point_candidate(item)) for item in inputs]
    job, = generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])
    raw = seed.structured(system=teaching.FACT_REVIEW_PROMPT,
        user=json.dumps({**job["payload"], "format_feedback": []}, ensure_ascii=False))
    assert len(raw["components"]) == 6
    assert all(set(row) == {"point_id", "component_id", "assertion_checks", "scope_matrix"}
               for row in raw["components"])
    return job, raw


def component(raw, point, name="explanation"):
    return next(row for row in raw["components"]
                if row["point_id"] == point.point_id and row["component_id"] == name)


def selected(batch):
    job, raw = batch
    materialized = deepcopy(raw)
    for row in materialized["components"]:
        teaching.TeachingContentGenerator._normalize_component_selection(
            row, job["payload"], job["evidence"], job["spans"])
    return teaching.TeachingContentGenerator._decode_fact_selection(materialized, job["batch"], set())


def request(responses):
    client = SequenceClient(responses)
    return teaching.TeachingContentGenerator(client), client


@pytest.mark.parametrize("name", ["explanation", "flashcard", "practice"])
def test_correction_names_exact_owner_component_boundary_and_actual_field(batch, name):
    job, _ = batch
    point = job["batch"][1]
    review = selected(batch)
    proof = getattr(review.points[1].components, name)
    field = {"explanation": "explanation", "flashcard": "back", "practice": "expected_answer"}[name]
    check = next(row for row in proof.scope_checks if row.claim_field == field)
    check.claim_quote = "这段无效引用没有出现在当前教学字段中"
    before = review.model_dump()
    detail = teaching.TeachingContentGenerator._point_quote_format_correction(review, job["batch"])
    assert (detail["point_id"], detail["component_id"], detail["boundary_id"]) == (
        point.point_id, name, check.boundary_id)
    assert detail["quote_key"] == "claim_quote" and detail["field"] == field
    assert detail["actual_field_text"] == teaching.TeachingContentGenerator._point_component_fields(point, name)[field]
    assert detail["invalid_quote"] == check.claim_quote
    assert detail["quote_max_characters"] == 240
    assert review.model_dump() == before


def test_only_invalid_quote_changes_before_complete_batch_validation(batch):
    job, good = batch
    bad = deepcopy(good)
    component(bad, job["batch"][1])["assertion_checks"][0]["assertion_id"] = "foreign_assertion"
    snapshot = deepcopy(bad)
    generator, client = request([bad, good])
    review = generator._request_fact_batch(**job)
    assert review.accepted and len(review.points) == 2
    assert len(client.requests) == teaching.FACT_FORMAT_ATTEMPTS == 2
    first, second = deepcopy(client.requests)
    assert first.pop("format_feedback") == []
    assert "exactly cover" in " ".join(second.pop("format_feedback"))
    assert first == second and bad == snapshot
    # The source text and exact field spans are already in the fixed directory;
    # a provider no longer needs to copy a claim_quote back to prove its ID.
    assert all("claim_quote" not in row for row in good["components"])


@pytest.mark.parametrize("mode", ["deleted_middle", "cross_field", "cross_point", "source_text"])
def test_noncontiguous_or_foreign_quote_still_exhausts_two_attempts(batch, mode):
    job, good = batch
    bad = deepcopy(good)
    target = component(bad, job["batch"][1])
    if mode == "deleted_middle":
        original = target["assertion_checks"][0]["assertion_id"]
        foreign = original[:12] + original[16:]
    elif mode == "cross_field":
        foreign = component(good, job["batch"][1], "flashcard")["assertion_checks"][0]["assertion_id"]
    elif mode == "cross_point":
        foreign = component(good, job["batch"][0])["assertion_checks"][0]["assertion_id"]
    else:
        foreign = job["evidence"].sources["s1"].quote
    target["assertion_checks"][0]["assertion_id"] = foreign
    generator, client = request([bad, bad])
    with pytest.raises(teaching.TeachingServiceUnavailableError):
        generator._request_fact_batch(**job)
    assert len(client.requests) == teaching.FACT_FORMAT_ATTEMPTS == 2


def test_limitation_correction_reports_its_own_field_not_the_claim_field(batch):
    job, _ = batch
    review = selected(batch)
    proof = review.points[1].components.flashcard
    check = proof.scope_checks[0]
    fields = teaching.TeachingContentGenerator._point_component_fields(job["batch"][1], "flashcard")
    check.claim_field, check.claim_quote = "front", fields["front"]
    check.status, check.candidate_explicitly_limited = "bounded", True
    check.limitation_field, check.limitation_quote = "back", "其他字段中不存在的限缩原句"
    detail = teaching.TeachingContentGenerator._point_quote_format_correction(review, job["batch"])
    assert detail["quote_key"] == "limitation_quote" and detail["field"] == "back"
    assert detail["actual_field_text"] == fields["back"]


def test_quote_repair_cannot_turn_another_component_negative_verdict_true(batch):
    job, good = batch
    rejected = deepcopy(good)
    point = job["batch"][0]
    row = component(rejected, point, "practice")
    part = job["payload"]["candidate"]["points"][0]["component_evidence"]["practice"]
    assertion = next(a for a in part["assertion_contract"]["assertions"] if a["field"] == "expected_answer")
    check = next(a for a in row["assertion_checks"] if a["assertion_id"] == assertion["assertion_id"])
    check.update(source_entailment=False, support_span_ids=[], findings=[{
        "kind": "invalid_inference", "quote": assertion["text"],
        "issue": "当前答案的这项具体推理不受原文前提支持。", "evidence_source_ids": ["s1"]}])
    bad = deepcopy(rejected)
    component(bad, job["batch"][1])["assertion_checks"][0]["assertion_id"] = "unknown"
    generator, client = request([bad, rejected])
    review = generator._request_fact_batch(**job)
    assert not review.accepted and review.points[0].failed_components == ["practice"]
    assert len(client.requests) == 2
    assert review.points[0].components.practice.findings


def test_different_second_format_error_preserves_history_in_terminal_feedback(batch):
    job, good = batch
    first, second = deepcopy(good), deepcopy(good)
    component(first, job["batch"][1])["assertion_checks"][0]["assertion_id"] = "unknown"
    component(second, job["batch"][1])["assertion_checks"][0]["support_span_ids"] = ["unknown_span"]
    generator, client = request([first, second])
    with pytest.raises(teaching._TeachingReviewFormatError) as caught:
        generator._request_fact_batch(**job)
    issues = caught.value.review_feedback["format_issues"]
    assert len(client.requests) == 2 and len(issues) == 2 and issues[0] != issues[1]
