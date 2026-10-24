"""One-point review load bounds must retain the complete publication contract."""

from collections import Counter

import pytest
from test_teaching_content import course, point_candidate
from test_teaching_v15_integration import CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching


def jobs(count=7, *, boundaries=False):
    bundle, chapter, evidence = course(count)
    client = CandidateClient()
    generator = teaching.TeachingContentGenerator(client, scope_planner=Planner())
    inputs = generator._inputs(bundle, evidence)
    for item in inputs:
        item["scope_conditions"] = [
            {"boundary_type": "precondition", "condition": f"第{index}项需核对温度不变与密闭条件。",
             "source_ids": ["s1"], "scope_effect": "条件改变后需重新测量。"}
            for index in range(4)
        ] if boundaries else []
    points = [teaching._Point.model_validate(point_candidate(item)) for item in inputs]
    return generator, points, evidence, generator._build_fact_jobs(points, None, inputs, chapter, evidence, [])


def test_each_request_has_one_complete_point_and_all_own_evidence_and_boundaries():
    _, points, evidence, requests = jobs(boundaries=True)
    assert len(requests) == len(points)
    assert Counter(key for job in requests for key in job["payload"]["output_contract"]["point_ids"]) == Counter(
        {point.point_id: 1 for point in points})
    for job in requests:
        payload = job["payload"]
        assert len(job["batch"]) == len(payload["candidate"]["points"]) == 1
        point = payload["candidate"]["points"][0]
        assert set(point["component_evidence"]) == {"explanation", "flashcard", "practice"}
        assert len(point["scope_boundaries"]) == 4
        assert all(boundary["actual_source_text"][0]["quote"] == evidence.sources["s1"].quote
                   for boundary in point["scope_boundaries"])
        assert payload["sources"] == {key: source.model_dump() for key, source in evidence.sources.items()}
        for part in point["component_evidence"].values():
            assert part["component_fields"] and part["allowed_support_span_ids"]
            assert part["actual_source_text"] == [{"source_id": "s1", "quote": evidence.sources["s1"].quote}]


@pytest.mark.parametrize("fault", ["missing", "duplicate"])
def test_small_batches_cannot_publish_an_incomplete_or_duplicate_point_review(fault):
    generator, points, _, requests = jobs()
    reviews = [generator._request_fact_batch(**job) for job in requests]
    assert generator._aggregate_fact_reviews(reviews, points, None).accepted
    damaged = reviews[:-1] if fault == "missing" else [*reviews, reviews[0]]
    with pytest.raises(teaching._TeachingReviewFormatError, match="知识点"):
        generator._aggregate_fact_reviews(damaged, points, None)
