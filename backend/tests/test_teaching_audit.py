import json
import stat
from collections import Counter

import pytest
from test_teaching_content import Client, course

from adaptive_learning.api.teaching_audit import PrivateTeachingAudit
from adaptive_learning.personalization.rubric_alignment import RUBRIC_ALIGNMENT_PROMPT
from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    GENERATION_PROMPT,
    REVIEW_PROMPT,
    TeachingContentError,
    TeachingContentGenerator,
)
from adaptive_learning.personalization.teaching_scope import (
    SCOPE_PLANNING_PROMPT,
    TeachingScopeServiceError,
)


def test_audit_records_canonical_candidates_and_raw_reviews_without_extra_model_calls():
    bundle, chapter, evidence = course(2)
    client, events = Client(), []
    result = TeachingContentGenerator(client, audit_callback=events.append).generate(bundle, chapter, evidence)
    assert result.teaching_review["accepted"] is True
    # Lesson facts remain independent; v18 adds one bounded two-item rubric audit.
    assert Counter(client.systems) == {
        SCOPE_PLANNING_PROMPT: 1, GENERATION_PROMPT: 2,
        FACT_REVIEW_PROMPT: 8, REVIEW_PROMPT: 1, RUBRIC_ALIGNMENT_PROMPT: 1}
    assert events[0]["event"] == "started" and events[-1]["event"] == "accepted"
    assert len({event["invocation_id"] for event in events}) == 1
    candidate = next(event["candidate"] for event in events if event["event"] == "candidate")
    assert candidate["lesson"]["checkpoints"][0]["expected_answer"]
    requests = {event["call_id"] for event in events if event["event"] == "model_request"}
    responses = {event["call_id"] for event in events if event["event"] == "model_response"}
    assert requests == responses and len(requests) == len(client.systems)
    fact_payloads = [payload for system, payload in zip(client.systems, client.payloads, strict=True)
        if system == FACT_REVIEW_PROMPT]
    assert Counter(key for payload in fact_payloads for key in payload["lesson_components"]) == {
        "opening": 1, "summary": 1, "example_1": 1, "example_2": 1,
        "checkpoint_1": 1, "checkpoint_2": 1}
    rubric_requests = [event for event in events if event["event"] == "model_request"
        and event.get("review_stage") == "rubric"]
    assert len(rubric_requests) == 1
    assert rubric_requests[0]["candidate_sha256"]
    rubric_payloads = [payload for system, payload in zip(client.systems, client.payloads, strict=True)
        if system == RUBRIC_ALIGNMENT_PROMPT]
    assert len(rubric_payloads) == 1 and len(rubric_payloads[0]["items"]) == 2
    assert all(set(item) == {"item_id", "prompt", "rubric"} for item in rubric_payloads[0]["items"])
    rubric_events = [event for event in events if event["event"] == "rubric_review"]
    assert len(rubric_events) == 1 and rubric_events[0]["review"]["accepted"]
    assert rubric_events[0]["candidate_sha256"] == rubric_requests[0]["candidate_sha256"]


def test_failed_review_retains_candidate_responses_and_final_feedback_without_publication():
    bundle, chapter, evidence = course(2)
    before = bundle.model_dump_json()
    events = []
    with pytest.raises(TeachingContentError):
        TeachingContentGenerator(Client("fact_reject"), audit_callback=events.append).generate(bundle, chapter, evidence)
    assert any(event["event"] == "candidate" for event in events)
    assert any(event["event"] == "model_response" for event in events)
    assert events[-1]["event"] == "failed" and events[-1]["review_feedback"]
    assert bundle.model_dump_json() == before


def test_audit_storage_failure_stops_before_paid_calls_and_does_not_echo_private_error():
    bundle, chapter, evidence = course(1)
    client = Client()
    def unavailable(event):
        raise OSError("PRIVATE_STORAGE_PATH /private/secrets")
    with pytest.raises(TeachingContentError) as caught:
        TeachingContentGenerator(client, audit_callback=unavailable).generate(bundle, chapter, evidence)
    assert client.systems == []
    assert "PRIVATE" not in str(caught.value)


def test_scope_structure_failure_retains_specific_private_feedback_before_generation():
    bundle, chapter, evidence = course(1)
    feedback = {"stage": "scope_planning", "pending_point_ids": ["p1"],
                "format_feedback": ["p1: conditions.source_ids缺少boundary"]}
    class Planner:
        def plan(self, inputs, evidence):
            raise TeachingScopeServiceError("有界格式纠错后未完成", feedback)
    client, events = Client(), []
    with pytest.raises(TeachingContentError) as caught:
        TeachingContentGenerator(client, scope_planner=Planner(), audit_callback=events.append).generate(bundle, chapter, evidence)
    assert caught.value.review_feedback == feedback
    assert events[-1]["event"] == "failed" and events[-1]["review_feedback"] == feedback
    assert client.systems == []


def test_private_storage_redacts_credentials_and_uses_non_public_permissions(tmp_path):
    secret = "example-provider-credential-value"
    writer = PrivateTeachingAudit(tmp_path / "private-audits", context={"provider": "test", "model": "text"}, secrets=(secret,))
    writer({"invocation_id": "a" * 32, "course_id": "../../outside", "event": "model_response",
        "response": {"candidate": "preserved teaching text", "detail": secret,
            "cookies": {"session": "PRIVATE_COOKIE"}, "nested": {"authorization": "PRIVATE_AUTH"},
            "echoed_key": "sk-1234567890abcdefghijkl"}})
    paths = list((tmp_path / "private-audits").rglob("*.json"))
    assert len(paths) == 1
    content = paths[0].read_text()
    assert all(value not in content for value in (secret, "PRIVATE_COOKIE", "PRIVATE_AUTH", "sk-1234567890abcdefghijkl"))
    assert json.loads(content)["response"]["candidate"] == "preserved teaching text"
    assert stat.S_IMODE(paths[0].stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "private-audits").stat().st_mode) == 0o700
    assert stat.S_IMODE(paths[0].parent.stat().st_mode) == 0o700


def test_api_failure_records_feedback_but_never_exposes_it_in_response(tmp_path, monkeypatch):
    from test_teaching_publication import publication_environment
    env = publication_environment(tmp_path, monkeypatch)
    captured = []
    monkeypatch.setattr(env.module, "teaching_audit", captured.append)
    def rejected(*args, **kwargs):
        raise TeachingContentError("审核未通过", {"candidate": "PRIVATE_CANDIDATE", "reason": "PRIVATE_REVIEW"})
    monkeypatch.setattr(env.generator, "generate", rejected)
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 503 and "PRIVATE" not in response.text
    assert captured[0]["event"] == "publication_failed"
    assert captured[0]["review_feedback"]["reason"] == "PRIVATE_REVIEW"
    assert captured[0]["session_hash"] != "session"
    assert env.repo.get_latest_course("session", "morgan") is None
