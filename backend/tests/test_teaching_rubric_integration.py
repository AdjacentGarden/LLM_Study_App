"""Offline orchestration tests: rubric checks add vetoes, never approvals."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest
from test_teaching_content import (
    Client,
    course,
    lesson_candidate,
    point_candidate,
    positive_rubric_alignment_response,
)

from adaptive_learning.llm.client import LLMError
from adaptive_learning.personalization import teaching_content as module
from adaptive_learning.personalization.rubric_alignment import (
    RUBRIC_ALIGNMENT_PROMPT,
    make_rubric_alignment_batch,
    parse_rubric_alignment_response,
)


def batch():
    return make_rubric_alignment_batch(
        [
            {
                "item_id": "practice_1",
                "prompt": "温度改变后能直接沿用原判断吗？请说明理由。",
                "rubric": ["判断不能直接沿用", "解释原结论依赖温度不变"],
                "expected_answer": "PRIVATE_REFERENCE_MUST_NOT_ENTER_THE_REQUEST",
            }
        ]
    )


def response(b, *, rejected=False):
    result = positive_rubric_alignment_response(b.to_payload())
    if rejected:
        row = result["items"][0]["rubric_checks"][0]
        row.update(
            required=False,
            reason="该附加要求不是题干的必答部分，省略仍能完整回答明确问项。",
            counterexample_answer="原结论的温度不变条件已被破坏，因此不能直接沿用，应重新测量。",
            repair="删除这项额外评分要求，保留题干实际要求的判断与理由。",
        )
    return result


class RequestClient:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def structured(self, **kwargs):
        self.calls.append(deepcopy(kwargs))
        output = self.outputs.pop(0)
        if isinstance(output, Exception):
            raise output
        return deepcopy(output)


def test_production_rubric_hook_uses_independent_client_and_exact_source_free_wire():
    b = batch()
    writer, reviewer = RequestClient([]), RequestClient([response(b)])
    generator = module.TeachingContentGenerator(writer, review_client=reviewer)
    assert generator._request_rubric_review(b).accepted
    assert writer.calls == []
    assert len(reviewer.calls) == 1
    call = reviewer.calls[0]
    payload = json.loads(call["user"])
    assert call["system"] == RUBRIC_ALIGNMENT_PROMPT
    assert call["max_tokens"] == 6000
    assert payload == {**b.to_payload(), "format_feedback": []}
    assert "PRIVATE_REFERENCE" not in call["user"]
    assert "sources" not in payload and "expected_answer" not in payload["items"][0]


def test_malformed_alignment_retries_only_same_batch_without_generation():
    b = batch()
    malformed = response(b)
    malformed["items"][0]["rubric_checks"].pop()
    writer, reviewer = RequestClient([]), RequestClient([malformed, response(b)])
    generator = module.TeachingContentGenerator(writer, review_client=reviewer)
    assert generator._request_rubric_review(b).accepted
    assert writer.calls == [] and len(reviewer.calls) == 2
    first, second = [json.loads(call["user"]) for call in reviewer.calls]
    assert first["items"] == second["items"]
    assert first["output_contract"] == second["output_contract"]
    assert second["format_feedback"] == ["rubric checks must cover every exact index once"]


def test_malformed_alignment_exhaustion_is_service_error_without_content_approval():
    b = batch()
    reviewer = RequestClient([{"items": []}, {"items": []}])
    generator = module.TeachingContentGenerator(RequestClient([]), review_client=reviewer)
    with pytest.raises(module.TeachingServiceUnavailableError) as caught:
        generator._request_rubric_review(b)
    assert caught.value.review_feedback["stage"] == "rubric_alignment_format"
    assert len(reviewer.calls) == 2


@pytest.mark.parametrize("error", [LLMError("upstream timeout"), LLMError("HTTP 429")])
def test_provider_error_does_not_masquerade_as_content_failure_or_retry(error):
    b = batch()
    reviewer = RequestClient([error])
    generator = module.TeachingContentGenerator(RequestClient([]), review_client=reviewer)
    with pytest.raises(module.TeachingServiceUnavailableError):
        generator._request_rubric_review(b)
    assert len(reviewer.calls) == 1


def point_models():
    bundle, chapter, evidence = course(2)
    inputs = module.TeachingContentGenerator._inputs(bundle, evidence)
    points = [module._Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = module._Lesson.model_validate(lesson_candidate())
    teacher = module._Review(
        accepted=True,
        issues=[],
        points=[
            module._PointReview(
                point_id=point.point_id,
                explanation_ok=True,
                flashcard_ok=True,
                practice_ok=True,
                evidence_ok=True,
                issues=[],
            )
            for point in points
        ],
        lesson=module._LessonReview(
            reading_ok=True, examples_ok=True, checkpoints_ok=True, evidence_ok=True, issues=[]
        ),
    )
    b = make_rubric_alignment_batch(
        [
            {
                key: value
                for key, value in point.practice.model_dump().items()
                if key != "source_ids"
            }
            for point in points
        ]
    )
    return points, lesson, teacher, b


def test_alignment_veto_changes_only_practice_and_preserves_original_teacher_issues():
    points, _, teacher, b = point_models()
    teacher.accepted = False
    teacher.issues = ["original global veto"]
    teacher.points[1].flashcard_ok = False
    teacher.points[1].evidence_ok = False
    teacher.points[1].issues = ["original unsupported flashcard"]
    raw = response(b, rejected=True)
    result = parse_rubric_alignment_response(raw, b)
    generator = module.TeachingContentGenerator(RequestClient([]))
    merged = generator._merge_rubric_reviews(teacher, [result], points)
    assert not merged.accepted and merged.issues == teacher.issues
    first, second = merged.points
    assert not first.practice_ok
    assert first.explanation_ok and first.flashcard_ok and first.evidence_ok
    assert first.issues[0].startswith(f"practice_{points[0].practice.item_id} ")
    assert raw["items"][0]["rubric_checks"][0]["counterexample_answer"] in first.issues[0]
    assert second.model_dump() == teacher.points[1].model_dump()
    assert teacher.points[0].practice_ok and teacher.points[0].issues == []
    assert merged._rubric_alignment["items"] == result.to_dict()["items"]


def test_alignment_success_cannot_override_teacher_rejection():
    points, _, teacher, b = point_models()
    teacher.accepted = False
    teacher.points[0].practice_ok = False
    teacher.points[0].issues = ["teacher rejected an answer"]
    result = parse_rubric_alignment_response(response(b), b)
    merged = module.TeachingContentGenerator(RequestClient([]))._merge_rubric_reviews(
        teacher, [result], points
    )
    assert not merged.accepted
    assert merged.model_dump() == teacher.model_dump()
    assert merged._rubric_alignment["accepted"]


@pytest.mark.parametrize("mode", ["missing", "duplicate", "unknown"])
def test_aggregate_alignment_cannot_omit_duplicate_or_substitute_an_item(mode):
    points, _, teacher, b = point_models()
    result = parse_rubric_alignment_response(response(b), b)
    if mode == "missing":
        points = points + [
            points[0].model_copy(
                update={"practice": points[0].practice.model_copy(update={"item_id": "missing"})}
            )
        ]
    elif mode == "duplicate":
        result = type(result)((result.items[0], result.items[0]))
    else:
        item = result.items[0]
        result = type(result)(
            (
                type(item)("unknown", item.minimal_sufficient_answer, item.rubric_checks),
                result.items[1],
            )
        )
    with pytest.raises(module.TeachingServiceUnavailableError, match="完整且唯一"):
        module.TeachingContentGenerator(RequestClient([]))._merge_rubric_reviews(
            teacher, [result], points
        )


class AlignmentRepairClient:
    def __init__(self, failed_item_id):
        self.base = Client()
        self.failed_item_id = failed_item_id
        self.rejected_item_ids = []

    def __getattr__(self, name):
        return getattr(self.base, name)

    def structured(self, *, system, user, **kwargs):
        payload = json.loads(user)
        if system == RUBRIC_ALIGNMENT_PROMPT:
            self.rubric_calls += 1
            self.payloads.append(payload)
            self.systems.append(system)
            output = positive_rubric_alignment_response(payload)
            for item in output["items"]:
                if (
                    item["item_id"] == self.failed_item_id
                    and "公平性已修复" not in item["rubric_checks"][0]["rubric_text"]
                ):
                    self.rejected_item_ids.append(item["item_id"])
                    item["rubric_checks"][0].update(
                        required=False,
                        reason="该评分项包含题干未要求的额外目标，应只考本题所问的判断与理由。",
                        counterexample_answer="温度改变后原判断的前提不成立，应重新测量，不能直接沿用。",
                        repair="删除未问的附加要求，保持原判断与理由两个明确目标。",
                    )
            return output
        output = self.base.structured(system=system, user=user, **kwargs)
        if system == module.GENERATION_PROMPT and payload.get("previous_candidate"):
            for point in output["points"]:
                point["practice"]["rubric"][0] += "，公平性已修复"
        return output


def test_new_rubric_gate_repairs_only_failed_practice_and_persists_independent_evidence():
    bundle, chapter, evidence = course(2)
    client = AlignmentRepairClient(bundle.practice_items[0].item_id)
    audits = []
    result = module.TeachingContentGenerator(client, audit_callback=audits.append).generate(
        bundle, chapter, evidence
    )
    assert client.review_calls == 2
    assert client.rubric_calls == 2
    assert client.rejected_item_ids == [bundle.practice_items[0].item_id]
    generations = [
        payload
        for system, payload in zip(client.systems, client.payloads, strict=True)
        if system == module.GENERATION_PROMPT
    ]
    assert len(generations[-1]["batch_points"]) == 1
    assert list(generations[-1]["allowed_repairs"].values()) == [["practice"]]
    candidates = [event["candidate"] for event in audits if event["event"] == "candidate"]
    before = {point["point_id"]: point for point in candidates[0]["points"]}
    after = {point["point_id"]: point for point in candidates[1]["points"]}
    repaired = generations[-1]["batch_points"][0]["point_id"]
    assert repaired == bundle.practice_items[0].point_id
    for point_id in before:
        if point_id == repaired:
            assert before[point_id]["explanation"] == after[point_id]["explanation"]
            assert before[point_id]["flashcard"] == after[point_id]["flashcard"]
        else:
            assert before[point_id] == after[point_id]
    review = result.teaching_review["rubric_alignment"]
    assert review["accepted"] and len(review["items"]) == 2
    assert len([event for event in audits if event["event"] == "rubric_review"]) == 2
    requests = [
        event
        for event in audits
        if event["event"] == "model_request" and event.get("review_stage") == "rubric"
    ]
    assert len(requests) == 2 and {event["attempt"] for event in requests} == {1, 2}
    assert all(event["candidate_sha256"] for event in requests)
    assert all("expected_answer" not in event["request"]["items"][0] for event in requests)
