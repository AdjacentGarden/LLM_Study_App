"""A misplaced transport hint must not consume a semantic repair attempt."""
import hashlib
import json
from copy import deepcopy

import pytest
from pydantic import ValidationError
from test_teaching_content import course, lesson_candidate
from test_teaching_v15_integration import CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching


def response():
    # Exact shape observed in F's first lesson-only response: all business
    # fields, plus the transport marker at the same level.
    return {"type": "json_object", "points": [], "lesson": lesson_candidate()}


def test_observed_transport_marker_preserves_content_and_records_nonapproval():
    events = []
    generator = teaching.TeachingContentGenerator(CandidateClient(), audit_callback=events.append)
    raw = response()
    before = deepcopy(raw)
    parsed = generator._validate_generation_response(raw)
    assert parsed.model_dump() == {"points": [], "lesson": before["lesson"]}
    assert raw == before
    event, = events
    assert event["event"] == "generation_envelope_normalized"
    assert event["approved"] is False and event["independent_review_required"] is True
    assert event["original_sha256"] == hashlib.sha256(
        json.dumps(before, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


@pytest.mark.parametrize("extras", [
    {"type": "json_schema"}, {"type": {"type": "json_object"}},
    {"type": "JSON_OBJECT"}, {"type": None},
    {"type": "json_object", "approved": True},
    {"type": "json_object", "response_format": {"type": "json_object"}},
])
def test_other_envelopes_and_approval_fields_remain_forbidden(extras):
    generator = teaching.TeachingContentGenerator(CandidateClient())
    with pytest.raises(ValidationError):
        generator._validate_generation_response({**response(), **extras})


@pytest.mark.parametrize("change", [
    {"points": "not-a-list"}, {"lesson": {"opening": "incomplete"}},
    {"lesson": {**lesson_candidate(), "summary": "字" * (teaching.SUMMARY_MAX_CHARS + 1)}},
])
def test_marker_cannot_repair_invalid_content_or_raise_existing_limits(change):
    events = []
    generator = teaching.TeachingContentGenerator(CandidateClient(), audit_callback=events.append)
    with pytest.raises(ValidationError):
        generator._validate_generation_response({**response(), **change})
    assert events == []


def test_required_normalization_audit_failure_stops_before_review_or_publication():
    def fail(_):
        raise OSError("audit unavailable")
    generator = teaching.TeachingContentGenerator(CandidateClient(), audit_callback=fail)
    with pytest.raises(teaching.TeachingServiceUnavailableError):
        generator._validate_generation_response(response())


class EchoFormatClient(CandidateClient):
    def structured(self, *, system, user, **kwargs):
        result = super().structured(system=system, user=user, **kwargs)
        if system == teaching.GENERATION_PROMPT:
            return {**result, "type": "json_object"}
        return result


def test_observed_shape_reaches_independent_reviews_in_one_existing_attempt():
    bundle, chapter, evidence = course()
    client = EchoFormatClient()
    events = []
    result = teaching.TeachingContentGenerator(
        client, review_client=client,
        scope_planner=Planner(), max_attempts=1, audit_callback=events.append,
    ).generate(bundle, chapter, evidence)
    assert result.teaching_review["accepted"] and result.teaching_fact_review["accepted"]
    assert len(client.generations) == 2  # One points batch and one lesson batch.
    assert all(data["wire_format"] == "teaching-fact-source-refs-v1"
               for data in client.generations)
    assert {call[0][0] for call in client.calls} == {"fact", "teacher", "rubric"}
    assert len([event for event in events if event["event"] == "candidate"]) == 1
    normalized = [event for event in events if event["event"] == "generation_envelope_normalized"]
    assert len(normalized) == 2 and all(not event["approved"] for event in normalized)
    raw = [event for event in events if event["event"] == "model_response"
           and event.get("model_role") == "generation"]
    assert len(raw) == 2 and all(event["response"]["type"] == "json_object" for event in raw)


def test_normalized_shape_does_not_bypass_a_real_teacher_rejection():
    bundle, chapter, evidence = course()
    client = EchoFormatClient(teacher_lesson={"example_1"})
    with pytest.raises(teaching.TeachingContentError):
        teaching.TeachingContentGenerator(
            client, review_client=client,
            scope_planner=Planner(), max_attempts=1,
        ).generate(bundle, chapter, evidence)
    assert len(client.generations) == 2
    assert any(call[0][0] == "teacher" for call in client.calls)
