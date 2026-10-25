"""An observed unplanned condition must remain a real, scoped veto.

The bb fixture records a *failed unpublished* candidate and its incorrect
positive component verdicts. Replays exercise transport/validators/cache, not
LLM compliance or scientific approval. No model or server is contacted.
"""

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from test_teaching_content import advertised_fact_response, lesson_candidate
from test_teaching_v15_integration import CandidateClient

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import (
    _SCOPE_MARKERS,
    CANDIDATE_CONTEXT_VERSION,
    TeachingEvidence,
    candidate_context_source_ids,
    scope_context_source_ids,
)
from adaptive_learning.personalization.teaching_fact_wire import (
    compact_fact_payload,
    restore_fact_payload,
)
from adaptive_learning.personalization.teaching_review_reuse import VerifiedComponentReuse

OBSERVED = json.loads((Path(__file__).parent / "fixtures/teaching_candidate_context_bb.json").read_text())
CONDITION = OBSERVED["condition_source_id"]


def actual_fixture(client=None):
    point = teaching._Point.model_validate(OBSERVED["point"])
    anchor = point.source_ids[0]
    evidence = TeachingEvidence(
        {key: SourceCitation.model_validate(value) for key, value in OBSERVED["sources"].items()},
        {point.point_id: [anchor]}, [], "actual-bb-bounded-sources",
    )
    generator = teaching.TeachingContentGenerator(client or object())
    bundle = SimpleNamespace(
        knowledge_points=[SimpleNamespace(point_id=point.point_id, explanation=OBSERVED["label"])],
        flashcards=[SimpleNamespace(point_id=point.point_id, card_id=point.flashcard.card_id)],
        practice_items=[SimpleNamespace(point_id=point.point_id, item_id=point.practice.item_id)],
    )
    inputs = generator._inputs(bundle, evidence)
    inputs[0]["scope_conditions"] = deepcopy(OBSERVED["scope_conditions"])
    chapter = SimpleNamespace(chapter_id="actual-bb-chapter", title="实际未发布课程范围回归")
    job = generator._build_fact_jobs([point], None, inputs, chapter, evidence, [])[0]
    return generator, point, evidence, inputs, chapter, job


def negative_finding(component):
    return {
        "kind": "missing_condition", "candidate_field": "explanation" if component == "explanation" else "back",
        "candidate_quote": OBSERVED["claim_quotes"][component],
        "issue": "当前组件将有限定的原文关系写成无条件命题，遗漏了同章真实适用条件。",
        "evidence_source_ids": [CONDITION], "missing_text": OBSERVED["missing_text"],
    }


class RecordedPointReviewer:
    def __init__(self, *, negative=True, fault=None):
        self.negative = negative
        self.fault = fault
        self.calls = []

    def structured(self, *, system, user, **kwargs):
        assert system == teaching.FACT_REVIEW_PROMPT
        payload = json.loads(user)
        self.calls.append(payload)
        result = deepcopy(OBSERVED["observed_response"])
        wanted = payload["output_contract"].get("component_ids_by_point", {}).get(
            OBSERVED["point"]["point_id"], ["explanation", "flashcard", "practice"]
        )
        result["components"] = [row for row in result["components"] if row["component_id"] in wanted]
        if self.negative:
            for row in result["components"]:
                if row["component_id"] not in OBSERVED["claim_quotes"]:
                    continue
                # An unplanned condition has no invented matrix column. Its
                # actual assertion remains a source-negative semantic veto.
                row["source_entailment"] = False
                row["findings"] = [negative_finding(row["component_id"])]
                if self.fault == "invented_source_phrase":
                    row["findings"][0]["missing_text"] = "不存在的限定原句"
                elif self.fault == "foreign_candidate_quote":
                    row["findings"][0]["candidate_quote"] = "其他组件中的一句话"
                elif self.fault == "unknown_boundary":
                    row["scope_checks"][0]["boundary_id"] = "not_a_planned_boundary"
        return advertised_fact_response(result, payload)


@pytest.mark.parametrize("component", ["explanation", "flashcard"])
def test_actual_marker_free_condition_is_recalled_for_actual_claims_and_before_planning(component):
    generator, point, evidence, inputs, _, job = actual_fixture()
    before = {key: value.model_dump() for key, value in evidence.sources.items()}
    assert not _SCOPE_MARKERS.search(evidence.sources[CONDITION].quote)
    assert CONDITION not in scope_context_source_ids(evidence, OBSERVED["label"], point.source_ids[:1])
    assert CONDITION in inputs[0]["scope_context_source_ids"]  # Before the existing planner call.
    part = job["payload"]["candidate"]["points"][0]["component_evidence"][component]
    assert part["candidate_context_version"] == CANDIDATE_CONTEXT_VERSION
    assert CONDITION in {row["source_id"] for row in part["candidate_context_sources"]}
    assert len(part["candidate_context_sources"]) <= 2
    assert len({evidence.sources[row["source_id"]].page_number for row in part["candidate_context_sources"]}) == len(part["candidate_context_sources"])
    assert before == {key: value.model_dump() for key, value in evidence.sources.items()}
    assert evidence.fingerprint == "actual-bb-bounded-sources"
    assert not any(CONDITION in row["source_ids"] for row in inputs[0]["scope_conditions"])
    assert not any(job["spans"][key]["source_id"] == CONDITION for key in part["allowed_support_span_ids"])
    assert CONDITION not in [row["source_id"] for row in part["actual_source_text"]]
    assert generator._candidate_navigation_identity(part)["source_ids"] == [row["source_id"] for row in part["candidate_context_sources"]]


def test_candidate_context_wire_is_lossless_reference_not_new_citation_or_boundary():
    _, point, evidence, inputs, _, job = actual_fixture()
    before = deepcopy(job["payload"])
    wire = compact_fact_payload(job["payload"])
    assert restore_fact_payload(wire) == before == job["payload"]
    part = wire["candidate"]["points"][0]["component_evidence"]["explanation"]
    context = next(row for row in part["candidate_context_sources"] if row["source_id"] == CONDITION)
    assert context["quote"] == {"$source_quote_ref": CONDITION}
    assert wire["sources"][CONDITION]["quote"] == evidence.sources[CONDITION].quote
    assert CONDITION not in point.source_ids + point.flashcard.source_ids
    assert len(wire["candidate"]["points"][0]["scope_boundaries"]) == len(inputs[0]["scope_conditions"]) == 3


def test_unmarked_structural_condition_in_another_domain_is_navigation_not_an_automatic_veto():
    sources = {
        "anchor": SourceCitation(page_number=1, quote="请求按照入库时间依次处理。"),
        "structure": SourceCitation(page_number=2, quote="已登记的普通请求按照入库时间依次处理。紧急请求进入单独通道。"),
        "unrelated": SourceCitation(page_number=3, quote="但是海水在特定条件下凝固，实验记录应归档。"),
    }
    evidence = TeachingEvidence(sources, {"point": ["anchor"]}, [], "unmodified")
    fields = {"back": sources["anchor"].quote}
    assert not _SCOPE_MARKERS.search(sources["structure"].quote)
    assert candidate_context_source_ids(evidence, fields.values(), ["anchor"]) == ["structure"]
    assert candidate_context_source_ids(evidence, fields.values(), ["anchor"], limit=0) == []
    finding = {"kind": "missing_condition", "candidate_field": "back", "candidate_quote": fields["back"],
        "issue": "当前命题没有保留原文对象类别的限定，需要回到同主题原文比较。",
        "evidence_source_ids": ["structure"], "missing_text": "已登记的普通请求"}
    selection = teaching._ComponentFactSelection(source_entailment=True, scope_exceptions=False,
        findings=[finding], scope_checks=[])
    teaching.TeachingContentGenerator._validate_boundary_checks(selection, fields, [])
    teaching.TeachingContentGenerator._validate_component_findings(selection, fields, {"anchor"}, evidence)
    limited = {"back": sources["structure"].quote}
    with pytest.raises(teaching.TeachingContentError, match="限定已存在"):
        teaching.TeachingContentGenerator._validate_component_findings(selection, limited, {"structure"}, evidence)
    positive = teaching._ComponentFactSelection(source_entailment=True, scope_exceptions=True,
        findings=[], scope_checks=[])
    teaching.TeachingContentGenerator._validate_boundary_checks(positive, limited, [])
    teaching.TeachingContentGenerator._validate_component_findings(positive, limited, {"structure"}, evidence)
    assert positive.source_entailment and positive.scope_exceptions


def test_unplanned_actual_condition_is_a_legal_component_veto_without_invented_boundary():
    client = RecordedPointReviewer()
    generator, _, _, _, _, job = actual_fixture(client)
    result = generator._run_fact_job(job)
    assert len(client.calls) == 1 and not result.accepted
    point = result.points[0]
    for component in ("explanation", "flashcard"):
        proof = getattr(point.components, component)
        assert not proof.source_entailment and proof.scope_exceptions
        assert proof.findings[0].model_dump() == {**negative_finding(component)}
        assert all(row.status in {"bounded", "not_applicable"} for row in proof.scope_checks)
    assert {"explanation", "flashcard"} <= set(point.failed_components)


@pytest.mark.parametrize("fault", ["invented_source_phrase", "foreign_candidate_quote", "unknown_boundary"])
def test_unplanned_rejection_cannot_invent_evidence_quotes_or_boundary_ids(fault):
    client = RecordedPointReviewer(fault=fault)
    generator, _, _, _, _, job = actual_fixture(client)
    with pytest.raises(teaching.TeachingServiceUnavailableError):
        generator._run_fact_job(job)
    assert len(client.calls) == teaching.FACT_FORMAT_ATTEMPTS


def test_navigation_alone_changes_the_real_reuse_key_while_identical_navigation_still_reuses():
    client = RecordedPointReviewer(negative=False)
    generator, point, evidence, inputs, chapter, current = actual_fixture(client)
    previous = deepcopy(current)
    old_part = previous["payload"]["candidate"]["points"][0]["component_evidence"]["explanation"]
    old_part["candidate_context_sources"] = []
    cache = VerifiedComponentReuse(source_texts={key: value.quote for key, value in evidence.sources.items()},
        context=generator._reuse_context(chapter, evidence, inputs))
    token = teaching._FACT_REUSE.set(cache)
    try:
        first = generator._run_fact_job(previous)
        assert first.points[0].components.explanation.scope_exceptions
        generator._run_fact_job(previous)
        # The same navigation reuses exactly the old positive components;
        # the originally rejected practice is independently requested again.
        assert client.calls[-1]["output_contract"]["component_ids_by_point"][point.point_id] == ["practice"]
        client.negative = True
        changed = generator._run_fact_job(current)
        assert client.calls[-1]["output_contract"]["component_ids_by_point"][point.point_id] == ["explanation", "practice"]
        assert not changed.points[0].components.explanation.source_entailment
        assert len(client.calls) == 3
    finally:
        teaching._FACT_REUSE.reset(token)
    before = generator._reuse_context(chapter, evidence, inputs)
    updated = deepcopy(inputs)
    updated[0]["scope_context_source_ids"] = []
    assert before != generator._reuse_context(chapter, evidence, updated)


class LessonReviewer(CandidateClient):
    def __init__(self, *, incorrectly_positive=False):
        super().__init__()
        self.incorrectly_positive = incorrectly_positive

    def fact(self, data):
        result = super().fact(data)
        for component in result["components"]:
            for row in component["assertion_checks"]:
                assertion = next(a for a in data["lesson_components"][component["component_id"]]["assertion_contract"]["assertions"]
                                 if a["assertion_id"] == row["assertion_id"])
                claim = OBSERVED["claim_quotes"]["explanation"]
                if claim not in assertion["text"]:
                    continue
                row["source_entailment"] = self.incorrectly_positive
                row["findings"] = [{"kind": "missing_condition", "quote": claim,
                    "issue": "这条实际断言遗漏了同章原文的对象限定，当前无条件命题不能成立。",
                    "evidence_source_ids": [CONDITION], "missing_text": OBSERVED["missing_text"]}]
        return result


@pytest.mark.parametrize("incorrectly_positive", [False, True])
def test_lesson_can_reject_an_unplanned_condition_without_fabricating_matrix_columns(incorrectly_positive):
    client = LessonReviewer(incorrectly_positive=incorrectly_positive)
    generator, point, evidence, _, chapter, _ = actual_fixture(client)
    lesson = teaching._Lesson.model_validate({**lesson_candidate(), "summary": point.explanation,
                                             "reading_source_ids": point.source_ids})
    job = generator._build_fact_jobs([], lesson, [], chapter, evidence, [], lesson_component_ids={"summary"})[0]
    assert job["payload"]["lesson_components"]["summary"]["scope_boundaries"] == []
    if incorrectly_positive:
        with pytest.raises(teaching.TeachingServiceUnavailableError):
            generator._run_fact_job(job)
        assert len(client.calls) == teaching.FACT_FORMAT_ATTEMPTS
    else:
        result = generator._run_fact_job(job)
        assert not result.accepted and not result.lesson.accepted
        proof = result.lesson.components["summary"]
        assert not proof.source_entailment
        assert any(row.kind == "missing_condition" and row.evidence_source_ids == [CONDITION] for row in proof.findings)
        assert all(row["checks"] == [] for row in proof.scope_matrix)
        assert len(client.calls) == 1


def test_actual_overlong_second_writer_response_remains_invalid_with_explicit_writer_budget():
    observed = OBSERVED["terminal_task_feedback"]
    summary = observed["attempt2_summary"]
    assert len(summary) == observed["attempt2_summary_length"] == 713
    assert hashlib.sha256(summary.encode()).hexdigest() == observed["attempt2_summary_sha256"]
    with pytest.raises(ValidationError) as caught:
        teaching._Generation.model_validate({"points": [], "lesson": {
            **lesson_candidate(), "summary": summary,
        }})
    errors = caught.value.errors()
    assert any(row["loc"] == ("lesson", "summary") and row["type"] == "string_too_long"
               and row["ctx"]["max_length"] == 680 for row in errors)
    # The request now exposes the existing hard contract; it does not relax it
    # or spend another model call to truncate a scientific summary mechanically.
    assert "summary须30-680字" in teaching.GENERATION_PROMPT


def test_actual_unanswerable_task_and_lesson_source_distinctness_vetoes_reach_only_their_writers():
    observed = OBSERVED["terminal_task_feedback"]
    point_id = observed["point_id"]
    row = teaching._PointReview.model_validate(observed["point_review"])
    assert not row.practice_ok and row.explanation_ok and row.flashcard_ok
    assert "为什么" in observed["practice"]["prompt"]
    feedback = [row.model_dump(), {"lesson": observed["lesson_review"]},
                {"accepted": False, "issues": observed["overall_issues"]}]
    own = teaching.TeachingContentGenerator._scoped_feedback(feedback, {point_id}, False)
    assert next(value for value in own if value.get("point_id") == point_id)["issues"] == row.issues
    lesson = teaching.TeachingContentGenerator._scoped_feedback(feedback, set(), True)
    assert {"lesson": observed["lesson_review"]} in lesson
    assert not any(value.get("point_id") == point_id for value in lesson)
    lesson_issues = [issue for value in lesson for issue in value.get("issues", [])]
    assert all(issue in lesson_issues for issue in observed["overall_issues"] if issue.startswith("lesson."))
    # This source/teacher rejection stays negative. Prompt guidance cannot turn
    # a structurally valid old practice or a merely rephrased answer into approval.
    assert not observed["lesson_review"]["reading_ok"]
    assert not observed["lesson_review"]["examples_ok"]
    prompt = teaching.GENERATION_PROMPT
    assert prompt.index("第一步") < prompt.index("未提供原因或足够推导前提") < prompt.index("第二步")
    assert "同步重写最小答案和所有评分点" in prompt
