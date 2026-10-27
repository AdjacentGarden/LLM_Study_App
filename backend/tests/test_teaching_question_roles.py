"""Regress the real 2026-10-24 misconception-example rejection and repair loop.

These offline controls validate payload/proof mechanics and draft routing.
Authored semantic decisions are explicit; no real model quality is claimed.
"""

import json
from collections import Counter
from copy import deepcopy

import pytest
from test_teaching_content import course, lesson_candidate, point_candidate
from test_teaching_v15_integration import FIXED, CandidateClient, Planner

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.lesson_review_contract import build_assertion_contract
from adaptive_learning.personalization.teaching_fact_wire import (
    compact_fact_payload,
    restore_fact_payload,
)

# The actual failed example quoted a misconception and then corrected it.
REAL_PROBLEM = (
    "某同学说：“减数分裂是细胞连续分裂两次、染色体复制两次的分裂，所有生物都能进行，"
    "染色体数目减半发生在减数第二次分裂。”请逐条判断并改正。"
)
REAL_ANSWER = (
    "三处均需改正：减数分裂只适用于进行有性生殖的生物产生成熟生殖细胞时；"
    "染色体只复制一次而细胞分裂两次；染色体数目减半发生在减数第一次分裂。"
)


def lesson_payload(fields):
    # A bounded source control, not a claim that the source model approved it.
    raw = "进行有性生殖的生物产生成熟生殖细胞时，染色体只复制一次，而细胞分裂两次；染色体数目减半发生在减数第一次分裂。"
    sources = {"s1": {"page_number": 11, "quote": raw}}
    spans = {"span1": {"source_id": "s1", "quote": raw}}
    assertions = build_assertion_contract("example_1", fields, ["s1"], spans, sources)
    part = {"source_ids": ["s1"], "component_fields": fields,
            "assertion_contract": assertions, "scope_boundaries": []}
    return {"sources": sources, "lesson_components": {"example_1": part}}, spans


def authored_review(payload, *, bad_field=None):
    """Explicit semantic control: never infer truth from question punctuation."""
    part = payload["lesson_components"]["example_1"]
    rows = []
    for assertion in part["assertion_contract"]["assertions"]:
        bad = assertion["field"] == bad_field
        # This positive control explicitly classifies a corrected quotation as
        # a question object. A negative control marks its actual false premise.
        factual = assertion["field"] != "problem" or bad
        rows.append({
            "assertion_id": assertion["assertion_id"], "is_factual": factual,
            "source_entailment": not bad,
            "support_span_ids": ["span1"] if factual and not bad else [],
            "findings": [{"kind": "contradiction", "quote": assertion["text"],
                          "issue": "该教师结论或未纠正的真实前提与引用原文相反。",
                          "evidence_source_ids": ["s1"]}] if bad else [],
        })
    return {"point_id": None, "component_id": "example_1", "assertion_checks": rows,
            "scope_matrix": [{"assertion_id": row["assertion_id"], "checks": []} for row in rows]}


def test_real_corrected_misconception_keeps_all_fields_and_independent_assertion_proofs():
    fields = {"problem": REAL_PROBLEM, "answer": REAL_ANSWER,
              "step_1": "染色体只复制一次，而细胞分裂两次。"}
    payload, spans = lesson_payload(fields)
    contract = payload["lesson_components"]["example_1"]["assertion_contract"]
    roles = contract["component_roles"]
    assert roles["question_fields"] == ["problem"]
    assert set(roles["teacher_response_fields"]) == {"answer", "step_1"}
    assert not roles["semantic_classification_verified"]
    assert not roles["question_fields_automatically_nonfactual"]
    assert restore_fact_payload(compact_fact_payload(payload)) == payload
    for name, text in fields.items():
        assert "".join(a["text"] for a in contract["assertions"] if a["field"] == name) == text
    review = authored_review(payload)
    teaching.TeachingContentGenerator._normalize_lesson_selection(review, payload, evidence(), spans)
    assert review["source_entailment"] and review["scope_exceptions"]
    assert not review["findings"]
    assert review["assertions"] == contract["assertions"]


def evidence():
    from adaptive_learning.personalization.models import SourceCitation
    from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
    payload, _ = lesson_payload({})
    return TeachingEvidence(sources={key: SourceCitation.model_validate(value)
                                    for key, value in payload["sources"].items()},
                            point_sources={}, warnings=[], fingerprint="role-control")


@pytest.mark.parametrize("mode", ["wrong_answer", "uncorrected_quote", "real_question_premise"])
def test_question_role_never_erases_an_actual_negative_semantic_verdict(mode):
    if mode == "wrong_answer":
        fields = {"problem": REAL_PROBLEM, "answer": "原说法正确，染色体复制两次。"}
        bad_field = "answer"
    elif mode == "uncorrected_quote":
        fields = {"problem": REAL_PROBLEM, "answer": "请继续思考这个说法。"}
        bad_field = "problem"
    else:
        fields = {"problem": "减数分裂的染色体复制两次，请说明这个过程的意义。", "answer": REAL_ANSWER}
        bad_field = "problem"
    payload, spans = lesson_payload(fields)
    review = authored_review(payload, bad_field=bad_field)
    teaching.TeachingContentGenerator._normalize_lesson_selection(review, payload, evidence(), spans)
    assert not review["source_entailment"]
    assert any(f["candidate_field"] == bad_field for f in review["findings"])


def test_semantic_roles_reach_point_practice_and_lesson_without_dropping_context():
    bundle, chapter, sources = course(1)
    generator = teaching.TeachingContentGenerator(object(), scope_planner=Planner())
    inputs = generator._inputs(bundle, sources)
    for item in inputs:
        item["scope_conditions"] = []
    points = [teaching._Point.model_validate(point_candidate(item)) for item in inputs]
    lesson = teaching._Lesson.model_validate(lesson_candidate())
    jobs = generator._build_fact_jobs(points, lesson, inputs, chapter, sources, [])
    point_job = next(job for job in jobs if job["payload"]["candidate"]["points"])
    context = point_job["payload"]["candidate"]["points"][0]["component_evidence"]["practice"]
    assert context["component_roles"]["question_fields"] == ["prompt"]
    assert "expected_answer" in context["component_roles"]["teacher_response_fields"]
    assert context["component_fields"]["expected_answer"] == points[0].practice.expected_answer
    lesson_job = next(job for job in jobs if "example_1" in job["payload"]["lesson_components"])
    example = lesson_job["payload"]["lesson_components"]["example_1"]
    assert example["component_roles"]["question_fields"] == ["problem"]
    assert example["component_fields"]["answer"] == lesson.worked_examples[0].answer


class LessonRetryClient(CandidateClient):
    def __init__(self, point_ids, *, veto_final=False, unchanged_point=False):
        super().__init__(fact_failures={(key, "practice") for key in point_ids}, lesson_fact={"example_1"})
        self.lesson_writes = 0
        self.teacher_payloads = []
        self.veto_final = veto_final
        self.unchanged_point = unchanged_point
        self.target_point = point_ids[0]

    def structured(self, *, system, user, **kwargs):
        if system != teaching.GENERATION_PROMPT:
            return super().structured(system=system, user=user, **kwargs)
        data = json.loads(user)
        self.generations.append(deepcopy(data))
        points = [point_candidate(row) for row in data["batch_points"]]
        if data["previous_candidate"] is not None and not self.unchanged_point:
            for point in points:
                point["practice"]["expected_answer"] += FIXED
        lesson = None
        if data["include_lesson"]:
            self.lesson_writes += 1
            lesson = lesson_candidate()
            # Reproduce the real failed repair: all point batches changed, but
            # the first lesson repair left example_1 byte-for-byte unchanged.
            if self.lesson_writes >= 3:
                lesson["worked_examples"][0]["steps"][0] += FIXED
        return {"points": points, "lesson": lesson}

    def teacher(self, data):
        self.teacher_payloads.append(deepcopy(data))
        result = super().teacher(data)
        if FIXED not in data["candidate"]["lesson"]["worked_examples"][0]["steps"][0]:
            result["accepted"] = False
            result["lesson"]["examples_ok"] = False
            result["lesson"]["issues"] = ["lesson.worked_examples[0]：该例题必须修正。"]
        elif self.veto_final == "teacher":
            # Mechanically retained drafts still require a fresh semantic
            # review; this veto must prevent publication.
            result["accepted"] = False
            result["points"][0]["practice_ok"] = False
            result["points"][0]["issues"] = ["practice：新的答案仍存在语义错误。"]
        return result

    def fact(self, data):
        result = super().fact(data)
        if self.veto_final == "fact":
            for point in data["candidate"]["points"]:
                if point["point_id"] != self.target_point or FIXED not in point["practice"]["expected_answer"]:
                    continue
                row = next(row for row in result["components"]
                           if row["point_id"] == self.target_point and row["component_id"] == "practice")
                row["source_entailment"] = False
                row["findings"] = [{"kind": "invalid_inference", "candidate_field": "expected_answer",
                                    "candidate_quote": point["practice"]["expected_answer"][:40],
                                    "issue": "修订后的练习答案仍有无原文依据的推理，不能发布。",
                                    "evidence_source_ids": ["s1"]}]
        return result


@pytest.mark.parametrize("veto_final", [False, "teacher", "fact"])
def test_unchanged_lesson_retry_keeps_repaired_drafts_but_requires_both_reviews(veto_final):
    bundle, chapter, sources = course(8)
    client = LessonRetryClient([p.point_id for p in bundle.knowledge_points], veto_final=veto_final)
    events = []
    generator = teaching.TeachingContentGenerator(client, scope_planner=Planner(), audit_callback=events.append)
    if veto_final:
        with pytest.raises(teaching.TeachingContentError):
            generator.generate(bundle, chapter, sources)
        assert not any(event["event"] == "accepted" for event in events)
    else:
        result = generator.generate(bundle, chapter, sources)
        assert result.teaching_review["accepted"] and result.teaching_fact_review["accepted"]
    assert client.lesson_writes == 3
    sizes = [len(row["batch_points"]) for row in client.generations]
    assert Counter(sizes[:2]) == Counter(sizes[3:5]) == {6: 1, 2: 1}
    assert sizes[2] == 0 and sizes[5:] == [0, 0]
    assert len(client.teacher_payloads) == 2  # No review of the incomplete repair.
    last = client.teacher_payloads[-1]["candidate"]
    assert all(FIXED in point["practice"]["expected_answer"] for point in last["points"])
    assert FIXED in last["lesson"]["worked_examples"][0]["steps"][0]
    fact_practices = [point for call in client.calls if call[0][0] == "fact"
                      for point in call[1]["candidate"]["points"]
                      if FIXED in point["practice"]["expected_answer"]]
    assert {point["point_id"] for point in fact_practices} == {point.point_id for point in bundle.knowledge_points}


def test_an_unchanged_rejected_point_is_not_reused_by_the_lesson_retry_path():
    bundle, chapter, sources = course(8)
    client = LessonRetryClient([p.point_id for p in bundle.knowledge_points], unchanged_point=True)
    events = []
    with pytest.raises(teaching.TeachingContentError):
        teaching.TeachingContentGenerator(client, scope_planner=Planner(), audit_callback=events.append).generate(
            bundle, chapter, sources)
    assert client.lesson_writes == 1
    assert sum(bool(row["batch_points"]) for row in client.generations) > 2
    assert not any(event["event"] == "accepted" for event in events)
