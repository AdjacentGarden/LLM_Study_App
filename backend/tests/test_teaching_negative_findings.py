"""Actual A6171 contradiction: retain a located veto, never manufacture approval.

The frozen fixture is limited to one example and its two original fact replies.
No provider call, teacher approval or textbook-quality assertion is simulated.
"""

import json
from copy import deepcopy
from pathlib import Path

import pytest
from test_teaching_content import lesson_candidate
from test_teaching_quote_format_feedback import SequenceClient

from adaptive_learning.personalization import teaching_content as teaching
from adaptive_learning.personalization.models import SourceCitation
from adaptive_learning.personalization.teaching_evidence import TeachingEvidence
from adaptive_learning.personalization.teaching_review_reuse import VerifiedComponentReuse


@pytest.fixture
def observed():
    data = json.loads((Path(__file__).parent / "fixtures" /
                       "teaching_negative_findings_a6171.json").read_text())
    evidence = TeachingEvidence(
        sources={key: SourceCitation(**value) for key, value in data["sources"].items()},
        point_sources={}, warnings=[], fingerprint="a6171-example-3-original",
    )
    lesson = lesson_candidate()
    lesson["worked_examples"].append({**deepcopy(data["part"]["content"]),
                                      "source_ids": data["part"]["source_ids"]})
    lesson = teaching._Lesson.model_validate(lesson)
    payload = {
        "sources": data["sources"], "support_spans": data["spans"],
        "lesson_components": {"example_3": data["part"]},
        "candidate": {"points": [], "lesson": {"review_component_ids": ["example_3"]}},
        "knowledge_points": [], "scope_contexts": [], "scope_conditions": {},
        "output_contract": {"point_ids": [], "lesson_component_ids": ["example_3"],
                            "lesson_required": True},
    }
    job = {"payload": payload, "batch": [], "lesson": lesson, "evidence": evidence,
           "spans": data["spans"], "include_lesson": True,
           "lesson_component_ids": {"example_3"}}
    return data, job


def step4(row):
    return next(check for check in row["assertion_checks"] if any(
        finding["quote"] == "又通过测交等方法验证" for finding in check["findings"]
    ))


def test_actual_second_reply_downgrades_only_the_inconsistent_flag_before_full_validation(observed):
    data, job = observed
    row = deepcopy(data["responses"][1]["components"][0])
    original = deepcopy(row)
    target = step4(row)
    assert target["source_entailment"] is True
    assert target["findings"][0]["kind"] == "unsupported_claim"
    events = []
    generator = teaching.TeachingContentGenerator(SequenceClient([]), audit_callback=events.append)
    generator._preserve_lesson_negative_findings(row, job["payload"], job["evidence"], job["spans"])
    expected = deepcopy(original)
    step4(expected)["source_entailment"] = False
    assert row == expected
    event, = events
    assert event["event"] == "lesson_negative_normalization"
    assert event["approval_reusable"] is False
    assert event["changes"][0]["raw_assertion_check"] == step4(original)
    assert event["changes"][0]["normalized_source_entailment"] is False
    generator._normalize_lesson_selection(row, job["payload"], job["evidence"], job["spans"])
    assert row["source_entailment"] is False and row["scope_exceptions"] is False
    assert any(finding["candidate_quote"] == "又通过测交等方法验证"
               and finding["kind"] == "unsupported_claim" for finding in row["findings"])


def test_actual_two_reply_sequence_returns_a_scoped_negative_instead_of_format_failure(observed):
    data, job = observed
    client = SequenceClient(data["responses"])
    events = []
    result = teaching.TeachingContentGenerator(client, audit_callback=events.append)._request_fact_batch(**job)
    assert len(client.requests) == 2  # The existing bound; no added retry.
    first_issue = client.requests[1]["format_feedback"][0]
    assert "assertion_36a3b91fc6e2382c31f46deb" in first_issue
    assert "field=step_1" in first_issue and "scope_overreach" in first_issue
    assert "same assertion" in first_issue
    assert not result.accepted and not result.lesson.source_entailment
    proof = result.lesson.components["example_3"]
    assert not proof.source_entailment and not proof.scope_exceptions
    assert any(finding.candidate_quote == "又通过测交等方法验证"
               and finding.kind == "unsupported_claim" for finding in proof.findings)
    raw = [event for event in events if event["event"] == "model_response"]
    assert step4(raw[1]["response"]["components"][0])["source_entailment"] is True
    assert [event["event"] for event in events].index("lesson_negative_normalization") > [
        event["event"] for event in events].index("model_response")
    feedback = teaching.TeachingContentGenerator._fact_feedback(result, set())
    assert "又通过测交等方法验证" in json.dumps(feedback, ensure_ascii=False)


def test_normalized_negative_never_enters_approval_reuse(observed):
    data, job = observed
    client = SequenceClient([data["responses"][1], data["responses"][1]])
    events = []
    generator = teaching.TeachingContentGenerator(client, audit_callback=events.append)
    # Historical isolated jobs predate candidate navigation. Add the real
    # current derived metadata, preserving the recorded review and all its vetoes.
    part = job["payload"]["lesson_components"]["example_3"]
    part.update(generator._candidate_source_context(
        generator._lesson_component_fields(job["lesson"], "example_3"),
        part["source_ids"], job["evidence"],
    ))
    cache = VerifiedComponentReuse(
        source_texts={key: value.quote for key, value in job["evidence"].sources.items()},
        context={"chapter_id": "example_3", "evidence_fingerprint": "a6171",
                 "review_identity": "recorded-independent-reviewer", "prompt_sha256": "recorded",
                 "version": teaching.TEACHING_CONTENT_VERSION},
    )
    token = teaching._FACT_REUSE.set(cache)
    try:
        first = generator._run_fact_job(job)
        second = generator._run_fact_job(job)
    finally:
        teaching._FACT_REUSE.reset(token)
    assert not first.accepted and not second.accepted
    assert len(client.requests) == 2  # Both re-audit; the first is not approval.
    assert not any(event["event"] == "fact_components_reused" for event in events)


@pytest.mark.parametrize("kind", ["unsupported_claim", "contradiction", "invalid_inference"])
def test_legal_located_factual_veto_can_only_downgrade_source_positive(kind, observed):
    data, job = observed
    row = deepcopy(data["responses"][1]["components"][0])
    check = step4(row)
    check["findings"][0]["kind"] = kind
    original = deepcopy(row)
    proposed = teaching.TeachingContentGenerator._lesson_negative_candidate(
        row, job["payload"], job["evidence"], job["spans"]
    )
    assert proposed is not None and row == original
    candidate, _ = proposed
    expected = deepcopy(original)
    step4(expected)["source_entailment"] = False
    assert candidate == expected


@pytest.mark.parametrize("damage", ["wrong_quote", "unknown_source", "duplicate_source", "unknown_span",
                                    "raw_span_changed", "omitted_assertion", "omitted_matrix",
                                    "string_bool", "nonfactual", "foreign_finding_quote"])
def test_invalid_provenance_or_coverage_is_not_normalized_or_approved(damage, observed):
    data, job = observed
    row = deepcopy(data["responses"][1]["components"][0])
    target = step4(row)
    spans = deepcopy(job["spans"])
    if damage == "wrong_quote":
        target["findings"][0]["quote"] = "当前断言没有出现的伪造问题片段"
    elif damage == "unknown_source":
        target["findings"][0]["evidence_source_ids"] = ["unknown"]
    elif damage == "duplicate_source":
        target["findings"][0]["evidence_source_ids"] *= 2
    elif damage == "unknown_span":
        target["support_span_ids"] = ["unknown"]
    elif damage == "raw_span_changed":
        spans[target["support_span_ids"][0]]["quote"] = "伪造的连续原文证明不能被负向规范化隐藏。"
    elif damage == "omitted_assertion":
        row["assertion_checks"] = row["assertion_checks"][1:]
    elif damage == "omitted_matrix":
        row["scope_matrix"] = row["scope_matrix"][1:]
    elif damage == "string_bool":
        target["source_entailment"] = "true"
    elif damage == "nonfactual":
        target["is_factual"] = False
    else:
        target["findings"][0]["quote"] = data["part"]["component_fields"]["problem"][:30]
    original = deepcopy(row)
    assert teaching.TeachingContentGenerator._lesson_negative_candidate(
        row, job["payload"], job["evidence"], spans
    ) is None
    assert row == original
    with pytest.raises((ValueError, teaching.TeachingContentError)):
        teaching.TeachingContentGenerator._normalize_lesson_selection(
            row, job["payload"], job["evidence"], spans
        )


def test_scope_only_inconsistency_is_not_turned_into_fabricated_negative_source_or_matrix(observed):
    data, job = observed
    row = deepcopy(data["responses"][0]["components"][0])
    original = deepcopy(row)
    assert teaching.TeachingContentGenerator._lesson_negative_candidate(
        row, job["payload"], job["evidence"], job["spans"]
    ) is None
    assert row == original
    with pytest.raises(ValueError, match="assertion_36a3b91fc6e2382c31f46deb.*step_1"):
        teaching.TeachingContentGenerator._normalize_lesson_selection(
            row, job["payload"], job["evidence"], job["spans"]
        )


def test_audit_failure_prevents_mutating_the_raw_inconsistent_row(observed):
    data, job = observed
    row = deepcopy(data["responses"][1]["components"][0])
    original = deepcopy(row)
    def unavailable(_):
        raise OSError("audit unavailable")
    generator = teaching.TeachingContentGenerator(SequenceClient([]), audit_callback=unavailable)
    with pytest.raises(teaching.TeachingServiceUnavailableError):
        generator._preserve_lesson_negative_findings(row, job["payload"], job["evidence"], job["spans"])
    assert row == original
