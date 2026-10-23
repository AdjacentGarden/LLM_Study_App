"""Regression for the captured 93k lesson request and batch-local JSON failures."""
import json
from collections import Counter
from copy import deepcopy

import pytest
from test_teaching_content import Client, course

from adaptive_learning.llm.client import LLMError
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_content import (
    FACT_REVIEW_PROMPT,
    REVIEW_PROMPT,
    TeachingContentGenerator,
    TeachingServiceUnavailableError,
)


def malformed_json():
    error = LLMError("structured model call failed: JSONDecodeError")
    error.__cause__ = json.JSONDecodeError("Extra data", '{}{}', 2)
    return error


@pytest.mark.parametrize("stage", ["point_fact", "lesson_fact", "teacher"])
def test_json_object_failure_retries_only_current_audit_and_preserves_teaching(stage):
    bundle, chapter, evidence = course(4)

    class Once(Client):
        failed = False
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            target = (stage == "point_fact" and system == FACT_REVIEW_PROMPT
                and bundle.knowledge_points[0].point_id in data["output_contract"]["point_ids"]
                or stage == "lesson_fact" and system == FACT_REVIEW_PROMPT and "example_1" in data["lesson_components"]
                or stage == "teacher" and system == REVIEW_PROMPT)
            if target and not self.failed:
                self.failed = True
                raise malformed_json()
            return result

    client = Once()
    result = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    assert result.teaching_review["accepted"] is True
    assert len([batch for batch in client.generation_batches if batch]) == 1
    assert len(client.generation_batches) == 2  # point writer, then lesson-only writer
    requests = [(system, payload) for system, payload in zip(client.systems, client.payloads, strict=True)
                if system in {FACT_REVIEW_PROMPT, REVIEW_PROMPT}]
    repairs = [(system, payload) for system, payload in requests if payload["format_feedback"]]
    assert len(repairs) == 1
    system, repaired = repairs[0]
    originals = [payload for original_system, payload in requests
        if original_system == system and not payload["format_feedback"]
        and payload.get("output_contract") == repaired.get("output_contract")]
    assert len(originals) == 1
    original = deepcopy(originals[0])
    repaired = deepcopy(repaired)
    original.pop("format_feedback")
    repaired.pop("format_feedback")
    assert original == repaired
    counts = Counter((system, tuple(payload.get("output_contract", {}).get("point_ids", [])),
        tuple(payload.get("output_contract", {}).get("lesson_component_ids", [])))
        for system, payload in requests)
    repaired_key = (system, tuple(repaired.get("output_contract", {}).get("point_ids", [])),
        tuple(repaired.get("output_contract", {}).get("lesson_component_ids", [])))
    assert counts[repaired_key] == 2
    assert all(count == (2 if key == repaired_key else 1) for key, count in counts.items())


@pytest.mark.parametrize("error", [LLMError("authentication failed"), LLMError("upstream unavailable")])
def test_transport_failure_does_not_multiply_calls_or_become_content_rejection(error):
    bundle, chapter, evidence = course(1)
    class Unavailable(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and json.loads(user)["candidate"]["points"]:
                raise error
            return result
    client = Unavailable()
    with pytest.raises(TeachingServiceUnavailableError):
        TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    point_requests = [payload for system, payload in zip(client.systems, client.payloads, strict=True)
        if system == FACT_REVIEW_PROMPT and payload["candidate"]["points"]]
    assert len(point_requests) == 1 and not point_requests[0]["format_feedback"]
    assert len([batch for batch in client.generation_batches if batch]) == 1
    assert len(client.generation_batches) == 2


def test_exhausted_lesson_json_failure_keeps_exact_batch_and_cannot_publish():
    bundle, chapter, evidence = course(1)
    before = bundle.model_dump_json()
    class Broken(Client):
        def structured(self, *, system, user, **kwargs):
            data = json.loads(user)
            result = super().structured(system=system, user=user, **kwargs)
            if system == FACT_REVIEW_PROMPT and "example_1" in data["lesson_components"]:
                raise malformed_json()
            return result
    client = Broken()
    events = []
    with pytest.raises(TeachingServiceUnavailableError) as caught:
        TeachingContentGenerator(client, audit_callback=events.append).generate(bundle, chapter, evidence)
    assert caught.value.review_feedback["lesson_component_ids"] == ["example_1"]
    assert client.review_calls <= 1 and len(client.generation_batches) == 2
    failed_batches = [data for system, data in zip(client.systems, client.payloads, strict=True)
        if system == FACT_REVIEW_PROMPT and "example_1" in data["lesson_components"]]
    assert len(failed_batches) == 2
    assert all(set(data["lesson_components"]) == {"example_1"} for data in failed_batches)
    assert not failed_batches[0]["format_feedback"] and failed_batches[1]["format_feedback"]
    originals = [deepcopy(data) for data in failed_batches]
    for data in originals:
        data.pop("format_feedback")
    assert originals[0] == originals[1]
    assert bundle.model_dump_json() == before
    assert not any(event["event"] == "accepted" for event in events)
    assert events[-1]["event"] == "failed"


def test_large_reading_directory_is_not_repeated_in_same_lesson_batch():
    bundle, chapter, evidence = course(13)
    # A chapter-sized immutable directory recreates the real request's pressure,
    # without a model call, book-specific rules or secret configuration.
    for index in range(20):
        key = f"context_{index}"
        evidence.sources[key] = SourceCitation(page_number=index + 2,
            quote=(f"容器{index}，温度不变时保持稳定；温度改变后重新测量。" * 18)[:490])
        evidence.point_sources[bundle.knowledge_points[index % 13].point_id].append(key)
    class BroadReading(Client):
        def structured(self, *, system, user, **kwargs):
            result = super().structured(system=system, user=user, **kwargs)
            if system not in {FACT_REVIEW_PROMPT, REVIEW_PROMPT} and result.get("lesson"):
                result["lesson"]["reading_source_ids"] = list(evidence.sources)
            return result
    client = BroadReading()
    result = TeachingContentGenerator(client).generate(bundle, chapter, evidence)
    requests = [data for system, data in zip(client.systems, client.payloads, strict=True) if system == FACT_REVIEW_PROMPT]
    lesson_batches = [data for data in requests if data["candidate"]["lesson"]]
    assert all(not {"opening", "summary"} <= set(data["lesson_components"]) for data in lesson_batches)
    assert max(len(json.dumps(data, ensure_ascii=False)) for data in lesson_batches) < 93716
    assert all(len(data["lesson_components"]) == 1 for data in lesson_batches)
    assert all("worked_examples" not in data["candidate"]["lesson"] for data in lesson_batches)
    covered = [key for data in lesson_batches for key in data["lesson_components"]]
    assert len(covered) == len(set(covered)) == 6
    assert set(result.teaching_fact_review["lesson"]["components"]) == set(covered)


def test_api_service_failure_message_is_distinct_from_review_rejection(tmp_path, monkeypatch):
    from test_teaching_publication import publication_environment
    env = publication_environment(tmp_path, monkeypatch)
    def unavailable(*args, **kwargs):
        raise TeachingServiceUnavailableError("PRIVATE_ERROR")
    monkeypatch.setattr(env.generator, "generate", unavailable)
    response = env.client.post("/api/interviews/session/courses/morgan")
    assert response.status_code == 503
    assert "服务暂时不可用" in response.json()["detail"]
    assert "未通过完整性与原文审核" not in response.text and "PRIVATE" not in response.text


def test_lesson_feedback_is_scoped_to_the_exact_component_batch():
    rows = [{"factual_lesson": {"accepted": False, "issues": [
        "lesson.example_1: 缺少该例题真正支持引用", "lesson.summary: 总结的成立范围不完整"],
        "components": {
            "opening": {"source_entailment": True, "scope_exceptions": True},
            "summary": {"source_entailment": True, "scope_exceptions": False},
            "example_1": {"source_entailment": False, "scope_exceptions": True}}}}]
    first = TeachingContentGenerator._fact_batch_feedback(rows, set(), {"opening"})
    assert first == []
    example = TeachingContentGenerator._fact_batch_feedback(rows, set(), {"example_1", "example_2"})
    assert set(example[0]["factual_lesson"]["components"]) == {"example_1"}
    assert example[0]["factual_lesson"]["issues"] == ["lesson.example_1: 缺少该例题真正支持引用"]
    assert "summary" not in json.dumps(example, ensure_ascii=False)
